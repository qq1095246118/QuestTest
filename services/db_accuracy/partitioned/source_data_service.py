"""统一分区 Binance source 数据缓存准备服务。"""

from __future__ import annotations

from datetime import UTC, datetime
import time
from typing import Any

import polars as pl

from services.db_accuracy.cached.cache_models import MarketShard
from services.db_accuracy.cached.frame_normalizer_service import (
    source_rows_to_normalized_frame,
)
from services.db_accuracy.compare_service import normalize_value
from services.db_accuracy.db_reader_service import DBAccuracyReaderService
from services.db_accuracy.models import (
    KeyTimeRange,
    ResolvedTableSpec,
    SourceRow,
    TableSpec,
    ValidationKey,
)
from services.db_accuracy.partitioned.cache_store_service import (
    PartitionedCacheStoreService,
    fingerprint_frame,
)
from services.db_accuracy.partitioned.models import (
    SCHEMA_VERSION,
    CacheManifest,
    CachePolicy,
    CacheSide,
    CacheStatus,
    ExecutionOptions,
    PartitionTask,
)
from services.db_accuracy.source_service import BinanceSourceService


class SourceRequestFailed(RuntimeError):
    """Raised after source retries are exhausted."""

    def __init__(
        self,
        *,
        task: PartitionTask,
        window_start_ms: int | None,
        window_end_ms: int | None,
        attempts: int,
        original_exception: Exception,
    ) -> None:
        self.task = task
        self.window_start_ms = window_start_ms
        self.window_end_ms = window_end_ms
        self.attempts = attempts
        self.original_exception = original_exception
        window_label = (
            "registry"
            if window_start_ms is None or window_end_ms is None
            else f"window={window_start_ms}-{window_end_ms}"
        )
        super().__init__(
            f"source request failed for {task.label} {window_label} "
            f"after {attempts} attempts: {original_exception}"
        )


class PartitionedSourceDataService:
    def __init__(self, store: PartitionedCacheStoreService, source: Any = None):
        self.store = store
        self.source = source if source is not None else BinanceSourceService()
        self.window_builder = DBAccuracyReaderService(db=None)

    def ensure_source_frame(
        self,
        task: PartitionTask,
        policy: CachePolicy,
        execution: ExecutionOptions | None = None,
    ) -> tuple[pl.DataFrame, CacheManifest]:
        execution = execution or ExecutionOptions()
        if policy.use_source_cache:
            hit = self.store.find_covering_data_cache(CacheSide.SOURCE, task)
            if hit is not None:
                frame = self.store.read_data_frame(hit.paths, task, task.time_field)
                exact_paths = self.store.data_paths(CacheSide.SOURCE, task)
                manifest = self._manifest(
                    task=task,
                    frame=frame,
                    artifact_path=self.store.relative_to_root(exact_paths.data_path),
                )
                if hit.paths != exact_paths or _manifest_range_exceeds_task(hit.manifest, task):
                    self.store.write_data_frame(exact_paths, frame, manifest)
                return frame, manifest

        paths = self.store.data_paths(CacheSide.SOURCE, task)
        try:
            frame = self._fetch_frame(task, execution)
        except SourceRequestFailed:
            self.store.clear_data_cache(CacheSide.SOURCE, task)
            raise

        manifest = self._manifest(
            task=task,
            frame=frame,
            artifact_path=self.store.relative_to_root(paths.data_path),
        )
        self.store.write_data_frame(paths, frame, manifest)
        return frame, manifest

    def _fetch_frame(
        self,
        task: PartitionTask,
        execution: ExecutionOptions,
    ) -> pl.DataFrame:
        spec = _table_spec(task)
        if task.is_registry:
            rows = self._fetch_registry_rows_with_retries(task, spec, execution)
            return _registry_rows_to_frame(task, rows)

        if task.start_ms is None or task.end_ms is None:
            raise ValueError(f"{task.table} source partition requires start_ms and end_ms")
        if task.time_field is None:
            raise ValueError(f"{task.table} source partition requires a time field")

        resolved = _resolved_spec(task, spec)
        key = ValidationKey(dict(task.key_values))
        time_range = KeyTimeRange(
            table=task.table,
            key=key,
            start_ms=task.start_ms,
            end_ms=task.end_ms,
        )
        rows: list[SourceRow] = []
        for window in self.window_builder.build_windows(resolved, time_range):
            if window.start_ms is None or window.end_ms is None:
                continue
            rows.extend(
                self._fetch_rows_with_retries(
                    task,
                    spec,
                    key,
                    window.start_ms,
                    window.end_ms,
                    execution,
                )
            )

        shard = MarketShard(
            table=task.table,
            endpoint=task.endpoint,
            kind=task.kind,
            key_values=task.key_values,
            time_field=task.time_field,
            source_time_field=task.source_time_field or task.time_field,
            compare_fields=task.compare_fields,
            request_limit=task.request_limit,
        )
        return source_rows_to_normalized_frame(shard, rows)

    def _fetch_rows_with_retries(
        self,
        task: PartitionTask,
        spec: TableSpec,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
        execution: ExecutionOptions,
    ) -> list[SourceRow]:
        attempts = max(1, int(execution.source_retries))
        for attempt in range(1, attempts + 1):
            try:
                return self.source.fetch_rows(spec, key, start_ms, end_ms)
            except Exception as exc:  # noqa: BLE001 - source adapters raise API/client exceptions
                if attempt >= attempts:
                    raise SourceRequestFailed(
                        task=task,
                        window_start_ms=start_ms,
                        window_end_ms=end_ms,
                        attempts=attempt,
                        original_exception=exc,
                    ) from exc
                self._sleep_before_retry(execution, attempt)
        raise RuntimeError("unreachable source retry state")

    def _fetch_registry_rows_with_retries(
        self,
        task: PartitionTask,
        spec: TableSpec,
        execution: ExecutionOptions,
    ) -> list[Any]:
        attempts = max(1, int(execution.source_retries))
        for attempt in range(1, attempts + 1):
            try:
                return self.source.fetch_registry_rows(spec)
            except Exception as exc:  # noqa: BLE001 - source adapters raise API/client exceptions
                if attempt >= attempts:
                    raise SourceRequestFailed(
                        task=task,
                        window_start_ms=None,
                        window_end_ms=None,
                        attempts=attempt,
                        original_exception=exc,
                    ) from exc
                self._sleep_before_retry(execution, attempt)
        raise RuntimeError("unreachable source retry state")

    def _sleep_before_retry(self, execution: ExecutionOptions, attempt: int) -> None:
        if execution.source_retry_backoff_ms > 0:
            time.sleep((execution.source_retry_backoff_ms * attempt) / 1000)

    def _manifest(
        self,
        *,
        task: PartitionTask,
        frame: pl.DataFrame,
        artifact_path: str | None,
    ) -> CacheManifest:
        status = CacheStatus.EMPTY if frame.is_empty() else CacheStatus.COMPLETE
        return CacheManifest(
            schema_version=SCHEMA_VERSION,
            side=CacheSide.SOURCE,
            table=task.table,
            endpoint=task.endpoint,
            market_key=dict(task.key_values),
            start_ms=task.start_ms,
            end_ms=task.end_ms,
            status=status,
            row_count=frame.height,
            fingerprint=None if status == CacheStatus.EMPTY else fingerprint_frame(frame),
            schema_fingerprint=task.schema_fingerprint,
            error_type=None,
            error_message=None,
            artifact_path=artifact_path if status == CacheStatus.COMPLETE else None,
            created_at_utc=datetime.now(UTC).isoformat(),
        )


def _table_spec(task: PartitionTask) -> TableSpec:
    return TableSpec(
        table=task.table,
        kind=task.kind,
        endpoint=task.endpoint,
        key_fields=task.key_fields or tuple(task.key_values.keys()),
        time_fields=() if task.time_field is None else (task.time_field,),
        interval_field=task.interval_field,
        compare_fields=task.compare_fields,
        request_limit=task.request_limit,
        fixed_interval=task.fixed_interval,
        contract_type_field=task.contract_type_field,
        pair_field=task.pair_field,
        symbol_field=task.symbol_field,
        source_time_field=task.source_time_field,
    )


def _resolved_spec(task: PartitionTask, spec: TableSpec) -> ResolvedTableSpec:
    fields = (*spec.key_fields, *spec.time_fields, *task.compare_fields)
    return ResolvedTableSpec(
        spec=spec,
        columns=tuple(dict.fromkeys(fields)),
        time_field=task.time_field,
        interval_field=task.interval_field,
        compare_fields=task.compare_fields,
        key_fields=spec.key_fields,
    )


def _registry_rows_to_frame(task: PartitionTask, rows: list[Any]) -> pl.DataFrame:
    columns = tuple(dict.fromkeys((*task.key_fields, *task.compare_fields)))
    normalized_rows = []
    for row in rows:
        fields = row.fields if hasattr(row, "fields") else row
        if not _matches_task_key_values(fields, task):
            continue
        normalized_rows.append(
            {
                column: _normalized_to_string(normalize_value(fields.get(column)))
                for column in columns
            }
        )
    return pl.DataFrame(
        normalized_rows,
        schema={column: pl.String for column in columns},
        orient="row",
    )


def _matches_task_key_values(fields: dict[str, Any], task: PartitionTask) -> bool:
    for field, expected in task.key_values.items():
        actual_text = _normalized_to_string(normalize_value(fields.get(field)))
        expected_text = _normalized_to_string(normalize_value(expected))
        if actual_text != expected_text:
            return False
    return True


def _normalized_to_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 4 and value[0] == "decimal":
        _, sign, digits, exponent = value
        if any(not isinstance(digit, int) or digit < 0 or digit > 9 for digit in digits):
            digits_text = ",".join(str(digit) for digit in digits)
            return f"decimal:{sign}:{digits_text}:{exponent}"
        digits_text = "".join(str(digit) for digit in digits)
        if exponent < 0:
            split_at = len(digits_text) + exponent
            if split_at <= 0:
                digits_text = "0." + "0" * abs(split_at) + digits_text
            else:
                digits_text = digits_text[:split_at] + "." + digits_text[split_at:]
        elif exponent > 0:
            digits_text = digits_text + "0" * exponent
        if sign:
            digits_text = "-" + digits_text
        return digits_text
    return str(value)


def _manifest_range_exceeds_task(manifest: CacheManifest, task: PartitionTask) -> bool:
    return manifest.start_ms != task.start_ms or manifest.end_ms != task.end_ms
