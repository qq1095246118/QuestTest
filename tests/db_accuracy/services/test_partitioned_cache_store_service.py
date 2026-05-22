from __future__ import annotations

from pathlib import Path

import polars as pl

from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService
from services.db_accuracy.partitioned.models import (
    CacheManifest,
    CacheSide,
    CacheStatus,
    DataFingerprint,
    PartitionTask,
)


def _task(start_ms: int = 1704067200000, end_ms: int = 1704153599999) -> PartitionTask:
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
        is_registry=False,
    )


def _manifest(start_ms: int, end_ms: int, row_count: int = 1) -> CacheManifest:
    return CacheManifest(
        schema_version=1,
        side=CacheSide.DB,
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        market_key={"symbol": "BTCUSDT", "interval": "1m"},
        start_ms=start_ms,
        end_ms=end_ms,
        status=CacheStatus.COMPLETE,
        row_count=row_count,
        fingerprint=DataFingerprint(row_count=row_count, content_hash="abc"),
        error_type=None,
        error_message=None,
        artifact_path="db/table=binance_kline_all_future_raw/symbol=BTCUSDT/interval=1m/date=2024-01-01/data.parquet",
        created_at_utc="2026-05-22T00:00:00+00:00",
    )


def test_store_builds_stable_db_source_and_compare_paths(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()

    db_paths = store.data_paths(CacheSide.DB, task)
    source_paths = store.data_paths(CacheSide.SOURCE, task)
    compare_paths = store.compare_paths(task)

    assert db_paths.data_path == (
        tmp_path
        / "db"
        / "table=binance_kline_all_future_raw"
        / "symbol=BTCUSDT"
        / "interval=1m"
        / "date=2024-01-01"
        / "data.parquet"
    )
    assert source_paths.data_path == (
        tmp_path
        / "source"
        / "table=binance_kline_all_future_raw"
        / "symbol=BTCUSDT"
        / "interval=1m"
        / "date=2024-01-01"
        / "data.parquet"
    )
    assert compare_paths.report_path.name == "report.txt"
    assert compare_paths.diff_path.name == "diff.json"
    assert compare_paths.manifest_path.name == "manifest.json"


def test_store_reuses_cache_that_covers_requested_range_and_filters_rows(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    large_task = _task(start_ms=1704067200000, end_ms=1704153599999)
    small_task = _task(start_ms=1704070800000, end_ms=1704074399999)
    paths = store.data_paths(CacheSide.DB, large_task)
    frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT", "BTCUSDT", "BTCUSDT"],
            "interval": ["1m", "1m", "1m"],
            "timestamp": ["1704067200000", "1704070800000", "1704074400000"],
            "timestamp__compare": ["1704067200000", "1704070800000", "1704074400000"],
            "open": ["1", "2", "3"],
            "close": ["1.1", "2.2", "3.3"],
        }
    )

    store.write_data_frame(paths, frame, _manifest(large_task.start_ms, large_task.end_ms, row_count=3))

    hit = store.find_covering_data_cache(CacheSide.DB, small_task)

    assert hit is not None
    filtered = store.read_data_frame(hit.paths, task=small_task, time_field="timestamp")
    assert filtered.to_dict(as_series=False)["timestamp"] == ["1704070800000"]
    assert filtered.to_dict(as_series=False)["open"] == ["2"]


def test_store_does_not_reuse_cache_that_does_not_cover_requested_range(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    cached_task = _task(start_ms=1704067200000, end_ms=1704070799999)
    requested_task = _task(start_ms=1704067200000, end_ms=1704153599999)
    paths = store.data_paths(CacheSide.DB, cached_task)
    frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "timestamp__compare": ["1704067200000"],
            "open": ["1"],
            "close": ["1.1"],
        }
    )
    store.write_data_frame(paths, frame, _manifest(cached_task.start_ms, cached_task.end_ms))

    assert store.find_covering_data_cache(CacheSide.DB, requested_task) is None


def test_store_cleans_tmp_files_without_removing_formal_cache(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    paths = store.data_paths(CacheSide.DB, task)
    frame = pl.DataFrame({"timestamp": ["1704067200000"]})
    store.write_data_frame(paths, frame, _manifest(task.start_ms, task.end_ms))
    tmp_file = store.tmp_root / "run-1" / "leftover.tmp"
    tmp_file.parent.mkdir(parents=True)
    tmp_file.write_text("partial", encoding="utf-8")

    store.cleanup_tmp()

    assert not tmp_file.exists()
    assert paths.data_path.exists()
    assert paths.manifest_path.exists()
