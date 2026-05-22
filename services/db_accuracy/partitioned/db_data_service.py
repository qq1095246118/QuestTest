"""统一分区 DB 数据缓存准备服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl

from services.db_accuracy.cached.cache_models import MarketShard, TimePartition
from services.db_accuracy.cached.cached_db_reader_service import CachedDBReaderService
from services.db_accuracy.cached.frame_normalizer_service import rows_to_normalized_frame
from services.db_accuracy.compare_service import normalize_value
from services.db_accuracy.db_reader_service import DBAccuracyReaderService
from services.db_accuracy.models import ResolvedTableSpec, TableSpec
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
    PartitionTask,
)


class PartitionedDBDataService:
    def __init__(self, db: Any, store: PartitionedCacheStoreService):
        self.db = db
        self.store = store
        self.cached_reader = CachedDBReaderService(db)
        self.direct_reader = DBAccuracyReaderService(db)

    def ensure_db_frame(
        self,
        task: PartitionTask,
        policy: CachePolicy,
    ) -> tuple[pl.DataFrame, CacheManifest]:
        if policy.use_db_cache:
            hit = self.store.find_covering_data_cache(CacheSide.DB, task)
            if hit is not None:
                frame = self.store.read_data_frame(hit.paths, task, task.time_field)
                exact_paths = self.store.data_paths(CacheSide.DB, task)
                manifest = self._manifest(
                    task=task,
                    frame=frame,
                    artifact_path=self.store.relative_to_root(exact_paths.data_path),
                )
                if hit.paths != exact_paths or _manifest_range_exceeds_task(hit.manifest, task):
                    self.store.write_data_frame(exact_paths, frame, manifest)
                return frame, manifest

        paths = self.store.data_paths(CacheSide.DB, task)
        frame = self._fetch_frame(task)
        manifest = self._manifest(
            task=task,
            frame=frame,
            artifact_path=self.store.relative_to_root(paths.data_path),
        )
        self.store.write_data_frame(paths, frame, manifest)
        return frame, manifest

    def _fetch_frame(self, task: PartitionTask) -> pl.DataFrame:
        if task.is_registry:
            rows = self.direct_reader.registry_rows(_resolved_spec(task))
            return _registry_rows_to_frame(task, rows)

        if task.start_ms is None or task.end_ms is None:
            raise ValueError(f"{task.table} partition requires start_ms and end_ms")
        if task.time_field is None:
            raise ValueError(f"{task.table} partition requires a time field")

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
        partition = TimePartition(start_ms=task.start_ms, end_ms=task.end_ms)
        return rows_to_normalized_frame(shard, self.cached_reader.rows_for_partition(shard, partition))

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
            side=CacheSide.DB,
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


def _resolved_spec(task: PartitionTask) -> ResolvedTableSpec:
    spec = TableSpec(
        table=task.table,
        kind=task.kind,
        endpoint=task.endpoint,
        key_fields=task.key_fields,
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
    fields = (*task.key_fields, *(() if task.time_field is None else (task.time_field,)), *task.compare_fields)
    return ResolvedTableSpec(
        spec=spec,
        columns=tuple(dict.fromkeys(fields)),
        time_field=task.time_field,
        interval_field=task.interval_field,
        compare_fields=task.compare_fields,
        key_fields=task.key_fields,
    )


def _manifest_range_exceeds_task(manifest: CacheManifest, task: PartitionTask) -> bool:
    return manifest.start_ms != task.start_ms or manifest.end_ms != task.end_ms


def _registry_rows_to_frame(task: PartitionTask, rows: list[dict[str, Any]]) -> pl.DataFrame:
    columns = tuple(dict.fromkeys((*task.key_fields, *task.compare_fields)))
    normalized_rows = [
        {
            column: _normalized_to_string(normalize_value(row.get(column)))
            for column in columns
        }
        for row in rows
    ]
    return pl.DataFrame(
        normalized_rows,
        schema={column: pl.String for column in columns},
        orient="row",
    )


def _normalized_to_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 4 and value[0] == "decimal":
        _, sign, digits, exponent = value
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
