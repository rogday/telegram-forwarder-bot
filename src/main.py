import asyncio

from app_logging import get_logger
from telegram_forwarder_bot import TelegramForwarderBot

logger = get_logger(__name__)


async def main() -> None:
    try:
        telegram_forwarder_bot = TelegramForwarderBot()
        await telegram_forwarder_bot.start()
    except Exception:
        logger.exception(
            "Fatal error occurred in application core context.")
        raise
    finally:
        logger.info("Beginning teardown.")

    try:
        await telegram_forwarder_bot.stop()
    except Exception:
        print("Failed to shut down the bot.")

if __name__ == "__main__":
    asyncio.run(main())
