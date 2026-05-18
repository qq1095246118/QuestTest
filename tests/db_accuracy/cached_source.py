from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tests.db_accuracy.binance_source import BinanceSource
from tests.db_accuracy.cache_models import CacheManifest, MarketShard, TimePartition
from tests.db_accuracy.cache_store import CacheStore
from tests.db_accuracy.frame_normalizer import source_rows_to_normalized_frame
from tests.db_accuracy.models import TableSpec, ValidationKey


class CachedBinanceSource:
    def __init__(self, store: CacheStore, source: Any = None):
        self.store = store
        self.source = source if source is not None else BinanceSource()

    def ensure_partition(
        self,
        spec: TableSpec,
        shard: MarketShard,
        partition: TimePartition,
        refresh: bool,
    ) -> CacheManifest:
        paths = self.store.paths_for(shard, partition)
        if not refresh and self.store.has_complete_partition(paths):
            manifest = self.store.read_manifest(paths)
            if manifest is not None:
                return manifest

        try:
            rows = self.source.fetch_rows(
                spec,
                ValidationKey(dict(shard.key_values)),
                partition.start_ms,
                partition.end_ms,
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
            return manifest

        if not rows:
            manifest = self._manifest(
                shard,
                partition,
                status="empty",
                row_count=0,
                source_error=None,
            )
            self.store.write_manifest(paths, manifest)
            return manifest

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
        return manifest

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
