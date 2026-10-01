"""Tests for tools/migrate_storage.py."""

import json
import shutil
from pathlib import Path

import pytest

from migrate_storage import migrate

# Written by the pickle-based StorageManager with protocol 4, Python 3.12's default
FIXTURE = Path(__file__).parent / "fixtures" / "pickled_storage.db"


@pytest.fixture
def storage_path(tmp_path: Path) -> Path:
    path = tmp_path / "storage.db"
    shutil.copy(FIXTURE, path)
    return path


def test_converts_pickle_to_json_and_keeps_backup(storage_path: Path):
    migrate(storage_path)

    storage = json.loads(storage_path.read_text(encoding="utf-8"))
    assert storage["paused"] is True
    assert sorted((sorted(g["positives"]), g["negatives"]) for g in storage["keyword_groups"]) == [
        (["machine-learning", "python"], ["course"]),
        (["работа"], []),
    ]
    assert sorted((c["id"], c["username"], sorted(c["topic_ids"], key=str))
                  for c in storage["chats"]) == [
        (987654321, "otherchannel", [None]),
        (1234567890, "SomeGroup", [5, None]),
    ]
    # SHA-256 prefixes of "first message", "second message", "third message"
    assert storage["seen_hashes"] == [
        "db01a79b2801d711", "2bbc8b6b338a7c9e", "d0fac776dc41302d"]
    backup = storage_path.with_name("storage.db.pickle-backup")
    assert backup.read_bytes() == FIXTURE.read_bytes()


def test_existing_backup_is_never_overwritten(storage_path: Path):
    backup = storage_path.with_name("storage.db.pickle-backup")
    backup.write_bytes(b"older backup")

    with pytest.raises(SystemExit):
        migrate(storage_path)

    assert backup.read_bytes() == b"older backup"
    assert storage_path.read_bytes() == FIXTURE.read_bytes()
