"""FastAPI 웹훅 서버 테스트 - 웹훅 수신 및 리뷰 트리거 검증."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.server import app


@pytest.fixture()
def client():
    return TestClient(app)


def _mr_payload(action: str = "open", project_id: int = 42, mr_iid: int = 7) -> dict:
    """테스트용 MR 웹훅 페이로드 생성."""
    return {
        "object_kind": "merge_request",
        "project": {"id": project_id},
        "object_attributes": {
            "iid": mr_iid,
            "action": action,
            "title": "Test MR",
        },
    }


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "model" in data
        assert "embed_model" in data


class TestWebhookAuth:
    def test_rejects_invalid_token(self, client):
        with patch("src.server.settings") as mock_settings:
            mock_settings.webhook_secret = "correct-secret"
            mock_settings.llm_model = "test"
            mock_settings.embed_model = "test"

            resp = client.post(
                "/webhook",
                json=_mr_payload(),
                headers={"X-Gitlab-Token": "wrong-secret"},
            )

            assert resp.status_code == 401

    def test_accepts_valid_token(self, client):
        with patch("src.server.settings") as mock_settings, \
             patch("src.server.run_review"):
            mock_settings.webhook_secret = "correct-secret"

            resp = client.post(
                "/webhook",
                json=_mr_payload(),
                headers={"X-Gitlab-Token": "correct-secret"},
            )

            assert resp.status_code == 200

    def test_no_secret_configured_allows_all(self, client):
        with patch("src.server.settings") as mock_settings, \
             patch("src.server.run_review"):
            mock_settings.webhook_secret = ""

            resp = client.post(
                "/webhook",
                json=_mr_payload(),
            )

            assert resp.status_code == 200


class TestWebhookRouting:
    def test_ignores_non_mr_events(self, client):
        with patch("src.server.settings") as mock_settings:
            mock_settings.webhook_secret = ""

            resp = client.post(
                "/webhook",
                json={"object_kind": "push"},
            )

            assert resp.status_code == 200
            assert resp.json()["status"] == "ignored"

    def test_ignores_close_action(self, client):
        with patch("src.server.settings") as mock_settings:
            mock_settings.webhook_secret = ""

            resp = client.post(
                "/webhook",
                json=_mr_payload(action="close"),
            )

            assert resp.status_code == 200
            assert resp.json()["status"] == "ignored"

    def test_ignores_merge_action(self, client):
        with patch("src.server.settings") as mock_settings:
            mock_settings.webhook_secret = ""

            resp = client.post(
                "/webhook",
                json=_mr_payload(action="merge"),
            )

            assert resp.status_code == 200
            assert resp.json()["status"] == "ignored"

    @pytest.mark.parametrize("action", ["open", "update", "reopen"])
    def test_accepts_reviewable_actions(self, client, action):
        with patch("src.server.settings") as mock_settings, \
             patch("src.server.run_review"):
            mock_settings.webhook_secret = ""

            resp = client.post(
                "/webhook",
                json=_mr_payload(action=action),
            )

            assert resp.status_code == 200
            assert resp.json()["status"] == "accepted"

    def test_rejects_missing_project_id(self, client):
        with patch("src.server.settings") as mock_settings:
            mock_settings.webhook_secret = ""

            resp = client.post(
                "/webhook",
                json={
                    "object_kind": "merge_request",
                    "project": {},
                    "object_attributes": {"iid": 7, "action": "open"},
                },
            )

            assert resp.status_code == 400


class TestWebhookReviewTrigger:
    def test_triggers_background_review(self, client):
        with patch("src.server.settings") as mock_settings, \
             patch("src.server.run_review") as mock_review:
            mock_settings.webhook_secret = ""

            resp = client.post(
                "/webhook",
                json=_mr_payload(project_id=99, mr_iid=15),
            )

            assert resp.status_code == 200
            assert resp.json()["project_id"] == 99
            assert resp.json()["mr_iid"] == 15
            # BackgroundTasks로 호출되므로 TestClient에서는 동기 실행됨
            mock_review.assert_called_once_with(99, 15)


class TestRunReview:
    def test_full_pipeline(self):
        """run_review 통합 테스트 - 모든 외부 의존성 모킹."""
        from src.server import run_review

        mock_gitlab = MagicMock()
        mock_gitlab.get_mr_diff_text.return_value = """\
diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 import os
+import sys
"""
        mock_gitlab.post_review.return_value = {
            "posted_inline": 0,
            "posted_summary": True,
            "errors": [],
        }

        mock_reviewer = MagicMock()
        mock_reviewer.review.return_value = []

        with patch("src.server.GitLabClient", return_value=mock_gitlab), \
             patch("src.server.Reviewer", return_value=mock_reviewer):
            run_review(42, 7)

        mock_gitlab.get_mr_diff_text.assert_called_once_with(42, 7)
        mock_reviewer.review.assert_called_once()
        mock_gitlab.post_review.assert_called_once_with(42, 7, [])

    def test_skips_empty_diff(self):
        from src.server import run_review

        mock_gitlab = MagicMock()
        mock_gitlab.get_mr_diff_text.return_value = ""

        with patch("src.server.GitLabClient", return_value=mock_gitlab), \
             patch("src.server.Reviewer") as mock_reviewer_cls:
            run_review(42, 7)

        # diff가 비어있으면 Reviewer를 호출하지 않음
        mock_reviewer_cls.assert_not_called()
