"""E2E 통합 테스트 - 웹훅 수신 → 리뷰 생성 → 코멘트 게시 전체 흐름 검증.

모든 외부 의존성(GitLab API, Ollama LLM, VectorStore)을 모킹하여
파이프라인의 연결이 올바른지 검증한다.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.reviewer import ReviewComment
from src.server import app

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def sample_diff_text() -> str:
    return (FIXTURES_DIR / "sample.diff").read_text()


def _mr_payload(action: str = "open", project_id: int = 100, mr_iid: int = 42) -> dict:
    return {
        "object_kind": "merge_request",
        "project": {"id": project_id},
        "object_attributes": {
            "iid": mr_iid,
            "action": action,
            "title": "E2E Test MR",
        },
    }


class TestE2EPipeline:
    """웹훅 → diff 조회 → 리뷰 → 코멘트 게시 전체 파이프라인."""

    def test_full_flow_with_issues(self, client, sample_diff_text):
        """이슈가 있는 MR → 인라인 코멘트 + 요약 코멘트 게시."""
        mock_gitlab = MagicMock()
        mock_gitlab.get_mr_diff_text.return_value = sample_diff_text
        mock_gitlab.post_review.return_value = {
            "posted_inline": 2,
            "posted_summary": True,
            "errors": [],
        }

        fake_comments = [
            ReviewComment(
                file="src/main.py", line=12, severity="critical",
                message="하드코딩된 비밀번호입니다.",
            ),
            ReviewComment(
                file="src/main.py", line=20, severity="warning",
                message="빈 except 절입니다.",
            ),
        ]

        mock_reviewer = MagicMock()
        mock_reviewer.review.return_value = fake_comments

        with patch("src.server.settings") as mock_settings, \
             patch("src.server.GitLabClient", return_value=mock_gitlab), \
             patch("src.server.Reviewer", return_value=mock_reviewer):
            mock_settings.webhook_secret = ""

            resp = client.post("/webhook", json=_mr_payload())

        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

        # GitLab에 diff 조회 호출됨
        mock_gitlab.get_mr_diff_text.assert_called_once_with(100, 42)

        # 리뷰 실행됨
        mock_reviewer.review.assert_called_once_with(sample_diff_text)

        # 리뷰 결과 게시됨
        mock_gitlab.post_review.assert_called_once_with(100, 42, fake_comments)

    def test_full_flow_clean_code(self, client, sample_diff_text):
        """이슈 없는 MR → 깨끗한 결과 코멘트만 게시."""
        mock_gitlab = MagicMock()
        mock_gitlab.get_mr_diff_text.return_value = sample_diff_text
        mock_gitlab.post_review.return_value = {
            "posted_inline": 0,
            "posted_summary": True,
            "errors": [],
        }

        mock_reviewer = MagicMock()
        mock_reviewer.review.return_value = []  # 이슈 없음

        with patch("src.server.settings") as mock_settings, \
             patch("src.server.GitLabClient", return_value=mock_gitlab), \
             patch("src.server.Reviewer", return_value=mock_reviewer):
            mock_settings.webhook_secret = ""

            resp = client.post("/webhook", json=_mr_payload())

        assert resp.status_code == 200
        mock_gitlab.post_review.assert_called_once_with(100, 42, [])

    def test_update_action_triggers_review(self, client, sample_diff_text):
        """MR 업데이트 (새 커밋 push) → 리뷰 재실행."""
        mock_gitlab = MagicMock()
        mock_gitlab.get_mr_diff_text.return_value = sample_diff_text
        mock_gitlab.post_review.return_value = {
            "posted_inline": 0,
            "posted_summary": True,
            "errors": [],
        }

        mock_reviewer = MagicMock()
        mock_reviewer.review.return_value = []

        with patch("src.server.settings") as mock_settings, \
             patch("src.server.GitLabClient", return_value=mock_gitlab), \
             patch("src.server.Reviewer", return_value=mock_reviewer):
            mock_settings.webhook_secret = ""

            resp = client.post("/webhook", json=_mr_payload(action="update"))

        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
        mock_reviewer.review.assert_called_once()

    def test_rejects_unauthorized(self, client):
        """시크릿 토큰 불일치 → 리뷰 미실행."""
        with patch("src.server.settings") as mock_settings:
            mock_settings.webhook_secret = "real-secret"

            resp = client.post(
                "/webhook",
                json=_mr_payload(),
                headers={"X-Gitlab-Token": "wrong-secret"},
            )

        assert resp.status_code == 401


class TestE2EDiffConversion:
    """GitLab API 응답 → unified diff 변환 → 파서 → 리뷰 연결 테스트."""

    def test_gitlab_changes_to_review_comments(self):
        """GitLab changes 형식 → unified diff → 파서 → 리뷰 코멘트."""
        from src.diff_parser import parse_diff
        from src.gitlab_client import GitLabClient

        # GitLab API가 반환하는 형식 시뮬레이션
        mock_http = MagicMock()
        mock_http.get.return_value = MagicMock(
            json=MagicMock(return_value={
                "changes": [
                    {
                        "old_path": "src/auth.py",
                        "new_path": "src/auth.py",
                        "diff": (
                            "@@ -1,3 +1,5 @@\n"
                            " import os\n"
                            "+DB_PASSWORD = \"secret123\"\n"
                            "+\n"
                            " def login():\n"
                            "     pass\n"
                        ),
                    },
                    {
                        "old_path": "config.json",
                        "new_path": "config.json",
                        "new_file": True,
                        "diff": (
                            "@@ -0,0 +1,3 @@\n"
                            "+{\n"
                            '+  "debug": true\n'
                            "+}\n"
                        ),
                    },
                ]
            }),
            raise_for_status=MagicMock(),
        )

        with patch("src.gitlab_client.httpx.Client", return_value=mock_http):
            client = GitLabClient(base_url="https://test.com", token="t")
            diff_text = client.get_mr_diff_text(1, 1)

        # diff 파서로 구조화
        result = parse_diff(diff_text)

        assert len(result.files) == 2
        assert result.files[0].filename == "src/auth.py"
        assert result.files[0].status == "modified"
        assert result.files[1].filename == "config.json"
        assert result.files[1].status == "added"

        # 변경 내용 확인
        added = result.files[0].added_lines
        assert any("DB_PASSWORD" in line.content for line in added)


class TestE2EMultiFileReview:
    """다중 파일 MR 리뷰 테스트."""

    def test_reviews_multiple_files(self, sample_diff_text):
        """여러 파일이 포함된 diff → 파일별 리뷰 생성."""
        from src.diff_parser import parse_diff

        result = parse_diff(sample_diff_text)
        reviewable = result.reviewable_files

        # sample.diff에는 리뷰 가능한 파일이 여러 개
        assert len(reviewable) >= 2

        # 바이너리 파일과 락 파일은 제외
        filenames = [f.filename for f in reviewable]
        assert not any(f.endswith(".png") for f in filenames)
        assert not any("package-lock" in f for f in filenames)

    def test_large_diff_handling(self):
        """100+ 라인 변경이 있는 diff 파싱이 정상 동작하는지 확인."""
        from src.diff_parser import parse_diff

        # 큰 diff 생성
        lines = ["diff --git a/big.py b/big.py"]
        lines.append("--- a/big.py")
        lines.append("+++ b/big.py")
        lines.append("@@ -1,0 +1,200 @@")
        for i in range(1, 201):
            lines.append(f"+line_{i} = {i}")

        diff_text = "\n".join(lines)
        result = parse_diff(diff_text)

        assert len(result.files) == 1
        assert len(result.files[0].added_lines) == 200


class TestE2ESummaryFormat:
    """리뷰 요약 포맷 테스트."""

    def test_summary_with_mixed_severities(self):
        """다양한 심각도의 코멘트가 요약에 올바르게 포함되는지 확인."""
        from src.gitlab_client import GitLabClient

        comments = [
            ReviewComment(file="a.py", line=1, severity="critical", message="SQL 인젝션"),
            ReviewComment(file="a.py", line=5, severity="critical", message="하드코딩 비밀번호"),
            ReviewComment(file="b.py", line=10, severity="warning", message="빈 except"),
            ReviewComment(file="c.py", line=3, severity="info", message="타입 힌트 추가"),
        ]

        summary = GitLabClient._build_summary(comments)

        assert "총 **4**건" in summary
        assert "Critical 2" in summary
        assert "Warning 1" in summary
        assert "Info 1" in summary
        assert "SQL 인젝션" in summary
        assert "하드코딩 비밀번호" in summary
