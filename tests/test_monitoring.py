import asyncio
from contextlib import contextmanager
from unittest.mock import Mock

from config import (
    MonitorDynamicConfig,
    MonitorStaticConfig,
    RuntimeInstrumentationManagerDynamicConfig,
    RuntimeInstrumentationManagerStaticConfig,
)
from metrics import RuntimeInstrumentationManager
from monitoring import Monitor


def test_registered_handler_uses_current_instrumentation(monkeypatch, tmp_path):
    sessions = []

    def session(name):
        @contextmanager
        def enter(_func):
            sessions.append(name)
            yield
        return enter

    monkeypatch.setattr(RuntimeInstrumentationManager, '_profile_session',
                        staticmethod(session('profiling')))
    monkeypatch.setattr(RuntimeInstrumentationManager, '_stack_dump_session',
                        staticmethod(session('stack_dump')))
    # Restrict instrumentation discovery and restore the class after the test.
    monkeypatch.setattr(Monitor, '_handle_new_message_impl', Monitor._handle_new_message_impl)
    monkeypatch.setattr(Monitor, '__subclasses__', lambda: [])
    monkeypatch.setattr('metrics.RuntimeInstrumentationBase.__subclasses__',
                        lambda: [Monitor])
    manager = RuntimeInstrumentationManager(
        RuntimeInstrumentationManagerStaticConfig(profile_dir=tmp_path),
        RuntimeInstrumentationManagerDynamicConfig(),
    )
    client, storage = Mock(), Mock()
    storage.paused.return_value = True
    Monitor(client, storage, Mock(), MonitorStaticConfig(), MonitorDynamicConfig())
    callback = client.add_event_handler.call_args.args[0]

    async def exercise_modes():
        for mode in ('disabled', 'profiling', 'stack_dump', 'disabled'):
            manager.on_config_update(
                RuntimeInstrumentationManagerDynamicConfig(mode=mode)
            )
            await callback(Mock())

    asyncio.run(exercise_modes())
    assert sessions == ['profiling', 'stack_dump']
    assert storage.paused.call_count == 4
