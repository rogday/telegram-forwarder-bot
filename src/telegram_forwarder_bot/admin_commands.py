import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

from telethon import events

from .app_logging import get_logger
from .monitoring import ChatResolver
from .storage import ChatTopic, KeywordGroup, StorageManager

logger = get_logger(__name__)

T = TypeVar("T")

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def _ok(message: str) -> str:
    return f"✅ {message}"


def _fail(message: str) -> str:
    return f"❌ {message}"


def _toggle(items: Iterable[T], remove: Callable[[T], bool], add: Callable[[T], None],
            count: Callable[[], int]) -> str:
    added: list[str] = []
    removed: list[str] = []
    for item in items:
        if remove(item):
            removed.append(str(item))
        else:
            add(item)
            added.append(str(item))
    return _ok(f"removed: {', '.join(removed)}; added: {', '.join(added)}; total: {count()} item(s)")


def _split_message(text: str) -> list[str]:
    """Splits text into messages Telegram accepts, at line boundaries."""
    messages = [""]
    for line in text.splitlines(keepends=True):
        if messages[-1] and len(messages[-1]) + len(line) > TELEGRAM_MAX_MESSAGE_LENGTH:
            messages.append("")
        messages[-1] += line
    return messages


class AdminCommands:
    """Handles the admin's messages to the bot: commands, chat links and keyword groups."""

    def __init__(
        self,
        storage: StorageManager,
        chat_resolver: ChatResolver,
        admin_id: int,
    ):
        self._storage = storage
        self._subscriptions = storage.subscriptions
        self._chat_resolver = chat_resolver
        self._admin_id = admin_id
        self._commands: dict[str, Callable[[list[str]], Awaitable[str]]] = {
            "/pause": self._cmd_pause,
            "/status": self._cmd_status,
        }

    async def handle(self, event: events.NewMessage.Event) -> None:
        if event.sender_id != self._admin_id:
            return

        text = str((event.text or "").strip())
        parts = text.split()
        if not parts:
            return
        cmd = parts[0].lower()

        handler = self._commands.get(cmd)
        args: list[str] = parts[1:] if handler else parts

        if handler is None:
            handler = (
                self._handle_chats
                if text.startswith("https://t.me/")
                else self._handle_keywords
            )

        try:
            result = await handler(args)
        except Exception as e:
            logger.exception("Bot command execution failed", cmd=cmd)
            return await event.respond(f"⚠️ Could not execute '{cmd}': {e}")

        for message in _split_message(result):
            await event.respond(message)

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

    async def _handle_chats(self, args: list[str]) -> str:
        chat_topics = await asyncio.gather(
            *(self._resolve_chat(arg) for arg in args)
        )

        for arg, chat_topic in zip(args, chat_topics):
            if chat_topic is None:
                return _fail(
                    f"Could not resolve chat '{arg}', check state and try again"
                )

        result = _toggle(chat_topics, self._subscriptions.remove_chat,
                         self._subscriptions.add_chat, self._subscriptions.chat_count)
        self._storage.flush()
        return result

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

        result = _toggle(keyword_groups, self._subscriptions.remove_keyword_group,
                         self._subscriptions.add_keyword_group,
                         self._subscriptions.keyword_group_count)
        self._storage.flush()
        return result

    async def _cmd_pause(self, _: list[str]) -> str:
        self._subscriptions.toggle_paused()
        self._storage.flush()
        status = "Paused" if self._subscriptions.paused() else "Unpaused"
        return _ok(f"{status} notifications")

    async def _cmd_status(self, _: list[str]) -> str:
        # One item per line, so a long status splits between items
        keyword_groups = sorted(str(group) for group in self._subscriptions.keyword_groups())
        chats = sorted(self._subscriptions.list_chats(), key=lambda chat: chat.username.casefold())
        return "\n".join([
            "⚙️ **Bot Status**",
            "",
            f"Paused: `{self._subscriptions.paused()}`",
            f"Keyword groups: {len(keyword_groups)}",
            *(f"`{group}`" for group in keyword_groups),
            f"Chats: {len(chats)}",
            *(link for chat in chats for link in chat.links()),
        ])
