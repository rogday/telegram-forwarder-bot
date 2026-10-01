import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch

from telethon import utils
from telethon.tl.types import PeerChannel

from telegram_forwarder_bot.config import (
    MonitorDynamicConfig,
    MonitorStaticConfig,
    RuntimeInstrumentationManagerDynamicConfig,
    RuntimeInstrumentationManagerStaticConfig,
    StorageManagerDynamicConfig,
)
from telegram_forwarder_bot.metrics import RuntimeInstrumentationManager
from telegram_forwarder_bot.monitoring import ChatInfo, Monitor
from telegram_forwarder_bot.storage import ChatTopic, Deduplicator, KeywordGroup, Subscriptions


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
    Monitor(client, storage, Mock(), Mock(), MonitorStaticConfig(), MonitorDynamicConfig())
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
    monitor = Monitor(Mock(), storage, Mock(), Mock(), MonitorStaticConfig(),
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
    monitor = Monitor(Mock(), Mock(), Mock(), Mock(), MonitorStaticConfig(), MonitorDynamicConfig())

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
    monitor = Monitor(Mock(), storage, Mock(), Mock(), MonitorStaticConfig(), MonitorDynamicConfig())
    asyncio.run(monitor._handle_new_message(Mock()))
    asyncio.run(monitor._handle_new_message(Mock()))

    with patch("telegram_forwarder_bot.monitoring.logger") as logger:
        monitor._log_status()
        monitor._log_status()

    assert [c.kwargs["messages_handled"] for c in logger.info.call_args_list] == [2, 0]
    assert logger.info.call_args.kwargs["status"] is True


def test_repost_is_recorded_as_duplicate_and_not_notified():
    subscriptions = Subscriptions()
    subscriptions.add_keyword_group(KeywordGroup.from_lists(["python"], []))
    subscriptions.add_chat(ChatTopic(id=123, topic_id=None, username="chat"))
    chat_resolver = Mock(resolve_cached=AsyncMock(
        return_value=ChatInfo(id=123, forum=False, title="Chat", username="chat")))
    monitor = Monitor(Mock(), subscriptions, Deduplicator(StorageManagerDynamicConfig()),
                      chat_resolver, MonitorStaticConfig(), MonitorDynamicConfig())
    event = Mock(is_channel=True, chat_id=utils.get_peer_id(PeerChannel(123)))
    event.message = Mock(message="python job", id=42, reply_to=None)

    async def receive_twice():
        await monitor._handle_new_message(event)
        await monitor._handle_new_message(event)

    with patch("telegram_forwarder_bot.monitoring.get_metric_recorder") as get_recorder:
        asyncio.run(receive_twice())

    records = [c.args[0].fields for c in get_recorder.return_value.record.call_args_list]
    assert [(r["is_matched"], r["is_duplicate"]) for r in records] == [(True, False), (True, True)]
    assert records[0]["message_link"] == "https://t.me/chat/42"
    assert monitor.get_match_queue().qsize() == 1
