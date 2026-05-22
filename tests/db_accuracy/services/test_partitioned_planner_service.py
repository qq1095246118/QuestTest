from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.db_accuracy.models import TableSpec
from services.db_accuracy.partitioned.models import AccuracyMode, PartitionedAccuracyRequest
from services.db_accuracy.partitioned.planner_service import PartitionPlannerService


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
                {"Field": "status"},
            ]
        if "MIN(" in sql and "MAX(" in sql:
            return [
                {
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "min_time_ms": 1704067200000,
                    "max_time_ms": 1704239999999,
                }
            ]
        if "GROUP BY" in sql:
            return [{"symbol": "BTCUSDT", "interval": "1m"}]
        return []


class MultiKeyFakeDB(FakeDB):
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
            if "BTCUSDT" in params:
                return [{"symbol": "BTCUSDT", "interval": "1m"}]
            if "ETHUSDT" in params:
                return [{"symbol": "ETHUSDT", "interval": "1m"}]
            return [{"symbol": "SOLUSDT", "interval": "1m"}]
        return []


def _kline_spec() -> TableSpec:
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


def _registry_spec() -> TableSpec:
    return TableSpec(
        table="binance_futures_symbols",
        kind="registry",
        endpoint="usdm_exchange_info",
        key_fields=("symbol",),
        time_fields=(),
        interval_field=None,
        compare_fields=("symbol", "status"),
        request_limit=1000,
    )


def test_direct_without_range_discovers_db_ranges_and_splits_partitions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    planner = PartitionPlannerService(FakeDB())

    tasks = planner.plan(
        PartitionedAccuracyRequest(
            mode=AccuracyMode.DIRECT,
            tables=("binance_kline_all_future_raw",),
            cache_root=tmp_path,
            partition_days=1,
        )
    )

    assert [(task.start_ms, task.end_ms, task.partition_bucket) for task in tasks] == [
        (1704067200000, 1704153599999, "date=2024-01-01"),
        (1704153600000, 1704239999999, "date=2024-01-02"),
    ]
    assert tasks[0].key_values == {"symbol": "BTCUSDT", "interval": "1m"}


def test_direct_with_explicit_range_and_filters_uses_discovery_filters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db = FakeDB()
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    planner = PartitionPlannerService(db)

    tasks = planner.plan(
        PartitionedAccuracyRequest(
            mode=AccuracyMode.DIRECT,
            tables=("binance_kline_all_future_raw",),
            cache_root=tmp_path,
            symbols=("BTCUSDT",),
            intervals=("1m",),
            start_ms=1704110400000,
            end_ms=1704113999999,
        )
    )

    assert len(tasks) == 1
    assert tasks[0].start_ms == 1704110400000
    assert tasks[0].end_ms == 1704113999999
    assert any("GROUP BY `symbol`, `interval`" in sql for sql, _ in db.queries)


def test_explicit_range_filters_multi_value_discovery_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    planner = PartitionPlannerService(MultiKeyFakeDB())

    tasks = planner.plan(
        PartitionedAccuracyRequest(
            mode=AccuracyMode.DIRECT,
            tables=("binance_kline_all_future_raw",),
            cache_root=tmp_path,
            symbols=("BTCUSDT", "ETHUSDT"),
            intervals=("1m",),
            start_ms=1704110400000,
            end_ms=1704113999999,
            max_shards=1,
        )
    )

    assert [task.key_values["symbol"] for task in tasks] == ["BTCUSDT"]
    group_by_params = [params for sql, params in planner.db.queries if "GROUP BY" in sql]
    assert any("BTCUSDT" in params for params in group_by_params)
    assert any("ETHUSDT" in params for params in group_by_params)
    assert all("SOLUSDT" not in params for params in group_by_params)


def test_cached_mode_requires_single_table_and_explicit_range(tmp_path: Path) -> None:
    planner = PartitionPlannerService(FakeDB())

    with pytest.raises(ValueError, match="cached mode requires exactly one table"):
        planner.plan(
            PartitionedAccuracyRequest(
                mode=AccuracyMode.CACHED,
                tables=(),
                cache_root=tmp_path,
                start_ms=1704067200000,
                end_ms=1704153599999,
            )
        )

    with pytest.raises(ValueError, match="cached mode requires start_ms and end_ms"):
        planner.plan(
            PartitionedAccuracyRequest(
                mode=AccuracyMode.CACHED,
                tables=("binance_kline_all_future_raw",),
                cache_root=tmp_path,
            )
        )


def test_registry_table_becomes_single_registry_partition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_registry_spec()],
    )
    planner = PartitionPlannerService(FakeDB())

    tasks = planner.plan(
        PartitionedAccuracyRequest(
            mode=AccuracyMode.DIRECT,
            tables=("binance_futures_symbols",),
            cache_root=tmp_path,
        )
    )

    assert len(tasks) == 1
    assert tasks[0].is_registry is True
    assert tasks[0].start_ms is None
    assert tasks[0].end_ms is None
    assert tasks[0].partition_bucket == "registry"
    assert tasks[0].key_fields == ("symbol",)
    assert tasks[0].compare_fields == ("symbol", "status")
