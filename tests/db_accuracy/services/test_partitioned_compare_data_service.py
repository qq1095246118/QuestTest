from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from services.db_accuracy.partitioned.cache_store_service import (
    PartitionedCacheStoreService,
    fingerprint_frame,
)
from services.db_accuracy.partitioned.compare_data_service import PartitionedCompareDataService
from services.db_accuracy.partitioned.models import CompareStatus, PartitionTask


def _task(
    start_ms: int = 1704067200000,
    end_ms: int = 1704153599999,
    symbol: str = "BTCUSDT",
    partition_label: str | None = None,
) -> PartitionTask:
    if partition_label is None:
        partition_label = f"{start_ms}-{end_ms}"
    return PartitionTask(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_values={"symbol": symbol, "interval": "1m"},
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        start_ms=start_ms,
        end_ms=end_ms,
        partition_label=partition_label,
        partition_bucket="date=2024-01-01",
        is_registry=False,
    )


def _frame(close: str = "2") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "open": ["1"],
            "close": [close],
        }
    )


def test_ensure_compare_writes_passed_manifest_and_fixed_artifacts(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedCompareDataService(store)
    task = _task()
    frame = _frame()

    manifest = service.ensure_compare(
        task,
        db_frame=frame,
        source_frame=frame,
        db_fingerprint=fingerprint_frame(frame),
        source_fingerprint=fingerprint_frame(frame),
    )

    paths = store.compare_paths(task)
    assert manifest.status == CompareStatus.PASSED
    assert manifest.differences == 0
    assert manifest.report_path == store.relative_to_root(paths.report_path)
    assert manifest.diff_path == store.relative_to_root(paths.diff_path)
    assert paths.report_path.exists()
    assert paths.diff_path.exists()
    assert paths.manifest_path.exists()
    assert json.loads(paths.diff_path.read_text(encoding="utf-8"))["unequal_count"] == 0


def test_ensure_compare_writes_failed_with_differences_as_complete(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedCompareDataService(store)
    task = _task()
    db_frame = _frame(close="2")
    source_frame = _frame(close="3")

    manifest = service.ensure_compare(
        task,
        db_frame=db_frame,
        source_frame=source_frame,
        db_fingerprint=fingerprint_frame(db_frame),
        source_fingerprint=fingerprint_frame(source_frame),
    )

    paths = store.compare_paths(task)
    assert manifest.status == CompareStatus.FAILED_WITH_DIFFERENCES
    assert manifest.complete is True
    assert manifest.differences == 1
    assert manifest.message == "cached comparison found differences"
    payload = json.loads(paths.diff_path.read_text(encoding="utf-8"))
    assert payload["unequal_count"] == 1


def test_ensure_compare_reuses_only_exact_range_and_matching_fingerprints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedCompareDataService(store)
    task = _task()
    frame = _frame()
    db_fp = fingerprint_frame(frame)
    source_fp = fingerprint_frame(frame)

    first_manifest = service.ensure_compare(task, frame, frame, db_fp, source_fp)
    report_path = store.compare_paths(task).report_path
    first_report = report_path.read_text(encoding="utf-8")

    def fail_if_recomputed(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("DataComPy should not run for a reusable compare manifest")

    monkeypatch.setattr(
        "services.db_accuracy.partitioned.compare_data_service.DataComPyCompareService.compare",
        fail_if_recomputed,
    )

    reused_manifest = service.ensure_compare(task, frame, frame, db_fp, source_fp)

    assert reused_manifest == first_manifest
    assert report_path.read_text(encoding="utf-8") == first_report

    different_range = _task(start_ms=1704070800000, end_ms=1704074399999)
    with pytest.raises(AssertionError, match="DataComPy should not run"):
        service.ensure_compare(different_range, frame, frame, db_fp, source_fp)

    different_fp = fingerprint_frame(_frame(close="4"))
    with pytest.raises(AssertionError, match="DataComPy should not run"):
        service.ensure_compare(task, frame, frame, db_fp, different_fp)


def test_ensure_compare_uses_isolated_temp_outputs_for_long_task_labels(
    tmp_path: Path,
) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedCompareDataService(store)
    task = _task(
        symbol="BTC" + "USDT" * 80,
        partition_label="range-" + "2024-01-01-" * 80,
    )
    frame = _frame()

    manifest = service.ensure_compare(
        task,
        db_frame=frame,
        source_frame=frame,
        db_fingerprint=fingerprint_frame(frame),
        source_fingerprint=fingerprint_frame(frame),
    )

    artifact_root = tmp_path
    report_path = artifact_root / str(manifest.report_path)
    diff_path = artifact_root / str(manifest.diff_path)
    manifest_path = report_path.parent / "manifest.json"
    assert manifest.status == CompareStatus.PASSED
    assert report_path.exists()
    assert diff_path.exists()
    assert manifest_path.exists()
    assert not list(report_path.parent.glob("*.report.txt"))
    assert not list(report_path.parent.glob("*.diff.json"))
    assert not (tmp_path / "_tmp" / "compare").exists()
    assert json.loads(diff_path.read_text(encoding="utf-8"))["unequal_count"] == 0


def test_ensure_compare_recompute_overwrites_fixed_artifacts_without_deleting_them(
    tmp_path: Path,
) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedCompareDataService(store)
    task = _task()
    first_frame = _frame(close="2")
    second_db_frame = _frame(close="2")
    second_source_frame = _frame(close="3")

    first_manifest = service.ensure_compare(
        task,
        db_frame=first_frame,
        source_frame=first_frame,
        db_fingerprint=fingerprint_frame(first_frame),
        source_fingerprint=fingerprint_frame(first_frame),
    )
    second_manifest = service.ensure_compare(
        task,
        db_frame=second_db_frame,
        source_frame=second_source_frame,
        db_fingerprint=fingerprint_frame(second_db_frame),
        source_fingerprint=fingerprint_frame(second_source_frame),
    )

    paths = store.compare_paths(task)
    assert first_manifest.status == CompareStatus.PASSED
    assert second_manifest.status == CompareStatus.FAILED_WITH_DIFFERENCES
    assert paths.report_path.exists()
    assert paths.diff_path.exists()
    assert json.loads(paths.diff_path.read_text(encoding="utf-8"))["unequal_count"] == 1
