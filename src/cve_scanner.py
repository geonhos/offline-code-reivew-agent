"""CVE 취약점 스캐너 - PostgreSQL cve_entries 테이블에서 취약점을 조회한다."""

import logging
from dataclasses import dataclass, field

import psycopg

from src.config import settings
from src.dependency_parser import Dependency

logger = logging.getLogger(__name__)

# severity 우선순위 (높을수록 심각)
SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class CveEntry:
    cve_id: str
    package_name: str
    severity: str
    description: str
    fixed_version: str | None = None
    affected_version_start: str | None = None
    affected_version_end: str | None = None


@dataclass
class CveResult:
    dependency: Dependency
    cve_entries: list[CveEntry] = field(default_factory=list)


class CveScanner:
    def __init__(self, conninfo: str | None = None):
        self._conninfo = conninfo or settings.database_url

    def scan_dependencies(self, deps: list[Dependency]) -> list[CveResult]:
        """의존성 목록에서 CVE 취약점을 검색한다."""
        results: list[CveResult] = []
        threshold = SEVERITY_ORDER.get(settings.cve_severity_threshold, 2)

        try:
            with psycopg.connect(self._conninfo) as conn:
                for dep in deps:
                    if not dep.version:
                        continue
                    entries = self._query_cve(conn, dep.name, dep.version, threshold)
                    if entries:
                        results.append(CveResult(dependency=dep, cve_entries=entries))
        except Exception:
            logger.warning("CVE DB 연결 실패", exc_info=True)

        return results

    def _query_cve(
        self, conn: psycopg.Connection, package_name: str, version: str, threshold: int
    ) -> list[CveEntry]:
        """DB에서 패키지의 CVE를 조회한다."""
        try:
            rows = conn.execute(
                """
                SELECT cve_id, package_name, severity, description,
                       fixed_version, affected_version_start, affected_version_end
                FROM cve_entries
                WHERE package_name = %s
                ORDER BY
                    CASE severity
                        WHEN 'critical' THEN 4
                        WHEN 'high' THEN 3
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 1
                        ELSE 0
                    END DESC
                """,
                (package_name,),
            ).fetchall()
        except Exception:
            logger.warning("CVE DB 조회 실패: %s", package_name, exc_info=True)
            return []

        entries = []
        for row in rows:
            severity = row[2]
            if SEVERITY_ORDER.get(severity, 0) < threshold:
                continue

            entry = CveEntry(
                cve_id=row[0],
                package_name=row[1],
                severity=severity,
                description=row[3] or "",
                fixed_version=row[4],
                affected_version_start=row[5],
                affected_version_end=row[6],
            )

            if self._is_version_affected(version, entry):
                entries.append(entry)

        return entries

    @staticmethod
    def _is_version_affected(version: str, entry: CveEntry) -> bool:
        """현재 버전이 취약한 범위에 포함되는지 확인한다.

        affected_version_end는 exclusive (NVD versionEndExcluding 기준).
        """
        from packaging.version import Version

        try:
            ver = Version(version)
            if entry.affected_version_start and entry.affected_version_end:
                return Version(entry.affected_version_start) <= ver < Version(
                    entry.affected_version_end
                )
            if entry.affected_version_end:
                return ver < Version(entry.affected_version_end)
            if entry.fixed_version:
                return ver < Version(entry.fixed_version)
            # 범위 정보가 없으면 해당 패키지 전체에 영향
            return True
        except Exception:
            logger.warning("버전 비교 실패: %s (entry: %s)", version, entry.cve_id)
            return True
