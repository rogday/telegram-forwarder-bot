import hashlib
import os
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import ahocorasick
from pydantic import BaseModel

from .config import StorageManagerDynamicConfig, StorageManagerStaticConfig
from .metrics import RuntimeInstrumentationBase, traceable


def _build_link(username: str, topic_id: int | None) -> str:
    if topic_id:
        return f"https://t.me/{username}/{topic_id}"
    return f"https://t.me/{username}"


@dataclass(frozen=True)
class ChatTopic:
    id: int
    topic_id: int | None
    username: str = field(compare=False)

    @property
    def link(self) -> str:
        return _build_link(self.username, self.topic_id)

    def message_link(self, message_id: int) -> str:
        return f"{self.link}/{message_id}"

    # The link the admin sends, so it can be pasted back to toggle the chat
    def __str__(self) -> str:
        return self.link


@dataclass
class ChatSubscription:
    id: int
    username: str
    # None means the whole chat
    topic_ids: set[int | None]

    def links(self) -> list[str]:
        # None goes first; sorted() can't compare it with int
        topic_ids = sorted(self.topic_ids, key=lambda topic_id: (topic_id is not None, topic_id or 0))
        return [_build_link(self.username, topic_id) for topic_id in topic_ids]


@dataclass(frozen=True)
class KeywordGroup:
    positives: frozenset[str]
    negatives: frozenset[str]

    @classmethod
    def from_lists(cls, positives: list[str], negatives: list[str]) -> "KeywordGroup":
        positives_set = frozenset(p.lower() for p in positives)
        negatives_set = frozenset(n.lower() for n in negatives)
        return cls(positives=positives_set, negatives=negatives_set)

    # The syntax the admin types, so it can be pasted back to toggle the group
    def __str__(self) -> str:
        return "_".join([*sorted(self.positives), *("!" + n for n in sorted(self.negatives))])


class StorageFile(BaseModel):
    """The storage file's JSON format, also written by tools/migrate_storage.py."""
    paused: bool = False
    keyword_groups: list[KeywordGroup] = []
    chats: list[ChatSubscription] = []
    # Oldest first, so the LRU order survives
    seen_hashes: list[str] = []


class KeywordMatcher(RuntimeInstrumentationBase):
    """Immutable, so build a new one when the groups change."""

    def __init__(self, groups: Iterable[KeywordGroup] = ()):
        self._groups = frozenset(groups)
        self._automaton: ahocorasick.Automaton | None = None
        self._build_automaton()

    @traceable
    def _build_automaton(self) -> None:
        if not self._groups:
            return

        automaton = ahocorasick.Automaton()
        for group in self._groups:
            for keyword in group.positives | group.negatives:
                automaton.add_word(keyword, keyword)
        automaton.make_automaton()
        self._automaton = automaton

    @traceable
    def match(self, text: str) -> tuple[bool, set[str]]:
        """Returns whether a group matched, and every keyword found in the text.

        The keywords include ones from other groups, exclusions too, by design:
        they give the reader more context.
        """
        found_keywords: set[str] = set()
        if self._automaton is None:
            return False, found_keywords

        text_lower = text.lower()
        for end_index, keyword in self._automaton.iter(text_lower):
            start_index = end_index - len(keyword) + 1
            # Register match only at the start of a word
            if start_index == 0 or not text_lower[start_index - 1].isalpha():
                found_keywords.add(keyword)

        is_matched = any(
            found_keywords.issuperset(group.positives)
            and not found_keywords.intersection(group.negatives)
            for group in self._groups
        )
        return is_matched, found_keywords


class Deduplicator:
    def __init__(self, dynamic_config: StorageManagerDynamicConfig,
                 seen_hashes: Iterable[str] = ()):
        self._dynamic_config = dynamic_config
        self._seen_hashes: OrderedDict[str, None] = OrderedDict.fromkeys(seen_hashes)

    def on_config_update(self, dynamic_config: StorageManagerDynamicConfig) -> None:
        self._dynamic_config = dynamic_config

    def seen_before(self, text: str) -> bool:
        """Returns whether text was seen before, and remembers it."""
        config = self._dynamic_config  # so that we read consistent state of config
        if not config.dedup_enabled:
            return False

        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        if digest in self._seen_hashes:
            self._seen_hashes.move_to_end(digest)
            return True
        self._seen_hashes[digest] = None
        while len(self._seen_hashes) > config.dedup_cache_size:
            self._seen_hashes.popitem(last=False)
        return False

    def seen_hashes(self) -> list[str]:
        return list(self._seen_hashes)


class Subscriptions:
    def __init__(self, paused: bool = False, keyword_groups: Iterable[KeywordGroup] = (),
                 chats: Iterable[ChatSubscription] = ()):
        self._paused = paused
        self._keyword_groups: set[KeywordGroup] = set(keyword_groups)
        self._chats: dict[int, ChatSubscription] = {chat.id: chat for chat in chats}
        self._chat_ids_by_username: dict[str, int] = {
            chat.username.casefold(): chat.id for chat in self._chats.values()
        }
        self.matcher = KeywordMatcher(self._keyword_groups)

    def toggle_paused(self):
        self._paused ^= True

    def paused(self) -> bool:
        return self._paused

    def add_keyword_group(self, group: KeywordGroup) -> None:
        self._keyword_groups.add(group)
        self.matcher = KeywordMatcher(self._keyword_groups)

    def remove_keyword_group(self, group: KeywordGroup) -> bool:
        if group not in self._keyword_groups:
            return False
        self._keyword_groups.discard(group)
        self.matcher = KeywordMatcher(self._keyword_groups)
        return True

    def keyword_groups(self) -> Iterator[KeywordGroup]:
        yield from self._keyword_groups

    def keyword_group_count(self) -> int:
        return len(self._keyword_groups)

    def add_chat(self, chat_topic: ChatTopic) -> None:
        subscription = self._chats.setdefault(
            chat_topic.id,
            ChatSubscription(id=chat_topic.id, username=chat_topic.username, topic_ids=set()),
        )
        self._chat_ids_by_username[subscription.username.casefold()] = subscription.id
        subscription.topic_ids.add(chat_topic.topic_id)

    def remove_chat(self, chat_topic: ChatTopic) -> bool:
        subscription = self._chats.get(chat_topic.id)
        if subscription is None or chat_topic.topic_id not in subscription.topic_ids:
            return False
        subscription.topic_ids.remove(chat_topic.topic_id)
        if not subscription.topic_ids:
            del self._chats[chat_topic.id]
            username = subscription.username.casefold()
            if self._chat_ids_by_username.get(username) == chat_topic.id:
                del self._chat_ids_by_username[username]
        return True

    def find_chat(self, username: str, topic_id: int | None) -> ChatTopic | None:
        chat_id = self._chat_ids_by_username.get(username.casefold())
        if chat_id is None:
            return None
        subscription = self._chats[chat_id]
        if topic_id not in subscription.topic_ids:
            return None
        return ChatTopic(id=subscription.id, topic_id=topic_id, username=subscription.username)

    def is_chat_id_monitored(self, chat_id: int) -> bool:
        return chat_id in self._chats

    def is_topic_monitored(self, chat_topic: ChatTopic) -> bool:
        subscription = self._chats.get(chat_topic.id)
        if subscription is None:
            return False
        return (
            chat_topic.topic_id in subscription.topic_ids
            or None in subscription.topic_ids
        )

    def list_chats(self) -> Iterator[ChatSubscription]:
        yield from self._chats.values()

    def chat_count(self) -> int:
        return len(self._chats)


class StorageManager:
    def __init__(self, static_config: StorageManagerStaticConfig,
                 dynamic_config: StorageManagerDynamicConfig):
        self._static_config = static_config

        database_path = self._static_config.database_path
        file = StorageFile()
        if database_path.exists():
            file = StorageFile.model_validate_json(database_path.read_bytes())
        self.subscriptions = Subscriptions(file.paused, file.keyword_groups, file.chats)
        self.deduplicator = Deduplicator(dynamic_config, file.seen_hashes)

    def on_config_update(self, dynamic_config: StorageManagerDynamicConfig) -> None:
        self.deduplicator.on_config_update(dynamic_config)

    def flush(self) -> None:
        file = StorageFile(
            paused=self.subscriptions.paused(),
            keyword_groups=list(self.subscriptions.keyword_groups()),
            chats=list(self.subscriptions.list_chats()),
            seen_hashes=self.deduplicator.seen_hashes(),
        )
        database_path = self._static_config.database_path
        tmp = database_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(file.model_dump_json(indent=2))
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, database_path)
