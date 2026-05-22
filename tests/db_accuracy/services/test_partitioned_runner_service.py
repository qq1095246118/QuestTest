from __future__ import annotations

from pathlib import Path
from typing import Any

from services.db_accuracy.models import SourceRow, TableSpec
from services.db_accuracy.partitioned.models import (
    AccuracyMode,
    CachePolicy,
    ExecutionOptions,
    PartitionedAccuracyRequest,
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
            stop_on_source_failure=True,
        ),
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
