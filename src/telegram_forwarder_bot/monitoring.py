import asyncio
import contextlib
import random
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from telethon import TelegramClient, events, utils
from telethon.tl.custom.message import Message as TelethonMessage
from telethon.tl.functions import PingRequest
from telethon.tl.types import (
    Channel,
    MessageReplyHeader,
    PeerChannel,
    TypeMessageReplyHeader,
)

from .app_logging import get_logger
from .config import MonitorDynamicConfig, MonitorStaticConfig
from .metrics import (
    MetricRecord,
    RuntimeInstrumentationBase,
    get_metric_recorder,
    profileable,
    traceable,
)
from .storage import ChatSubscription, ChatTopic, Deduplicator, Subscriptions

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChatInfo:
    id: int
    forum: bool
    title: str
    username: str


def _chat_info(entity: object) -> ChatInfo | None:
    if not isinstance(entity, Channel) or not entity.username:
        return None
    return ChatInfo(
        id=entity.id,
        forum=bool(entity.forum),
        title=entity.title,
        username=entity.username,
    )


class ChatResolver:
    def __init__(self, client: TelegramClient) -> None:
        self._client: TelegramClient = client
        # FIXME: Add cache updater with configurable interval
        self._cache: dict[int | str, ChatInfo | None] = dict()

    def _remember(self, chat: ChatInfo) -> None:
        self._cache[chat.id] = chat
        self._cache[chat.username] = chat

    async def warm_cache(self, chats: Iterable[ChatSubscription]) -> None:
        """Caches the subscribed chats by ID.

        Resolving a username always costs a request that Telegram rate-limits
        heavily, and usernames can change or be taken over, so they are only
        used for chats the session doesn't know.
        """
        for chat in chats:
            try:
                entity = await self._query_subscribed_chat(chat)
            except Exception:
                logger.exception("Skipping chat cache population after resolution error",
                                 chat_id=chat.id, username=chat.username)
                continue
            chat_info = _chat_info(entity)
            if chat_info is not None:
                self._remember(chat_info)

    async def _query_subscribed_chat(self, chat: ChatSubscription) -> object:
        try:
            return await self._client.get_entity(PeerChannel(chat.id))
        except ValueError:  # The session doesn't know this chat
            return await self._client.get_entity(chat.username)

    async def resolve_cached(self, identifier: int | str) -> ChatInfo | None:
        if identifier in self._cache:
            return self._cache[identifier]

        logger.warning("Chat resolution cache miss", identifier=identifier)
        chat = _chat_info(await self._client.get_entity(identifier))

        if chat is not None:
            self._remember(chat)
        else:
            self._cache[identifier] = None

        return chat

    async def resolve(
        self, identifier: int | str, topic_id: int | None
    ) -> ChatTopic | None:
        chat = await self.resolve_cached(identifier)
        if chat is None:
            return None
        return ChatTopic(id=chat.id, topic_id=topic_id, username=chat.username)


@dataclass(frozen=True)
class MatchEvent:
    source_title: str
    chat: ChatTopic
    message_id: int
    date: datetime | None
    matched_keywords: set[str]
    # time.time_ns() when the message arrived
    received_at_ns: int


class Monitor(RuntimeInstrumentationBase):
    def __init__(
        self,
        client: TelegramClient,
        subscriptions: Subscriptions,
        deduplicator: Deduplicator,
        chat_resolver: ChatResolver,
        static_config: MonitorStaticConfig,
        dynamic_config: MonitorDynamicConfig
    ):
        self._static_config = static_config
        self._dynamic_config = dynamic_config

        self._force_sync_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None
        self._client: TelegramClient = client
        self._subscriptions: Subscriptions = subscriptions
        self._deduplicator: Deduplicator = deduplicator
        self._match_queue: asyncio.Queue[MatchEvent] = asyncio.Queue(
            maxsize=self._static_config.match_queue_size)
        self._chat_resolver: ChatResolver = chat_resolver
        self._last_message_at: float = time.monotonic()
        self._messages_handled: int = 0

    def start(self) -> None:
        self._force_sync_task = asyncio.create_task(self._ping_loop())
        self._status_task = asyncio.create_task(self._status_loop())

    async def stop(self) -> None:
        for task in (self._force_sync_task, self._status_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def on_config_update(self, dynamic_config: MonitorDynamicConfig) -> None:
        self._dynamic_config = dynamic_config

    def get_match_queue(self) -> asyncio.Queue[MatchEvent]:
        return self._match_queue

    def health(self) -> dict[str, Any]:
        seconds_since_last_message = time.monotonic() - self._last_message_at
        return dict(
            status=seconds_since_last_message <= self._dynamic_config.max_silence_seconds,
            seconds_since_last_message=seconds_since_last_message,
        )

    async def _ping_loop(self):
        while True:
            try:
                await self._client(PingRequest(ping_id=random.randint(0, 2**31 - 1)))
            except Exception:
                logger.exception("Ping request failed")
            await asyncio.sleep(self._dynamic_config.ping_interval_seconds)

    def _log_status(self) -> None:
        logger.info("Monitor is running", messages_handled=self._messages_handled,
                    **self.health())
        self._messages_handled = 0

    # A rare INFO line that shows the bot and its logging are alive
    async def _status_loop(self):
        while True:
            await asyncio.sleep(self._dynamic_config.status_log_interval_seconds)
            self._log_status()

    @profileable
    @traceable
    async def _handle_new_message_impl(self, event: events.NewMessage.Event,
                                       received_at_ns: int) -> None:
        if self._subscriptions.paused():
            return

        message: TelethonMessage = event.message  # type: ignore
        # NOTE: If tags parsing is needed, we need
        # to use @property message.text, its expensive,
        # but it will be cached in the message object.
        message_text = message.message

        chat_id = event.chat_id
        # NOTE: 'Chat' is only for legacy private group chats
        # limited to 200 users, which cannot have usernames
        # NOTE: message_text check is easier than chat id lookup,
        # and albums are sent as separate messages with empty text.
        if not message_text or not event.is_channel or not isinstance(chat_id, int):
            return

        chat_id, _ = utils.resolve_id(chat_id)
        logger.debug("Got Chat id from message", chat_id=chat_id)

        if not self._subscriptions.is_chat_id_monitored(chat_id):
            return

        logger.debug("Chat id is monitored", chat_id=chat_id)
        chat_info = await self._chat_resolver.resolve_cached(chat_id)
        logger.debug("Got ChatInfo", chat_info=chat_info)

        # No username if private group chat
        if not chat_info or not chat_info.username:
            return
        chat_topic = self._get_chat_topic(chat_info, message.reply_to)
        logger.debug("Got ChatTopic", chat_topic=chat_topic)
        if not self._subscriptions.is_topic_monitored(chat_topic):
            return

        # NOTE: Blocking event loop, but too fast for multiprocessing
        is_matched, matched_keywords = self._subscriptions.matcher.match(message_text)
        # Only matched texts are remembered, so other messages can't push them out of the cache
        is_duplicate = is_matched and self._deduplicator.seen_before(message_text)
        payload_stats = dict(
            size=len(message_text),
            is_matched=is_matched,
            is_duplicate=is_duplicate,
            matched_keyword_count=len(matched_keywords),
            message_link=chat_topic.message_link(message.id),
        )
        if message.date is not None:
            # How late Telegram delivered the message; message.date has 1 s resolution
            payload_stats["telegram_delay_ms"] = (
                received_at_ns / 1e6 - message.date.timestamp() * 1000)
        get_metric_recorder().record(MetricRecord(
            table_name="payload_stats",
            tags=dict(metric_type="message_size"),
            fields=payload_stats,
        ))
        if not is_matched or is_duplicate:
            return

        try:
            self._match_queue.put_nowait(
                MatchEvent(
                    source_title=chat_info.title,
                    chat=chat_topic,
                    message_id=message.id,
                    date=message.date,
                    matched_keywords=matched_keywords,
                    received_at_ns=received_at_ns,
                )
            )
        except asyncio.QueueFull:
            logger.exception(
                "Notifier queue is full",
                queue_size=self._match_queue.qsize(),
                max_size=self._match_queue.maxsize,
            )

    async def handle_new_message(self, event: events.NewMessage.Event) -> None:
        received_at_ns = time.time_ns()
        self._last_message_at = time.monotonic()
        self._messages_handled += 1
        try:
            await self._handle_new_message_impl(event, received_at_ns)
        except Exception:
            logger.exception("Monitor error")

    def _get_chat_topic(
        self, chat: ChatInfo, reply_header: TypeMessageReplyHeader | None
    ) -> ChatTopic:
        if isinstance(reply_header, MessageReplyHeader) and reply_header.forum_topic:
            topic_id = reply_header.reply_to_top_id or reply_header.reply_to_msg_id
        elif chat.forum:
            topic_id = 1  # General
        else:
            topic_id = None
        return ChatTopic(id=chat.id, topic_id=topic_id, username=chat.username)
