import asyncio
import contextlib
import random
from dataclasses import dataclass

from telethon import TelegramClient, events, utils
from telethon.tl.custom.message import Message as TelethonMessage
from telethon.tl.functions import PingRequest
from telethon.tl.types import (
    Channel,
    MessageReplyHeader,
    TypeMessageReplyHeader,
    datetime,
)

from app_logging import get_logger
from config import MonitorDynamicConfig, MonitorStaticConfig
from metrics import (
    MetricRecord,
    RuntimeInstrumentationBase,
    get_metric_recorder,
    profileable,
    traceable,
)
from storage import ResolvedChat, StorageData

logger = get_logger(__name__)


@dataclass(frozen=True)
class CachedChat:
    id: int
    forum: bool
    title: str
    username: str


class ChatResolver:
    def __init__(self, client: TelegramClient) -> None:
        self._client: TelegramClient = client
        # FIXME: Add cache updater with configurable interval
        self._cache: dict[int | str, CachedChat | None] = dict()

    async def _query_chat(self, identifier: int | str) -> CachedChat | None:
        try:
            chat = await self._client.get_entity(identifier)
            if chat is None or not isinstance(chat, Channel) or not chat.username:
                return None
            return CachedChat(
                id=chat.id,
                forum=bool(chat.forum),
                title=chat.title,
                username=chat.username,
            )
        except Exception:
            logger.exception("Chat resolution error", identifier=identifier)
            raise

    async def resolve_cached(self, identifier: int | str) -> CachedChat | None:
        if identifier in self._cache:
            return self._cache[identifier]

        logger.warning("Chat resolution cache miss", identifier=identifier)
        chat = await self._query_chat(identifier)

        if chat is not None:
            self._cache[chat.id] = chat
            self._cache[chat.username] = chat
        else:
            self._cache[identifier] = None

        return chat

    async def resolve(
        self, identifier: int | str, topic_id: int | None
    ) -> ResolvedChat | None:
        chat = await self.resolve_cached(identifier)
        if chat is None:
            return None
        return ResolvedChat(id=chat.id, topic_id=topic_id, username=chat.username)


@dataclass(frozen=True)
class MatchEvent:
    source_title: str
    chat: ResolvedChat
    message_id: int
    date: datetime | None
    matched_keywords: set[str]


class Monitor(RuntimeInstrumentationBase):
    def __init__(
        self,
        client: TelegramClient,
        storage: StorageData,
        chat_resolver: ChatResolver,
        static_config: MonitorStaticConfig,
        dynamic_config: MonitorDynamicConfig
    ):
        self._static_config = static_config
        self._dynamic_config = dynamic_config

        self._force_sync_task: asyncio.Task | None = None
        self._client: TelegramClient = client
        self._storage: StorageData = storage
        self._match_queue: asyncio.Queue[MatchEvent] = asyncio.Queue(
            maxsize=self._static_config.match_queue_size)
        self._chat_resolver: ChatResolver = chat_resolver
        self._client.add_event_handler(
            self._handle_new_message, events.NewMessage())

    def start(self) -> None:
        self._force_sync_task = asyncio.create_task(self._ping_loop())

    async def stop(self) -> None:
        if self._force_sync_task is not None:
            self._force_sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._force_sync_task

    def on_config_update(self, dynamic_config: MonitorDynamicConfig) -> None:
        self._dynamic_config = dynamic_config

    def get_match_queue(self) -> asyncio.Queue[MatchEvent]:
        return self._match_queue

    async def _ping_loop(self):
        while True:
            try:
                await self._client(PingRequest(ping_id=random.randint(0, 2**31 - 1)))
            except Exception:
                logger.exception("Ping request failed")
            await asyncio.sleep(self._dynamic_config.ping_interval_seconds)

    # FIXME: Maybe add argument to decorator and somehow measure end to end latency from receive to send
    @profileable
    @traceable
    async def _handle_new_message_impl(self, event: events.NewMessage.Event) -> None:
        if self._storage.paused():
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

        if not self._storage.is_chat_id_monitored(chat_id):
            return

        logger.debug("Chat id is monitored", chat_id=chat_id)
        cached_chat = await self._chat_resolver.resolve_cached(chat_id)
        logger.debug("Got CachedChat", cached_chat=cached_chat)

        # No username if private group chat
        if not cached_chat or not cached_chat.username:
            return
        resolved_chat = self._get_resolved_chat(cached_chat, message.reply_to)
        logger.debug("Got ResolvedChat", resolved_chat=resolved_chat)
        if not self._storage.is_resolved_chat_monitored(resolved_chat):
            return

        # NOTE: Blocking event loop, but too fast for multiprocessing
        is_matched, matched_keywords = self._storage.matches_keywords(
            message_text)
        get_metric_recorder().record(
            MetricRecord(
                table_name="payload_stats",
                tags=dict(metric_type="message_size"),
                fields=dict(
                    size=len(message_text),
                    is_matched=is_matched,
                    matched_keywords=len(matched_keywords),
                    message_preview=message_text[:100],
                ),
            )
        )
        if not is_matched:
            return

        try:
            self._match_queue.put_nowait(
                MatchEvent(
                    source_title=cached_chat.title,
                    chat=resolved_chat,
                    message_id=message.id,
                    date=message.date,
                    matched_keywords=matched_keywords,
                )
            )
        except asyncio.QueueFull:
            logger.exception(
                "Notifier queue is full",
                queue_size=self._match_queue.qsize(),
                max_size=self._match_queue.maxsize,
            )

    async def _handle_new_message(self, event: events.NewMessage.Event) -> None:
        try:
            await self._handle_new_message_impl(event)
        except Exception:
            logger.exception("Monitor error")

    def _get_resolved_chat(
        self, chat: CachedChat, reply_header: TypeMessageReplyHeader | None
    ) -> ResolvedChat:
        is_forum = chat.forum
        topic_id = None
        if reply_header is not None:
            if isinstance(reply_header, MessageReplyHeader):
                if reply_header.forum_topic:
                    topic_id = (
                        reply_header.reply_to_top_id or reply_header.reply_to_msg_id
                    )
                elif is_forum:
                    topic_id = 1
        elif is_forum:
            topic_id = 1

        return ResolvedChat(id=chat.id, topic_id=topic_id, username=chat.username)
