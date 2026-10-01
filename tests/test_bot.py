import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from telegram_forwarder_bot.bot import TelegramForwarderBot


def test_stop_continues_after_resource_failure():
    bot = TelegramForwarderBot.__new__(TelegramForwarderBot)
    bot._config_watcher = Mock()
    bot._monitor = Mock(stop=AsyncMock())
    bot._user_client = Mock(disconnect=AsyncMock(side_effect=RuntimeError('disconnect failed')))
    bot._bot_client = Mock(disconnect=AsyncMock())
    bot._storage_manager = Mock(flush=Mock(side_effect=OSError('disk full')))
    bot._log_manager = Mock(stop=AsyncMock())
    recorder = Mock(stop=AsyncMock())

    with patch('telegram_forwarder_bot.bot.get_metric_recorder', return_value=recorder):
        asyncio.run(bot.stop())

    bot._config_watcher.stop.assert_called_once()
    bot._monitor.stop.assert_awaited_once()
    bot._bot_client.disconnect.assert_awaited_once()
    recorder.stop.assert_awaited_once()
    bot._storage_manager.flush.assert_called_once()
    bot._log_manager.stop.assert_awaited_once()


def test_runtime_error_cancels_other_running_tasks():
    bot = TelegramForwarderBot.__new__(TelegramForwarderBot)
    bot._config_watcher = Mock()
    bot._storage_manager = Mock()
    bot._static_config = Mock()
    bot._monitor = Mock()
    bot._chat_resolver = Mock(warm_cache=AsyncMock())
    recorder = Mock(start=AsyncMock())
    cancelled = []

    async def wait_for_disconnect():
        try:
            await asyncio.Future()
        finally:
            cancelled.append(True)

    async def fail():
        await asyncio.sleep(0)
        raise RuntimeError('listener failed')

    bot._user_client = Mock(start=AsyncMock(), run_until_disconnected=wait_for_disconnect)
    bot._bot_client = Mock(start=AsyncMock(), run_until_disconnected=wait_for_disconnect)
    bot._notifier = Mock(run=fail)

    async def run():
        with pytest.raises(ExceptionGroup) as error:
            await bot.start()
        assert error.group_contains(RuntimeError, match='listener failed')
        assert len(cancelled) == 2

    with patch('telegram_forwarder_bot.bot.get_metric_recorder', return_value=recorder):
        asyncio.run(run())



def test_metrics_stop_immediately_after_start():
    from telegram_forwarder_bot.config import MetricRecorderDynamicConfig, MetricRecorderStaticConfig
    from telegram_forwarder_bot.metrics import MetricRecorder

    client = Mock()
    recorder = MetricRecorder(client, MetricRecorderStaticConfig(), MetricRecorderDynamicConfig())

    async def run():
        await recorder.start()
        await recorder.stop()
        assert all(task.done() for task in (
            recorder._heartbeat_task, recorder._gc_task, recorder._worker_task))

    asyncio.run(run())
    client.close.assert_called_once()
    client.flush.assert_not_called()


def test_metrics_stop_sends_queued_records_before_closing():
    from telegram_forwarder_bot.config import MetricRecorderDynamicConfig, MetricRecorderStaticConfig
    from telegram_forwarder_bot.metrics import MetricRecord, MetricRecorder

    client = Mock()
    recorder = MetricRecorder(client, MetricRecorderStaticConfig(), MetricRecorderDynamicConfig())

    async def run():
        await recorder.start()
        recorder.record(MetricRecord(table_name="health", tags={}, fields=dict(status=True)))
        await recorder.stop()

    asyncio.run(run())
    assert [name for name, _, _ in client.method_calls] == ["write", "close"]


def test_heartbeat_reports_health_check():
    from telegram_forwarder_bot.config import MetricRecorderDynamicConfig, MetricRecorderStaticConfig
    from telegram_forwarder_bot.metrics import MetricRecorder

    recorder = MetricRecorder(
        Mock(), MetricRecorderStaticConfig(), MetricRecorderDynamicConfig())

    def failing_check():
        raise RuntimeError("check failed")

    with patch("telegram_forwarder_bot.metrics.logger") as logger:
        recorder._record_heartbeat()
        recorder.set_health_check(
            lambda: dict(status=False, seconds_since_last_message=43201.0))
        recorder._record_heartbeat()
        recorder.set_health_check(failing_check)
        recorder._record_heartbeat()

    assert [recorder._queue.get_nowait().fields for _ in range(3)] == [
        dict(status=True),
        dict(status=False, seconds_since_last_message=43201.0),
        dict(status=False),
    ]
    logger.exception.assert_called_once()
    assert logger.warning.call_count == 2

