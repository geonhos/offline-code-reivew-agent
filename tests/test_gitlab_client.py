"""GitLab 클라이언트 테스트 - API 호출 모킹으로 동작 검증."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.gitlab_client import GitLabClient
from src.reviewer import ReviewComment


@pytest.fixture()
def mock_client():
    """httpx.Client를 모킹한 GitLabClient."""
    with patch("src.gitlab_client.httpx.Client") as mock_cls:
        mock_http = MagicMock()
        mock_cls.return_value = mock_http
        client = GitLabClient(base_url="https://gitlab.test.com", token="test-token")
        yield client, mock_http


class TestGetMrChanges:
    def test_calls_correct_endpoint(self, mock_client):
        client, mock_http = mock_client
        mock_http.get.return_value = MagicMock(
            json=MagicMock(return_value={"changes": []}),
            raise_for_status=MagicMock(),
        )

        client.get_mr_changes(project_id=42, mr_iid=7)

        mock_http.get.assert_called_once_with(
            "/projects/42/merge_requests/7/changes",
        )


class TestGetMrDiffText:
    def test_builds_unified_diff_for_modified_file(self, mock_client):
        client, mock_http = mock_client
        mock_http.get.return_value = MagicMock(
            json=MagicMock(return_value={
                "changes": [
                    {
                        "old_path": "src/main.py",
                        "new_path": "src/main.py",
                        "diff": "@@ -1,3 +1,4 @@\n import os\n+import sys\n",
                    }
                ]
            }),
            raise_for_status=MagicMock(),
        )

        result = client.get_mr_diff_text(42, 7)

        assert "diff --git a/src/main.py b/src/main.py" in result
        assert "--- a/src/main.py" in result
        assert "+++ b/src/main.py" in result
        assert "+import sys" in result

    def test_builds_unified_diff_for_new_file(self, mock_client):
        client, mock_http = mock_client
        mock_http.get.return_value = MagicMock(
            json=MagicMock(return_value={
                "changes": [
                    {
                        "old_path": "new.py",
                        "new_path": "new.py",
                        "new_file": True,
                        "diff": "@@ -0,0 +1,2 @@\n+print('hello')\n",
                    }
                ]
            }),
            raise_for_status=MagicMock(),
        )

        result = client.get_mr_diff_text(42, 7)

        assert "new file mode 100644" in result
        assert "--- /dev/null" in result
        assert "+++ b/new.py" in result

    def test_builds_unified_diff_for_deleted_file(self, mock_client):
        client, mock_http = mock_client
        mock_http.get.return_value = MagicMock(
            json=MagicMock(return_value={
                "changes": [
                    {
                        "old_path": "old.py",
                        "new_path": "old.py",
                        "deleted_file": True,
                        "diff": "@@ -1,2 +0,0 @@\n-print('bye')\n",
                    }
                ]
            }),
            raise_for_status=MagicMock(),
        )

        result = client.get_mr_diff_text(42, 7)

        assert "deleted file mode 100644" in result
        assert "--- a/old.py" in result
        assert "+++ /dev/null" in result

    def test_skips_changes_without_diff(self, mock_client):
        client, mock_http = mock_client
        mock_http.get.return_value = MagicMock(
            json=MagicMock(return_value={
                "changes": [
                    {"old_path": "empty.py", "new_path": "empty.py", "diff": ""},
                    {
                        "old_path": "real.py",
                        "new_path": "real.py",
                        "diff": "@@ -1 +1 @@\n-old\n+new\n",
                    },
                ]
            }),
            raise_for_status=MagicMock(),
        )

        result = client.get_mr_diff_text(42, 7)

        assert "empty.py" not in result
        assert "real.py" in result


class TestPostMrComment:
    def test_posts_discussion(self, mock_client):
        client, mock_http = mock_client
        mock_http.post.return_value = MagicMock(
            json=MagicMock(return_value={"id": "abc"}),
            raise_for_status=MagicMock(),
        )

        client.post_mr_comment(42, 7, "리뷰 완료")

        mock_http.post.assert_called_once_with(
            "/projects/42/merge_requests/7/discussions",
            json={"body": "리뷰 완료"},
        )


class TestPostInlineComment:
    def test_posts_with_position(self, mock_client):
        client, mock_http = mock_client
        mock_http.post.return_value = MagicMock(
            json=MagicMock(return_value={"id": "def"}),
            raise_for_status=MagicMock(),
        )

        client.post_inline_comment(
            project_id=42,
            mr_iid=7,
            body="보안 이슈",
            new_path="src/auth.py",
            new_line=15,
            base_sha="aaa",
            start_sha="bbb",
            head_sha="ccc",
        )

        call_args = mock_http.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")

        assert payload["body"] == "보안 이슈"
        assert payload["position"]["new_path"] == "src/auth.py"
        assert payload["position"]["new_line"] == 15
        assert payload["position"]["position_type"] == "text"


class TestBuildSummary:
    def test_summary_format(self):
        comments = [
            ReviewComment(file="a.py", line=1, severity="critical", message="위험"),
            ReviewComment(file="b.py", line=2, severity="warning", message="주의"),
            ReviewComment(file="c.py", line=3, severity="info", message="참고"),
        ]

        summary = GitLabClient._build_summary(comments)

        assert "AI 코드 리뷰 완료" in summary
        assert "총 **3**건" in summary
        assert "Critical 1" in summary
        assert "Warning 1" in summary
        assert "Info 1" in summary
        assert "| `a.py` |" in summary

    def test_summary_table_rows(self):
        comments = [
            ReviewComment(file="x.py", line=10, severity="critical", message="SQL 인젝션"),
        ]

        summary = GitLabClient._build_summary(comments)

        assert "L10" in summary
        assert "SQL 인젝션" in summary


class TestPostReview:
    def test_no_comments_posts_clean_message(self, mock_client):
        client, mock_http = mock_client
        mock_http.post.return_value = MagicMock(
            json=MagicMock(return_value={"id": "x"}),
            raise_for_status=MagicMock(),
        )
        mock_http.get.return_value = MagicMock(
            json=MagicMock(return_value=[]),
            raise_for_status=MagicMock(),
        )

        result = client.post_review(42, 7, [])

        assert result["posted_summary"] is True
        body = mock_http.post.call_args.kwargs.get("json", {}).get("body", "")
        assert "이슈가 발견되지 않았습니다" in body

    def test_with_comments_posts_inline_and_summary(self, mock_client):
        client, mock_http = mock_client

        # get (versions) 응답
        mock_http.get.return_value = MagicMock(
            json=MagicMock(return_value=[
                {
                    "base_commit_sha": "aaa",
                    "start_commit_sha": "bbb",
                    "head_commit_sha": "ccc",
                }
            ]),
            raise_for_status=MagicMock(),
        )
        # post 응답
        mock_http.post.return_value = MagicMock(
            json=MagicMock(return_value={"id": "y"}),
            raise_for_status=MagicMock(),
        )

        comments = [
            ReviewComment(file="a.py", line=5, severity="critical", message="문제 발견"),
        ]
        result = client.post_review(42, 7, comments)

        assert result["posted_inline"] == 1
        assert result["posted_summary"] is True
        # 인라인 1건 + 요약 1건 = 2번 post 호출
        assert mock_http.post.call_count == 2
