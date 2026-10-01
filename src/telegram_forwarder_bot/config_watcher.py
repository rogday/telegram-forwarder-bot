import asyncio
from collections.abc import Callable
from functools import partial
from pathlib import Path
from threading import Lock, Timer

from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileSystemMovedEvent
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from .config import DYNAMIC_CONFIG_NAME, ConfigWatcherStaticConfig, DynamicConfig


def _reload_and_notify(callbacks: list[Callable]) -> DynamicConfig:
    if not Path(DYNAMIC_CONFIG_NAME).is_file():
        raise FileNotFoundError(DYNAMIC_CONFIG_NAME)
    loaded_config = DynamicConfig()
    logger.info(
        f"Got new dynamic config from {DYNAMIC_CONFIG_NAME}: {loaded_config}")
    for callback in callbacks:
        callback(loaded_config)
    return loaded_config


class _ConfigFileHandler(FileSystemEventHandler):
    def __init__(self, static_config: ConfigWatcherStaticConfig, callbacks: list[Callable]):
        self._static_config = static_config
        self._callbacks = callbacks
        self._path = Path(DYNAMIC_CONFIG_NAME).resolve()
        self._lock = Lock()
        self._debounce_timer: Timer | None = None
        self._closed = False

    def _schedule_reload(self, path: str) -> None:
        if Path(path).resolve() != self._path:
            return
        with self._lock:
            if self._closed:
                return
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            debounce_timer = Timer(
                self._static_config.debounce_interval_ms / 1000,
                lambda: self._reload(debounce_timer))
            debounce_timer.daemon = True
            self._debounce_timer = debounce_timer
            debounce_timer.start()

    def _reload(self, debounce_timer: Timer) -> None:
        with self._lock:
            if self._closed or self._debounce_timer is not debounce_timer:
                return
            self._debounce_timer = None
            try:
                _reload_and_notify(self._callbacks)
            except Exception:
                logger.exception("Failed to reload dynamic config; keeping current settings")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_reload(str(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_reload(str(event.src_path))

    def on_moved(self, event: FileSystemMovedEvent) -> None:
        if not event.is_directory:
            self._schedule_reload(str(event.dest_path))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None


class ConfigWatcher:
    def __init__(self, static_config: ConfigWatcherStaticConfig,
                 callbacks: list[Callable] | None = None):
        self._static_config = static_config
        self._observer: BaseObserver = Observer()
        self._callbacks: list[Callable] = callbacks if callbacks is not None else []
        self._handler: _ConfigFileHandler | None = None

    def start(self) -> None:
        # The file is parsed and validated on the watcher's thread, but the
        # callbacks run on the event loop, like the rest of the bot
        loop = asyncio.get_running_loop()
        callbacks = [partial(loop.call_soon_threadsafe, callback) for callback in self._callbacks]
        self._handler = _ConfigFileHandler(self._static_config, callbacks)
        self._observer.schedule(self._handler, str(
            Path(DYNAMIC_CONFIG_NAME).parent), recursive=False)
        self._observer.start()
        logger.info(f"Config watcher started for {DYNAMIC_CONFIG_NAME}")

    def stop(self) -> None:
        if self._handler is not None:
            self._handler.close()
        self._observer.stop()
        self._observer.join()
