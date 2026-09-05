import asyncio
import signal
from unittest.mock import AsyncMock, Mock

import pytest

import main


@pytest.mark.parametrize('outcome', ['normal', 'startup_error', 'constructor_error', 'sigterm'])
def test_main_cleans_up_and_preserves_errors(monkeypatch, outcome):
    bot = Mock(start=AsyncMock(), stop=AsyncMock())
    factory = Mock(return_value=bot)
    monkeypatch.setattr(main, 'TelegramForwarderBot', factory)
    monkeypatch.setattr(main, 'logger', Mock())
    if outcome == 'constructor_error':
        factory.side_effect = ValueError('constructor failed')
    elif outcome == 'startup_error':
        bot.start.side_effect = ValueError('startup failed')
    elif outcome == 'sigterm':
        async def start():
            signal.raise_signal(signal.SIGTERM)
            await asyncio.Future()
        bot.start.side_effect = start

    async def run():
        await asyncio.wait_for(main.main(), timeout=2)

    if outcome.endswith('_error'):
        with pytest.raises(ValueError):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert bot.stop.await_count == (0 if outcome == 'constructor_error' else 1)
