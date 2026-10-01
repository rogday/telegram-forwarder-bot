"""Serialization and persistence tests for storage.py."""

import shutil
from pathlib import Path

from migrate_storage import migrate
from storage import KeywordGroup, ResolvedChat, StorageData, StorageFile

PICKLED_STORAGE = Path(__file__).parent.parent / "fixtures" / "pickled_storage.db"


class TestSerialization:
    """Tests for converting StorageData to and from the storage file."""

    def test_json_roundtrip(self, storage_data_class, storage_dynamic_config):
        data = storage_data_class()
        data.toggle_paused()
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], ["bye"]))
        data.add_chat(ResolvedChat(username="test", id=123456, topic_id=789))
        data.add_chat(ResolvedChat(username="test", id=123456, topic_id=None))
        data.matches_keywords("hello world")  # Remembers the message hash

        file = StorageFile.model_validate_json(data.to_file().model_dump_json())
        restored = StorageData.from_file(file, storage_dynamic_config)

        assert restored.paused()
        assert list(restored.list_keyword_groups()) == ["hello_world_!bye"]
        assert restored.find_chat("TEST", 789) == ResolvedChat(
            username="test", id=123456, topic_id=789
        )
        # Automaton is rebuilt, and the remembered hash marks a repost
        assert restored.matches_keywords("hello world") == (False, {"hello", "world"})
        assert restored.matches_keywords("hello again, world") == (True, {"hello", "world"})

    def test_restore_with_no_keywords(self, storage_data_class, storage_dynamic_config):
        data = storage_data_class()

        restored = StorageData.from_file(data.to_file(), storage_dynamic_config)

        assert restored._automaton is None
        matched, found = restored.matches_keywords("any text")
        assert not matched
        assert found == set()


class TestStorageManager:
    """Tests for StorageManager persistence."""

    def test_flush_and_reload(self, storage_manager_class, tmp_path):
        db_path = tmp_path / "test.db"

        manager = storage_manager_class(db_path)
        manager.data.add_keyword_group(
            KeywordGroup.from_lists(["hello", "world"], [])
        )
        manager.data.add_chat(
            ResolvedChat(username="test", id=123456, topic_id=789)
        )
        manager.flush()

        restored = storage_manager_class(db_path)
        assert restored.data.keyword_group_count() == 1
        assert restored.data.chat_count() == 1
        matched, found = restored.data.matches_keywords("hello world")
        assert matched
        assert "hello" in found

    def test_empty_storage(self, storage_manager_class, tmp_path):
        db_path = tmp_path / "empty.db"

        manager = storage_manager_class(db_path)
        assert manager.data.keyword_group_count() == 0
        assert manager.data.chat_count() == 0

    def test_multiple_operations_persisted(self, storage_manager_class, tmp_path):
        db_path = tmp_path / "multi.db"

        manager = storage_manager_class(db_path)
        manager.data.add_keyword_group(KeywordGroup.from_lists(["test1"], []))
        manager.data.add_keyword_group(KeywordGroup.from_lists(["test2"], []))
        manager.data.add_chat(ResolvedChat(username="111", id=111, topic_id=1))
        manager.data.add_chat(ResolvedChat(
            username="222", id=222, topic_id=None))
        manager.flush()

        restored = storage_manager_class(db_path)
        assert restored.data.keyword_group_count() == 2
        assert restored.data.chat_count() == 2

    def test_loads_storage_converted_by_migration_tool(self, storage_manager_class, tmp_path):
        db_path = tmp_path / "storage.db"
        shutil.copy(PICKLED_STORAGE, db_path)
        migrate(db_path)

        data = storage_manager_class(db_path).data

        assert data.paused()
        assert sorted(data.list_keyword_groups()) == [
            "machine-learning_python_!course", "работа"]
        assert data.find_chat("somegroup", 5) == ResolvedChat(
            username="SomeGroup", id=1234567890, topic_id=5)
        assert data.is_chat_id_monitored(987654321)
        # "first message" is in the converted dedup cache
        data.add_keyword_group(KeywordGroup.from_lists(["message"], []))
        assert data.matches_keywords("first message") == (False, {"message"})
