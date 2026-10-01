import logging
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, PositiveFloat, PositiveInt, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, YamlConfigSettingsSource

STATIC_CONFIG_NAME = ".static.yml"
DYNAMIC_CONFIG_NAME = ".dynamic.yml"
SUPPORTED_LOG_LEVELS = frozenset(
    {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
)

logger = logging.getLogger(__name__)


class ClientStaticConfig(BaseModel):
    api_id: int
    api_hash: str
    bot_token: str
    admin_id: int
    admin_phone: str

    session_dir: Path = Path("./sessions")


class ConfigWatcherStaticConfig(BaseModel):
    debounce_interval_ms: PositiveInt = 100


class MonitorStaticConfig(BaseModel):
    match_queue_size: PositiveInt = 1000


class StorageManagerStaticConfig(BaseModel):
    database_path: Path = Path("./storage.db")


class InfluxDB3StaticConfig(BaseModel):
    token: str | None = None
    endpoint: str | None = None
    database_name: str | None = None


class MetricRecorderStaticConfig(BaseModel):
    influxdb3: InfluxDB3StaticConfig = Field(
        default_factory=InfluxDB3StaticConfig
    )
    telemetry_instance_id: str = "instance-0"


class RuntimeInstrumentationManagerStaticConfig(BaseModel):
    profile_dir: Path = Path("./profiles")


class StaticConfig(BaseSettings):
    model_config = SettingsConfigDict(
        yaml_file=STATIC_CONFIG_NAME, yaml_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    client: ClientStaticConfig
    config_watcher: ConfigWatcherStaticConfig = Field(
        default_factory=ConfigWatcherStaticConfig
    )
    monitor: MonitorStaticConfig = Field(default_factory=MonitorStaticConfig)
    storage_manager: StorageManagerStaticConfig = Field(
        default_factory=StorageManagerStaticConfig
    )
    metric_recorder: MetricRecorderStaticConfig = Field(
        default_factory=MetricRecorderStaticConfig
    )
    runtime_instrumentation_manager: RuntimeInstrumentationManagerStaticConfig = Field(
        default_factory=RuntimeInstrumentationManagerStaticConfig
    )

    @classmethod
    def settings_customise_sources(cls, settings_cls, *args, **kwargs):
        return (YamlConfigSettingsSource(settings_cls),)


class RuntimeInstrumentationMode(StrEnum):
    DISABLED = "disabled"
    PROFILING = "profiling"
    STACK_DUMP = "stack_dump"


class ProfilingDynamicConfig(BaseModel):
    threshold_nanos: PositiveInt = 200_000


class StackDumpDynamicConfig(BaseModel):
    frame_count_limit: PositiveInt = 40
    threshold_nanos: PositiveInt = 150_000
    interval_nanos: PositiveInt = 50_000


class RuntimeInstrumentationManagerDynamicConfig(BaseModel):
    mode: RuntimeInstrumentationMode = RuntimeInstrumentationMode.DISABLED
    profiling: ProfilingDynamicConfig = Field(
        default_factory=ProfilingDynamicConfig
    )
    stack_dump: StackDumpDynamicConfig = Field(
        default_factory=StackDumpDynamicConfig
    )


class RetryDynamicConfig(BaseModel):
    timeout_seconds: PositiveFloat = 90
    attempts: PositiveInt = 3
    min_backoff_seconds: PositiveFloat = 1
    max_backoff_seconds: PositiveFloat = 30


class ClientDynamicConfig(BaseModel):
    # NOTE: Keep the timeout above Telethon's 60s flood_sleep_threshold,
    # otherwise flood-wait sleeps are cut short and retried.
    request_retry: RetryDynamicConfig = Field(
        default_factory=RetryDynamicConfig)


class NotifierDynamicConfig(BaseModel):
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(
                f"timezone must be a valid IANA timezone, got {value!r}"
            ) from error
        return value


class StorageManagerDynamicConfig(BaseModel):
    dedup_enabled: bool = True
    dedup_cache_size: PositiveInt = 5_000


class MetricRecorderDynamicConfig(BaseModel):
    heartbeat_interval_seconds: PositiveInt = 30
    gc_interval_seconds: PositiveInt = 10
    metric_interval_seconds: PositiveInt = 20
    close_timeout_seconds: PositiveFloat = 90


class MonitorDynamicConfig(BaseModel):
    ping_interval_seconds: PositiveInt = 5
    # Health turns red when no message from any chat was handled for this long
    max_silence_seconds: PositiveInt = 12 * 60 * 60
    status_log_interval_seconds: PositiveInt = 60 * 60


class LogManagerDynamicConfig(BaseModel):
    log_file_path: Path = Path("logs/app.jsonl")
    max_bytes: PositiveInt = 10 * 2**20  # 10 MB
    max_files: PositiveInt = 3
    service_name: str = "telegram-forwarder-bot"
    log_level: str = "INFO"
    thirdparty_log_level: str = "WARNING"

    @field_validator("log_level", "thirdparty_log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized_value = value.upper()
        if normalized_value not in SUPPORTED_LOG_LEVELS:
            supported = ", ".join(sorted(SUPPORTED_LOG_LEVELS))
            raise ValueError(
                f"log level must be one of {supported}, got {value!r}"
            )
        return normalized_value


class DynamicConfig(BaseSettings):
    model_config = SettingsConfigDict(
        yaml_file=DYNAMIC_CONFIG_NAME,
        yaml_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    client: ClientDynamicConfig = Field(default_factory=ClientDynamicConfig)
    notifier: NotifierDynamicConfig = Field(
        default_factory=NotifierDynamicConfig)
    storage_manager: StorageManagerDynamicConfig = Field(
        default_factory=StorageManagerDynamicConfig
    )
    metric_recorder: MetricRecorderDynamicConfig = Field(
        default_factory=MetricRecorderDynamicConfig
    )
    monitor: MonitorDynamicConfig = Field(default_factory=MonitorDynamicConfig)
    log_manager: LogManagerDynamicConfig = Field(
        default_factory=LogManagerDynamicConfig
    )
    runtime_instrumentation_manager: RuntimeInstrumentationManagerDynamicConfig = Field(
        default_factory=RuntimeInstrumentationManagerDynamicConfig
    )

    @classmethod
    def settings_customise_sources(cls, settings_cls, *args, **kwargs):
        return (YamlConfigSettingsSource(settings_cls),)


def load_dynamic_config() -> DynamicConfig:
    """Load dynamic settings, falling back to all defaults on any file error."""
    default_config = DynamicConfig.model_construct()
    try:
        return DynamicConfig()
    except Exception:
        try:
            logger.exception(
                "Invalid dynamic configuration in %s; using defaults",
                DYNAMIC_CONFIG_NAME,
            )
        except Exception:
            # Configuration fallback must not depend on telemetry working.
            pass
        return default_config
