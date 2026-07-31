"""Pytest configuration and shared fixtures for storage tests."""

from functools import partial
from pathlib import Path

import pytest

from config import StorageManagerDynamicConfig, StorageManagerStaticConfig
from storage import StorageData, StorageManager


@pytest.fixture
def storage_dynamic_config() -> StorageManagerDynamicConfig:
    return StorageManagerDynamicConfig()


@pytest.fixture
def storage_data_class(storage_dynamic_config):
    return partial(StorageData, storage_dynamic_config)


@pytest.fixture
def storage_manager_class(storage_dynamic_config):
    def create(database_path: str | Path) -> StorageManager:
        static_config = StorageManagerStaticConfig(
            database_path=Path(database_path)
        )
        return StorageManager(static_config, storage_dynamic_config)

    return create
