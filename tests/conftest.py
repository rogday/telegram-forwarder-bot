"""Pytest configuration and shared fixtures for storage tests."""

from pathlib import Path

import pytest

from telegram_forwarder_bot.config import StorageManagerDynamicConfig, StorageManagerStaticConfig
from telegram_forwarder_bot.storage import StorageManager


@pytest.fixture
def storage_dynamic_config() -> StorageManagerDynamicConfig:
    return StorageManagerDynamicConfig()


@pytest.fixture
def storage_manager_class(storage_dynamic_config):
    def create(database_path: str | Path) -> StorageManager:
        static_config = StorageManagerStaticConfig(
            database_path=Path(database_path)
        )
        return StorageManager(static_config, storage_dynamic_config)

    return create
