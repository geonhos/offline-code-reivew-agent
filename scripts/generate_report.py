"""벤치마크 결과 → Markdown 리포트 생성.

Usage:
    python -m scripts.generate_report                          # results/ 내 모든 JSON 로드
    python -m scripts.generate_report --input results/benchmark_cloud.json
    python -m scripts.generate_report --output docs/report.md  # 출력 경로 지정
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.metrics_collector import BenchmarkResult, MetricsCollector

logger = logging.getLogger(__name__)

# ─── 상수 ─────────────────────────────────────────────────────

_RESULTS_DIR = Path(__file__).parent.parent / "results"
_DEFAULT_OUTPUT = _RESULTS_DIR / "benchmark_report.md"

# 클라우드 모델 판별 접두사
_CLOUD_PREFIXES = ("gpt-", "claude-")

# diff_name → 사람이 읽기 좋은 레이블 매핑
_DIFF_LABELS: dict[str, str] = {
    "sample.diff": "sample (Python)",
    "springboot-ddd.diff": "springboot (Java/DDD)",
    "ecommerce-platform.diff": "ecommerce (Java/Security)",
}


# ─── 유틸리티 ────────────────────────────────────────────────


def _is_cloud(model: str) -> bool:
    """모델 이름으로 클라우드 모델 여부를 판별한다.

    Args:
        model: 모델 식별자 (예: "gpt-4o", "codegemma:7b-instruct").

    Returns:
        gpt- 또는 claude- 접두사가 있으면 True.
    """
    return any(model.startswith(p) for p in _CLOUD_PREFIXES)


def _model_type(model: str) -> str:
    """모델 유형 문자열을 반환한다.

    Args:
        model: 모델 식별자.

    Returns:
        "Cloud" 또는 "Offline".
    """
    return "Cloud" if _is_cloud(model) else "Offline"


def _pct(rate: float) -> str:
    """0–1 비율을 백분율 문자열로 변환한다 (예: 0.75 → "75.0%").

    Args:
        rate: 0.0–1.0 범위의 비율.

    Returns:
        소수점 1자리 백분율 문자열.
    """
    return f"{rate * 100:.1f}%"


def _fmt_cost(cost: float) -> str:
    """달러 비용을 표시용 문자열로 변환한다.

    Args:
        cost: USD 금액.

    Returns:
        "$0.0000" 형식의 문자열. 오프라인 모델(0.0)도 그대로 표시.
    """
    return f"${cost:.4f}"


def _diff_label(diff_name: str) -> str:
    """diff 파일명을 사람이 읽기 좋은 레이블로 변환한다.

    Args:
        diff_name: diff 파일명 (예: "sample.diff").

    Returns:
        레이블 문자열. 매핑이 없으면 원본 파일명 반환.
    """
    return _DIFF_LABELS.get(diff_name, diff_name)


# ─── 데이터 로드 ──────────────────────────────────────────────


def load_results(input_path: str | None) -> list[BenchmarkResult]:
    """JSON 파일에서 BenchmarkResult 목록을 로드한다.

    input_path가 지정되면 해당 파일만, 없으면 results/benchmark_*.json
    패턴에 해당하는 모든 파일을 로드한다.

    Args:
        input_path: 특정 JSON 파일 경로. None이면 자동 탐색.

    Returns:
        BenchmarkResult 인스턴스 목록.

    Raises:
        FileNotFoundError: 입력 파일이 없을 때.
        ValueError: 결과가 하나도 없을 때.
    """
    collector = MetricsCollector()

    if input_path:
        path = Path(input_path)
        if not path.exists():
            raise FileNotFoundError(f"입력 파일 없음: {path}")
        logger.info("파일 로드: %s", path)
        collector.load_json(str(path))
    else:
        json_files = sorted(_RESULTS_DIR.glob("benchmark_*.json"))
        if not json_files:
            raise FileNotFoundError(
                f"JSON 파일 없음: {_RESULTS_DIR}/benchmark_*.json\n"
                "먼저 scripts/benchmark_models.py 를 실행하세요."
            )
        for p in json_files:
            logger.info("파일 로드: %s", p)
            collector.load_json(str(p))

    results = collector.get_results()
    if not results:
        raise ValueError("로드된 벤치마크 결과가 없습니다.")

    logger.info("총 %d 건 로드 완료", len(results))
    return results


# ─── 섹션 생성 함수 ──────────────────────────────────────────


def _section_overview(results: list[BenchmarkResult]) -> str:
    """1. 개요 섹션을 생성한다.

    Args:
        results: 전체 BenchmarkResult 목록.

    Returns:
        Markdown 문자열.
    """
    models = sorted({r.model for r in results})
    diffs = sorted({r.diff_name for r in results})
    offline_models = [m for m in models if not _is_cloud(m)]
    cloud_models = [m for m in models if _is_cloud(m)]

    lines: list[str] = [
        "## 1. 개요",
        "",
        f"- **벤치마크 생성일**: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- **총 실행 수**: {len(results)}",
        f"- **모델 수**: {len(models)} ({len(offline_models)} Offline / {len(cloud_models)} Cloud)",
        f"- **테스트 diff 수**: {len(diffs)}",
        "",
        "### 테스트 환경",
        "",
        "| 구분 | 모델 |",
        "| --- | --- |",
    ]

    for m in offline_models:
        lines.append(f"| Offline (Ollama) | `{m}` |")
    for m in cloud_models:
        lines.append(f"| Cloud (API) | `{m}` |")

    lines += [
        "",
        "### 테스트 Diff",
        "",
        "| 파일명 | 레이블 |",
        "| --- | --- |",
    ]
    for d in diffs:
        lines.append(f"| `{d}` | {_diff_label(d)} |")

    lines.append("")
    return "\n".join(lines)


def _section_model_comparison(summary: dict[str, dict[str, Any]]) -> str:
    """2. 모델별 종합 성능 비교표 섹션을 생성한다.

    감지율 내림차순으로 정렬한다.

    Args:
        summary: MetricsCollector.summary_by_model() 반환값.

    Returns:
        Markdown 문자열.
    """
    sorted_models = sorted(
        summary.items(),
        key=lambda kv: kv[1]["avg_detection_rate"],
        reverse=True,
    )

    lines: list[str] = [
        "## 2. 모델별 종합 성능 비교",
        "",
        "| 모델 | 유형 | 평균 시간 | 감지율 | JSON 파싱률 | 평균 코멘트 | 총 토큰 | 총 비용 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for model, stats in sorted_models:
        mtype = _model_type(model)
        avg_time = f"{stats['avg_time_sec']:.1f}s"
        detect = _pct(stats["avg_detection_rate"])
        json_parse = _pct(stats["json_parse_success_rate"])
        avg_comments = f"{stats['avg_comment_count']:.1f}"
        total_tokens = str(stats["total_tokens"]) if stats["total_tokens"] > 0 else "-"
        cost = _fmt_cost(stats["total_cost_usd"]) if stats["total_cost_usd"] > 0 else "-"

        lines.append(
            f"| `{model}` | {mtype} | {avg_time} | {detect} | {json_parse}"
            f" | {avg_comments} | {total_tokens} | {cost} |"
        )

    lines.append("")
    return "\n".join(lines)


def _section_per_diff(results: list[BenchmarkResult]) -> str:
    """3. Diff 크기별 성능 비교표 섹션을 생성한다.

    각 diff별로 모델 결과를 나열한다.

    Args:
        results: 전체 BenchmarkResult 목록.

    Returns:
        Markdown 문자열.
    """
    # diff_name → list[BenchmarkResult] 그룹화
    by_diff: dict[str, list[BenchmarkResult]] = defaultdict(list)
    for r in results:
        by_diff[r.diff_name].append(r)

    lines: list[str] = ["## 3. Diff 별 성능 비교", ""]

    for diff_name in sorted(by_diff.keys()):
        diff_results = sorted(
            by_diff[diff_name],
            key=lambda r: r.detection_rate,
            reverse=True,
        )
        label = _diff_label(diff_name)
        # diff_lines는 동일 diff이면 동일하므로 첫 번째 결과에서 취득
        diff_lines = diff_results[0].diff_lines if diff_results else 0

        lines += [
            f"### {label} (`{diff_name}`, {diff_lines} lines)",
            "",
            "| 모델 | 시간(s) | 코멘트 수 | 감지율 | 감지 이슈 | 미감지 이슈 |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]

        for r in diff_results:
            missing = [i for i in r.expected_issues if i not in r.detected_issues]
            detected_str = ", ".join(r.detected_issues) if r.detected_issues else "-"
            missing_str = ", ".join(missing) if missing else "-"

            lines.append(
                f"| `{r.model}` | {r.review_time_sec:.1f} | {r.comment_count}"
                f" | {_pct(r.detection_rate)} | {detected_str} | {missing_str} |"
            )

        lines.append("")

    return "\n".join(lines)


def _section_offline_vs_cloud(results: list[BenchmarkResult]) -> str:
    """4. Offline vs Cloud 비교 섹션을 생성한다.

    Args:
        results: 전체 BenchmarkResult 목록.

    Returns:
        Markdown 문자열.
    """
    offline = [r for r in results if not _is_cloud(r.model)]
    cloud = [r for r in results if _is_cloud(r.model)]

    def _avg(lst: list[float]) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    def _stats(group: list[BenchmarkResult]) -> dict[str, Any]:
        if not group:
            return {"count": 0, "avg_time": 0.0, "avg_detect": 0.0, "total_cost": 0.0}
        return {
            "count": len(group),
            "avg_time": _avg([r.review_time_sec for r in group]),
            "avg_detect": _avg([r.detection_rate for r in group]),
            "total_cost": sum(r.cost_usd for r in group),
        }

    off_stats = _stats(offline)
    cld_stats = _stats(cloud)

    lines: list[str] = [
        "## 4. Offline vs Cloud 비교",
        "",
        "| 항목 | Offline (Ollama) | Cloud (API) |",
        "| --- | ---: | ---: |",
        f"| 실행 수 | {off_stats['count']} | {cld_stats['count']} |",
        f"| 평균 리뷰 시간 | {off_stats['avg_time']:.1f}s | {cld_stats['avg_time']:.1f}s |",
        f"| 평균 감지율 | {_pct(off_stats['avg_detect'])} | {_pct(cld_stats['avg_detect'])} |",
        f"| 총 비용 | - | {_fmt_cost(cld_stats['total_cost'])} |",
        "",
    ]

    return "\n".join(lines)


def _section_token_analysis(results: list[BenchmarkResult]) -> str:
    """5. 토큰 소모량 분석 섹션을 생성한다 (Cloud 모델만).

    Args:
        results: 전체 BenchmarkResult 목록.

    Returns:
        Markdown 문자열.
    """
    cloud_results = [r for r in results if _is_cloud(r.model)]

    lines: list[str] = ["## 5. 토큰 소모량 분석 (Cloud)", ""]

    if not cloud_results:
        lines += ["_Cloud 모델 결과 없음_", ""]
        return "\n".join(lines)

    lines += [
        "| 모델 | Diff | Input Tokens | Output Tokens | Total | 비용(USD) |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]

    total_input = total_output = total_all = 0
    total_cost = 0.0

    for r in sorted(cloud_results, key=lambda x: (x.model, x.diff_name)):
        lines.append(
            f"| `{r.model}` | {_diff_label(r.diff_name)}"
            f" | {r.input_tokens:,} | {r.output_tokens:,}"
            f" | {r.total_tokens:,} | {_fmt_cost(r.cost_usd)} |"
        )
        total_input += r.input_tokens
        total_output += r.output_tokens
        total_all += r.total_tokens
        total_cost += r.cost_usd

    # 합계 행
    lines += [
        f"| **합계** | - | **{total_input:,}** | **{total_output:,}**"
        f" | **{total_all:,}** | **{_fmt_cost(total_cost)}** |",
        "",
    ]

    return "\n".join(lines)


def _section_mermaid_charts(summary: dict[str, dict[str, Any]]) -> str:
    """6. Mermaid 차트 코드 섹션을 생성한다.

    감지율 내림차순으로 정렬된 bar 차트 2개(감지율, 리뷰 시간)를 출력한다.

    Args:
        summary: MetricsCollector.summary_by_model() 반환값.

    Returns:
        Markdown 문자열.
    """
    sorted_models = sorted(
        summary.items(),
        key=lambda kv: kv[1]["avg_detection_rate"],
        reverse=True,
    )

    # 모델 레이블: ":" 문자가 포함될 수 있어 따옴표로 감싼다
    model_labels = [f'"{m}"' for m, _ in sorted_models]
    detection_values = [
        round(stats["avg_detection_rate"] * 100, 1) for _, stats in sorted_models
    ]
    time_values = [round(stats["avg_time_sec"], 1) for _, stats in sorted_models]

    x_axis = ", ".join(model_labels)

    detection_chart = (
        "```mermaid\n"
        "xychart-beta\n"
        '    title "모델별 감지율 비교"\n'
        f"    x-axis [{x_axis}]\n"
        '    y-axis "감지율 (%)" 0 --> 100\n'
        f"    bar [{', '.join(str(v) for v in detection_values)}]\n"
        "```"
    )

    time_chart = (
        "```mermaid\n"
        "xychart-beta\n"
        '    title "모델별 평균 리뷰 시간 비교"\n'
        f"    x-axis [{x_axis}]\n"
        '    y-axis "시간 (초)"\n'
        f"    bar [{', '.join(str(v) for v in time_values)}]\n"
        "```"
    )

    lines: list[str] = [
        "## 6. 시각화 차트",
        "",
        "### 감지율 비교",
        "",
        detection_chart,
        "",
        "### 평균 리뷰 시간 비교",
        "",
        time_chart,
        "",
    ]

    return "\n".join(lines)


# ─── 리포트 조립 ─────────────────────────────────────────────


def generate_report(results: list[BenchmarkResult]) -> str:
    """BenchmarkResult 목록으로부터 전체 Markdown 리포트 문자열을 생성한다.

    Args:
        results: 로드된 BenchmarkResult 인스턴스 목록.

    Returns:
        완성된 Markdown 문자열.
    """
    collector = MetricsCollector()
    for r in results:
        collector.record(r)
    summary = collector.summary_by_model()

    sections: list[str] = [
        "# LLM 코드 리뷰 벤치마크 리포트",
        "",
        "> 오프라인(Ollama) 모델과 클라우드(GPT-4o, Claude) 모델의 코드 리뷰 성능 비교 결과입니다.",
        "",
        _section_overview(results),
        _section_model_comparison(summary),
        _section_per_diff(results),
        _section_offline_vs_cloud(results),
        _section_token_analysis(results),
        _section_mermaid_charts(summary),
    ]

    return "\n".join(sections)


# ─── CLI ──────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    """CLI 인자를 파싱한다.

    Returns:
        파싱된 argparse.Namespace.
    """
    parser = argparse.ArgumentParser(
        description="벤치마크 결과 JSON → Markdown 리포트 생성",
    )
    parser.add_argument(
        "--input",
        metavar="PATH",
        default=None,
        help="특정 JSON 파일 경로 (기본값: results/benchmark_*.json 전체 로드)",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=str(_DEFAULT_OUTPUT),
        help=f"출력 Markdown 파일 경로 (기본값: {_DEFAULT_OUTPUT})",
    )
    return parser.parse_args()


def main() -> None:
    """리포트 생성 메인 진입점."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args = _parse_args()

    # 결과 로드
    print("벤치마크 결과 로드 중...")
    results = load_results(args.input)
    print(f"  로드 완료: {len(results)} 건\n")

    # 리포트 생성
    print("Markdown 리포트 생성 중...")
    report_md = generate_report(results)

    # 출력 파일 저장
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_md, encoding="utf-8")

    print(f"리포트 저장 완료: {output_path.resolve()}")
    print(f"  총 {len(report_md.splitlines())} 줄, {len(report_md):,} 바이트")


if __name__ == "__main__":
    main()
