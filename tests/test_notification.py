import asyncio
from unittest.mock import AsyncMock

import pytest

from notification import Notifier
from storage import ResolvedChat, StorageData


def create_notifier(data: StorageData) -> tuple[Notifier, AsyncMock]:
    notifier = Notifier.__new__(Notifier)
    notifier._data = data
    resolve_chat = AsyncMock()
    chat_resolver = AsyncMock()
    chat_resolver.resolve = resolve_chat
    notifier._chat_resolver = chat_resolver
    return notifier, resolve_chat


def test_handle_chats_removes_deleted_chat_from_storage(
    storage_data_class
) -> None:
    data = storage_data_class()
    data.add_chat(
        ResolvedChat(username="DeletedChannel", id=123456, topic_id=None)
    )
    notifier, resolve_chat = create_notifier(data)

    result = asyncio.run(
        notifier._handle_chats(["https://t.me/deletedchannel"])
    )

    assert data.chat_count() == 0
    assert data.find_chat("deletedchannel", None) is None
    resolve_chat.assert_not_awaited()
    assert "removed: https://t.me/DeletedChannel" in result


def test_handle_chats_removes_deleted_chat_topic_from_storage(
    storage_data_class
) -> None:
    data = storage_data_class()
    data.add_chat(
        ResolvedChat(username="DeletedChannel", id=123456, topic_id=789)
    )
    notifier, resolve_chat = create_notifier(data)

    result = asyncio.run(
        notifier._handle_chats(["https://t.me/deletedchannel/789"])
    )

    assert data.chat_count() == 0
    resolve_chat.assert_not_awaited()
    assert "removed: https://t.me/DeletedChannel/789" in result


def test_handle_chats_resolves_and_adds_unknown_chat(
    storage_data_class
) -> None:
    data = storage_data_class()
    notifier, resolve_chat = create_notifier(data)
    resolved_chat = ResolvedChat(
        username="NewChannel", id=123456, topic_id=None
    )
    resolve_chat.return_value = resolved_chat

    result = asyncio.run(
        notifier._handle_chats(["https://t.me/newchannel"])
    )

    assert data.is_resolved_chat_monitored(resolved_chat)
    resolve_chat.assert_awaited_once_with("newchannel", None)
    assert "added: https://t.me/NewChannel" in result


def test_handle_chats_rejects_unresolvable_unknown_chat(
    storage_data_class
) -> None:
    data = storage_data_class()
    notifier, resolve_chat = create_notifier(data)
    resolve_chat.return_value = None

    result = asyncio.run(
        notifier._handle_chats(["https://t.me/missingchannel"])
    )

    assert data.chat_count() == 0
    assert result.startswith("❌ Could not resolve chat")


@pytest.mark.parametrize("invalid", ["!", "python_!", "!spam"])
def test_invalid_keyword_groups_do_not_modify_storage(storage_data_class, invalid):
    data = storage_data_class()
    notifier, _ = create_notifier(data)

    result = asyncio.run(notifier._handle_keywords(["valid", invalid]))

    assert result.startswith("❌")
    assert data.keyword_group_count() == 0
    assert data.matches_keywords("hello") == (False, set())


def test_keyword_group_with_exclusion_can_be_added_and_removed(storage_data_class):
    data = storage_data_class()
    notifier, _ = create_notifier(data)

    assert asyncio.run(notifier._handle_keywords(["python_!course"])).startswith("✅")
    assert not data.matches_keywords("python course")[0]
    assert data.matches_keywords("python job")[0]
    assert asyncio.run(notifier._handle_keywords(["python_!course"])).startswith("✅")
    assert data.keyword_group_count() == 0
