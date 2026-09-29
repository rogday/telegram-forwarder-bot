import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from telegram_forwarder_bot import TelegramForwarderBot


def test_populate_chat_cache_skips_resolution_errors() -> None:
    chats = [
        SimpleNamespace(username="available_chat"),
        SimpleNamespace(username="deleted_chat"),
        SimpleNamespace(username="another_available_chat"),
    ]
    resolver = AsyncMock()
    resolver.resolve_cached.side_effect = [
        object(),
        ValueError("chat no longer exists"),
        object(),
    ]

    bot = TelegramForwarderBot.__new__(TelegramForwarderBot)
    bot._chat_resolver = resolver
    bot._storage_manager = Mock()
    bot._storage_manager.data.list_chats.return_value = iter(chats)

    with patch("telegram_forwarder_bot.logger") as logger:
        asyncio.run(bot._populate_chat_cache())

    assert resolver.resolve_cached.await_args_list == [
        call("available_chat"),
        call("deleted_chat"),
        call("another_available_chat"),
    ]
    bot._storage_manager.data.remove_chat.assert_not_called()
    logger.warning.assert_called_once_with(
        "Skipping chat cache population after resolution error",
        identifier="deleted_chat",
    )


def test_stop_continues_after_resource_failure():
    bot = TelegramForwarderBot.__new__(TelegramForwarderBot)
    bot._config_watcher = Mock()
    bot._monitor = Mock(stop=AsyncMock())
    bot._user_client = Mock(disconnect=AsyncMock(side_effect=RuntimeError('disconnect failed')))
    bot._bot_client = Mock(disconnect=AsyncMock())
    bot._log_manager = Mock(stop=AsyncMock())
    recorder = Mock(stop=AsyncMock())

    with patch('telegram_forwarder_bot.get_metric_recorder', return_value=recorder):
        asyncio.run(bot.stop())

    bot._config_watcher.stop.assert_called_once()
    bot._monitor.stop.assert_awaited_once()
    bot._bot_client.disconnect.assert_awaited_once()
    recorder.stop.assert_awaited_once()
    bot._log_manager.stop.assert_awaited_once()


def test_runtime_error_cancels_other_running_tasks():
    bot = TelegramForwarderBot.__new__(TelegramForwarderBot)
    bot._config_watcher = Mock()
    bot._storage_manager = Mock()
    bot._static_config = Mock()
    bot._monitor = Mock()
    bot._populate_chat_cache = AsyncMock()
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
    bot._notifier = Mock(listen=fail)

    async def run():
        try:
            await bot.start()
        except RuntimeError:
            assert len(cancelled) == 2
        else:
            raise AssertionError('Runtime error was swallowed')

    with patch('telegram_forwarder_bot.get_metric_recorder', return_value=recorder):
        asyncio.run(run())



def test_metrics_stop_immediately_after_start():
    from config import MetricRecorderDynamicConfig, MetricRecorderStaticConfig
    from metrics import MetricRecorder

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


def test_failed_metrics_write_is_logged_and_recording_continues():
    from influxdb_client_3.exceptions.exceptions import InfluxDBError

    from config import (
        MetricRecorderDynamicConfig,
        MetricRecorderStaticConfig,
        RetryDynamicConfig,
    )
    from metrics import MetricRecord, MetricRecorder

    client = Mock()
    client.write.side_effect = InfluxDBError(message="unavailable")
    recorder = MetricRecorder(client, MetricRecorderStaticConfig(), MetricRecorderDynamicConfig(
        write_retry=RetryDynamicConfig(
            attempts=2, min_backoff_seconds=0.01, max_backoff_seconds=0.01)))
    record = MetricRecord(table_name="health", tags={}, fields=dict(status=True))
    recorder.record(record)

    with patch("metrics.logger") as logger, patch("retry.logger"):
        asyncio.run(recorder._flush())

    assert client.write.call_count == 2
    logger.exception.assert_called_once()
    recorder.record(record)
    assert recorder._queue.qsize() == 1
