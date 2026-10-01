import hashlib
import os
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field

import ahocorasick
from pydantic import BaseModel

from config import StorageManagerDynamicConfig, StorageManagerStaticConfig
from metrics import RuntimeInstrumentationBase, traceable


def _build_link(username: str, topic_id: int | None) -> str:
    if topic_id:
        return f"https://t.me/{username}/{topic_id}"
    return f"https://t.me/{username}"


@dataclass(frozen=True)
class ResolvedChat:
    id: int
    topic_id: int | None
    username: str = field(compare=False)

    @property
    def link(self) -> str:
        return _build_link(self.username, self.topic_id)


@dataclass(frozen=True)
class MonitoredChat:
    id: int
    username: str = field(compare=False)
    topic_ids: set[int | None]

    @staticmethod
    def from_resolved(resolved: ResolvedChat) -> "MonitoredChat":
        return MonitoredChat(
            id=resolved.id,
            username=resolved.username,
            topic_ids=set([resolved.topic_id]),
        )

    def get_links(self, delimiter: str = ", ") -> str:
        return delimiter.join(
            map(lambda x: _build_link(self.username, x), self.topic_ids)
        )


@dataclass(frozen=True)
class KeywordGroup:
    positives: frozenset[str]
    negatives: frozenset[str]

    @classmethod
    def from_lists(cls, positives: list[str], negatives: list[str]) -> "KeywordGroup":
        positives_set = frozenset(p.lower() for p in positives)
        negatives_set = frozenset(n.lower() for n in negatives)
        return cls(positives=positives_set, negatives=negatives_set)


class StorageFile(BaseModel):
    """The storage file's JSON format, also written by tools/migrate_storage.py."""
    paused: bool = False
    keyword_groups: list[KeywordGroup] = []
    chats: list[MonitoredChat] = []
    # Oldest first, so the LRU order survives
    seen_hashes: list[str] = []


class StorageData(RuntimeInstrumentationBase):
    def __init__(self, dynamic_config: StorageManagerDynamicConfig | None = None):
        # Persisted fields
        self._paused: bool = False
        self._keyword_groups: set[KeywordGroup] = set()
        self._monitored_chats: dict[int, MonitoredChat] = dict()
        self._seen_hashes: OrderedDict[str, None] = OrderedDict()

        # Derived fields
        self._automaton: ahocorasick.Automaton | None = None
        self._chat_ids_by_username: dict[str, int] = dict()
        self._dynamic_config = dynamic_config

    def on_config_update(self, dynamic_config: StorageManagerDynamicConfig) -> None:
        self._dynamic_config = dynamic_config

    @classmethod
    def from_file(cls, file: StorageFile,
                  dynamic_config: StorageManagerDynamicConfig) -> "StorageData":
        data = cls(dynamic_config)
        data._paused = file.paused
        data._keyword_groups = set(file.keyword_groups)
        data._monitored_chats = {chat.id: chat for chat in file.chats}
        data._seen_hashes = OrderedDict.fromkeys(file.seen_hashes)
        data._build_chat_index()
        data._build_automaton()
        return data

    def to_file(self) -> StorageFile:
        return StorageFile(
            paused=self._paused,
            keyword_groups=list(self._keyword_groups),
            chats=list(self._monitored_chats.values()),
            seen_hashes=list(self._seen_hashes),
        )

    def _build_chat_index(self) -> None:
        self._chat_ids_by_username = {
            chat.username.casefold(): chat.id
            for chat in self._monitored_chats.values()
        }

    @traceable
    def _build_automaton(self) -> None:
        if not self._keyword_groups:
            self._automaton = None
            return

        automaton = ahocorasick.Automaton()
        for group in self._keyword_groups:
            for keyword in group.positives | group.negatives:
                automaton.add_word(keyword, keyword)
        automaton.make_automaton()
        self._automaton = automaton

    def toggle_paused(self):
        self._paused ^= True

    def paused(self) -> bool:
        return self._paused

    def add_keyword_group(self, group: KeywordGroup) -> None:
        self._keyword_groups.add(group)
        self._build_automaton()

    def remove_keyword_group(self, group: KeywordGroup) -> bool:
        if group in self._keyword_groups:
            self._keyword_groups.discard(group)
            self._build_automaton()
            return True
        return False

    def list_keyword_groups(self) -> Iterator[str]:
        for group in self._keyword_groups:
            parts = "_".join(sorted(group.positives))
            if group.negatives:
                parts += "_" + \
                    "_".join("!" + n for n in sorted(group.negatives))
            yield parts

    def keyword_group_count(self) -> int:
        return len(self._keyword_groups)

    def _is_duplicate(self, text: str) -> bool:
        config = self._dynamic_config  # so that we read consistent state of config
        assert config is not None

        if not config.dedup_enabled:
            return False

        hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        if hash in self._seen_hashes:
            self._seen_hashes.move_to_end(hash)
            return True
        self._seen_hashes[hash] = None
        while len(self._seen_hashes) > config.dedup_cache_size:
            self._seen_hashes.popitem(last=False)

        return False

    @traceable
    def matches_keywords(self, text: str) -> tuple[bool, set[str]]:
        found_keywords: set[str] = set()
        if not self._keyword_groups or self._automaton is None:
            return False, found_keywords

        text_lower = text.lower()
        for end_index, keyword in self._automaton.iter(text_lower):
            start_index = end_index - len(keyword) + 1
            # Register match only at the start of a word
            if start_index == 0 or not text_lower[start_index - 1].isalpha():
                found_keywords.add(keyword)

        if not found_keywords:
            return False, found_keywords

        for group in self._keyword_groups:
            if found_keywords.issuperset(
                group.positives
            ) and not found_keywords.intersection(group.negatives):
                # Matches should be rarer than non-matches, so we're checking for duplicates here
                if self._is_duplicate(text):
                    return False, found_keywords
                # It's a group match, but let's return all keywords found in the message
                return True, found_keywords

        return False, found_keywords

    def add_chat(self, chat: ResolvedChat) -> None:
        monitored = self._monitored_chats.setdefault(
            chat.id, MonitoredChat.from_resolved(chat)
        )
        self._chat_ids_by_username[monitored.username.casefold(
        )] = monitored.id
        monitored.topic_ids.add(chat.topic_id)

    def remove_chat(self, resolved_chat: ResolvedChat) -> bool:
        if resolved_chat.id not in self._monitored_chats:
            return False
        monitored_chat = self._monitored_chats[resolved_chat.id]
        if resolved_chat.topic_id not in monitored_chat.topic_ids:
            return False
        monitored_chat.topic_ids.remove(resolved_chat.topic_id)
        if not monitored_chat.topic_ids:
            del self._monitored_chats[resolved_chat.id]
            username = monitored_chat.username.casefold()
            if self._chat_ids_by_username.get(username) == resolved_chat.id:
                del self._chat_ids_by_username[username]
        return True

    def find_chat(
        self, username: str, topic_id: int | None
    ) -> ResolvedChat | None:
        chat_id = self._chat_ids_by_username.get(username.casefold())
        if chat_id is None:
            return None
        monitored_chat = self._monitored_chats[chat_id]
        if topic_id not in monitored_chat.topic_ids:
            return None
        return ResolvedChat(
            id=monitored_chat.id,
            topic_id=topic_id,
            username=monitored_chat.username,
        )

    def is_chat_id_monitored(self, chat_id: int) -> bool:
        return chat_id in self._monitored_chats

    def is_resolved_chat_monitored(self, resolved_chat: ResolvedChat) -> bool:
        if resolved_chat.id not in self._monitored_chats:
            return False
        monitored_chat = self._monitored_chats[resolved_chat.id]
        return (
            resolved_chat.topic_id in monitored_chat.topic_ids
            or None in monitored_chat.topic_ids
        )

    def list_chats(self) -> Iterator[MonitoredChat]:
        yield from self._monitored_chats.values()

    def chat_count(self) -> int:
        return len(self._monitored_chats)


class StorageManager:
    def __init__(self, static_config: StorageManagerStaticConfig,
                 dynamic_config: StorageManagerDynamicConfig):
        self._static_config = static_config
        self._dynamic_config = dynamic_config

        self.data: StorageData = StorageData(dynamic_config)
        database_path = self._static_config.database_path
        if database_path.exists():
            file = StorageFile.model_validate_json(database_path.read_bytes())
            self.data = StorageData.from_file(file, dynamic_config)

    def on_config_update(self, dynamic_config: StorageManagerDynamicConfig) -> None:
        self._dynamic_config = dynamic_config
        self.data.on_config_update(self._dynamic_config)

    def flush(self) -> None:
        database_path = self._static_config.database_path
        tmp = database_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self.data.to_file().model_dump_json(indent=2))
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, database_path)
