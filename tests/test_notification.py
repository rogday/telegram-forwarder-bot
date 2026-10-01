import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from telegram_forwarder_bot.config import NotifierDynamicConfig
from telegram_forwarder_bot.monitoring import MatchEvent
from telegram_forwarder_bot.notification import Notifier
from telegram_forwarder_bot.storage import ChatTopic


def deliver(send_message: AsyncMock) -> list:
    """Delivers one match and returns the recorded metric records."""
    async def run():
        queue: asyncio.Queue[MatchEvent] = asyncio.Queue()
        notifier = Notifier(Mock(send_message=send_message), queue, 42, NotifierDynamicConfig())
        task = asyncio.create_task(notifier.listen())
        queue.put_nowait(MatchEvent(
            source_title="Chat", chat=ChatTopic(id=1, topic_id=None, username="chat"),
            message_id=7, date=None, matched_keywords={"python"},
            received_at_ns=time.time_ns()))
        await queue.join()
        task.cancel()

    with patch("telegram_forwarder_bot.notification.get_metric_recorder") as get_recorder, \
            patch("telegram_forwarder_bot.notification.logger"):
        asyncio.run(run())
    return [c.args[0] for c in get_recorder.return_value.record.call_args_list]


@pytest.mark.parametrize(("send_message", "status"), [
    (AsyncMock(), "sent"),
    (AsyncMock(side_effect=ConnectionError("offline")), "failed"),
])
def test_delivery_is_recorded_with_latency_since_receipt(send_message, status):
    [record] = deliver(send_message)

    assert record.table_name == "delivery_stats"
    assert record.tags == dict(status=status)
    assert record.fields["latency_ns"] >= 0
