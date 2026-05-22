from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from services.db_accuracy.partitioned.cache_store_service import (
    PartitionedCacheStoreService,
    fingerprint_frame,
)
from services.db_accuracy.partitioned.db_data_service import PartitionedDBDataService
from services.db_accuracy.partitioned.models import (
    CacheManifest,
    CachePolicy,
    CacheSide,
    CacheStatus,
    PartitionTask,
)


class FakeDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.raise_on_query = False

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append((sql, params))
        if self.raise_on_query:
            raise RuntimeError("DB unavailable")
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
        if "FROM `binance_futures_symbols`" in sql:
            return [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                }
            ]
        return []


def _task(
    start_ms: int = 1704067200000,
    end_ms: int = 1704153599999,
) -> PartitionTask:
    return PartitionTask(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_values={"symbol": "BTCUSDT", "interval": "1m"},
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        start_ms=start_ms,
        end_ms=end_ms,
        partition_label=f"{start_ms}-{end_ms}",
        partition_bucket="date=2024-01-01",
        key_fields=("symbol", "interval"),
    )


def _registry_task() -> PartitionTask:
    return PartitionTask(
        table="binance_futures_symbols",
        kind="registry",
        endpoint="exchange_info",
        key_values={},
        time_field=None,
        source_time_field=None,
        compare_fields=("status",),
        request_limit=1000,
        start_ms=None,
        end_ms=None,
        partition_label="registry",
        partition_bucket="registry",
        is_registry=True,
        key_fields=("symbol",),
    )


def _manifest(task: PartitionTask, frame: pl.DataFrame) -> CacheManifest:
    status = CacheStatus.EMPTY if frame.is_empty() else CacheStatus.COMPLETE
    return CacheManifest(
        schema_version=1,
        side=CacheSide.DB,
        table=task.table,
        endpoint=task.endpoint,
        market_key=task.key_values,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        status=status,
        row_count=frame.height,
        fingerprint=None if status == CacheStatus.EMPTY else fingerprint_frame(frame),
        schema_fingerprint=task.schema_fingerprint,
        error_type=None,
        error_message=None,
        artifact_path=None if status == CacheStatus.EMPTY else "db/data.parquet",
        created_at_utc="2026-05-22T00:00:00+00:00",
    )


def test_missing_partition_queries_db_normalizes_rows_and_writes_complete_cache(
    tmp_path: Path,
) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedDBDataService(db, store)
    task = _task()

    frame, manifest = service.ensure_db_frame(task, CachePolicy(use_db_cache=True))

    assert len(db.calls) == 1
    assert frame.to_dict(as_series=False) == {
        "symbol": ["BTCUSDT"],
        "interval": ["1m"],
        "timestamp": ["1704067200000"],
        "timestamp__compare": ["1704067200000"],
        "open": ["1"],
        "close": ["2"],
    }
    assert manifest.status == CacheStatus.COMPLETE
    assert manifest.row_count == 1
    assert manifest.schema_fingerprint == task.schema_fingerprint
    assert manifest.fingerprint == fingerprint_frame(frame)
    assert manifest.artifact_path is not None

    paths = store.data_paths(CacheSide.DB, task)
    assert paths.data_path.exists()
    assert store.read_data_manifest(paths) == manifest


def test_use_db_cache_reuses_existing_cache_without_querying_db(tmp_path: Path) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "timestamp__compare": ["1704067200000"],
            "open": ["10"],
            "close": ["20"],
        }
    )
    store.write_data_frame(
        store.data_paths(CacheSide.DB, task),
        frame,
        _manifest(task, frame),
    )
    service = PartitionedDBDataService(db, store)

    cached_frame, manifest = service.ensure_db_frame(task, CachePolicy(use_db_cache=True))

    assert db.calls == []
    assert cached_frame.equals(frame)
    assert manifest.fingerprint == fingerprint_frame(cached_frame)


def test_use_db_cache_false_ignores_cache_and_queries_db_again(tmp_path: Path) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    cached_frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "timestamp__compare": ["1704067200000"],
            "open": ["10"],
            "close": ["20"],
        }
    )
    store.write_data_frame(
        store.data_paths(CacheSide.DB, task),
        cached_frame,
        _manifest(task, cached_frame),
    )
    service = PartitionedDBDataService(db, store)

    frame, manifest = service.ensure_db_frame(task, CachePolicy(use_db_cache=False))

    assert len(db.calls) == 1
    assert frame.to_dict(as_series=False)["open"] == ["1"]
    assert manifest.fingerprint == fingerprint_frame(frame)
    assert pl.read_parquet(store.data_paths(CacheSide.DB, task).data_path).equals(frame)


def test_larger_cache_satisfies_smaller_range_and_manifest_matches_filtered_frame(
    tmp_path: Path,
) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    large_task = _task(start_ms=1704067200000, end_ms=1704153599999)
    small_task = _task(start_ms=1704070800000, end_ms=1704074399999)
    large_frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT", "BTCUSDT", "BTCUSDT"],
            "interval": ["1m", "1m", "1m"],
            "timestamp": ["1704067200000", "1704070800000", "1704074400000"],
            "timestamp__compare": ["1704067200000", "1704070800000", "1704074400000"],
            "open": ["1", "2", "3"],
            "close": ["1.1", "2.2", "3.3"],
        }
    )
    store.write_data_frame(
        store.data_paths(CacheSide.DB, large_task),
        large_frame,
        _manifest(large_task, large_frame),
    )
    large_paths = store.data_paths(CacheSide.DB, large_task)
    small_paths = store.data_paths(CacheSide.DB, small_task)
    assert large_paths.data_path != small_paths.data_path
    service = PartitionedDBDataService(db, store)

    frame, manifest = service.ensure_db_frame(small_task, CachePolicy(use_db_cache=True))

    assert db.calls == []
    assert frame.to_dict(as_series=False)["timestamp"] == ["1704070800000"]
    assert manifest.start_ms == small_task.start_ms
    assert manifest.end_ms == small_task.end_ms
    assert manifest.row_count == 1
    assert manifest.fingerprint == fingerprint_frame(frame)
    assert manifest.schema_fingerprint == small_task.schema_fingerprint

    assert small_paths.data_path.exists()
    assert manifest.artifact_path == store.relative_to_root(small_paths.data_path)
    assert pl.read_parquet(small_paths.data_path).to_dict(as_series=False)["timestamp"] == [
        "1704070800000"
    ]
    assert pl.read_parquet(large_paths.data_path).to_dict(as_series=False)["timestamp"] == [
        "1704067200000",
        "1704070800000",
        "1704074400000",
    ]


def test_use_db_cache_false_preserves_existing_cache_when_fetch_fails(tmp_path: Path) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    cached_frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "timestamp__compare": ["1704067200000"],
            "open": ["10"],
            "close": ["20"],
        }
    )
    exact_paths = store.data_paths(CacheSide.DB, task)
    store.write_data_frame(exact_paths, cached_frame, _manifest(task, cached_frame))
    db.raise_on_query = True
    service = PartitionedDBDataService(db, store)

    with pytest.raises(RuntimeError, match="DB unavailable"):
        service.ensure_db_frame(task, CachePolicy(use_db_cache=False))

    assert exact_paths.data_path.exists()
    assert exact_paths.manifest_path.exists()
    hit = store.find_covering_data_cache(CacheSide.DB, task)
    assert hit is not None
    assert store.read_data_frame(hit.paths, task, task.time_field).equals(cached_frame)


def test_registry_task_queries_registry_rows_and_writes_complete_manifest(
    tmp_path: Path,
) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedDBDataService(db, store)
    task = _registry_task()

    frame, manifest = service.ensure_db_frame(task, CachePolicy(use_db_cache=True))

    sql, params = db.calls[0]
    assert "SELECT `symbol`, `status`" in sql
    assert "FROM `binance_futures_symbols`" in sql
    assert params == ()
    assert frame.to_dict(as_series=False) == {
        "symbol": ["BTCUSDT"],
        "status": ["TRADING"],
    }
    assert manifest.status == CacheStatus.COMPLETE
    assert manifest.row_count == 1
    assert manifest.fingerprint == fingerprint_frame(frame)
