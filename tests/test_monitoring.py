import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from telethon import utils
from telethon.tl.types import Channel, MessageReplyHeader, MessageReplyStoryHeader, PeerChannel

from telegram_forwarder_bot.config import (
    MonitorDynamicConfig,
    MonitorStaticConfig,
    RuntimeInstrumentationManagerDynamicConfig,
    RuntimeInstrumentationManagerStaticConfig,
    StorageManagerDynamicConfig,
)
from telegram_forwarder_bot.metrics import RuntimeInstrumentationManager
from telegram_forwarder_bot.monitoring import ChatInfo, ChatResolver, Monitor
from telegram_forwarder_bot.storage import (
    ChatSubscription,
    ChatTopic,
    Deduplicator,
    KeywordGroup,
    Subscriptions,
)


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
    storage = Mock()
    storage.paused.return_value = True
    monitor = Monitor(Mock(), storage, Mock(), Mock(), MonitorStaticConfig(), MonitorDynamicConfig())
    # Bound when the bot registers it, before the instrumentation mode changes
    callback = monitor.handle_new_message

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
    asyncio.run(monitor.handle_new_message(Mock()))
    assert monitor.health()["status"] is True


def test_monitor_stop_before_start_and_during_ping():
    monitor = Monitor(Mock(), Mock(), Mock(), Mock(), MonitorStaticConfig(), MonitorDynamicConfig())

    async def run():
        await monitor.stop()
        monitor._ping_loop = lambda: asyncio.sleep(60)
        monitor.start()
        await monitor.stop()
        assert monitor._ping_task.cancelled()

    asyncio.run(run())


def test_status_log_counts_messages_since_previous_line():
    storage = Mock()
    storage.paused.return_value = True
    monitor = Monitor(Mock(), storage, Mock(), Mock(), MonitorStaticConfig(), MonitorDynamicConfig())
    asyncio.run(monitor.handle_new_message(Mock()))
    asyncio.run(monitor.handle_new_message(Mock()))

    with patch("telegram_forwarder_bot.monitoring.logger") as logger:
        monitor._log_status()
        monitor._log_status()

    assert [c.kwargs["messages_handled"] for c in logger.info.call_args_list] == [2, 0]
    assert logger.info.call_args.kwargs["status"] is True


def test_records_telegram_delay_and_reposts_as_duplicates():
    subscriptions = Subscriptions()
    subscriptions.add_keyword_group(KeywordGroup.from_lists(["python"], []))
    subscriptions.add_chat(ChatTopic(id=123, topic_id=None, username="chat"))
    chat_resolver = Mock(resolve_cached=AsyncMock(
        return_value=ChatInfo(id=123, forum=False, title="Chat", username="chat")))
    monitor = Monitor(Mock(), subscriptions, Deduplicator(StorageManagerDynamicConfig()),
                      chat_resolver, MonitorStaticConfig(), MonitorDynamicConfig())
    event = Mock(is_channel=True, chat_id=utils.get_peer_id(PeerChannel(123)))
    sent_at = datetime.now(UTC) - timedelta(seconds=2)
    event.message = Mock(message="python job", id=42, reply_to=None, date=sent_at)

    async def receive_twice():
        await monitor.handle_new_message(event)
        await monitor.handle_new_message(event)

    with patch("telegram_forwarder_bot.monitoring.get_metric_recorder") as get_recorder:
        asyncio.run(receive_twice())

    records = [c.args[0].fields for c in get_recorder.return_value.record.call_args_list]
    assert [(r["is_matched"], r["is_duplicate"]) for r in records] == [(True, False), (True, True)]
    assert records[0]["message_link"] == "https://t.me/chat/42"
    assert 2000 <= records[0]["telegram_delay_ms"] < 3000
    assert monitor.get_match_queue().qsize() == 1


@pytest.mark.parametrize(("forum", "reply_header", "topic_id"), [
    (False, None, None),
    (False, MessageReplyHeader(reply_to_msg_id=5), None),
    (True, None, 1),
    (True, MessageReplyHeader(reply_to_msg_id=5), 1),
    (True, MessageReplyHeader(forum_topic=True, reply_to_msg_id=5), 5),
    (True, MessageReplyHeader(forum_topic=True, reply_to_msg_id=9, reply_to_top_id=5), 5),
    # Story replies aren't in a topic, so they belong to General
    (True, MessageReplyStoryHeader(peer=PeerChannel(1), story_id=3), 1),
])
def test_message_topic(forum, reply_header, topic_id):
    monitor = Monitor(Mock(), Mock(), Mock(), Mock(), MonitorStaticConfig(), MonitorDynamicConfig())
    chat = ChatInfo(id=123, forum=forum, title="Chat", username="chat")

    assert monitor._get_chat_topic(chat, reply_header).topic_id == topic_id


def channel(chat_id: int, username: str) -> Mock:
    return Mock(spec=Channel, id=chat_id, username=username, forum=False, title=username.title())


def subscription(chat_id: int, username: str) -> ChatSubscription:
    return ChatSubscription(id=chat_id, username=username, topic_ids={None})


def test_warm_cache_resolves_usernames_only_for_chats_the_session_does_not_know():
    known_ids = {1: channel(1, "one")}
    usernames = {"two": channel(2, "two")}

    async def get_entity(identifier):
        if isinstance(identifier, PeerChannel):
            if identifier.channel_id not in known_ids:
                raise ValueError("Could not find the input entity")
            return known_ids[identifier.channel_id]
        if identifier in usernames:
            return usernames[identifier]
        raise ConnectionError("chat no longer exists")

    client = Mock(get_entity=AsyncMock(side_effect=get_entity))
    resolver = ChatResolver(client)

    with patch("telegram_forwarder_bot.monitoring.logger") as logger:
        asyncio.run(resolver.warm_cache(
            [subscription(1, "one"), subscription(2, "two"), subscription(3, "three")]))

    resolved_usernames = [c.args[0] for c in client.get_entity.await_args_list
                          if isinstance(c.args[0], str)]
    assert resolved_usernames == ["two", "three"]
    assert set(resolver._cache) == {1, "one", 2, "two"}
    logger.exception.assert_called_once()
