from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from services.db_accuracy.partitioned.cache_store_service import (
    PartitionedCacheStoreService,
    fingerprint_frame,
)
from services.db_accuracy.partitioned.models import (
    CacheManifest,
    CacheSide,
    CacheStatus,
    CompareManifest,
    CompareStatus,
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


def _manifest(
    start_ms: int,
    end_ms: int,
    row_count: int = 1,
    *,
    side: CacheSide = CacheSide.DB,
    status: CacheStatus = CacheStatus.COMPLETE,
    fingerprint: DataFingerprint | None = None,
    task: PartitionTask | None = None,
) -> CacheManifest:
    task = task or _task(start_ms, end_ms)
    return CacheManifest(
        schema_version=1,
        side=side,
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        market_key={"symbol": "BTCUSDT", "interval": "1m"},
        start_ms=start_ms,
        end_ms=end_ms,
        status=status,
        row_count=row_count,
        fingerprint=fingerprint or DataFingerprint(row_count=row_count, content_hash="abc"),
        schema_fingerprint=task.schema_fingerprint,
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

    store.write_data_frame(
        paths,
        frame,
        _manifest(
            large_task.start_ms,
            large_task.end_ms,
            row_count=3,
            fingerprint=fingerprint_frame(frame),
            task=large_task,
        ),
    )

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
    store.write_data_frame(
        paths,
        frame,
        _manifest(
            cached_task.start_ms,
            cached_task.end_ms,
            fingerprint=fingerprint_frame(frame),
            task=cached_task,
        ),
    )

    assert store.find_covering_data_cache(CacheSide.DB, requested_task) is None


def test_store_cleans_tmp_files_without_removing_formal_cache(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    paths = store.data_paths(CacheSide.DB, task)
    frame = pl.DataFrame({"timestamp": ["1704067200000"]})
    store.write_data_frame(
        paths,
        frame,
        _manifest(task.start_ms, task.end_ms, fingerprint=fingerprint_frame(frame), task=task),
    )
    tmp_file = store.tmp_root / "run-1" / "leftover.tmp"
    tmp_file.parent.mkdir(parents=True)
    tmp_file.write_text("partial", encoding="utf-8")

    store.cleanup_tmp()

    assert not tmp_file.exists()
    assert paths.data_path.exists()
    assert paths.manifest_path.exists()


def test_store_does_not_reuse_cache_when_manifest_fingerprint_differs_from_parquet(
    tmp_path: Path,
) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    paths = store.data_paths(CacheSide.DB, task)
    frame = pl.DataFrame({"timestamp": ["1704067200000"], "open": ["1"]})
    wrong_fingerprint = DataFingerprint(row_count=1, content_hash="not-the-frame-hash")

    store.write_data_frame(
        paths,
        frame,
        _manifest(
            task.start_ms,
            task.end_ms,
            fingerprint=wrong_fingerprint,
            task=task,
        ),
    )

    assert store.find_covering_data_cache(CacheSide.DB, task) is None


def test_store_failed_write_does_not_destroy_previous_complete_cache(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    paths = store.data_paths(CacheSide.DB, task)
    frame = pl.DataFrame({"timestamp": ["1704067200000"], "open": ["1"]})
    complete_manifest = _manifest(
        task.start_ms,
        task.end_ms,
        fingerprint=fingerprint_frame(frame),
        task=task,
    )

    store.write_data_frame(paths, frame, complete_manifest)
    store.write_data_frame(
        paths,
        pl.DataFrame(),
        _manifest(
            task.start_ms,
            task.end_ms,
            row_count=0,
            status=CacheStatus.FAILED,
            fingerprint=None,
            task=task,
        ),
    )

    loaded = store.read_data_manifest(paths)
    assert loaded is not None
    assert loaded.status == CacheStatus.COMPLETE
    assert paths.data_path.exists()


def test_store_empty_write_removes_previous_data_and_writes_empty_manifest(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    paths = store.data_paths(CacheSide.DB, task)
    frame = pl.DataFrame({"timestamp": ["1704067200000"], "open": ["1"]})
    store.write_data_frame(
        paths,
        frame,
        _manifest(task.start_ms, task.end_ms, fingerprint=fingerprint_frame(frame), task=task),
    )

    store.write_data_frame(
        paths,
        pl.DataFrame(),
        _manifest(
            task.start_ms,
            task.end_ms,
            row_count=0,
            status=CacheStatus.EMPTY,
            fingerprint=None,
            task=task,
        ),
    )

    loaded = store.read_data_manifest(paths)
    assert loaded is not None
    assert loaded.status == CacheStatus.EMPTY
    assert not paths.data_path.exists()


def test_store_treats_bad_manifest_files_as_cache_misses(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    bad_paths = store.data_paths(CacheSide.DB, task)
    bad_paths.manifest_path.parent.mkdir(parents=True)
    bad_paths.manifest_path.write_text("{not-json", encoding="utf-8")
    other_task = _task(start_ms=1704070800000, end_ms=1704074399999)
    other_paths = store.data_paths(CacheSide.DB, other_task)
    other_paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    other_paths.manifest_path.write_text('{"status": "complete"}', encoding="utf-8")

    assert store.find_covering_data_cache(CacheSide.DB, task) is None


def test_store_does_not_reuse_manifest_from_wrong_side(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    paths = store.data_paths(CacheSide.SOURCE, task)
    frame = pl.DataFrame({"timestamp": ["1704067200000"], "open": ["1"]})

    store.write_data_frame(
        paths,
        frame,
        _manifest(
            task.start_ms,
            task.end_ms,
            side=CacheSide.DB,
            fingerprint=fingerprint_frame(frame),
            task=task,
        ),
    )

    assert store.find_covering_data_cache(CacheSide.SOURCE, task) is None


def test_store_does_not_reuse_manifest_when_task_schema_changes(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    changed_task = PartitionTask(
        table=task.table,
        kind=task.kind,
        endpoint=task.endpoint,
        key_values=task.key_values,
        time_field=task.time_field,
        source_time_field=task.source_time_field,
        compare_fields=("timestamp", "open", "close", "volume"),
        request_limit=task.request_limit,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        partition_label=task.partition_label,
        partition_bucket=task.partition_bucket,
        is_registry=task.is_registry,
    )
    paths = store.data_paths(CacheSide.DB, task)
    frame = pl.DataFrame({"timestamp": ["1704067200000"], "open": ["1"], "close": ["1.1"]})
    store.write_data_frame(
        paths,
        frame,
        _manifest(task.start_ms, task.end_ms, fingerprint=fingerprint_frame(frame), task=task),
    )

    assert store.find_covering_data_cache(CacheSide.DB, changed_task) is None


def test_read_data_frame_raises_when_requested_time_field_is_missing(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    paths = store.data_paths(CacheSide.DB, task)
    frame = pl.DataFrame({"open": ["1"]})
    store.write_data_frame(
        paths,
        frame,
        _manifest(task.start_ms, task.end_ms, fingerprint=fingerprint_frame(frame), task=task),
    )

    with pytest.raises(ValueError, match="timestamp"):
        store.read_data_frame(paths, task=task, time_field="timestamp")


def test_path_values_are_slugged_to_safe_segments(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = PartitionTask(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_values={"symbol": "../BTC\\USDT\x01", "interval": "."},
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        start_ms=1704067200000,
        end_ms=1704153599999,
        partition_label="1704067200000-1704153599999",
        partition_bucket="date=2024-01-01",
    )

    paths = store.data_paths(CacheSide.DB, task)

    assert "symbol=BTC_USDT" in paths.data_path.parts
    assert "interval=_" in paths.data_path.parts
    assert ".." not in paths.data_path.parts


def test_read_compare_manifest_misses_when_report_or_diff_is_missing(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    paths = store.compare_paths(task)
    paths.manifest_path.parent.mkdir(parents=True)
    manifest = CompareManifest(
        schema_version=1,
        table=task.table,
        endpoint=task.endpoint,
        market_key=task.key_values,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        status=CompareStatus.PASSED,
        db_fingerprint=DataFingerprint(row_count=1, content_hash="db"),
        source_fingerprint=DataFingerprint(row_count=1, content_hash="source"),
        db_rows=1,
        source_rows=1,
        differences=0,
        report_path="report.txt",
        diff_path="diff.json",
        message=None,
        created_at_utc="2026-05-22T00:00:00+00:00",
    )
    paths.manifest_path.write_text(
        '{"schema_version": 1, "table": "binance_kline_all_future_raw", '
        '"endpoint": "usdm_klines", "market_key": {"symbol": "BTCUSDT", "interval": "1m"}, '
        '"start_ms": 1704067200000, "end_ms": 1704153599999, "status": "passed", '
        '"db_fingerprint": {"row_count": 1, "content_hash": "db"}, '
        '"source_fingerprint": {"row_count": 1, "content_hash": "source"}, '
        '"db_rows": 1, "source_rows": 1, "differences": 0, '
        '"report_path": "report.txt", "diff_path": "diff.json", '
        '"message": null, "created_at_utc": "2026-05-22T00:00:00+00:00"}',
        encoding="utf-8",
    )

    assert manifest.reusable_for(
        task,
        DataFingerprint(row_count=1, content_hash="db"),
        DataFingerprint(row_count=1, content_hash="source"),
    )
    assert store.read_compare_manifest(paths) is None
