import asyncio

from telethon import events

from .admin_commands import AdminCommands
from .app_logging import LogManager, get_logger
from .config import (
    DynamicConfig,
    StaticConfig,
    load_dynamic_config,
)
from .config_watcher import ConfigWatcher
from .metrics import (
    RuntimeInstrumentationManager,
    get_metric_recorder,
    init_metric_recorder,
)
from .monitoring import ChatResolver, Monitor
from .notification import Notifier
from .storage import StorageManager
from .telegram_client import ResilientTelegramClient

logger = get_logger(__name__)


class TelegramForwarderBot:
    def __init__(self) -> None:
        self._static_config: StaticConfig = StaticConfig()  # type: ignore
        self._dynamic_config: DynamicConfig = load_dynamic_config()

        self._log_manager: LogManager = LogManager(
            self._dynamic_config.log_manager)

        self._static_config.client.session_dir.mkdir(
            parents=True, exist_ok=True)
        self._static_config.runtime_instrumentation_manager.profile_dir.mkdir(
            parents=True, exist_ok=True)

        self._config_watcher: ConfigWatcher = ConfigWatcher(
            self._static_config.config_watcher, [self.on_config_update])

        init_metric_recorder(self._static_config.metric_recorder,
                             self._dynamic_config.metric_recorder)

        self._runtime_instrumentation_manager: RuntimeInstrumentationManager = RuntimeInstrumentationManager(
            static_config=self._static_config.runtime_instrumentation_manager,
            dynamic_config=self._dynamic_config.runtime_instrumentation_manager)

        self._user_client: ResilientTelegramClient = ResilientTelegramClient(
            self._static_config.client.session_dir / "user",
            api_id=self._static_config.client.api_id,
            api_hash=self._static_config.client.api_hash,
            client_name="user",
            dynamic_config=self._dynamic_config.client,
        )
        self._bot_client: ResilientTelegramClient = ResilientTelegramClient(
            self._static_config.client.session_dir / "bot",
            api_id=self._static_config.client.api_id,
            api_hash=self._static_config.client.api_hash,
            client_name="bot",
            dynamic_config=self._dynamic_config.client,
        )

        self._storage_manager: StorageManager = StorageManager(
            self._static_config.storage_manager,
            self._dynamic_config.storage_manager)

        self._chat_resolver: ChatResolver = ChatResolver(self._user_client)

        self._monitor: Monitor = Monitor(
            self._user_client, self._storage_manager.subscriptions,
            self._storage_manager.deduplicator, self._chat_resolver,
            self._static_config.monitor,
            self._dynamic_config.monitor)
        get_metric_recorder().set_health_check(self._monitor.health)

        self._admin_commands: AdminCommands = AdminCommands(
            self._storage_manager,
            self._chat_resolver,
            self._static_config.client.admin_id)

        self._notifier: Notifier = Notifier(
            self._bot_client,
            self._monitor.get_match_queue(),
            self._static_config.client.admin_id,
            self._dynamic_config.notifier)

        # Registered before the clients connect, so no update arrives unhandled
        self._user_client.add_event_handler(
            self._monitor.handle_new_message, events.NewMessage())
        self._bot_client.add_event_handler(
            self._admin_commands.handle, events.NewMessage())

    def on_config_update(self, config: DynamicConfig) -> None:
        get_metric_recorder().on_config_update(config.metric_recorder)
        self._user_client.on_config_update(config.client)
        self._bot_client.on_config_update(config.client)
        self._storage_manager.on_config_update(config.storage_manager)
        self._monitor.on_config_update(config.monitor)
        self._notifier.on_config_update(config.notifier)
        self._log_manager.on_config_update(config.log_manager)
        self._runtime_instrumentation_manager.on_config_update(
            config.runtime_instrumentation_manager)

    async def _populate_chat_cache(self) -> None:
        async def resolve_chat(username: str) -> None:
            try:
                await self._chat_resolver.resolve_cached(username)
            except Exception:
                logger.exception(
                    "Skipping chat cache population after resolution error",
                    identifier=username,
                )

        await asyncio.gather(
            *(
                # FIXME: should be list[str] instead, and add support for list of chats to resolver as well.
                resolve_chat(chat.username)
                for chat in self._storage_manager.subscriptions.list_chats()
            )
        )

    async def start(self) -> None:
        await get_metric_recorder().start()

        self._config_watcher.start()

        await self._user_client.start(  # type: ignore
            phone=self._static_config.client.admin_phone)

        await self._bot_client.start(  # type: ignore
            bot_token=self._static_config.client.bot_token)

        self._monitor.start()

        logger.info("Clients and monitor started")

        await self._populate_chat_cache()

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._bot_client.run_until_disconnected())  # type: ignore
            tasks.create_task(self._user_client.run_until_disconnected())  # type: ignore
            tasks.create_task(self._notifier.run())

    async def stop(self) -> None:
        logger.info("Beginning teardown.")

        try:
            self._config_watcher.stop()
        except Exception:
            logger.exception("Failed to shut down config watcher.")

        for name, stop in (
            ("monitor", self._monitor.stop),
            ("user client", self._user_client.disconnect),
            ("bot client", self._bot_client.disconnect),
            ("metric recorder", get_metric_recorder().stop),
        ):
            try:
                await stop()
            except Exception:
                logger.exception(f"Failed to shut down {name}.")

        # Saves the dedup hashes seen since the last admin command. Both clients
        # are disconnected, so no handler can change storage anymore.
        try:
            self._storage_manager.flush()
        except Exception:
            logger.exception("Failed to save storage.")

        logger.info(
            "Teardown complete. Flushing logs and terminating.")
        await self._log_manager.stop()
