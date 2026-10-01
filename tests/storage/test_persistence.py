"""Persistence tests for StorageManager."""

import shutil
from pathlib import Path

from migrate_storage import migrate
from telegram_forwarder_bot.storage import ChatTopic, KeywordGroup

PICKLED_STORAGE = Path(__file__).parent.parent / "fixtures" / "pickled_storage.db"


class TestStorageManager:
    def test_flush_and_reload(self, storage_manager_class, tmp_path):
        db_path = tmp_path / "storage.db"
        manager = storage_manager_class(db_path)
        subscriptions = manager.subscriptions
        subscriptions.toggle_paused()
        subscriptions.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], ["bye"]))
        subscriptions.add_chat(ChatTopic(username="test", id=123456, topic_id=789))
        subscriptions.add_chat(ChatTopic(username="test", id=123456, topic_id=None))
        manager.deduplicator.seen_before("hello world")
        manager.flush()

        restored = storage_manager_class(db_path)

        assert restored.subscriptions.paused()
        assert [str(group) for group in restored.subscriptions.keyword_groups()] == ["hello_world_!bye"]
        assert restored.subscriptions.find_chat("TEST", 789) == ChatTopic(
            username="test", id=123456, topic_id=789)
        assert restored.subscriptions.is_topic_monitored(
            ChatTopic(username="test", id=123456, topic_id=5))
        assert restored.subscriptions.matcher.match("hello world") == (True, {"hello", "world"})
        assert restored.deduplicator.seen_before("hello world")

    def test_empty_storage(self, storage_manager_class, tmp_path):
        manager = storage_manager_class(tmp_path / "empty.db")

        assert manager.subscriptions.keyword_group_count() == 0
        assert manager.subscriptions.chat_count() == 0
        assert manager.subscriptions.matcher.match("any text") == (False, set())

    def test_loads_storage_converted_by_migration_tool(self, storage_manager_class, tmp_path):
        db_path = tmp_path / "storage.db"
        shutil.copy(PICKLED_STORAGE, db_path)
        migrate(db_path)

        manager = storage_manager_class(db_path)

        subscriptions = manager.subscriptions
        assert subscriptions.paused()
        assert sorted(str(group) for group in subscriptions.keyword_groups()) == [
            "machine-learning_python_!course", "работа"]
        assert subscriptions.find_chat("somegroup", 5) == ChatTopic(
            username="SomeGroup", id=1234567890, topic_id=5)
        assert subscriptions.is_chat_id_monitored(987654321)
        assert manager.deduplicator.seen_before("first message")
