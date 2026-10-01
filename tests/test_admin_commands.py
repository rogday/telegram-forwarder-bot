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


def send_all(commands: AdminCommands, text: str, sender_id: int = ADMIN_ID) -> list[str]:
    event = Mock(sender_id=sender_id, text=text, respond=AsyncMock())
    asyncio.run(commands.handle(event))
    return [c.args[0] for c in event.respond.await_args_list]


def send(commands: AdminCommands, text: str, sender_id: int = ADMIN_ID) -> str | None:
    replies = send_all(commands, text, sender_id)
    return replies[-1] if replies else None


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


def test_replies_use_the_syntax_the_admin_types(commands):
    assert send(commands, "python_!Course django").endswith(
        "removed: ; added: python_!course, django; total: 2 item(s)")
    # Pasting a reply's group back removes it
    assert "removed: python_!course;" in send(commands, "python_!course")


def test_status_lists_sorted_groups_and_chats(commands, storage):
    storage.subscriptions.add_chat(ChatTopic(username="zeta", id=2, topic_id=5))
    storage.subscriptions.add_chat(ChatTopic(username="zeta", id=2, topic_id=None))
    storage.subscriptions.add_chat(ChatTopic(username="Alpha", id=1, topic_id=None))
    send(commands, "web_rust python_!course")

    assert send(commands, "/status").splitlines()[3:] == [
        "Keyword groups: 2",
        "`python_!course`",
        "`rust_web`",
        "Chats: 2",
        "https://t.me/Alpha",
        "https://t.me/zeta",
        "https://t.me/zeta/5",
    ]


def test_long_status_is_split_between_lines(commands, storage):
    groups = [f"keyword{i:04}_{'x' * 40}" for i in range(300)]
    send(commands, " ".join(groups))

    replies = send_all(commands, "/status")

    assert len(replies) > 1
    assert all(len(reply) <= 4096 for reply in replies)
    lines = "".join(replies).splitlines()
    assert lines[4:304] == [f"`{group}`" for group in sorted(groups)]


def test_only_commands_that_change_state_save_storage(commands, storage, tmp_path):
    database_path = tmp_path / "storage.db"

    send(commands, "/status")
    assert not database_path.exists()

    send(commands, "/pause")
    assert database_path.exists()
