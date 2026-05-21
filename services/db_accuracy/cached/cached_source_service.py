"""cached 模式源数据分区缓存服务。

本模块负责按市场分片和时间分区获取 Binance 源数据，并写入本地缓存。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl

from services.db_accuracy.source_service import BinanceSourceService
from services.db_accuracy.cached.cache_models import CacheManifest, MarketShard, TimePartition
from services.db_accuracy.cached.cache_store_service import CacheStoreService
from services.db_accuracy.db_reader_service import DBAccuracyReaderService
from services.db_accuracy.cached.frame_normalizer_service import (
    normalized_compare_columns,
    source_rows_to_normalized_frame,
)
from services.db_accuracy.models import (
    KeyTimeRange,
    ResolvedTableSpec,
    TableSpec,
    ValidationKey,
)


class CachedBinanceSourceService:
    def __init__(self, store: CacheStoreService, source: Any = None):
        self.store = store
        self.source = source if source is not None else BinanceSourceService()

    def ensure_partition(
        self,
        spec: TableSpec,
        shard: MarketShard,
        partition: TimePartition,
        refresh: bool,
    ) -> tuple[pl.DataFrame, CacheManifest]:
        paths = self.store.paths_for(shard, partition)
        if not refresh and self.store.has_complete_partition(paths):
            manifest = self.store.read_manifest(paths)
            if manifest is not None:
                if manifest.status == "complete":
                    return self.store.read_frame(paths), manifest
                if manifest.status in {"empty", "source_market_unavailable"}:
                    return _empty_frame(shard), manifest

        key = ValidationKey(dict(shard.key_values))
        try:
            rows = []
            for start_ms, end_ms in _source_windows(spec, shard, partition, key):
                rows.extend(
                    self.source.fetch_rows(
                        spec,
                        key,
                        start_ms,
                        end_ms,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - cache records source state instead of raising
            manifest = self._manifest(
                shard,
                partition,
                status=_error_status(exc),
                row_count=0,
                source_error=str(exc),
            )
            self.store.write_manifest(paths, manifest)
            return _empty_frame(shard), manifest

        if not rows:
            manifest = self._manifest(
                shard,
                partition,
                status="empty",
                row_count=0,
                source_error=None,
            )
            self.store.write_manifest(paths, manifest)
            return _empty_frame(shard), manifest

        frame = source_rows_to_normalized_frame(shard, rows)
        self.store.write_frame(paths, frame)
        manifest = self._manifest(
            shard,
            partition,
            status="complete",
            row_count=frame.height,
            source_error=None,
        )
        self.store.write_manifest(paths, manifest)
        return frame, manifest

    def _manifest(
        self,
        shard: MarketShard,
        partition: TimePartition,
        status: str,
        row_count: int,
        source_error: str | None,
    ) -> CacheManifest:
        return CacheManifest(
            table=shard.table,
            endpoint=shard.endpoint,
            market_key=dict(shard.key_values),
            start_ms=partition.start_ms,
            end_ms=partition.end_ms,
            status=status,
            row_count=row_count,
            source_error=source_error,
            created_at_utc=datetime.now(UTC).isoformat(),
        )


def _error_status(exc: Exception) -> str:
    text = str(exc).lower()
    market_markers = (
        "invalid symbol",
        "invalid pair",
        "invalid contract",
        "unknown symbol",
        "symbol not found",
        "market is closed",
        "market unavailable",
        "not found",
        "no such market",
    )
    if any(marker in text for marker in market_markers):
        return "source_market_unavailable"
    return "source_request_failed"


def _empty_frame(shard: MarketShard) -> pl.DataFrame:
    return pl.DataFrame(schema={column: pl.String for column in _frame_columns(shard)})


def _frame_columns(shard: MarketShard) -> tuple[str, ...]:
    return (*shard.join_columns, *normalized_compare_columns(shard))


def _source_windows(
    spec: TableSpec,
    shard: MarketShard,
    partition: TimePartition,
    key: ValidationKey,
) -> list[tuple[int, int]]:
    resolved = ResolvedTableSpec(
        spec=spec,
        columns=(),
        time_field=shard.time_field,
        interval_field=spec.interval_field,
        compare_fields=shard.compare_fields,
        key_fields=tuple(shard.key_values.keys()),
    )
    time_range = KeyTimeRange(
        table=shard.table,
        key=key,
        start_ms=partition.start_ms,
        end_ms=partition.end_ms,
    )
    return [
        (window.start_ms, window.end_ms)
        for window in DBAccuracyReaderService(db=None).build_windows(resolved, time_range)
    ]
