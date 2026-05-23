from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any

from services.db_accuracy.models import SourceRow, TableSpec
from services.db_accuracy.partitioned.models import (
    AccuracyMode,
    CacheManifest,
    CachePolicy,
    CacheSide,
    CacheStatus,
    CompareManifest,
    CompareStatus,
    DataFingerprint,
    ExecutionOptions,
    PartitionTask,
    PartitionedAccuracyRequest,
    RunPauseReason,
    RunStatus,
)
from services.db_accuracy.partitioned.runner_service import PartitionedAccuracyService


class FakeDB:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        if sql.startswith("SHOW COLUMNS"):
            return [
                {"Field": "symbol"},
                {"Field": "interval"},
                {"Field": "timestamp"},
                {"Field": "open"},
                {"Field": "close"},
            ]
        if "GROUP BY" in sql:
            return [{"symbol": "BTCUSDT", "interval": "1m"}]
        if "FROM `binance_kline_all_future_raw`" in sql:
            return [
                {
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "timestamp": 1704067200000,
                    "open": "1",
                    "close": "2",
                }
            ]
        return []


class GoodSource:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_rows(
        self,
        spec: TableSpec,
        key: Any,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        self.calls += 1
        return [
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "1", "close": "2"},
            )
        ]

    def fetch_registry_rows(self, spec: TableSpec) -> list[Any]:
        raise AssertionError("registry source should not be used by these tests")


class FailingSource:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_rows(
        self,
        spec: TableSpec,
        key: Any,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        self.calls += 1
        raise RuntimeError("network down")

    def fetch_registry_rows(self, spec: TableSpec) -> list[Any]:
        raise RuntimeError("network down")


def _spec() -> TableSpec:
    return TableSpec(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
    )


def _request(
    tmp_path: Path,
    *,
    source_retries: int = 5,
    cache_policy: CachePolicy | None = None,
    stop_on_source_failure: bool = True,
) -> PartitionedAccuracyRequest:
    return PartitionedAccuracyRequest(
        mode=AccuracyMode.DIRECT,
        tables=("binance_kline_all_future_raw",),
        cache_root=tmp_path,
        symbols=("BTCUSDT",),
        intervals=("1m",),
        start_ms=1704067200000,
        end_ms=1704067259999,
        cache_policy=cache_policy or CachePolicy(use_db_cache=True, use_source_cache=True),
        execution=ExecutionOptions(
            workers=4,
            source_retries=source_retries,
            source_retry_backoff_ms=0,
            stop_on_source_failure=stop_on_source_failure,
        ),
    )


def _task(symbol: str) -> PartitionTask:
    return PartitionTask(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_values={"symbol": symbol, "interval": "1m"},
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        start_ms=1704067200000,
        end_ms=1704067259999,
        partition_label="1704067200000-1704067259999",
        partition_bucket="date=2024-01-01",
        key_fields=("symbol", "interval"),
        interval_field="interval",
    )


def _cache_manifest(task: PartitionTask, side: CacheSide) -> CacheManifest:
    return CacheManifest(
        schema_version=1,
        side=side,
        table=task.table,
        endpoint=task.endpoint,
        market_key=dict(task.key_values),
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        status=CacheStatus.COMPLETE,
        row_count=1,
        fingerprint=DataFingerprint(row_count=1, content_hash=f"{side.value}-{task.label}"),
        schema_fingerprint=task.schema_fingerprint,
        error_type=None,
        error_message=None,
        artifact_path=f"{side.value}/data.parquet",
        created_at_utc="2026-05-22T00:00:00+00:00",
    )


def _compare_manifest(task: PartitionTask) -> CompareManifest:
    return CompareManifest(
        schema_version=1,
        table=task.table,
        endpoint=task.endpoint,
        market_key=dict(task.key_values),
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        status=CompareStatus.PASSED,
        db_fingerprint=DataFingerprint(row_count=1, content_hash=f"db-{task.label}"),
        source_fingerprint=DataFingerprint(row_count=1, content_hash=f"source-{task.label}"),
        db_rows=1,
        source_rows=1,
        differences=0,
        report_path=f"compare/{task.label}/report.txt",
        diff_path=f"compare/{task.label}/diff.json",
        message=None,
        created_at_utc="2026-05-22T00:00:00+00:00",
    )


def test_runner_prepares_data_then_compares_successfully(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_spec()],
    )
    runner = PartitionedAccuracyService(db=FakeDB(), source=GoodSource())

    result = runner.run(_request(tmp_path))

    assert result.status == RunStatus.PASSED
    assert result.passed
    assert result.tasks_total == 1
    assert result.tasks_compared == 1
    assert result.differences == 0
    assert (tmp_path / "runs").exists()


def test_runner_pauses_on_source_failure_and_leaves_no_source_or_compare_cache(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_spec()],
    )
    source = FailingSource()
    runner = PartitionedAccuracyService(db=FakeDB(), source=source)

    result = runner.run(_request(tmp_path, source_retries=2))

    assert source.calls == 2
    assert result.status == RunStatus.PAUSED
    assert result.pause_reason is not None
    assert result.pause_reason.reason == "source_request_failed"
    assert "network down" in result.pause_reason.message
    assert not list((tmp_path / "source").glob("**/data.parquet"))
    assert not list((tmp_path / "source").glob("**/manifest.json"))
    assert not list((tmp_path / "compare").glob("**/manifest.json"))


def test_runner_can_continue_without_pause_after_source_failure(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_spec()],
    )
    runner = PartitionedAccuracyService(db=FakeDB(), source=FailingSource())

    result = runner.run(
        _request(
            tmp_path,
            source_retries=2,
            stop_on_source_failure=False,
        )
    )

    assert result.status == RunStatus.FAILED
    assert result.pause_reason is None
    assert result.tasks_total == 1
    assert result.tasks_compared == 0
    assert not list((tmp_path / "compare").glob("**/manifest.json"))


def test_runner_db_prepare_uses_single_worker_for_shared_db_client(tmp_path: Path) -> None:
    tasks = [_task("BTCUSDT"), _task("ETHUSDT"), _task("SOLUSDT")]
    active = 0
    max_active = 0
    lock = threading.Lock()

    class SlowDBService:
        def ensure_db_frame(
            self,
            task: PartitionTask,
            cache_policy: CachePolicy,
        ) -> tuple[object, CacheManifest]:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return object(), _cache_manifest(task, CacheSide.DB)

    runner = PartitionedAccuracyService(db=FakeDB(), source=GoodSource())

    manifests = runner._prepare_db(tasks, SlowDBService(), _request(tmp_path))

    assert set(manifests) == {task.label for task in tasks}
    assert max_active == 1


def test_runner_starts_db_and_source_prepare_stages_in_parallel(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    source_started = threading.Event()
    db_observed_source_start: list[bool] = []

    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_spec()],
    )

    def prepare_db(
        self: PartitionedAccuracyService,
        tasks: list[PartitionTask],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, CacheManifest]:
        db_observed_source_start.append(source_started.wait(timeout=0.2))
        return {tasks[0].label: _cache_manifest(tasks[0], CacheSide.DB)}

    def prepare_source(
        self: PartitionedAccuracyService,
        tasks: list[PartitionTask],
        *args: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, CacheManifest], RunPauseReason | None]:
        source_started.set()
        return {tasks[0].label: _cache_manifest(tasks[0], CacheSide.SOURCE)}, None

    def compare_all(
        self: PartitionedAccuracyService,
        *,
        tasks: list[PartitionTask],
        **kwargs: Any,
    ) -> list[CompareManifest]:
        return [_compare_manifest(tasks[0])]

    monkeypatch.setattr(PartitionedAccuracyService, "_prepare_db", prepare_db)
    monkeypatch.setattr(PartitionedAccuracyService, "_prepare_source", prepare_source)
    monkeypatch.setattr(PartitionedAccuracyService, "_compare_all", compare_all)

    runner = PartitionedAccuracyService(db=FakeDB(), source=GoodSource())

    result = runner.run(_request(tmp_path))

    assert result.status == RunStatus.PASSED
    assert db_observed_source_start == [True]


def test_runner_reuses_existing_complete_cache_on_second_run(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_spec()],
    )
    db = FakeDB()
    source = GoodSource()
    runner = PartitionedAccuracyService(db=db, source=source)
    runner.run(_request(tmp_path))
    db.queries.clear()
    source.calls = 0

    result = runner.run(_request(tmp_path))

    assert result.status == RunStatus.PASSED
    assert result.tasks_compared == 1
    assert source.calls == 0
    assert not any(
        "FROM `binance_kline_all_future_raw`" in sql
        and "ORDER BY `timestamp` ASC" in sql
        for sql, _ in db.queries
    )


def test_runner_uses_prepared_source_cache_before_compare(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_spec()],
    )
    compared_after_source_cache: list[bool] = []

    original_ensure_compare = (
        "services.db_accuracy.partitioned.compare_data_service"
        ".PartitionedCompareDataService.ensure_compare"
    )
    from services.db_accuracy.partitioned.compare_data_service import (
        PartitionedCompareDataService,
    )

    real_ensure_compare = PartitionedCompareDataService.ensure_compare

    def assert_source_cache_exists_before_compare(
        self: PartitionedCompareDataService,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        compared_after_source_cache.append(
            bool(list((tmp_path / "source").glob("**/manifest.json")))
        )
        return real_ensure_compare(self, *args, **kwargs)

    monkeypatch.setattr(original_ensure_compare, assert_source_cache_exists_before_compare)
    runner = PartitionedAccuracyService(db=FakeDB(), source=GoodSource())

    result = runner.run(_request(tmp_path))

    assert result.status == RunStatus.PASSED
    assert compared_after_source_cache == [True]


def test_runner_does_not_count_historical_compare_when_refreshed_source_fails(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_spec()],
    )
    first_runner = PartitionedAccuracyService(db=FakeDB(), source=GoodSource())
    first_result = first_runner.run(_request(tmp_path))
    assert first_result.status == RunStatus.PASSED

    second_runner = PartitionedAccuracyService(db=FakeDB(), source=FailingSource())
    result = second_runner.run(
        _request(
            tmp_path,
            source_retries=2,
            cache_policy=CachePolicy(use_db_cache=True, use_source_cache=False),
        )
    )

    assert result.status == RunStatus.PAUSED
    assert result.pause_reason is not None
    assert result.pause_reason.reason == "source_request_failed"
    assert result.tasks_total == 1
    assert result.tasks_compared == 0
    assert result.differences == 0
