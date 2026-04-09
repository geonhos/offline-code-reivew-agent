"""Offline LLM 전체 파일 벤치마크 — diff 내 모든 리뷰 대상 파일을 개별 리뷰.

Usage:
    python -m scripts.benchmark_offline_full --model codegemma:7b-instruct
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx

from src.config import settings
from src.diff_parser import parse_diff
from src.metrics_collector import BenchmarkResult, MetricsCollector, count_diff_lines
from src.prompt import build_review_prompt

_FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"

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


def check_issue(comments: list[dict], keywords: list[str]) -> bool:
    text = json.dumps(comments, ensure_ascii=False).lower()
    return any(kw.lower() in text for kw in keywords)


def parse_llm_response(response: str) -> tuple[list[dict], bool]:
    json_match = re.search(r"```(?:json)?\s*(\[.*?])\s*```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_match = re.search(r"\[.*]", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            return [], False
    try:
        items = json.loads(json_str)
        return (items, True) if isinstance(items, list) else ([], False)
    except json.JSONDecodeError:
        return [], False


def call_ollama(system_prompt: str, user_prompt: str, model: str) -> str:
    resp = httpx.post(
        f"{settings.ollama_base_url}/api/generate",
        json={
            "model": model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": settings.benchmark_num_ctx},
        },
        timeout=300.0,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    model = args.model
    safe = model.replace(":", "_")
    output = args.output or f"results/benchmark_{safe}_full.json"

    fixtures = {}
    for name in ["sample.diff", "springboot-ddd.diff", "ecommerce-platform.diff"]:
        fixtures[name] = (_FIXTURES_DIR / name).read_text(encoding="utf-8")

    collector = MetricsCollector()

    print(f"\n{'=' * 80}")
    print(f"  모델: {model}  (전체 파일 리뷰)")
    print(f"{'=' * 80}")

    for diff_name, diff_text in fixtures.items():
        diff_result = parse_diff(diff_text)
        reviewable = diff_result.reviewable_files
        expected = EXPECTED_ISSUES.get(diff_name, {})

        print(f"\n  [{diff_name}] 리뷰 대상 파일: {len(reviewable)}개")

        all_comments: list[dict] = []
        total_time = 0.0
        all_parse_ok = True

        for i, file_diff in enumerate(reviewable):
            total_lines = len(file_diff.added_lines) + len(file_diff.deleted_lines)
            if total_lines == 0:
                continue
            if total_lines > 500:
                print(f"    [{i+1}/{len(reviewable)}] {file_diff.filename} ({total_lines}줄) — SKIP (500줄 초과)")
                continue

            print(f"    [{i+1}/{len(reviewable)}] {file_diff.filename} ({total_lines}줄)...", end="", flush=True)

            system_prompt, user_prompt = build_review_prompt(file_diff, guidelines=[])

            start = time.monotonic()
            try:
                raw = call_ollama(system_prompt, user_prompt, model)
                elapsed = time.monotonic() - start
                comments, parse_ok = parse_llm_response(raw)
                all_comments.extend(comments)
                total_time += elapsed
                if not parse_ok:
                    all_parse_ok = False
                print(f" {elapsed:.1f}s, {len(comments)} comments")
            except Exception as exc:
                elapsed = time.monotonic() - start
                total_time += elapsed
                all_parse_ok = False
                print(f" ERROR ({elapsed:.1f}s): {exc}")

        detected = [label for label, kw in expected.items() if check_issue(all_comments, kw)]
        expected_labels = list(expected.keys())
        detection_rate = len(detected) / len(expected_labels) if expected_labels else 0.0

        print(f"\n    --- {diff_name} 종합 ---")
        print(f"    코멘트: {len(all_comments)}, 감지율: {detection_rate*100:.0f}% ({len(detected)}/{len(expected_labels)}), 시간: {total_time:.1f}s")
        for label in expected_labels:
            icon = "v" if label in detected else "x"
            print(f"      [{icon}] {label}")

        result = BenchmarkResult(
            model=f"{model} (full)",
            diff_name=diff_name,
            diff_lines=count_diff_lines(diff_text),
            review_time_sec=round(total_time, 3),
            comment_count=len(all_comments),
            comments_by_severity={
                "critical": sum(1 for c in all_comments if c.get("severity") == "critical"),
                "warning": sum(1 for c in all_comments if c.get("severity") == "warning"),
                "info": sum(1 for c in all_comments if c.get("severity") == "info"),
            },
            json_parse_success=all_parse_ok,
            raw_response=json.dumps(all_comments, ensure_ascii=False),
            input_tokens=0, output_tokens=0, total_tokens=0, cost_usd=0.0,
            detected_issues=detected,
            expected_issues=expected_labels,
            detection_rate=round(detection_rate, 4),
        )
        collector.record(result)

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    collector.save_json(output)

    summary = collector.summary_by_model()
    for m, s in summary.items():
        print(f"\n  {m}: detect={s['avg_detection_rate']*100:.1f}% time={s['avg_time_sec']:.1f}s")
    print(f"\n결과 저장: {output}")


if __name__ == "__main__":
    main()
