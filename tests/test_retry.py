import asyncio
from unittest.mock import patch

import pytest

from config import RetryDynamicConfig
from retry import retry_with_timeout

FAST_RETRY = RetryDynamicConfig(
    timeout_seconds=0.05, attempts=3,
    min_backoff_seconds=0.01, max_backoff_seconds=0.01)


def test_hung_attempt_times_out_and_is_retried():
    attempts = []

    async def operation():
        attempts.append(True)
        if len(attempts) == 1:
            await asyncio.Future()  # Never completes, like a lost response
        return "done"

    with patch("retry.logger") as logger:
        result = asyncio.run(
            retry_with_timeout(operation, FAST_RETRY, "request"))

    assert result == "done"
    assert len(attempts) == 2
    logger.warning.assert_called_once()


def test_last_timeout_is_raised_after_all_attempts():
    attempts = []

    async def operation():
        attempts.append(True)
        await asyncio.Future()

    with patch("retry.logger"), pytest.raises(TimeoutError):
        asyncio.run(retry_with_timeout(operation, FAST_RETRY, "request"))

    assert len(attempts) == 3


def test_only_listed_errors_are_retried():
    attempts = []

    async def operation():
        attempts.append(True)
        if len(attempts) == 1:
            raise ConnectionError("transient")
        raise ValueError("permanent")

    with patch("retry.logger"), pytest.raises(ValueError):
        asyncio.run(retry_with_timeout(
            operation, FAST_RETRY, "request", retry_on=(ConnectionError,)))

    assert len(attempts) == 2


def test_cancellation_is_not_retried():
    attempts = []

    async def operation():
        attempts.append(True)
        await asyncio.Future()

    async def run():
        config = FAST_RETRY.model_copy(update=dict(timeout_seconds=10))
        task = asyncio.create_task(
            retry_with_timeout(operation, config, "request"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(attempts) == 1
