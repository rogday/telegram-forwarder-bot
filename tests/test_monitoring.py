import asyncio
from contextlib import contextmanager
from unittest.mock import Mock, patch

from telegram_forwarder_bot.config import (
    MonitorDynamicConfig,
    MonitorStaticConfig,
    RuntimeInstrumentationManagerDynamicConfig,
    RuntimeInstrumentationManagerStaticConfig,
)
from telegram_forwarder_bot.metrics import RuntimeInstrumentationManager
from telegram_forwarder_bot.monitoring import Monitor


def test_registered_handler_uses_current_instrumentation(monkeypatch, tmp_path):
    sessions = []

    def session(name):
        @contextmanager
        def enter(_func):
            sessions.append(name)
            yield
        return enter

    monkeypatch.setattr(RuntimeInstrumentationManager, '_profile_session',
                        staticmethod(session('profiling')))
    monkeypatch.setattr(RuntimeInstrumentationManager, '_stack_dump_session',
                        staticmethod(session('stack_dump')))
    # Restrict instrumentation discovery and restore the class after the test.
    monkeypatch.setattr(Monitor, '_handle_new_message_impl', Monitor._handle_new_message_impl)
    monkeypatch.setattr(Monitor, '__subclasses__', lambda: [])
    monkeypatch.setattr('telegram_forwarder_bot.metrics.RuntimeInstrumentationBase.__subclasses__',
                        lambda: [Monitor])
    manager = RuntimeInstrumentationManager(
        RuntimeInstrumentationManagerStaticConfig(profile_dir=tmp_path),
        RuntimeInstrumentationManagerDynamicConfig(),
    )
    client, storage = Mock(), Mock()
    storage.paused.return_value = True
    Monitor(client, storage, Mock(), MonitorStaticConfig(), MonitorDynamicConfig())
    callback = client.add_event_handler.call_args.args[0]

    async def exercise_modes():
        for mode in ('disabled', 'profiling', 'stack_dump', 'disabled'):
            manager.on_config_update(
                RuntimeInstrumentationManagerDynamicConfig(mode=mode)
            )
            await callback(Mock())

    asyncio.run(exercise_modes())
    assert sessions == ['profiling', 'stack_dump']
    assert storage.paused.call_count == 4


def test_health_reports_silence_until_next_message():
    storage = Mock()
    storage.paused.return_value = True
    monitor = Monitor(Mock(), storage, Mock(), MonitorStaticConfig(),
                      MonitorDynamicConfig(max_silence_seconds=60))
    assert monitor.health()["status"] is True

    monitor._last_message_at -= 61
    health = monitor.health()
    assert health["status"] is False
    assert health["seconds_since_last_message"] >= 61

    # Any handled message counts, even while notifications are paused
    asyncio.run(monitor._handle_new_message(Mock()))
    assert monitor.health()["status"] is True


def test_monitor_stop_before_start_and_during_ping():
    monitor = Monitor(Mock(), Mock(), Mock(), MonitorStaticConfig(), MonitorDynamicConfig())

    async def run():
        await monitor.stop()
        monitor._ping_loop = lambda: asyncio.sleep(60)
        monitor.start()
        await monitor.stop()
        assert monitor._force_sync_task.cancelled()

    asyncio.run(run())


def test_status_log_counts_messages_since_previous_line():
    storage = Mock()
    storage.paused.return_value = True
    monitor = Monitor(Mock(), storage, Mock(), MonitorStaticConfig(), MonitorDynamicConfig())
    asyncio.run(monitor._handle_new_message(Mock()))
    asyncio.run(monitor._handle_new_message(Mock()))

    with patch("telegram_forwarder_bot.monitoring.logger") as logger:
        monitor._log_status()
        monitor._log_status()

    assert [c.kwargs["messages_handled"] for c in logger.info.call_args_list] == [2, 0]
    assert logger.info.call_args.kwargs["status"] is True
