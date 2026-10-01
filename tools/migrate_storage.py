"""Converts a pickled storage.db to JSON. Run it once per instance while the bot is stopped.

Usage: python migrate_storage.py PATH

The original is kept as PATH.pickle-backup. Doesn't import the app, so it
works no matter what the app's classes are called by now.
"""

import json
import pickle
import sys
from pathlib import Path


class _Object:
    pass


class _Unpickler(pickle.Unpickler):
    # The app's classes become plain objects; pickle fills their __dict__
    def find_class(self, module: str, name: str) -> type:
        if module == "storage":
            return _Object
        return super().find_class(module, name)


def convert(data: _Object) -> dict:
    return {
        "paused": data._paused,
        "keyword_groups": [
            {"positives": list(group.positives),
             "negatives": list(getattr(group, "negatives", ()))}
            for group in data._keyword_groups
        ],
        "chats": [
            {"id": chat.id, "username": chat.username, "topic_ids": list(chat.topic_ids)}
            for chat in data._monitored_chats.values()
        ],
        # Oldest first, so the LRU order survives
        "seen_hashes": list(getattr(data, "_seen_hashes", ())),
    }


def migrate(path: Path) -> None:
    backup = path.with_name(path.name + ".pickle-backup")
    if backup.exists():
        sys.exit(f"{backup} already exists")

    with open(path, "rb") as f:
        storage = convert(_Unpickler(f).load())

    path.rename(backup)
    path.write_text(json.dumps(storage, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Converted {path}, original kept as {backup}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    migrate(Path(sys.argv[1]))
