"""retry 유틸리티 테스트."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.retry import with_retry


class TestWithRetry:
    """with_retry 데코레이터 테스트."""

    def test_success_no_retry(self):
        """첫 시도에 성공하면 재시도 없이 결과 반환."""
        mock_fn = MagicMock(return_value="ok")
        decorated = with_retry(max_retries=3)(mock_fn)

        result = decorated()

        assert result == "ok"
        assert mock_fn.call_count == 1

    @patch("src.retry.time.sleep")
    def test_retry_then_success(self, mock_sleep):
        """일시적 장애 후 재시도하여 성공."""
        mock_fn = MagicMock(
            side_effect=[httpx.TimeoutException("timeout"), "ok"]
        )
        decorated = with_retry(max_retries=3, backoff_factor=0.1)(mock_fn)

        result = decorated()

        assert result == "ok"
        assert mock_fn.call_count == 2
        mock_sleep.assert_called_once()

    @patch("src.retry.time.sleep")
    def test_max_retries_exceeded(self, mock_sleep):
        """최대 재시도 횟수 초과 시 마지막 예외 발생."""
        exc = httpx.ConnectError("connection refused")
        mock_fn = MagicMock(side_effect=exc)
        decorated = with_retry(max_retries=2, backoff_factor=0.1)(mock_fn)

        with pytest.raises(httpx.ConnectError):
            decorated()

        assert mock_fn.call_count == 3  # 초기 1 + 재시도 2

    def test_non_retryable_exception_raises_immediately(self):
        """재시도 대상이 아닌 예외는 즉시 발생."""
        mock_fn = MagicMock(side_effect=ValueError("bad value"))
        decorated = with_retry(max_retries=3)(mock_fn)

        with pytest.raises(ValueError):
            decorated()

        assert mock_fn.call_count == 1

    @patch("src.retry.time.sleep")
    def test_http_5xx_is_retryable(self, mock_sleep):
        """HTTP 5xx 오류는 재시도 대상."""
        response_502 = httpx.Response(502, request=httpx.Request("GET", "http://test"))
        response_ok = MagicMock(return_value="ok")
        mock_fn = MagicMock(
            side_effect=[httpx.HTTPStatusError("bad gateway", request=httpx.Request("GET", "http://test"), response=response_502), "ok"]
        )
        decorated = with_retry(max_retries=3, backoff_factor=0.1)(mock_fn)

        result = decorated()

        assert result == "ok"
        assert mock_fn.call_count == 2

    def test_http_4xx_is_not_retryable(self):
        """HTTP 4xx 오류는 재시도하지 않음."""
        response_404 = httpx.Response(404, request=httpx.Request("GET", "http://test"))
        mock_fn = MagicMock(
            side_effect=httpx.HTTPStatusError("not found", request=httpx.Request("GET", "http://test"), response=response_404)
        )
        decorated = with_retry(max_retries=3)(mock_fn)

        with pytest.raises(httpx.HTTPStatusError):
            decorated()

        assert mock_fn.call_count == 1

    @patch("src.retry.time.sleep")
    def test_exponential_backoff(self, mock_sleep):
        """지수 백오프 대기 시간 검증."""
        mock_fn = MagicMock(
            side_effect=[
                httpx.TimeoutException("t1"),
                httpx.TimeoutException("t2"),
                httpx.TimeoutException("t3"),
            ]
        )
        decorated = with_retry(max_retries=2, backoff_factor=1.0)(mock_fn)

        with pytest.raises(httpx.TimeoutException):
            decorated()

        # delay = 1.0 * 2^0 = 1.0, 1.0 * 2^1 = 2.0
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)
