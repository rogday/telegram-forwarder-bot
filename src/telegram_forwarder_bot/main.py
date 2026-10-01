import asyncio
import signal

from loguru import logger

from .bot import TelegramForwarderBot


async def main() -> None:
    telegram_forwarder_bot = None
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()

    def on_sigterm() -> None:
        if main_task is not None and not main_task.cancelling():
            main_task.cancel()

    loop.add_signal_handler(signal.SIGTERM, on_sigterm)
    try:
        telegram_forwarder_bot = TelegramForwarderBot()
        await telegram_forwarder_bot.start()
    except asyncio.CancelledError:
        logger.info("Shutdown requested.")
    except Exception:
        logger.exception(
            "Fatal error occurred in application core context.")
        raise
    finally:
        try:
            if telegram_forwarder_bot is not None:
                await telegram_forwarder_bot.stop()
        except Exception:
            logger.exception("Failed to shut down the bot.")
        finally:
            loop.remove_signal_handler(signal.SIGTERM)
