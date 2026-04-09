"""LLM 모델 벤치마크 — Offline(Ollama) vs Cloud(GPT-4o, Claude Sonnet) 성능 비교.

Usage:
    python -m scripts.benchmark_models                    # 전체 실행
    python -m scripts.benchmark_models --offline-only     # 오프라인 모델만
    python -m scripts.benchmark_models --cloud-only       # 클라우드 모델만
    python -m scripts.benchmark_models --models codegemma:7b-instruct gpt-4o  # 특정 모델만
    python -m scripts.benchmark_models --output results/custom.json           # 출력 경로 지정
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

from src.cloud_reviewer import CloudReviewer
from src.config import settings
from src.diff_parser import parse_diff
from src.metrics_collector import BenchmarkResult, MetricsCollector, count_diff_lines
from src.prompt import build_review_prompt

logger = logging.getLogger(__name__)

# ─── 테스트 픽스처 경로 ────────────────────────────────────────

_FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"

# ─── 픽스처별 기대 이슈 정의 ──────────────────────────────────
# 각 diff_name → {이슈_레이블: [키워드_목록]} 형태
# compare_fewshot.py의 check_issue() 패턴과 동일하게 키워드 매칭으로 검출 판정

EXPECTED_ISSUES: dict[str, dict[str, list[str]]] = {
    "sample.diff": {
        "하드코딩 비밀번호": ["password", "하드코딩", "환경 변수", "secret", "api_key", "hardcod"],
        "SQL 인젝션": ["sql", "injection", "인젝션", "f-string", "파라미터", "format"],
        "빈 except 절": ["except", "예외", "exception"],
    },
    "springboot-ddd.diff": {
        "도메인 레이어 의존성 위반": ["domain", "도메인", "의존성", "infrastructure", "인프라"],
        "트랜잭션 처리 누락": ["transactional", "transaction", "트랜잭션"],
        "N+1 쿼리 문제": ["n+1", "fetchtype", "lazy", "eager", "join", "fetch"],
        "예외 처리 미흡": ["exception", "예외", "orelsethrow", "optional"],
    },
    "ecommerce-platform.diff": {
        "하드코딩 비밀번호": ["password", "secret", "하드코딩", "hardcod", "yml", "yaml"],
        "MD5 취약한 해시": ["md5", "sha", "bcrypt", "해시", "hash", "passwordencoder"],
        "SQL 인젝션": ["sql", "injection", "인젝션", "preparedstatement", "파라미터"],
        "JWT 시크릿 하드코딩": ["jwt", "secret", "signing", "서명", "토큰"],
    },
}

# ─── Ollama 호출 상수 ──────────────────────────────────────────

_OLLAMA_TIMEOUT = 300.0
_OLLAMA_TEMPERATURE = 0.1


# ─── 유틸리티 함수 ────────────────────────────────────────────


def check_issue(comments: list[dict], keywords: list[str]) -> bool:
    """코멘트 목록에서 키워드 중 하나라도 포함되면 검출로 판정한다.

    Args:
        comments: 파싱된 리뷰 코멘트 딕셔너리 목록.
        keywords: 검출 판정을 위한 키워드 목록 (대소문자 무시).

    Returns:
        키워드 중 하나라도 발견되면 True.
    """
    text = json.dumps(comments, ensure_ascii=False).lower()
    return any(kw.lower() in text for kw in keywords)


def parse_llm_response(response: str, filename: str) -> tuple[list[dict], bool]:
    """LLM 응답 문자열에서 JSON 배열을 추출한다.

    reviewer.py._parse_response와 동일한 regex 추출 로직을 사용한다.

    Args:
        response: LLM 원본 응답 텍스트.
        filename: 파일명 (파싱 실패 시 로그용).

    Returns:
        (comments_list, parse_success) 튜플.
        파싱 실패 시 빈 리스트와 False를 반환한다.
    """
    # 코드 블록 내 JSON 우선 시도
    json_match = re.search(r"```(?:json)?\s*(\[.*?])\s*```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # 코드 블록 없이 JSON 배열 직접 검색
        json_match = re.search(r"\[.*]", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            logger.debug("JSON 배열 미발견: filename=%s, response_head=%s", filename, response[:100])
            return [], False

    try:
        items = json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.debug("JSON 파싱 실패: filename=%s, error=%s", filename, exc)
        return [], False

    if not isinstance(items, list):
        return [], False

    return items, True


def get_comments_by_severity(comments: list[dict]) -> dict[str, int]:
    """심각도별 코멘트 수를 집계한다.

    Args:
        comments: 파싱된 리뷰 코멘트 딕셔너리 목록.

    Returns:
        {"critical": N, "warning": N, "info": N} 형태의 딕셔너리.
    """
    counts: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    for c in comments:
        severity = c.get("severity", "info")
        if severity in counts:
            counts[severity] += 1
        else:
            counts["info"] += 1
    return counts


# ─── Ollama 관련 함수 ──────────────────────────────────────────


def get_available_ollama_models() -> set[str]:
    """Ollama에서 현재 로드된 모델 목록을 가져온다.

    Returns:
        사용 가능한 모델 이름 집합. 요청 실패 시 빈 집합 반환.
    """
    try:
        resp = httpx.get(
            f"{settings.ollama_base_url}/api/tags",
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return {m["name"] for m in data.get("models", [])}
    except Exception as exc:
        logger.warning("Ollama 모델 목록 조회 실패: %s", exc)
        return set()


def call_ollama(system_prompt: str, user_prompt: str, model: str) -> str:
    """Ollama /api/generate 엔드포인트를 호출한다.

    Args:
        system_prompt: 시스템 프롬프트.
        user_prompt: 사용자 프롬프트.
        model: Ollama 모델 이름 (예: "codegemma:7b-instruct").

    Returns:
        LLM 원본 응답 텍스트.

    Raises:
        httpx.HTTPStatusError: API 오류 응답.
        httpx.TimeoutException: 타임아웃 발생.
    """
    resp = httpx.post(
        f"{settings.ollama_base_url}/api/generate",
        json={
            "model": model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {
                "temperature": _OLLAMA_TEMPERATURE,
                "num_ctx": settings.benchmark_num_ctx,
            },
        },
        timeout=_OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["response"]


# ─── 단일 벤치마크 실행 ────────────────────────────────────────


def run_offline_benchmark(
    model: str,
    diff_name: str,
    diff_text: str,
    expected_issues: dict[str, list[str]],
) -> BenchmarkResult:
    """오프라인 모델 단일 실행 결과를 반환한다.

    Args:
        model: Ollama 모델 이름.
        diff_name: 테스트 diff 파일 이름 (결과 레이블용).
        diff_text: 전체 diff 텍스트.
        expected_issues: {이슈_레이블: [키워드]} 매핑.

    Returns:
        BenchmarkResult 인스턴스.
    """
    diff_result = parse_diff(diff_text)
    reviewable = diff_result.reviewable_files
    # 일관성을 위해 첫 번째 리뷰 대상 파일만 사용
    file_diff = reviewable[0] if reviewable else diff_result.files[0]

    system_prompt, user_prompt = build_review_prompt(file_diff, guidelines=[])

    start = time.monotonic()
    try:
        raw_response = call_ollama(system_prompt, user_prompt, model)
        elapsed = time.monotonic() - start
        error_occurred = False
    except httpx.TimeoutException:
        elapsed = time.monotonic() - start
        logger.warning("Ollama 타임아웃: model=%s diff=%s (%.1fs)", model, diff_name, elapsed)
        raw_response = ""
        error_occurred = True
    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.warning("Ollama 호출 실패: model=%s diff=%s error=%s", model, diff_name, exc)
        raw_response = ""
        error_occurred = True

    comments, parse_success = parse_llm_response(raw_response, file_diff.filename)

    if error_occurred:
        parse_success = False

    # 이슈 검출 평가
    detected: list[str] = []
    for label, keywords in expected_issues.items():
        if check_issue(comments, keywords):
            detected.append(label)

    expected_labels = list(expected_issues.keys())
    detection_rate = len(detected) / len(expected_labels) if expected_labels else 0.0

    return BenchmarkResult(
        model=model,
        diff_name=diff_name,
        diff_lines=count_diff_lines(diff_text),
        review_time_sec=round(elapsed, 3),
        comment_count=len(comments),
        comments_by_severity=get_comments_by_severity(comments),
        json_parse_success=parse_success,
        raw_response=raw_response,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost_usd=0.0,
        detected_issues=detected,
        expected_issues=expected_labels,
        detection_rate=round(detection_rate, 4),
    )


def run_cloud_benchmark(
    model: str,
    diff_name: str,
    diff_text: str,
    expected_issues: dict[str, list[str]],
) -> BenchmarkResult:
    """클라우드 모델 단일 실행 결과를 반환한다.

    Args:
        model: 클라우드 모델 이름 (예: "gpt-4o", "claude-sonnet-4-20250514").
        diff_name: 테스트 diff 파일 이름 (결과 레이블용).
        diff_text: 전체 diff 텍스트.
        expected_issues: {이슈_레이블: [키워드]} 매핑.

    Returns:
        BenchmarkResult 인스턴스.
    """
    diff_result = parse_diff(diff_text)
    reviewable = diff_result.reviewable_files
    file_diff = reviewable[0] if reviewable else diff_result.files[0]

    system_prompt, user_prompt = build_review_prompt(file_diff, guidelines=[])

    reviewer = CloudReviewer()
    start = time.monotonic()
    try:
        llm_resp = reviewer.call_llm(system_prompt, user_prompt, model)
        elapsed = time.monotonic() - start
        raw_response = llm_resp.response
        input_tokens = llm_resp.input_tokens
        output_tokens = llm_resp.output_tokens
        total_tokens = llm_resp.total_tokens
        cost_usd = llm_resp.cost_usd
        error_occurred = False
    except httpx.TimeoutException:
        elapsed = time.monotonic() - start
        logger.warning("Cloud API 타임아웃: model=%s diff=%s (%.1fs)", model, diff_name, elapsed)
        raw_response = ""
        input_tokens = output_tokens = total_tokens = 0
        cost_usd = 0.0
        error_occurred = True
    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.warning("Cloud API 호출 실패: model=%s diff=%s error=%s", model, diff_name, exc)
        raw_response = ""
        input_tokens = output_tokens = total_tokens = 0
        cost_usd = 0.0
        error_occurred = True

    comments, parse_success = parse_llm_response(raw_response, file_diff.filename)

    if error_occurred:
        parse_success = False

    detected: list[str] = []
    for label, keywords in expected_issues.items():
        if check_issue(comments, keywords):
            detected.append(label)

    expected_labels = list(expected_issues.keys())
    detection_rate = len(detected) / len(expected_labels) if expected_labels else 0.0

    return BenchmarkResult(
        model=model,
        diff_name=diff_name,
        diff_lines=count_diff_lines(diff_text),
        review_time_sec=round(elapsed, 3),
        comment_count=len(comments),
        comments_by_severity=get_comments_by_severity(comments),
        json_parse_success=parse_success,
        raw_response=raw_response,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
        detected_issues=detected,
        expected_issues=expected_labels,
        detection_rate=round(detection_rate, 4),
    )


# ─── 결과 출력 함수 ────────────────────────────────────────────


def print_result(result: BenchmarkResult) -> None:
    """단일 벤치마크 결과를 콘솔에 출력한다.

    Args:
        result: 출력할 BenchmarkResult 인스턴스.
    """
    status_icon = "OK" if result.json_parse_success else "FAIL"
    detection_pct = f"{result.detection_rate * 100:.0f}%"
    token_info = f"tokens={result.total_tokens}" if result.total_tokens > 0 else "tokens=N/A (offline)"
    cost_info = f"  cost=${result.cost_usd:.4f}" if result.cost_usd > 0 else ""

    print(
        f"  [{status_icon}] {result.model:<35} diff={result.diff_name:<28}"
        f" time={result.review_time_sec:>6.1f}s"
        f" detect={detection_pct:>4}"
        f" comments={result.comment_count:>3}"
        f" {token_info}{cost_info}"
    )
    # 감지된 이슈 상세 출력
    for label in result.expected_issues:
        found = label in result.detected_issues
        icon = "v" if found else "x"
        print(f"       [{icon}] {label}")


def print_summary_table(summary: dict[str, dict]) -> None:
    """모델별 집계 통계 테이블을 콘솔에 출력한다.

    Args:
        summary: MetricsCollector.summary_by_model() 반환값.
    """
    print(f"\n{'=' * 100}")
    print("  최종 모델별 성능 요약")
    print(f"{'=' * 100}")

    header = (
        f"  {'모델':<35}"
        f" {'avg_time':>10}"
        f" {'detect_rate':>12}"
        f" {'json_parse':>10}"
        f" {'avg_comments':>13}"
        f" {'total_tokens':>13}"
        f" {'total_cost':>11}"
    )
    print(header)
    print(f"  {'─' * 96}")

    # detection_rate 기준 내림차순 정렬
    sorted_models = sorted(
        summary.items(),
        key=lambda kv: kv[1]["avg_detection_rate"],
        reverse=True,
    )

    for model, stats in sorted_models:
        avg_time = f"{stats['avg_time_sec']:.1f}s"
        detect = f"{stats['avg_detection_rate'] * 100:.1f}%"
        json_parse = f"{stats['json_parse_success_rate'] * 100:.1f}%"
        avg_comments = f"{stats['avg_comment_count']:.1f}"
        total_tokens = str(stats["total_tokens"]) if stats["total_tokens"] > 0 else "N/A"
        total_cost = f"${stats['total_cost_usd']:.4f}" if stats["total_cost_usd"] > 0 else "$0.0000"

        print(
            f"  {model:<35}"
            f" {avg_time:>10}"
            f" {detect:>12}"
            f" {json_parse:>10}"
            f" {avg_comments:>13}"
            f" {total_tokens:>13}"
            f" {total_cost:>11}"
        )

    print(f"{'=' * 100}")


# ─── 메인 로직 ────────────────────────────────────────────────


def load_fixtures() -> dict[str, str]:
    """tests/fixtures/ 에서 3개 diff 파일을 로드한다.

    Returns:
        {파일명: diff_텍스트} 딕셔너리.

    Raises:
        FileNotFoundError: 픽스처 파일이 없을 경우.
    """
    fixture_names = ["sample.diff", "springboot-ddd.diff", "ecommerce-platform.diff"]
    fixtures: dict[str, str] = {}
    for name in fixture_names:
        path = _FIXTURES_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"픽스처 파일 없음: {path}")
        fixtures[name] = path.read_text(encoding="utf-8")
        logger.debug("픽스처 로드: %s (%d bytes)", name, len(fixtures[name]))
    return fixtures


def resolve_models(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    """CLI 인자를 기반으로 실행할 오프라인/클라우드 모델 목록을 결정한다.

    Args:
        args: 파싱된 argparse.Namespace.

    Returns:
        (offline_models, cloud_models) 튜플.
    """
    if args.models:
        # --models 로 명시된 모델을 offline/cloud로 분류
        offline: list[str] = []
        cloud: list[str] = []
        cloud_prefixes = ("gpt-", "claude-")
        for m in args.models:
            if any(m.startswith(p) for p in cloud_prefixes):
                cloud.append(m)
            else:
                offline.append(m)
        return offline, cloud

    offline_models = settings.benchmark_offline_models if not args.cloud_only else []
    cloud_models = settings.benchmark_cloud_models if not args.offline_only else []
    return list(offline_models), list(cloud_models)


def main() -> None:
    """벤치마크 메인 진입점."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="LLM 모델 벤치마크 — 오프라인(Ollama) vs 클라우드 성능 비교",
    )
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help="오프라인(Ollama) 모델만 실행",
    )
    parser.add_argument(
        "--cloud-only",
        action="store_true",
        help="클라우드(GPT-4o, Claude) 모델만 실행",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        metavar="MODEL",
        help="특정 모델만 실행 (예: codegemma:7b-instruct gpt-4o)",
    )
    parser.add_argument(
        "--output",
        default="results/benchmark_results.json",
        metavar="PATH",
        help="결과 JSON 저장 경로 (기본값: results/benchmark_results.json)",
    )
    args = parser.parse_args()

    # 결과 디렉토리 생성
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 픽스처 로드
    print("픽스처 로드 중...")
    fixtures = load_fixtures()
    print(f"  로드 완료: {list(fixtures.keys())}\n")

    # 실행할 모델 목록 결정
    offline_models, cloud_models = resolve_models(args)

    # Ollama 사용 가능 모델 사전 확인
    available_ollama: set[str] = set()
    if offline_models:
        print("Ollama 사용 가능 모델 확인 중...")
        available_ollama = get_available_ollama_models()
        if available_ollama:
            print(f"  감지된 모델: {sorted(available_ollama)}")
        else:
            print("  Ollama 미응답 또는 모델 없음 — 오프라인 모델 전체 건너뜀")
        print()

    collector = MetricsCollector()
    total_runs = (len(offline_models) + len(cloud_models)) * len(fixtures)
    run_idx = 0

    # ── 오프라인 모델 벤치마크 ──────────────────────────────────
    for model in offline_models:
        # Ollama에 로드되지 않은 모델 스킵
        if available_ollama and model not in available_ollama:
            print(f"[SKIP] 오프라인 모델 미로드: {model}")
            continue

        print(f"\n모델: {model}  (오프라인 / Ollama)")
        print(f"{'─' * 80}")

        for diff_name, diff_text in fixtures.items():
            run_idx += 1
            expected = EXPECTED_ISSUES.get(diff_name, {})
            print(f"  [{run_idx}/{total_runs}] {diff_name}  진행 중...", end="", flush=True)

            result = run_offline_benchmark(model, diff_name, diff_text, expected)
            collector.record(result)

            # 한 줄 진행 상태 업데이트
            print(f"\r", end="")
            print_result(result)

    # ── 클라우드 모델 벤치마크 ─────────────────────────────────
    for model in cloud_models:
        print(f"\n모델: {model}  (클라우드)")
        print(f"{'─' * 80}")

        for diff_name, diff_text in fixtures.items():
            run_idx += 1
            expected = EXPECTED_ISSUES.get(diff_name, {})
            print(f"  [{run_idx}/{total_runs}] {diff_name}  진행 중...", end="", flush=True)

            result = run_cloud_benchmark(model, diff_name, diff_text, expected)
            collector.record(result)

            print(f"\r", end="")
            print_result(result)

    # ── 결과 저장 및 요약 출력 ─────────────────────────────────
    if not collector.get_results():
        print("\n실행된 벤치마크 결과 없음 — 종료합니다.")
        return

    collector.save_json(str(output_path))
    print(f"\n결과 저장 완료: {output_path.resolve()}")

    summary = collector.summary_by_model()
    print_summary_table(summary)


if __name__ == "__main__":
    main()
