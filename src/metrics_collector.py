"""벤치마크용 공통 메트릭 수집기.

오프라인(Ollama) 모델과 클라우드(GPT-4o, Claude Sonnet) 모델의
코드 리뷰 성능을 측정하고 집계하는 공통 컴포넌트.

사용 예:
    collector = MetricsCollector()
    result = BenchmarkResult(
        model="qwen2.5-coder:14b",
        diff_name="sample.diff",
        diff_lines=count_diff_lines(diff_text),
        review_time_sec=elapsed,
        comment_count=len(comments),
        comments_by_severity={"critical": 1, "warning": 2, "info": 3},
        json_parse_success=True,
        raw_response="...",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost_usd=0.0,
        detected_issues=["sql-injection"],
        expected_issues=["sql-injection", "hardcoded-secret"],
        detection_rate=0.5,
    )
    collector.record(result)
    collector.save_json("results/benchmark.json")
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BenchmarkResult:
    """단일 모델 × 단일 diff 조합의 벤치마크 결과.

    Args:
        model: 모델 식별자 (예: "qwen2.5-coder:14b", "gpt-4o").
        diff_name: 테스트에 사용된 diff 파일 이름 (예: "sample.diff").
        diff_lines: diff의 추가+삭제 라인 수.
        review_time_sec: 리뷰 완료까지 걸린 시간 (초).
        comment_count: 생성된 리뷰 코멘트 수.
        comments_by_severity: 심각도별 코멘트 수 {"critical": N, "warning": N, "info": N}.
        json_parse_success: LLM 응답의 JSON 파싱 성공 여부.
        raw_response: LLM 원본 응답 문자열.
        input_tokens: 입력 토큰 수 (오프라인 모델은 0).
        output_tokens: 출력 토큰 수 (오프라인 모델은 0).
        total_tokens: 전체 토큰 수 (오프라인 모델은 0).
        cost_usd: 추정 비용 달러 (오프라인 모델은 0.0).
        detected_issues: 실제로 감지된 이슈 레이블 목록.
        expected_issues: 기대되는 전체 이슈 레이블 목록.
        detection_rate: 감지율 = len(detected_issues) / len(expected_issues).
                        expected_issues가 비어 있으면 0.0.
    """

    model: str
    diff_name: str
    diff_lines: int
    review_time_sec: float
    comment_count: int
    comments_by_severity: dict[str, int]
    json_parse_success: bool
    raw_response: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    detected_issues: list[str]
    expected_issues: list[str]
    detection_rate: float


@dataclass
class MetricsCollector:
    """벤치마크 결과를 수집하고 집계하는 클래스.

    내부적으로 BenchmarkResult 목록을 유지하며 JSON 직렬화/역직렬화와
    모델별 통계 집계를 제공한다.
    """

    _results: list[BenchmarkResult] = field(default_factory=list, init=False, repr=False)

    def record(self, result: BenchmarkResult) -> None:
        """벤치마크 결과를 내부 목록에 추가한다.

        Args:
            result: 저장할 BenchmarkResult 인스턴스.
        """
        self._results.append(result)

    def get_results(self) -> list[BenchmarkResult]:
        """저장된 모든 벤치마크 결과를 반환한다.

        Returns:
            BenchmarkResult 인스턴스 목록 (삽입 순서 유지).
        """
        return list(self._results)

    def save_json(self, path: str) -> None:
        """결과를 JSON 파일로 저장한다.

        Args:
            path: 저장할 파일 경로.
        """
        data = [asdict(r) for r in self._results]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json(self, path: str) -> None:
        """JSON 파일에서 결과를 불러온다. 기존 목록에 추가(append)한다.

        Args:
            path: 불러올 파일 경로.
        """
        with open(path, encoding="utf-8") as f:
            data: list[dict[str, Any]] = json.load(f)
        for item in data:
            self._results.append(BenchmarkResult(**item))

    def summary_by_model(self) -> dict[str, dict[str, Any]]:
        """모델별 집계 통계를 반환한다.

        집계 항목:
            - run_count: 실행 횟수
            - avg_time_sec: 평균 리뷰 시간 (초)
            - avg_comment_count: 평균 코멘트 수
            - avg_detection_rate: 평균 감지율
            - json_parse_success_rate: JSON 파싱 성공률
            - total_input_tokens: 총 입력 토큰 (오프라인은 0)
            - total_output_tokens: 총 출력 토큰 (오프라인은 0)
            - total_tokens: 총 토큰
            - total_cost_usd: 총 비용 달러 (오프라인은 0.0)
            - severity_totals: 심각도별 코멘트 합계

        Returns:
            모델 이름을 키로 하는 통계 딕셔너리.
        """
        grouped: dict[str, list[BenchmarkResult]] = {}
        for r in self._results:
            grouped.setdefault(r.model, []).append(r)

        summary: dict[str, dict[str, Any]] = {}
        for model, results in grouped.items():
            n = len(results)
            severity_totals: dict[str, int] = {}
            for r in results:
                for severity, count in r.comments_by_severity.items():
                    severity_totals[severity] = severity_totals.get(severity, 0) + count

            summary[model] = {
                "run_count": n,
                "avg_time_sec": round(sum(r.review_time_sec for r in results) / n, 3),
                "avg_comment_count": round(sum(r.comment_count for r in results) / n, 2),
                "avg_detection_rate": round(sum(r.detection_rate for r in results) / n, 4),
                "json_parse_success_rate": round(
                    sum(1 for r in results if r.json_parse_success) / n, 4
                ),
                "total_input_tokens": sum(r.input_tokens for r in results),
                "total_output_tokens": sum(r.output_tokens for r in results),
                "total_tokens": sum(r.total_tokens for r in results),
                "total_cost_usd": round(sum(r.cost_usd for r in results), 6),
                "severity_totals": severity_totals,
            }
        return summary


def count_diff_lines(diff_text: str) -> int:
    """diff 텍스트에서 추가(+)와 삭제(-) 라인 수의 합계를 반환한다.

    diff 헤더 라인(+++, ---로 시작하는 파일명 라인)은 제외하고
    실제 변경 내용(+, -로 시작하는 라인)만 계산한다.

    Args:
        diff_text: unified diff 형식의 문자열.

    Returns:
        추가 라인 수 + 삭제 라인 수의 합계.
    """
    count = 0
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            count += 1
    return count
