from telethon import TelegramClient, utils

from app_logging import get_logger
from config import ClientDynamicConfig
from retry import retry_with_timeout

logger = get_logger(__name__)


def _request_name(request) -> str:
    requests = request if utils.is_list_like(request) else [request]
    return ", ".join(type(r).__name__ for r in requests)


class ResilientTelegramClient(TelegramClient):
    """TelegramClient whose requests time out and are retried.

    Telethon awaits responses without a timeout, including the getDifference
    requests of its update loop, so a single lost response can stall update
    handling indefinitely while pings keep working.
    """

    def __init__(self, *args, client_name: str,
                 dynamic_config: ClientDynamicConfig, **kwargs) -> None:
        self._client_name = client_name
        self._client_config = dynamic_config
        # Telethon logs of this client go to telethon.<client_name>.*
        super().__init__(*args, base_logger=f"telethon.{client_name}", **kwargs)

    def on_config_update(self, dynamic_config: ClientDynamicConfig) -> None:
        self._client_config = dynamic_config

    # NOTE: Every request goes through _call, including the ones Telethon makes
    # internally. Its update loop treats the final TimeoutError (an OSError)
    # like a network error and requests the difference again.
    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        call = super()._call
        retry_config = self._client_config.request_retry
        description = f"Telegram {self._client_name} request {_request_name(request)}"
        try:
            return await retry_with_timeout(
                lambda: call(sender, request, ordered, flood_sleep_threshold),
                retry_config,
                description,
            )
        except TimeoutError:
            logger.error(
                "{description} timed out on all {attempts} attempt(s)",
                description=description,
                attempts=retry_config.attempts,
            )
            raise
