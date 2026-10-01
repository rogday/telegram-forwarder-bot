from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import CoroutineType
from zoneinfo import ZoneInfo

from telethon import TelegramClient, events

from .app_logging import get_logger
from .config import NotifierDynamicConfig
from .monitoring import ChatResolver, MatchEvent
from .storage import ChatTopic, KeywordGroup, StorageManager

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
        f"[Link]({event.chat.link}/{event.message_id})"
    )


def _ok(message: str) -> str:
    return f"✅ {message}"


def _fail(message: str) -> str:
    return f"❌ {message}"


class Notifier:
    def __init__(
        self,
        client: TelegramClient,
        storage: StorageManager,
        match_queue: asyncio.Queue[MatchEvent],
        admin_id: int,
        chat_resolver: ChatResolver,
        dynamic_config: NotifierDynamicConfig,
    ):
        self._dynamic_config = dynamic_config
        self._client = client
        self._storage = storage
        self._match_queue = match_queue
        self._admin_id = admin_id
        self._chat_resolver = chat_resolver
        self._subscriptions = storage.subscriptions
        self._client.add_event_handler(
            self._route_command, events.NewMessage())

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

    async def _route_command(self, event: events.NewMessage.Event) -> None:
        if event.sender_id != self._admin_id:
            return

        text = str((event.text or "").strip())
        parts = text.split()
        if not parts:
            return
        cmd = parts[0].lower()

        handlers: dict[str, Callable[[list[str]], Awaitable[str]]] = {
            "/pause": self._cmd_pause,
            "/status": self._cmd_status,
        }

        handler = handlers.get(cmd)
        args: list[str] = parts[1:] if handler else parts

        if handler is None:
            handler = (
                self._handle_chats
                if text.startswith("https://t.me/")
                else self._handle_keywords
            )

        try:
            result = await handler(args)
            self._storage.flush()
        except Exception as e:
            logger.exception("Bot command execution failed", cmd=cmd)
            return await event.respond(f"⚠️ Could not execute '{cmd}': {e}")

        await event.respond(result)

    # FIXME: should be list[str] instead, and add support for list of chats to resolver as well.
    async def _resolve_chat(self, arg: str) -> ChatTopic | None:
        link = arg.strip()
        identifier = link.removeprefix("https://t.me/")
        if not identifier:
            return None

        parts = identifier.split("/")
        if not (1 <= len(parts) <= 2) or not all(parts):
            return None

        identifier = parts[0]
        topic_id = int(parts[1]) if len(parts) > 1 else None

        chat_topic = self._subscriptions.find_chat(identifier, topic_id)
        if chat_topic is not None:
            return chat_topic

        return await self._chat_resolver.resolve(identifier, topic_id)

    async def _handle_items(
        self, items, remove_fn, add_fn, display_fn, count_fn
    ) -> str:
        added: list[str] = []
        removed: list[str] = []

        for item in items:
            display = display_fn(item)
            if not remove_fn(item):
                add_fn(item)
                added.append(display)
            else:
                removed.append(display)

        return _ok(
            f"removed: {', '.join(removed)};"
            f"added: {', '.join(added)};"
            f"total: {count_fn()} item(s)"
        )

    async def _handle_chats(self, args: list[str]) -> str:
        chat_topics = await asyncio.gather(
            *(self._resolve_chat(arg) for arg in args)
        )

        for arg, chat_topic in zip(args, chat_topics):
            if chat_topic is None:
                return _fail(
                    f"Could not resolve chat '{arg}', check state and try again"
                )

        return await self._handle_items(
            items=chat_topics,
            remove_fn=self._subscriptions.remove_chat,
            add_fn=self._subscriptions.add_chat,
            display_fn=lambda chat: chat.link,
            count_fn=self._subscriptions.chat_count,
        )

    async def _handle_keywords(self, args: list[str]) -> str:
        # Each token is a group of keywords separated by "_"
        # Keywords prefixed with "!" are negative (exclusion) keywords
        keyword_groups: list[KeywordGroup] = []
        for token in args:
            parts = [p.strip().lower() for p in token.split("_") if p.strip()]
            positives = [p for p in parts if not p.startswith("!")]
            negatives = [p[1:] for p in parts if p.startswith("!")]
            if not positives or "" in negatives:
                return _fail("Each group needs a positive keyword and non-empty exclusions")
            keyword_groups.append(
                KeywordGroup.from_lists(positives, negatives))

        if not keyword_groups:
            return _fail("No valid keywords found")

        return await self._handle_items(
            items=keyword_groups,
            remove_fn=self._subscriptions.remove_keyword_group,
            add_fn=self._subscriptions.add_keyword_group,
            display_fn=lambda group: (
                " ".join(sorted(group.positives))
                + (
                    " " + " ".join("!" + ng for ng in sorted(group.negatives))
                    if group.negatives
                    else ""
                )
            ),
            count_fn=self._subscriptions.keyword_group_count,
        )

    async def _cmd_pause(self, _: list[str]) -> str:
        self._subscriptions.toggle_paused()
        status = "Paused" if self._subscriptions.paused() else "Unpaused"
        return _ok(f"{status} notifications")

    async def _cmd_status(self, _: list[str]) -> str:
        keyword_list = " ".join(self._subscriptions.list_keyword_groups()) or "none"
        chat_list = (
            " ".join([chat.get_links(" ") for chat in self._subscriptions.list_chats()])
            or "none"
        )
        return (
            f"⚙️ **Bot Status**\n\n"
            f"Paused: `{self._subscriptions.paused()}`\n"
            f"Keyword groups: {self._subscriptions.keyword_group_count()} (`{keyword_list}`)\n"
            f"Chats:   {self._subscriptions.chat_count()} chat(s) ({chat_list})\n"
        )
