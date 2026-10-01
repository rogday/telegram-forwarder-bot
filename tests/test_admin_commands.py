import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from telegram_forwarder_bot.admin_commands import AdminCommands
from telegram_forwarder_bot.storage import ChatTopic

ADMIN_ID = 42


@pytest.fixture
def storage(storage_manager_class, tmp_path):
    return storage_manager_class(tmp_path / "storage.db")


@pytest.fixture
def resolve_chat() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def commands(storage, resolve_chat) -> AdminCommands:
    return AdminCommands(storage, Mock(resolve=resolve_chat), ADMIN_ID)


def send(commands: AdminCommands, text: str, sender_id: int = ADMIN_ID) -> str | None:
    event = Mock(sender_id=sender_id, text=text, respond=AsyncMock())
    asyncio.run(commands.handle(event))
    return event.respond.await_args.args[0] if event.respond.await_args else None


def test_ignores_messages_from_others(commands, storage) -> None:
    assert send(commands, "/pause", sender_id=7) is None
    assert not storage.subscriptions.paused()


def test_removes_deleted_chat(commands, storage, resolve_chat) -> None:
    storage.subscriptions.add_chat(
        ChatTopic(username="DeletedChannel", id=123456, topic_id=None))

    result = send(commands, "https://t.me/deletedchannel")

    assert storage.subscriptions.chat_count() == 0
    assert storage.subscriptions.find_chat("deletedchannel", None) is None
    resolve_chat.assert_not_awaited()
    assert "removed: https://t.me/DeletedChannel" in result


def test_removes_deleted_chat_topic(commands, storage, resolve_chat) -> None:
    storage.subscriptions.add_chat(
        ChatTopic(username="DeletedChannel", id=123456, topic_id=789))

    result = send(commands, "https://t.me/deletedchannel/789")

    assert storage.subscriptions.chat_count() == 0
    resolve_chat.assert_not_awaited()
    assert "removed: https://t.me/DeletedChannel/789" in result


def test_resolves_and_adds_unknown_chat(commands, storage, resolve_chat) -> None:
    chat_topic = ChatTopic(username="NewChannel", id=123456, topic_id=None)
    resolve_chat.return_value = chat_topic

    result = send(commands, "https://t.me/newchannel")

    assert storage.subscriptions.is_topic_monitored(chat_topic)
    resolve_chat.assert_awaited_once_with("newchannel", None)
    assert "added: https://t.me/NewChannel" in result


def test_rejects_unresolvable_unknown_chat(commands, storage, resolve_chat) -> None:
    resolve_chat.return_value = None

    result = send(commands, "https://t.me/missingchannel")

    assert storage.subscriptions.chat_count() == 0
    assert result.startswith("❌ Could not resolve chat")


@pytest.mark.parametrize("invalid", ["!", "python_!", "!spam"])
def test_invalid_keyword_groups_do_not_modify_storage(commands, storage, invalid):
    result = send(commands, f"valid {invalid}")

    assert result.startswith("❌")
    assert storage.subscriptions.keyword_group_count() == 0


def test_keyword_group_with_exclusion_can_be_added_and_removed(commands, storage):
    assert send(commands, "python_!course").startswith("✅")
    assert not storage.subscriptions.matcher.match("python course")[0]
    assert storage.subscriptions.matcher.match("python job")[0]

    assert send(commands, "python_!course").startswith("✅")
    assert storage.subscriptions.keyword_group_count() == 0
