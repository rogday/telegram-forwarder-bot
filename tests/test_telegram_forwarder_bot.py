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
