import asyncio
from unittest.mock import AsyncMock

import pytest

from telegram_forwarder_bot.notification import Notifier
from telegram_forwarder_bot.storage import ChatTopic, Subscriptions


def create_notifier(data: Subscriptions) -> tuple[Notifier, AsyncMock]:
    notifier = Notifier.__new__(Notifier)
    notifier._subscriptions = data
    resolve_chat = AsyncMock()
    chat_resolver = AsyncMock()
    chat_resolver.resolve = resolve_chat
    notifier._chat_resolver = chat_resolver
    return notifier, resolve_chat


def test_handle_chats_removes_deleted_chat_from_storage() -> None:
    data = Subscriptions()
    data.add_chat(
        ChatTopic(username="DeletedChannel", id=123456, topic_id=None)
    )
    notifier, resolve_chat = create_notifier(data)

    result = asyncio.run(
        notifier._handle_chats(["https://t.me/deletedchannel"])
    )

    assert data.chat_count() == 0
    assert data.find_chat("deletedchannel", None) is None
    resolve_chat.assert_not_awaited()
    assert "removed: https://t.me/DeletedChannel" in result


def test_handle_chats_removes_deleted_chat_topic_from_storage() -> None:
    data = Subscriptions()
    data.add_chat(
        ChatTopic(username="DeletedChannel", id=123456, topic_id=789)
    )
    notifier, resolve_chat = create_notifier(data)

    result = asyncio.run(
        notifier._handle_chats(["https://t.me/deletedchannel/789"])
    )

    assert data.chat_count() == 0
    resolve_chat.assert_not_awaited()
    assert "removed: https://t.me/DeletedChannel/789" in result


def test_handle_chats_resolves_and_adds_unknown_chat() -> None:
    data = Subscriptions()
    notifier, resolve_chat = create_notifier(data)
    chat_topic = ChatTopic(
        username="NewChannel", id=123456, topic_id=None
    )
    resolve_chat.return_value = chat_topic

    result = asyncio.run(
        notifier._handle_chats(["https://t.me/newchannel"])
    )

    assert data.is_topic_monitored(chat_topic)
    resolve_chat.assert_awaited_once_with("newchannel", None)
    assert "added: https://t.me/NewChannel" in result


def test_handle_chats_rejects_unresolvable_unknown_chat() -> None:
    data = Subscriptions()
    notifier, resolve_chat = create_notifier(data)
    resolve_chat.return_value = None

    result = asyncio.run(
        notifier._handle_chats(["https://t.me/missingchannel"])
    )

    assert data.chat_count() == 0
    assert result.startswith("❌ Could not resolve chat")


@pytest.mark.parametrize("invalid", ["!", "python_!", "!spam"])
def test_invalid_keyword_groups_do_not_modify_storage(invalid):
    data = Subscriptions()
    notifier, _ = create_notifier(data)

    result = asyncio.run(notifier._handle_keywords(["valid", invalid]))

    assert result.startswith("❌")
    assert data.keyword_group_count() == 0
    assert data.matcher.match("hello") == (False, set())


def test_keyword_group_with_exclusion_can_be_added_and_removed():
    data = Subscriptions()
    notifier, _ = create_notifier(data)

    assert asyncio.run(notifier._handle_keywords(["python_!course"])).startswith("✅")
    assert not data.matcher.match("python course")[0]
    assert data.matcher.match("python job")[0]
    assert asyncio.run(notifier._handle_keywords(["python_!course"])).startswith("✅")
    assert data.keyword_group_count() == 0
