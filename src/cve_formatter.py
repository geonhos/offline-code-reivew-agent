"""CVE 결과를 ReviewComment 형식으로 변환한다."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.cve_scanner import CveResult

if TYPE_CHECKING:
    from src.reviewer import ReviewComment

# NVD severity → ReviewComment severity 매핑
_SEVERITY_MAP = {
    "critical": "critical",
    "high": "critical",
    "medium": "warning",
    "low": "info",
}


def format_cve_comments(results: list[CveResult]) -> list["ReviewComment"]:
    """CVE 스캔 결과를 ReviewComment 리스트로 변환한다."""
    from src.reviewer import ReviewComment

    comments: list[ReviewComment] = []
    for result in results:
        dep = result.dependency
        for cve in result.cve_entries:
            severity = _SEVERITY_MAP.get(cve.severity, "info")
            fix_msg = (
                f"권장 조치: {cve.fixed_version} 이상으로 업그레이드하세요."
                if cve.fixed_version
                else "현재 알려진 수정 버전이 없습니다. 대체 라이브러리를 검토하세요."
            )
            message = (
                f"보안 취약점 발견: {cve.cve_id} ({cve.severity.upper()}) — "
                f"{dep.name}=={dep.version}. "
                f"{cve.description} "
                f"{fix_msg}"
            )
            comments.append(ReviewComment(
                file=dep.source_file,
                line=dep.line_number,
                severity=severity,
                message=message,
            ))
    return comments
