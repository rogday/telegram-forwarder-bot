import asyncio
from unittest.mock import Mock, patch

import pytest
from telethon import TelegramClient
from telethon.tl.functions.updates import GetStateRequest

from config import ClientDynamicConfig, RetryDynamicConfig
from telegram_client import ResilientTelegramClient


def create_client(attempts: int) -> ResilientTelegramClient:
    client = ResilientTelegramClient.__new__(ResilientTelegramClient)
    client._client_name = "user"
    client._client_config = ClientDynamicConfig(request_retry=RetryDynamicConfig(
        timeout_seconds=0.05, attempts=attempts,
        min_backoff_seconds=0.01, max_backoff_seconds=0.01))
    return client


def test_request_without_response_is_sent_again(monkeypatch):
    sent = []

    async def telethon_call(self, sender, request, ordered=False,
                            flood_sleep_threshold=None):
        sent.append(request)
        if len(sent) == 1:
            await asyncio.Future()  # The response never arrives
        return "state"

    monkeypatch.setattr(TelegramClient, "_call", telethon_call)
    request = GetStateRequest()

    with patch("retry.logger"):
        result = asyncio.run(create_client(attempts=2)._call(Mock(), request))

    assert result == "state"
    assert sent == [request, request]


def test_final_timeout_is_logged_and_raised(monkeypatch):
    async def telethon_call(self, sender, request, ordered=False,
                            flood_sleep_threshold=None):
        await asyncio.Future()

    monkeypatch.setattr(TelegramClient, "_call", telethon_call)

    with patch("telegram_client.logger") as logger, patch("retry.logger"), \
            pytest.raises(TimeoutError):
        asyncio.run(create_client(attempts=1)._call(Mock(), GetStateRequest()))

    logger.error.assert_called_once()
    assert logger.error.call_args.kwargs["description"] == (
        "Telegram user request GetStateRequest")
