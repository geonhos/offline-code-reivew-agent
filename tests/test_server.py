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
        with patch("src.server.httpx.get") as mock_ollama, \
             patch("src.server.psycopg.connect") as mock_db:
            mock_ollama.return_value = MagicMock(
                json=MagicMock(return_value={"models": [{"name": "qwen2.5-coder:7b"}]}),
                raise_for_status=MagicMock(),
            )
            mock_conn = MagicMock()
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "model_primary" in data
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
            mock_review.assert_called_once_with(99, 15, False)


class TestRunReview:
    def test_full_pipeline(self):
        """run_review 통합 테스트 - 모든 외부 의존성 모킹."""
        from src.server import run_review

        mock_gitlab = MagicMock()
        mock_gitlab.get_mr_head_sha.return_value = "abc123"
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
        mock_gitlab.__enter__ = MagicMock(return_value=mock_gitlab)
        mock_gitlab.__exit__ = MagicMock(return_value=False)

        mock_reviewer = MagicMock()
        mock_reviewer.review.return_value = []

        mock_history = MagicMock()
        mock_history.is_reviewed.return_value = False

        with patch("src.server.GitLabClient", return_value=mock_gitlab), \
             patch("src.server.Reviewer", return_value=mock_reviewer), \
             patch("src.server.ReviewHistory", return_value=mock_history):
            run_review(42, 7)

        mock_gitlab.get_mr_diff_text.assert_called_once_with(42, 7)
        mock_reviewer.review.assert_called_once()
        mock_gitlab.post_review.assert_called_once_with(42, 7, [])
        mock_history.save_review.assert_called_once_with(42, 7, "abc123", "completed", 0)

    def test_skips_empty_diff(self):
        from src.server import run_review

        mock_gitlab = MagicMock()
        mock_gitlab.get_mr_head_sha.return_value = "abc123"
        mock_gitlab.get_mr_diff_text.return_value = ""
        mock_gitlab.__enter__ = MagicMock(return_value=mock_gitlab)
        mock_gitlab.__exit__ = MagicMock(return_value=False)

        mock_history = MagicMock()
        mock_history.is_reviewed.return_value = False

        with patch("src.server.GitLabClient", return_value=mock_gitlab), \
             patch("src.server.Reviewer") as mock_reviewer_cls, \
             patch("src.server.ReviewHistory", return_value=mock_history):
            run_review(42, 7)

        # diff가 비어있으면 Reviewer를 호출하지 않음
        mock_reviewer_cls.assert_not_called()
        mock_history.save_review.assert_called_once_with(42, 7, "abc123", "skipped", 0)


class TestDuplicateReviewPrevention:
    def test_skips_already_reviewed_commit(self):
        from src.server import run_review

        mock_gitlab = MagicMock()
        mock_gitlab.get_mr_head_sha.return_value = "abc123"
        mock_gitlab.__enter__ = MagicMock(return_value=mock_gitlab)
        mock_gitlab.__exit__ = MagicMock(return_value=False)

        mock_history = MagicMock()
        mock_history.is_reviewed.return_value = True

        with patch("src.server.GitLabClient", return_value=mock_gitlab), \
             patch("src.server.Reviewer") as mock_reviewer_cls, \
             patch("src.server.ReviewHistory", return_value=mock_history):
            run_review(42, 7)

        # 이미 리뷰됨이면 diff 조회도 하지 않음
        mock_gitlab.get_mr_diff_text.assert_not_called()
        mock_reviewer_cls.assert_not_called()

    def test_force_review_ignores_history(self):
        from src.server import run_review

        mock_gitlab = MagicMock()
        mock_gitlab.get_mr_head_sha.return_value = "abc123"
        mock_gitlab.get_mr_diff_text.return_value = """\
diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -1 +1,2 @@
 x
+y
"""
        mock_gitlab.post_review.return_value = {
            "posted_inline": 0, "posted_summary": True, "errors": [],
        }
        mock_gitlab.__enter__ = MagicMock(return_value=mock_gitlab)
        mock_gitlab.__exit__ = MagicMock(return_value=False)

        mock_history = MagicMock()
        mock_history.is_reviewed.return_value = True

        mock_reviewer = MagicMock()
        mock_reviewer.review.return_value = []

        with patch("src.server.GitLabClient", return_value=mock_gitlab), \
             patch("src.server.Reviewer", return_value=mock_reviewer), \
             patch("src.server.ReviewHistory", return_value=mock_history):
            run_review(42, 7, force=True)

        # force=True이면 이력 무시하고 리뷰 실행
        mock_reviewer.review.assert_called_once()


class TestLabelFiltering:
    def test_no_review_label_skips(self, client):
        with patch("src.server.settings") as mock_settings, \
             patch("src.server.run_review") as mock_review:
            mock_settings.webhook_secret = ""

            payload = _mr_payload()
            payload["object_attributes"]["labels"] = [{"title": "no-review"}]

            resp = client.post("/webhook", json=payload)

            assert resp.status_code == 200
            assert resp.json()["status"] == "skipped"
            mock_review.assert_not_called()

    def test_force_review_label_passes_force_true(self, client):
        with patch("src.server.settings") as mock_settings, \
             patch("src.server.run_review") as mock_review:
            mock_settings.webhook_secret = ""

            payload = _mr_payload(project_id=10, mr_iid=5)
            payload["object_attributes"]["labels"] = [{"title": "force-review"}]

            resp = client.post("/webhook", json=payload)

            assert resp.status_code == 200
            assert resp.json()["status"] == "accepted"
            mock_review.assert_called_once_with(10, 5, True)

    def test_no_labels_passes_force_false(self, client):
        with patch("src.server.settings") as mock_settings, \
             patch("src.server.run_review") as mock_review:
            mock_settings.webhook_secret = ""

            resp = client.post("/webhook", json=_mr_payload(project_id=10, mr_iid=5))

            assert resp.status_code == 200
            mock_review.assert_called_once_with(10, 5, False)


class TestDeepHealthCheck:
    def test_degraded_when_ollama_down(self, client):
        with patch("src.server.httpx.get", side_effect=Exception("connection refused")), \
             patch("src.server.psycopg.connect") as mock_db:
            mock_conn = MagicMock()
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get("/health")
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["ollama"] == "error"
            assert data["database"] == "ok"

    def test_degraded_when_db_down(self, client):
        with patch("src.server.httpx.get") as mock_ollama, \
             patch("src.server.psycopg.connect", side_effect=Exception("DB down")):
            mock_ollama.return_value = MagicMock(
                json=MagicMock(return_value={"models": []}),
                raise_for_status=MagicMock(),
            )

            resp = client.get("/health")
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["ollama"] == "ok"
            assert data["database"] == "error"
