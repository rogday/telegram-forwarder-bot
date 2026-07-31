from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from app_logging import get_logger
from config import DYNAMIC_CONFIG_NAME, DynamicConfig, load_dynamic_config

logger = get_logger(__name__)


def _load_dynamic_config(callbacks: list[Callable]) -> DynamicConfig:
    loaded_config = load_dynamic_config()
    logger.info(
        f"Got new dynamic config from {DYNAMIC_CONFIG_NAME}: {loaded_config}")
    for callback in callbacks:
        callback(loaded_config)
    return loaded_config


class _ConfigFileHandler(FileSystemEventHandler):
    def __init__(self, callbacks: list[Callable]):
        self._callbacks: list[Callable] = callbacks
        _load_dynamic_config(self._callbacks)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if Path(str(event.src_path)) != Path(DYNAMIC_CONFIG_NAME):
            return
        try:
            _load_dynamic_config(self._callbacks)
        except Exception:
            logger.exception("Failed to reload dynamic config")


class ConfigWatcher:
    def __init__(self, callbacks: list[Callable] = []):
        self._observer: BaseObserver = Observer()
        self._callbacks: list[Callable] = callbacks

    def start(self) -> None:
        handler = _ConfigFileHandler(self._callbacks)
        self._observer.schedule(handler, str(
            Path(DYNAMIC_CONFIG_NAME).parent), recursive=False)
        self._observer.start()
        logger.info(f"Config watcher started for {DYNAMIC_CONFIG_NAME}")

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

    def subscribe(self, callback: Callable) -> None:
        self.stop()
        self._callbacks.append(callback)
        self.start()
