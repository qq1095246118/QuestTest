from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.db_accuracy.cache_models import CachedCompareRequest, TimePartition
from tests.db_accuracy.models import ResolvedTableSpec


DAY_MS = 86_400_000


def validate_cached_request(request: CachedCompareRequest) -> None:
    if not request.table:
        raise ValueError("table is required for cached DB accuracy comparison")
    if request.start_ms is None or request.end_ms is None:
        raise ValueError("start_ms and end_ms are required for cached DB accuracy comparison")
    if request.end_ms < request.start_ms:
        raise ValueError("end_ms must be greater than or equal to start_ms")
    if request.partition_days < 1:
        raise ValueError("partition_days must be >= 1")
    if request.max_shards < 1:
        raise ValueError("max_shards must be >= 1")


def explicit_market_key(spec: ResolvedTableSpec, request: CachedCompareRequest) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for field in spec.key_fields:
        if field == "symbol":
            if not request.symbols:
                return None
            if len(request.symbols) != 1:
                raise ValueError("explicit cached comparison accepts one symbol per request")
            values[field] = request.symbols[0]
        elif field == "pair":
            if not request.pairs:
                return None
            if len(request.pairs) != 1:
                raise ValueError("explicit cached comparison accepts one pair per request")
            values[field] = request.pairs[0]
        elif field == "contract_type":
            if not request.contract_types:
                return None
            if len(request.contract_types) != 1:
                raise ValueError("explicit cached comparison accepts one contract_type per request")
            values[field] = request.contract_types[0]
        elif field == "interval":
            if not request.intervals:
                return None
            if len(request.intervals) != 1:
                raise ValueError("explicit cached comparison accepts one interval per request")
            values[field] = request.intervals[0]
        else:
            raise ValueError(f"unsupported cached market key field: {field}")
    return values


def split_time_partitions(start_ms: int, end_ms: int, partition_days: int) -> list[TimePartition]:
    if partition_days < 1:
        raise ValueError("partition_days must be >= 1")

    partitions: list[TimePartition] = []
    cursor = start_ms
    while cursor <= end_ms:
        cursor_date = datetime.fromtimestamp(cursor / 1000, UTC).date()
        bucket_end = datetime.combine(cursor_date, datetime.min.time(), UTC) + timedelta(days=partition_days)
        partition_end = min(int(bucket_end.timestamp() * 1000) - 1, end_ms)
        partitions.append(TimePartition(start_ms=cursor, end_ms=partition_end))
        cursor = partition_end + 1
    return partitions
