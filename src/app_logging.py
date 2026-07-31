import inspect
import logging
import sys

from loguru import logger as _logger

from config import LogManagerDynamicConfig


class InterceptHandler(logging.Handler):
    def _emit_impl(self, record: logging.LogRecord) -> None:
        level: str | int
        try:
            level = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        _logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._emit_impl(record)
        except Exception:
            self.handleError(record)


logging_intercept_handler: InterceptHandler = InterceptHandler()
logging.basicConfig(
    handlers=[logging_intercept_handler], level=logging.DEBUG, force=True
)


class LogManager:
    def __init__(self, dynamic_config: LogManagerDynamicConfig):
        self.on_config_update(dynamic_config)

    @staticmethod
    def _apply_config(config: LogManagerDynamicConfig) -> None:
        LogManager._apply_handlers(config)

        new_thirdparty_level_no = _logger.level(
            config.thirdparty_log_level.upper()).no
        logging.getLogger().setLevel(new_thirdparty_level_no)

    @staticmethod
    def _apply_handlers(config: LogManagerDynamicConfig) -> None:
        _logger.remove()

        level_str = config.log_level.upper()
        config.log_file_path.parent.mkdir(parents=True, exist_ok=True)

        _logger.add(
            str(config.log_file_path),
            rotation=f"{config.max_bytes / 2**20:.0f} MB",
            retention=config.max_files,  # Keep only max_files rotated files
            compression=".gz",
            enqueue=True,
            serialize=True,
            level=level_str,
            format="",  # empty because serialize=True handles formatting
        )

        _logger.add(
            sys.stdout,
            level=level_str,
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | {name}:{function}:{line} - <level>{message}</level>",
            colorize=True,
        )

        _logger.configure(extra=dict(service=config.service_name))

    def on_config_update(self, dynamic_config: LogManagerDynamicConfig) -> None:
        self._dynamic_config = dynamic_config
        self._apply_config(dynamic_config)

    @staticmethod
    async def stop() -> None:
        await _logger.complete()
        _logger.remove()


def get_logger(_name: str | None = None):
    return _logger
