from pathlib import Path

import pytest
from pydantic import ValidationError

from config import (
    ConfigWatcherStaticConfig,
    DynamicConfig,
    LogManagerDynamicConfig,
    MetricRecorderDynamicConfig,
    MonitorDynamicConfig,
    MonitorStaticConfig,
    NotifierDynamicConfig,
    ProfilingDynamicConfig,
    RetryDynamicConfig,
    RuntimeInstrumentationManagerDynamicConfig,
    RuntimeInstrumentationMode,
    StackDumpDynamicConfig,
    StorageManagerDynamicConfig,
    load_dynamic_config,
)
from config_watcher import _load_dynamic_config


@pytest.mark.parametrize(
    ("config_class", "field_name"),
    [
        (MonitorStaticConfig, "match_queue_size"),
        (ConfigWatcherStaticConfig, "debounce_interval_ms"),
        (ProfilingDynamicConfig, "threshold_nanos"),
        (StackDumpDynamicConfig, "frame_count_limit"),
        (StackDumpDynamicConfig, "threshold_nanos"),
        (StackDumpDynamicConfig, "interval_nanos"),
        (StorageManagerDynamicConfig, "dedup_cache_size"),
        (MetricRecorderDynamicConfig, "heartbeat_interval_seconds"),
        (MetricRecorderDynamicConfig, "gc_interval_seconds"),
        (MetricRecorderDynamicConfig, "metric_interval_seconds"),
        (MonitorDynamicConfig, "ping_interval_seconds"),
        (RetryDynamicConfig, "attempts"),
        (LogManagerDynamicConfig, "max_bytes"),
        (LogManagerDynamicConfig, "max_files"),
    ],
)
def test_bounded_integer_fields_accept_one_and_reject_non_positive_values(
    config_class: type,
    field_name: str,
) -> None:
    assert getattr(config_class(**{field_name: 1}), field_name) == 1

    for invalid_value in (0, -1):
        with pytest.raises(ValidationError):
            config_class(**{field_name: invalid_value})


@pytest.mark.parametrize("timezone", ["UTC", "America/New_York"])
def test_notifier_accepts_iana_timezones(timezone: str) -> None:
    assert NotifierDynamicConfig(timezone=timezone).timezone == timezone


def test_notifier_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        NotifierDynamicConfig(timezone="Nowhere/Imaginary")


@pytest.mark.parametrize(
    "field_name", ["log_level", "thirdparty_log_level"]
)
def test_log_levels_are_validated_and_normalized(field_name: str) -> None:
    config = LogManagerDynamicConfig.model_validate({field_name: "debug"})
    assert getattr(config, field_name) == "DEBUG"

    with pytest.raises(ValidationError, match="log level must be one of"):
        LogManagerDynamicConfig.model_validate({field_name: "verbose"})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("disabled", RuntimeInstrumentationMode.DISABLED),
        ("profiling", RuntimeInstrumentationMode.PROFILING),
        ("stack_dump", RuntimeInstrumentationMode.STACK_DUMP),
    ],
)
def test_instrumentation_has_one_mode(
    value: str, expected: RuntimeInstrumentationMode
) -> None:
    config = RuntimeInstrumentationManagerDynamicConfig.model_validate({
                                                                       "mode": value})
    assert config.mode is expected


def test_instrumentation_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        RuntimeInstrumentationManagerDynamicConfig.model_validate({
                                                                  "mode": "both"})


def test_invalid_dynamic_file_loads_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".dynamic.yml").write_text(
        "monitor:\n  ping_interval_seconds: 0\n",
        encoding="utf-8",
    )

    assert load_dynamic_config() == DynamicConfig.model_construct()


def test_invalid_dynamic_reload_preserves_current_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".dynamic.yml").write_text(
        "notifier:\n  timezone: Not/A-Timezone\n",
        encoding="utf-8",
    )
    received_configs: list[DynamicConfig] = []

    with pytest.raises(ValidationError):
        _load_dynamic_config([received_configs.append])

    assert received_configs == []


def test_dynamic_fallback_survives_logging_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".dynamic.yml").write_text(
        "monitor:\n  ping_interval_seconds: 0\n",
        encoding="utf-8",
    )

    def fail_to_log(*args: object, **kwargs: object) -> None:
        raise RuntimeError("logging unavailable")

    monkeypatch.setattr("config.logger.exception", fail_to_log)

    assert load_dynamic_config() == DynamicConfig.model_construct()
