"""CVE 스캐너 테스트 - DB 모킹을 사용한 취약점 조회 검증."""

from unittest.mock import MagicMock, patch

import pytest

from src.cve_scanner import CveEntry, CveResult, CveScanner
from src.dependency_parser import Dependency


@pytest.fixture()
def scanner():
    return CveScanner(conninfo="host=localhost dbname=test")


@pytest.fixture()
def flask_dep():
    return Dependency(name="flask", version="2.0.0", source_file="requirements.txt", line_number=1)


@pytest.fixture()
def safe_dep():
    return Dependency(name="flask", version="3.0.0", source_file="requirements.txt", line_number=1)


@pytest.fixture()
def no_version_dep():
    return Dependency(name="flask", version="", source_file="requirements.txt", line_number=1)


class TestVersionAffected:
    def test_version_in_range(self):
        entry = CveEntry(
            cve_id="CVE-2023-30861",
            package_name="flask",
            severity="high",
            description="test",
            fixed_version="2.3.2",
            affected_version_start="0.1",
            affected_version_end="2.3.2",
        )
        assert CveScanner._is_version_affected("2.0.0", entry) is True

    def test_version_at_end_boundary_is_not_affected(self):
        """affected_version_end는 exclusive — 경계값은 영향받지 않는다."""
        entry = CveEntry(
            cve_id="CVE-2023-30861",
            package_name="flask",
            severity="high",
            description="test",
            fixed_version="2.3.2",
            affected_version_start="0.1",
            affected_version_end="2.3.2",
        )
        assert CveScanner._is_version_affected("2.3.2", entry) is False

    def test_version_after_fix(self):
        entry = CveEntry(
            cve_id="CVE-2023-30861",
            package_name="flask",
            severity="high",
            description="test",
            fixed_version="2.3.2",
            affected_version_start="0.1",
            affected_version_end="2.3.2",
        )
        assert CveScanner._is_version_affected("3.0.0", entry) is False

    def test_version_with_fixed_only(self):
        entry = CveEntry(
            cve_id="CVE-2023-00001",
            package_name="pkg",
            severity="medium",
            description="test",
            fixed_version="2.0.0",
        )
        assert CveScanner._is_version_affected("1.5.0", entry) is True
        assert CveScanner._is_version_affected("2.0.0", entry) is False

    def test_no_range_info(self):
        entry = CveEntry(
            cve_id="CVE-2023-00002",
            package_name="pkg",
            severity="low",
            description="test",
        )
        # 범위 정보가 없으면 영향받는 것으로 처리
        assert CveScanner._is_version_affected("1.0.0", entry) is True


class TestScanDependencies:
    def test_skip_no_version(self, scanner, no_version_dep):
        """버전 정보가 없는 의존성은 스킵한다."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("psycopg.connect", return_value=mock_conn):
            with patch.object(scanner, "_query_cve") as mock_query:
                results = scanner.scan_dependencies([no_version_dep])
                mock_query.assert_not_called()
                assert results == []

    def test_vulnerable_dep_found(self, scanner, flask_dep):
        entry = CveEntry(
            cve_id="CVE-2023-30861",
            package_name="flask",
            severity="high",
            description="세션 하이재킹 취약점",
            fixed_version="2.3.2",
        )
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("psycopg.connect", return_value=mock_conn):
            with patch.object(scanner, "_query_cve", return_value=[entry]):
                results = scanner.scan_dependencies([flask_dep])
                assert len(results) == 1
                assert results[0].cve_entries[0].cve_id == "CVE-2023-30861"

    def test_safe_dep_no_results(self, scanner, safe_dep):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("psycopg.connect", return_value=mock_conn):
            with patch.object(scanner, "_query_cve", return_value=[]):
                results = scanner.scan_dependencies([safe_dep])
                assert results == []

    def test_db_failure_returns_empty(self, scanner, flask_dep):
        """DB 연결 실패 시 빈 리스트를 반환한다."""
        with patch("psycopg.connect", side_effect=Exception("DB error")):
            results = scanner.scan_dependencies([flask_dep])
            assert results == []


class TestQueryCve:
    def test_query_filters_by_severity(self, scanner):
        """severity threshold 이하인 CVE는 필터링된다."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("CVE-2023-001", "flask", "low", "minor issue", None, "0.1", "3.0"),
            ("CVE-2023-002", "flask", "high", "major issue", "2.3.2", "0.1", "2.3.2"),
        ]
        mock_conn.execute.return_value = mock_cursor

        # threshold=2 (medium) 이므로 low(1)는 필터링됨
        entries = scanner._query_cve(mock_conn, "flask", "2.0.0", threshold=2)
        assert len(entries) == 1
        assert entries[0].cve_id == "CVE-2023-002"

    def test_query_db_error_returns_empty(self, scanner):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("query failed")

        entries = scanner._query_cve(mock_conn, "flask", "2.0.0", threshold=2)
        assert entries == []
