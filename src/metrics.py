import asyncio
import contextlib
import functools
import gc
import inspect
import os
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import yappi
from influxdb_client_3 import (
    InfluxDBClient3,
    Point,
    WriteOptions,
    WritePrecision,
    write_client_options,
)
from influxdb_client_3.exceptions.exceptions import InfluxDBError

from app_logging import get_logger
from config import (
    MetricRecorderDynamicConfig,
    MetricRecorderStaticConfig,
    RuntimeInstrumentationManagerDynamicConfig,
    RuntimeInstrumentationManagerStaticConfig,
    RuntimeInstrumentationMode,
)
from retry import retry_with_timeout

logger = get_logger(__name__)


@dataclass(frozen=True)
class MetricRecord:
    table_name: str
    tags: dict[str, Any]
    fields: dict[str, Any]
    timestamp_ns: int = field(default_factory=time.time_ns)


class NoopMetricRecorder:
    def __init__(self):
        pass

    async def start(self) -> None:
        pass

    def record(self, _record: MetricRecord) -> None:
        pass

    async def stop(self) -> None:
        pass

    def on_config_update(self, dynamic_config: MetricRecorderDynamicConfig) -> None:
        self._dynamic_config = dynamic_config


class MetricRecorder(NoopMetricRecorder):
    _enabled: bool
    _queue: asyncio.Queue[MetricRecord]
    _influxdb_client: InfluxDBClient3
    _worker_task: asyncio.Task | None
    _heartbeat_task: asyncio.Task | None
    _gc_task: asyncio.Task | None

    def __init__(self, influxdb_client: InfluxDBClient3, static_config: MetricRecorderStaticConfig, dynamic_config: MetricRecorderDynamicConfig):
        self._static_config = static_config
        self._dynamic_config = dynamic_config

        self._enabled = True
        self._queue = asyncio.Queue()
        self._influxdb_client = influxdb_client

        self._worker_task = None
        self._heartbeat_task = None
        self._gc_task = None

    async def start(self) -> None:
        if not self._enabled:
            return
        self._worker_task = asyncio.create_task(self._worker())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._gc_task = asyncio.create_task(self._gc_loop())

    async def _run_loop(self, name: str, func):
        while self._enabled:
            try:
                await func()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(f"Error in {name} loop")
                raise

    async def _gc_loop(self):
        async def do_gc_work():
            await asyncio.sleep(self._dynamic_config.gc_interval_seconds)

            g_counts = gc.get_count()
            g_stats = gc.get_stats()

            for i in range(3):
                self.record(
                    MetricRecord(
                        table_name="gc_stats",
                        fields=dict(
                            allocated_since_last_gc=g_counts[i],
                            collections_since_last_gc=g_stats[i]["collections"],
                            collected_since_last_gc=g_stats[i]["collected"],
                            uncollectable_since_last_gc=g_stats[i]["uncollectable"],
                        ),
                        tags=dict(gc=i),
                    )
                )
            logger.info("Gc metrics sent")

        await self._run_loop("gc", do_gc_work)

    async def _heartbeat_loop(self):
        async def do_heartbeat_work():
            await asyncio.sleep(self._dynamic_config.heartbeat_interval_seconds)
            self.record(
                MetricRecord(
                    table_name="health", fields=dict(status=True), tags=dict()
                )
            )
            logger.info("Heartbeat sent")
        await self._run_loop("heartbeat", do_heartbeat_work)

    def record(self, record: MetricRecord) -> None:
        if not self._enabled:
            return

        self._queue.put_nowait(record)

    async def stop(self) -> None:
        self._enabled = False

        tasks = [task for task in (
            self._heartbeat_task, self._gc_task, self._worker_task
        ) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        loop = asyncio.get_running_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, self._influxdb_client.close),
            self._dynamic_config.write_retry.timeout_seconds)

    def _send_batch(self, batch: list[MetricRecord]) -> None:
        points = []
        for record in batch:
            point = (
                Point(record.table_name)
                .tag("instance_id", self._static_config.telemetry_instance_id)
                .time(record.timestamp_ns, write_precision=WritePrecision.NS)
            )
            for key, value in record.tags.items():
                point = point.tag(key, value)
            for key, value in record.fields.items():
                point = point.field(key, value)
            points.append(point)

        self._influxdb_client.write(record=points)

    async def _flush(self) -> None:
        batch = [self._queue.get_nowait() for _ in range(self._queue.qsize())]
        if not batch:
            return

        loop = asyncio.get_running_loop()
        try:
            await retry_with_timeout(
                lambda: loop.run_in_executor(None, self._send_batch, batch),
                self._dynamic_config.write_retry,
                "InfluxDB write",
                retry_on=(InfluxDBError,),
            )
        except Exception:
            logger.exception(
                "Dropping metrics batch after failed InfluxDB write",
                batch_size=len(batch),
            )

    async def _worker(self):
        while self._enabled:
            try:
                await asyncio.sleep(self._dynamic_config.metric_interval_seconds)
                await self._flush()
            except asyncio.CancelledError:
                break


def _make_metric_recorder(static_config: MetricRecorderStaticConfig,
                          dynamic_config: MetricRecorderDynamicConfig) -> MetricRecorder | NoopMetricRecorder:
    if static_config.influxdb3.endpoint is None:  # type: ignore
        return NoopMetricRecorder()

    try:
        batch_options = WriteOptions(
            batch_size=500,
            flush_interval=10_000,
            max_retries=5,
        )
        influxdb_client = InfluxDBClient3(
            host=static_config.influxdb3.endpoint,  # type: ignore
            token=static_config.influxdb3.token,  # type: ignore
            database=static_config.influxdb3.database_name,  # type: ignore
            write_client_options=write_client_options(
                write_options=batch_options),
        )
        return MetricRecorder(influxdb_client, static_config, dynamic_config)
    except Exception:
        logger.exception(
            "Failed to initialize InfluxDB client with enabled metrics.")
        raise


def init_metric_recorder(static_config: MetricRecorderStaticConfig,
                         dynamic_config: MetricRecorderDynamicConfig):
    global _metric_recorder
    _metric_recorder = _make_metric_recorder(static_config, dynamic_config)


_metric_recorder: MetricRecorder | NoopMetricRecorder = NoopMetricRecorder()


def get_metric_recorder():
    return _metric_recorder


def traceable(func):
    func.__traceable__ = True
    return func


def profileable(func):
    func.__profileable__ = True
    return func


class RuntimeInstrumentationBase:
    pass


@dataclass(frozen=True)
class _InstrumentedMethod:
    cls: type
    method_name: str
    original_func: Callable
    profiled_wrapper: Callable
    stack_dump_wrapper: Callable


class WatchdogAlarmHandler:
    def __init__(self, orig_func: Callable, frame_count_limit: int):
        self._orig_func = orig_func
        self._snapshots: list[list[tuple[str, str, int]]] = []
        self._frame_count_limit = frame_count_limit

    def handle_alarm(self, _signum, frame):
        stack: list[tuple[str, str, int]] = []
        curr_frame = frame
        while curr_frame and len(stack) < self._frame_count_limit:
            code = curr_frame.f_code
            stack.append((code.co_filename, code.co_name, curr_frame.f_lineno))
            curr_frame = curr_frame.f_back

        self._snapshots.append(stack)

    def get_snapshots(self):
        return self._snapshots


class RuntimeInstrumentationManager:
    def __init__(self,
                 static_config: RuntimeInstrumentationManagerStaticConfig,
                 dynamic_config: RuntimeInstrumentationManagerDynamicConfig):
        self._static_config = static_config
        self._dynamic_config = dynamic_config

        self._active_yappi_holders: int = 0
        self._yappi_lock = threading.Lock()

        self._methods: list[_InstrumentedMethod] = self._get_all_methods()
        # FIXME: check that all configurable classes use first dynamic config
        self._apply_instrumentation_mode(dynamic_config)

    def _get_all_methods(self) -> list[_InstrumentedMethod]:
        def get_subclasses(cls):
            subclasses = set(cls.__subclasses__())
            return subclasses.union([s for c in subclasses for s in get_subclasses(c)])

        methods: list[_InstrumentedMethod] = []
        for cls in get_subclasses(RuntimeInstrumentationBase):
            logger.info(f"Found class {cls.__name__}")
            for method_name, original_func in inspect.getmembers(
                cls, predicate=inspect.isfunction
            ):
                function = original_func
                if getattr(original_func, "__traceable__", False):
                    logger.info(f"Wrapping {method_name} with trace session")
                    function = self._create_wrapper(
                        original_func, self._trace_session)
                    # enable tracing right away, its cheap
                    setattr(cls, method_name, function)

                if getattr(original_func, "__profileable__", False):
                    logger.info(f"Wrapping {method_name} with profile session")
                    profiled_wrapper = self._create_wrapper(
                        function, self._profile_session
                    )
                    stack_dump_wrapper = self._create_wrapper(
                        function, self._stack_dump_session
                    )
                    methods.append(
                        _InstrumentedMethod(
                            cls, method_name, function, profiled_wrapper, stack_dump_wrapper
                        )
                    )
        return methods

    def on_config_update(self, dynamic_config: RuntimeInstrumentationManagerDynamicConfig):
        old_dynamic_config = self._dynamic_config
        self._dynamic_config = dynamic_config

        # NOTE: Thread-safe, but can be in inconsistent state if multiple threads are updating at once.
        # Which should be almost impossible to trigger under normal circumstances, so we're fine with it.
        if old_dynamic_config.mode != dynamic_config.mode:
            self._apply_instrumentation_mode(dynamic_config)

    @contextlib.contextmanager
    def _trace_session(self, orig_func: Callable):
        start_time = time.perf_counter_ns()
        try:
            yield
        finally:
            latency_ns = time.perf_counter_ns() - start_time
            get_metric_recorder().record(
                MetricRecord(
                    table_name="function_execution_stats",
                    tags=dict(module=orig_func.__module__,
                              function=orig_func.__name__),
                    fields=dict(latency_ns=latency_ns),
                )
            )

    @contextlib.contextmanager
    def _stack_dump_session(self, orig_func: Callable):
        config = self._dynamic_config
        handler = WatchdogAlarmHandler(
            orig_func, config.stack_dump.frame_count_limit)
        signal.signal(signal.SIGALRM, handler.handle_alarm)
        signal.setitimer(signal.ITIMER_REAL, config.stack_dump.threshold_nanos * 1e-9,
                         config.stack_dump.interval_nanos * 1e-9)

        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0, 0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            snapshots = handler.get_snapshots()
            if len(snapshots) > 0:
                logger.warning(
                    f"Stack dump detected for {orig_func.__name__}", snapshots=snapshots)

    @contextlib.contextmanager
    def _profile_session(self, orig_func: Callable):
        with self._yappi_lock:
            self._active_yappi_holders += 1
            if self._active_yappi_holders == 1:
                yappi.clear_stats()
                yappi.set_clock_type("wall")
                yappi.start(builtins=True)

        start_ns = time.perf_counter_ns()
        try:
            yield
        finally:
            duration_ns = time.perf_counter_ns() - start_ns
            func_stats = None

            with self._yappi_lock:
                self._active_yappi_holders -= 1
                if self._active_yappi_holders == 0:
                    yappi.stop()

                if duration_ns > self._dynamic_config.profiling.threshold_nanos:
                    func_stats = yappi.get_func_stats()

            if func_stats is not None:
                logger.warning("Profiling spike detected")
                duration_us = duration_ns / 1000
                timestamp = int(time.time())
                filename = (
                    f"{orig_func.__name__}_spike_{int(duration_us)}us_{timestamp}.prof"
                )
                filepath = os.path.join(
                    self._static_config.profile_dir, filename)

                func_stats.save(filepath, type="callgrind")
                logger.warning(
                    f"Captured {duration_us:.1f}µs spike. Saved to {filepath}"
                )

    def _create_wrapper(self, orig_func: Callable, context: Any) -> Callable:
        if inspect.iscoroutinefunction(orig_func):

            @functools.wraps(orig_func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with context(orig_func):
                    return await orig_func(*args, **kwargs)

            return async_wrapper
        else:

            @functools.wraps(orig_func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with context(orig_func):
                    return orig_func(*args, **kwargs)

            return sync_wrapper

    def _apply_instrumentation_mode(self, dynamic_config: RuntimeInstrumentationManagerDynamicConfig):
        for method in self._methods:
            new_method = method.original_func
            if dynamic_config.mode == RuntimeInstrumentationMode.PROFILING:
                new_method = method.profiled_wrapper
            elif dynamic_config.mode == RuntimeInstrumentationMode.STACK_DUMP:
                new_method = method.stack_dump_wrapper
            setattr(method.cls, method.method_name, new_method)
            logger.info(
                "Set instrumentation mode to "
                f"{dynamic_config.mode.value} for "
                f"{method.cls.__name__}.{method.method_name}"
            )
