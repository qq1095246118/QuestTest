from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from services.db_accuracy.models import SourceRow, ValidationKey
from services.db_accuracy.partitioned.cache_store_service import (
    PartitionedCacheStoreService,
    fingerprint_frame,
)
from services.db_accuracy.partitioned.models import (
    CachePolicy,
    CacheSide,
    CacheStatus,
    ExecutionOptions,
    PartitionTask,
)
from services.db_accuracy.partitioned.source_data_service import (
    PartitionedSourceDataService,
    SourceRequestFailed,
)


class RecordingSource:
    def __init__(
        self,
        rows: list[SourceRow] | None = None,
        *,
        fail_times: int = 0,
        registry_rows: list[Any] | None = None,
    ) -> None:
        self.rows = rows if rows is not None else []
        self.fail_times = fail_times
        self.registry_rows = registry_rows if registry_rows is not None else []
        self.calls: list[tuple[Any, ValidationKey, int, int]] = []
        self.registry_calls: list[Any] = []

    def fetch_rows(
        self,
        spec: Any,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        self.calls.append((spec, key, start_ms, end_ms))
        if len(self.calls) <= self.fail_times:
            raise RuntimeError(f"source down {len(self.calls)}")
        return [
            row
            for row in self.rows
            if start_ms <= int(row.fields["timestamp"]) <= end_ms
        ]

    def fetch_registry_rows(self, spec: Any) -> list[Any]:
        self.registry_calls.append(spec)
        if len(self.registry_calls) <= self.fail_times:
            raise RuntimeError(f"registry down {len(self.registry_calls)}")
        return self.registry_rows


class WindowFailingSource(RecordingSource):
    def __init__(
        self,
        rows: list[SourceRow],
        *,
        failures_by_window: dict[tuple[int, int], int],
    ) -> None:
        super().__init__(rows)
        self.failures_by_window = dict(failures_by_window)

    def fetch_rows(
        self,
        spec: Any,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        self.calls.append((spec, key, start_ms, end_ms))
        window = (start_ms, end_ms)
        remaining_failures = self.failures_by_window.get(window, 0)
        if remaining_failures > 0:
            self.failures_by_window[window] = remaining_failures - 1
            raise RuntimeError(f"source down for {start_ms}-{end_ms}")
        return [
            row
            for row in self.rows
            if start_ms <= int(row.fields["timestamp"]) <= end_ms
        ]


def _task(
    start_ms: int = 1704067200000,
    end_ms: int = 1704067259999,
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
        interval_field="interval",
    )


def _registry_task() -> PartitionTask:
    return PartitionTask(
        table="binance_futures_symbols",
        kind="registry",
        endpoint="usdm_exchange_info",
        key_values={},
        time_field=None,
        source_time_field=None,
        compare_fields=("symbol", "status", "contract_type"),
        request_limit=1000,
        start_ms=None,
        end_ms=None,
        partition_label="registry",
        partition_bucket="registry",
        is_registry=True,
        key_fields=("symbol",),
    )


def _source_frame(values: list[tuple[int, str, str]]) -> list[SourceRow]:
    return [
        SourceRow(
            key=timestamp,
            fields={"timestamp": timestamp, "open": open_, "close": close},
        )
        for timestamp, open_, close in values
    ]


def _service(
    tmp_path: Path,
    source: RecordingSource,
) -> tuple[PartitionedSourceDataService, PartitionedCacheStoreService]:
    store = PartitionedCacheStoreService(tmp_path)
    return PartitionedSourceDataService(store=store, source=source), store


def _no_backoff(retries: int = 5) -> ExecutionOptions:
    return ExecutionOptions(source_retries=retries, source_retry_backoff_ms=0)


def test_source_fetch_retries_each_window_without_repeating_completed_windows(
    tmp_path: Path,
) -> None:
    task = _task(end_ms=1704067319999)
    task = PartitionTask(
        table=task.table,
        kind=task.kind,
        endpoint=task.endpoint,
        key_values=task.key_values,
        time_field=task.time_field,
        source_time_field=task.source_time_field,
        compare_fields=task.compare_fields,
        request_limit=1,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        partition_label=task.partition_label,
        partition_bucket=task.partition_bucket,
        key_fields=task.key_fields,
        interval_field=task.interval_field,
    )
    first_window = (1704067200000, 1704067259999)
    second_window = (1704067260000, 1704067319999)
    source = WindowFailingSource(
        _source_frame(
            [
                (1704067200000, "1", "2"),
                (1704067260000, "3", "4"),
            ]
        ),
        failures_by_window={second_window: 2},
    )
    service, _ = _service(tmp_path, source)

    frame, _ = service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=False),
        _no_backoff(),
    )

    windows_called = [(call[2], call[3]) for call in source.calls]
    assert windows_called.count(first_window) == 1
    assert windows_called.count(second_window) == 3
    assert frame.to_dict(as_series=False)["timestamp"] == [
        "1704067200000",
        "1704067260000",
    ]


def test_source_fetch_retries_four_failures_then_writes_complete_cache(
    tmp_path: Path,
) -> None:
    task = _task()
    source = RecordingSource(
        _source_frame([(1704067200000, "1.0", "2.0")]),
        fail_times=4,
    )
    service, store = _service(tmp_path, source)

    frame, manifest = service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=False),
        _no_backoff(),
    )

    assert len(source.calls) == 5
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
    assert manifest.artifact_path == store.relative_to_root(
        store.data_paths(CacheSide.SOURCE, task).data_path
    )
    paths = store.data_paths(CacheSide.SOURCE, task)
    assert paths.data_path.exists()
    assert store.read_data_manifest(paths) == manifest


def test_source_retry_exhaustion_raises_structured_error_and_clears_only_source_cache(
    tmp_path: Path,
) -> None:
    task = _task()
    source = RecordingSource(_source_frame([(1704067200000, "1", "2")]), fail_times=5)
    service, store = _service(tmp_path, source)
    paths = store.data_paths(CacheSide.SOURCE, task)
    paths.data_path.parent.mkdir(parents=True)
    paths.data_path.write_text("stale", encoding="utf-8")
    paths.manifest_path.write_text('{"status": "complete"}', encoding="utf-8")
    tmp_file = store.tmp_root / "attempt" / "partial.parquet"
    tmp_file.parent.mkdir(parents=True)
    tmp_file.write_text("partial", encoding="utf-8")

    with pytest.raises(SourceRequestFailed, match="source down 5") as exc_info:
        service.ensure_source_frame(
            task,
            CachePolicy(use_source_cache=False),
            _no_backoff(),
        )

    exc = exc_info.value
    assert len(source.calls) == 5
    assert exc.task == task
    assert exc.window_start_ms == task.start_ms
    assert exc.window_end_ms == task.end_ms
    assert exc.attempts == 5
    assert isinstance(exc.original_exception, RuntimeError)
    assert not paths.data_path.exists()
    assert not paths.manifest_path.exists()
    assert tmp_file.exists()


def test_source_non_adapter_errors_are_not_wrapped_or_cleaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task()
    source = RecordingSource(_source_frame([(1704067200000, "1", "2")]))
    service, store = _service(tmp_path, source)
    paths = store.data_paths(CacheSide.SOURCE, task)
    paths.data_path.parent.mkdir(parents=True)
    paths.data_path.write_text("stale", encoding="utf-8")
    paths.manifest_path.write_text('{"status": "complete"}', encoding="utf-8")

    def fail_build_windows(*args: Any, **kwargs: Any) -> list[Any]:
        raise ValueError("bad spec")

    monkeypatch.setattr(service.window_builder, "build_windows", fail_build_windows)

    with pytest.raises(ValueError, match="bad spec"):
        service.ensure_source_frame(
            task,
            CachePolicy(use_source_cache=False),
            _no_backoff(),
        )

    assert source.calls == []
    assert paths.data_path.exists()
    assert paths.manifest_path.exists()


def test_use_source_cache_reuses_existing_complete_cache_without_fetching(
    tmp_path: Path,
) -> None:
    task = _task()
    source = RecordingSource(_source_frame([(1704067200000, "9", "9")]))
    service, store = _service(tmp_path, source)

    first_frame, first_manifest = service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=False),
        _no_backoff(),
    )
    second_frame, second_manifest = service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=True),
        _no_backoff(),
    )

    assert len(source.calls) == 1
    assert second_frame.equals(first_frame)
    assert second_manifest.fingerprint == first_manifest.fingerprint


def test_use_source_cache_false_refetches_and_overwrites_exact_source_cache(
    tmp_path: Path,
) -> None:
    task = _task()
    source = RecordingSource(_source_frame([(1704067200000, "1", "2")]))
    service, store = _service(tmp_path, source)
    service.ensure_source_frame(task, CachePolicy(use_source_cache=False), _no_backoff())
    source.rows = _source_frame([(1704067200000, "10", "20")])

    frame, manifest = service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=False),
        _no_backoff(),
    )

    assert len(source.calls) == 2
    assert frame.to_dict(as_series=False)["open"] == ["10"]
    assert manifest.fingerprint == fingerprint_frame(frame)
    assert pl.read_parquet(store.data_paths(CacheSide.SOURCE, task).data_path).equals(frame)


def test_larger_source_cache_satisfies_smaller_range_and_writes_exact_manifest(
    tmp_path: Path,
) -> None:
    large_task = _task(start_ms=1704067200000, end_ms=1704067379999)
    small_task = _task(start_ms=1704067260000, end_ms=1704067319999)
    source = RecordingSource(
        _source_frame(
            [
                (1704067200000, "1", "2"),
                (1704067260000, "3", "4"),
                (1704067320000, "5", "6"),
            ]
        )
    )
    service, store = _service(tmp_path, source)
    large_frame, _ = service.ensure_source_frame(
        large_task,
        CachePolicy(use_source_cache=False),
        _no_backoff(),
    )
    large_paths = store.data_paths(CacheSide.SOURCE, large_task)
    small_paths = store.data_paths(CacheSide.SOURCE, small_task)

    small_frame, small_manifest = service.ensure_source_frame(
        small_task,
        CachePolicy(use_source_cache=True),
        _no_backoff(),
    )

    assert len(source.calls) == 1
    assert small_frame.to_dict(as_series=False)["timestamp"] == ["1704067260000"]
    assert small_manifest.start_ms == small_task.start_ms
    assert small_manifest.end_ms == small_task.end_ms
    assert small_manifest.row_count == 1
    assert small_manifest.fingerprint == fingerprint_frame(small_frame)
    assert small_manifest.schema_fingerprint == small_task.schema_fingerprint
    assert small_manifest.artifact_path == store.relative_to_root(small_paths.data_path)
    assert small_paths.data_path.exists()
    assert pl.read_parquet(small_paths.data_path).equals(small_frame)
    assert pl.read_parquet(large_paths.data_path).equals(large_frame)


def test_registry_source_cache_path_has_no_range_and_can_be_reused(
    tmp_path: Path,
) -> None:
    task = _registry_task()
    source = RecordingSource(
        registry_rows=[
            SourceRow(
                key="BTCUSDT",
                fields={
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "contract_type": "PERPETUAL",
                },
            ),
            {
                "symbol": "ETHUSDT",
                "status": "BREAK",
                "contract_type": "PERPETUAL",
            },
        ]
    )
    service, store = _service(tmp_path, source)

    frame, manifest = service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=False),
        _no_backoff(),
    )
    cached_frame, cached_manifest = service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=True),
        _no_backoff(),
    )

    paths = store.data_paths(CacheSide.SOURCE, task)
    assert "range=" not in str(paths.data_path)
    assert source.registry_calls == [source.registry_calls[0]]
    assert frame.to_dict(as_series=False) == {
        "symbol": ["BTCUSDT", "ETHUSDT"],
        "status": ["TRADING", "BREAK"],
        "contract_type": ["PERPETUAL", "PERPETUAL"],
    }
    assert manifest.status == CacheStatus.COMPLETE
    assert manifest.schema_fingerprint == task.schema_fingerprint
    assert cached_frame.equals(frame)
    assert cached_manifest.fingerprint == manifest.fingerprint
