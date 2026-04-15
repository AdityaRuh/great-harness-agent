"""Retry decorator with exponential backoff for unreliable external calls.

Wraps LLM calls, Gmail API, GitHub clones, Clawvatar WebSocket — anything that can fail transiently.
"""

import asyncio
import functools
import logging
import random
import time
from typing import Callable, Type

logger = logging.getLogger(__name__)

# Default retry config
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 30.0  # seconds
DEFAULT_BACKOFF_FACTOR = 2.0

# Errors worth retrying (transient)
RETRYABLE_ERRORS = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def retry(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    retryable_exceptions: tuple[Type[Exception], ...] = RETRYABLE_ERRORS,
    on_retry: Callable | None = None,
):
    """Synchronous retry decorator with exponential backoff + jitter."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_error = e
                    if attempt >= max_retries:
                        logger.error(
                            f"[Retry] {func.__name__} failed after {max_retries + 1} attempts: {e}"
                        )
                        raise

                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.3)
                    sleep_time = delay + jitter

                    logger.warning(
                        f"[Retry] {func.__name__} attempt {attempt + 1}/{max_retries + 1} "
                        f"failed: {e}. Retrying in {sleep_time:.1f}s"
                    )

                    if on_retry:
                        on_retry(func.__name__, attempt + 1, e)

                    time.sleep(sleep_time)

            raise last_error

        return wrapper
    return decorator


def async_retry(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    retryable_exceptions: tuple[Type[Exception], ...] = RETRYABLE_ERRORS,
):
    """Async retry decorator with exponential backoff + jitter."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_error = e
                    if attempt >= max_retries:
                        logger.error(
                            f"[Retry] {func.__name__} failed after {max_retries + 1} attempts: {e}"
                        )
                        raise

                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.3)

                    logger.warning(
                        f"[Retry] {func.__name__} attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {delay + jitter:.1f}s"
                    )
                    await asyncio.sleep(delay + jitter)

            raise last_error

        return wrapper
    return decorator


# Pre-configured retry decorators for specific services
llm_retry = retry(
    max_retries=3,
    base_delay=2.0,
    retryable_exceptions=(ConnectionError, TimeoutError, OSError, Exception),
)

email_retry = retry(
    max_retries=2,
    base_delay=1.0,
    retryable_exceptions=(ConnectionError, TimeoutError, OSError),
)

github_retry = retry(
    max_retries=2,
    base_delay=3.0,
    max_delay=15.0,
    retryable_exceptions=(ConnectionError, TimeoutError, OSError),
)
