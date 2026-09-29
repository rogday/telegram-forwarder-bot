import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app_logging import get_logger
from config import RetryDynamicConfig

logger = get_logger(__name__)

T = TypeVar("T")


async def retry_with_timeout(
    operation: Callable[[], Awaitable[T]],
    config: RetryDynamicConfig,
    description: str,
    retry_on: tuple[type[Exception], ...] = (),
) -> T:
    """Run operation with a timeout per attempt, retrying timeouts and retry_on
    errors with exponential backoff. The last error is re-raised to the caller."""

    def log_retry(retry_state: RetryCallState) -> None:
        assert retry_state.outcome is not None
        logger.warning(
            "{description} failed, retrying",
            description=description,
            attempt=retry_state.attempt_number,
            attempts=config.attempts,
            delay_seconds=retry_state.upcoming_sleep,
            error=repr(retry_state.outcome.exception()),
        )

    async def attempt() -> T:
        return await asyncio.wait_for(operation(), config.timeout_seconds)

    retrying = AsyncRetrying(
        stop=stop_after_attempt(config.attempts),
        wait=wait_exponential(
            multiplier=config.min_backoff_seconds,
            min=config.min_backoff_seconds,
            max=config.max_backoff_seconds,
        ),
        retry=retry_if_exception_type((TimeoutError, *retry_on)),
        before_sleep=log_retry,
        reraise=True,
    )
    return await retrying(attempt)
