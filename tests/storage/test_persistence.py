"""Serialization and persistence tests for storage.py."""

import pickle

from storage import KeywordGroup, ResolvedChat


class TestSerialization:
    """Tests for StorageData pickling/unpickling."""

    def test_pickle_roundtrip(self, storage_data_class):
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], []))
        data.add_chat(ResolvedChat(username="test", id=123456, topic_id=789))

        # Pickle and unpickle
        pickled = pickle.dumps(data)
        restored = pickle.loads(pickled)

        assert restored.paused() == data.paused()
        assert restored.keyword_group_count() == data.keyword_group_count()
        assert restored.chat_count() == data.chat_count()
        assert restored.find_chat("TEST", 789) == ResolvedChat(
            username="test", id=123456, topic_id=789
        )
        # Automaton should be rebuilt after unpickling
        assert restored._automaton is not None

    def test_unpickle_restores_automaton(
        self, storage_data_class, storage_dynamic_config
    ):
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["test"], []))

        pickled = pickle.dumps(data)
        restored = pickle.loads(pickled)
        restored.on_config_update(storage_dynamic_config)

        matched, found = restored.matches_keywords("this is a test message")
        assert matched
        assert "test" in found

    def test_unpickle_with_no_keywords(self, storage_data_class):
        data = storage_data_class()

        pickled = pickle.dumps(data)
        restored = pickle.loads(pickled)

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
