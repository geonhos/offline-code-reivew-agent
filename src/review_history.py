"""리뷰 이력 관리 — 중복 리뷰 방지 및 이력 추적."""

from __future__ import annotations

import logging
from typing import Optional

import psycopg

from src.config import settings

logger = logging.getLogger(__name__)


class ReviewHistory:
    """리뷰 이력을 DB에 저장하고 조회한다."""

    def __init__(self, database_url: Optional[str] = None):
        self._database_url = database_url or settings.database_url

    def is_reviewed(self, project_id: int, mr_iid: int, commit_sha: str) -> bool:
        """해당 커밋에 대해 이미 리뷰가 완료되었는지 확인한다."""
        try:
            with psycopg.connect(self._database_url) as conn:
                row = conn.execute(
                    "SELECT status FROM review_history "
                    "WHERE project_id = %s AND mr_iid = %s AND commit_sha = %s",
                    (project_id, mr_iid, commit_sha),
                ).fetchone()
                return row is not None and row[0] in ("completed", "skipped")
        except Exception:
            logger.warning("리뷰 이력 조회 실패 — 리뷰를 진행합니다", exc_info=True)
            return False

    def save_review(
        self,
        project_id: int,
        mr_iid: int,
        commit_sha: str,
        status: str = "completed",
        comment_count: int = 0,
    ) -> None:
        """리뷰 결과를 저장한다. 중복 시 상태/코멘트 수를 업데이트한다."""
        try:
            with psycopg.connect(self._database_url) as conn:
                conn.execute(
                    """
                    INSERT INTO review_history
                        (project_id, mr_iid, commit_sha, status, comment_count)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, mr_iid, commit_sha)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        comment_count = EXCLUDED.comment_count,
                        updated_at = now()
                    """,
                    (project_id, mr_iid, commit_sha, status, comment_count),
                )
                conn.commit()
        except Exception:
            logger.warning("리뷰 이력 저장 실패", exc_info=True)
