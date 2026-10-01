import logging
import sys
from pathlib import Path

import pytest
from loguru import logger

from app_logging import LogManager  # Also routes standard logging through loguru
from config import LogManagerDynamicConfig, load_dynamic_config


@pytest.fixture
def records():
    captured = []
    handler_id = logger.add(
        lambda message: captured.append(message.record), level="DEBUG")
    yield captured
    logger.remove(handler_id)


@pytest.mark.parametrize(("logger_name", "source"), [
    ("telethon.user.network.mtprotosender", "telethon.user"),
    ("telethon.bot.client.updates", "telethon.bot"),
    ("influxdb_client_3.write_client.client.write_api", "influxdb_client_3"),
    ("urllib3.connectionpool", "urllib3"),
])
def test_library_logs_keep_logger_name_source_and_caller(
    records, logger_name, source
):
    logging.getLogger(logger_name).warning("library message")

    [record] = [r for r in records if r["message"] == "library message"]
    assert record["name"] == logger_name
    assert record["extra"]["source"] == source
    assert record["function"] == (
        "test_library_logs_keep_logger_name_source_and_caller")


def test_standard_logging_from_app_modules_is_attributed_to_app(
    records, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".dynamic.yml").write_text(
        "monitor:\n  ping_interval_seconds: 0\n", encoding="utf-8")

    load_dynamic_config()

    [record] = [r for r in records
                if r["message"].startswith("Invalid dynamic configuration")]
    assert record["name"] == "config"
    assert record["extra"]["source"] == "app"
    assert record["function"] == "load_dynamic_config"


@pytest.fixture
def restore_logging():
    root_level = logging.getLogger().level
    yield
    # LogManager removes every loguru handler, including the default one
    logger.remove()
    logger.add(sys.stderr)
    logging.getLogger().setLevel(root_level)


def test_log_file_rotates_by_size(restore_logging, tmp_path: Path):
    LogManager(LogManagerDynamicConfig(
        log_file_path=tmp_path / "app.jsonl", max_bytes=4096, max_files=100))

    for i in range(20):
        logger.info("line {}", i)
    logger.complete()  # Waits for the enqueued file sink
    logger.remove()

    # About 20 serialized lines of a few hundred bytes each fit into 2-3 files
    assert 2 <= len(list(tmp_path.iterdir())) < 5
