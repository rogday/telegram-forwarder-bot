import asyncio
import threading
from unittest.mock import Mock

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from telegram_forwarder_bot.config import ConfigWatcherStaticConfig
from telegram_forwarder_bot.config_watcher import ConfigWatcher, _ConfigFileHandler


@pytest.mark.parametrize('event_factory', [
    lambda path: FileModifiedEvent(path),
    lambda path: FileCreatedEvent(path),
    lambda path: FileMovedEvent('temporary.yml', path),
])
def test_reload_debounces_saves_and_preserves_config_on_error(
    tmp_path, monkeypatch, event_factory
):
    monkeypatch.chdir(tmp_path)
    timer_factory = Mock()
    monkeypatch.setattr('telegram_forwarder_bot.config_watcher.Timer', timer_factory)
    received = []
    handler = _ConfigFileHandler(
        ConfigWatcherStaticConfig(debounce_interval_ms=250), [received.append])
    config_path = tmp_path / '.dynamic.yml'
    config_path.write_text('monitor:\n  ping_interval_seconds: 11\n')

    first_timer, second_timer = Mock(), Mock()
    timer_factory.side_effect = [first_timer, second_timer]
    event = event_factory(str(config_path))
    handler.dispatch(event)
    assert timer_factory.call_args.args[0] == 0.25
    stale_reload = timer_factory.call_args.args[1]
    handler.dispatch(event)
    reload = timer_factory.call_args.args[1]
    first_timer.cancel.assert_called_once()
    stale_reload()
    assert received == []
    reload()
    assert [config.monitor.ping_interval_seconds for config in received] == [11]

    config_path.write_text('monitor:\n  ping_interval_seconds: 0\n')
    timer_factory.side_effect = None
    handler.dispatch(event)
    timer_factory.call_args.args[1]()
    assert len(received) == 1

    config_path.unlink()
    handler.dispatch(event)
    timer_factory.call_args.args[1]()
    assert len(received) == 1

    config_path.write_text('monitor:\n  ping_interval_seconds: 12\n')
    handler.dispatch(event)
    pending_reload = timer_factory.call_args.args[1]
    handler.close()
    pending_reload()
    assert len(received) == 1


def test_watcher_reloads_atomic_replacement_on_the_event_loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / '.dynamic.yml'
    config_path.write_text('{}\n')
    received = []

    async def run():
        updated = asyncio.Event()

        def on_update(config):
            received.append((config.monitor.ping_interval_seconds, threading.get_ident()))
            updated.set()

        watcher = ConfigWatcher(ConfigWatcherStaticConfig(), [on_update])
        watcher.start()
        try:
            replacement = tmp_path / 'replacement.yml'
            replacement.write_text('monitor:\n  ping_interval_seconds: 11\n')
            replacement.replace(config_path)
            await asyncio.wait_for(updated.wait(), 2)
        finally:
            watcher.stop()

    asyncio.run(run())
    assert received == [(11, threading.get_ident())]
