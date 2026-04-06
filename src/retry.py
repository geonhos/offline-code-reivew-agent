"""재시도 유틸리티 — 지수 백오프로 일시적 장애를 복구한다."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Optional, TypeVar

import httpx

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

# 기본 재시도 대상 예외
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


def _is_retryable_http_error(exc: Exception) -> bool:
    """5xx 서버 오류인 경우 재시도 대상."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


def with_retry(
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    retryable_exceptions: Optional[tuple[type[Exception], ...]] = None,
) -> Callable[[F], F]:
    """지수 백오프 재시도 데코레이터.

    Args:
        max_retries: 최대 재시도 횟수 (초기 시도 제외).
        backoff_factor: 백오프 기본 대기 시간(초). delay = backoff_factor * 2^attempt.
        retryable_exceptions: 재시도할 예외 타입 튜플.
    """
    exceptions = retryable_exceptions or RETRYABLE_EXCEPTIONS

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if isinstance(exc, exceptions) or _is_retryable_http_error(exc):
                        last_exc = exc
                        if attempt < max_retries:
                            delay = backoff_factor * (2 ** attempt)
                            logger.warning(
                                "재시도 %d/%d (%s) — %.1fs 후 재시도",
                                attempt + 1,
                                max_retries,
                                type(exc).__name__,
                                delay,
                            )
                            time.sleep(delay)
                        continue
                    raise
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator
