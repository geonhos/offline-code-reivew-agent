"""리뷰 이력 CRUD 테스트."""

from unittest.mock import MagicMock, patch

from src.review_history import ReviewHistory


class TestIsReviewed:
    def test_returns_true_for_completed(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("completed",)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("src.review_history.psycopg.connect", return_value=mock_conn):
            history = ReviewHistory()
            assert history.is_reviewed(1, 10, "abc123") is True

    def test_returns_true_for_skipped(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("skipped",)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("src.review_history.psycopg.connect", return_value=mock_conn):
            history = ReviewHistory()
            assert history.is_reviewed(1, 10, "abc123") is True

    def test_returns_false_for_pending(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("pending",)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("src.review_history.psycopg.connect", return_value=mock_conn):
            history = ReviewHistory()
            assert history.is_reviewed(1, 10, "abc123") is False

    def test_returns_false_for_no_record(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("src.review_history.psycopg.connect", return_value=mock_conn):
            history = ReviewHistory()
            assert history.is_reviewed(1, 10, "abc123") is False

    def test_returns_false_on_db_error(self):
        with patch("src.review_history.psycopg.connect", side_effect=Exception("DB down")):
            history = ReviewHistory()
            assert history.is_reviewed(1, 10, "abc123") is False


class TestSaveReview:
    def test_saves_review_with_upsert(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("src.review_history.psycopg.connect", return_value=mock_conn):
            history = ReviewHistory()
            history.save_review(1, 10, "abc123", "completed", 5)

        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert (1, 10, "abc123", "completed", 5) == call_args[0][1]

    def test_save_does_not_raise_on_db_error(self):
        with patch("src.review_history.psycopg.connect", side_effect=Exception("DB down")):
            history = ReviewHistory()
            # 예외를 발생시키지 않음
            history.save_review(1, 10, "abc123", "failed", 0)
