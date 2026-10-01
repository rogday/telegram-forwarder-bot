from __future__ import annotations

import asyncio
from types import CoroutineType
from zoneinfo import ZoneInfo

from telethon import TelegramClient

from .app_logging import get_logger
from .config import NotifierDynamicConfig
from .monitoring import MatchEvent

logger = get_logger(__name__)


def _format_match(event: MatchEvent, timezone: str) -> str:
    date_str = "Unknown"

    if event.date:
        date = event.date.astimezone(ZoneInfo(timezone))
        date_str = date.strftime("%Y-%m-%d %H:%M:%S")

    source = event.source_title.replace("`", "")
    keywords = ", ".join(event.matched_keywords).replace("`", "")

    return (
        f"🔍 **Found Match with** [`{keywords}`]:\n"
        f"Source: `{source}`\n"
        f"Time: {date_str}\n"
        f"[Link]({event.chat.message_link(event.message_id)})"
    )


class Notifier:
    def __init__(
        self,
        client: TelegramClient,
        match_queue: asyncio.Queue[MatchEvent],
        admin_id: int,
        dynamic_config: NotifierDynamicConfig,
    ):
        self._dynamic_config = dynamic_config
        self._client = client
        self._match_queue = match_queue
        self._admin_id = admin_id

    def on_config_update(self, dynamic_config: NotifierDynamicConfig) -> None:
        self._dynamic_config = dynamic_config

    def listen(self) -> CoroutineType[None, None, None]:
        return self._dispatch_matches()

    async def _dispatch_matches(self) -> None:
        try:
            while True:
                event = await self._match_queue.get()
                try:
                    text = _format_match(event, self._dynamic_config.timezone)
                    await self._client.send_message(
                        entity=self._admin_id,
                        message=text,
                        parse_mode="md",
                    )
                except Exception:
                    logger.exception("Admin notification message send failed")
                finally:
                    self._match_queue.task_done()
        except asyncio.CancelledError:
            pass
