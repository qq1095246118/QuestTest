from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CachedCompareRequest:
    table: str
    start_ms: int
    end_ms: int
    cache_root: Path
    symbols: tuple[str, ...] = ()
    pairs: tuple[str, ...] = ()
    contract_types: tuple[str, ...] = ()
    intervals: tuple[str, ...] = ()
    partition_days: int = 1
    refresh_cache: bool = False
    max_shards: int = 100


@dataclass(frozen=True)
class MarketShard:
    table: str
    endpoint: str
    kind: str
    key_values: dict[str, Any]
    time_field: str
    source_time_field: str
    compare_fields: tuple[str, ...]
    request_limit: int

    @property
    def join_columns(self) -> tuple[str, ...]:
        return (*self.key_values.keys(), self.time_field)

    @property
    def label(self) -> str:
        parts = [f"table={self.table}"]
        parts.extend(f"{key}={self.key_values[key]}" for key in self.key_values)
        return ",".join(parts)

    @property
    def path_parts(self) -> tuple[str, ...]:
        parts = [f"table={self.table}"]
        parts.extend(f"{key}={_path_value(self.key_values[key])}" for key in self.key_values)
        return tuple(parts)


@dataclass(frozen=True)
class TimePartition:
    start_ms: int
    end_ms: int

    @property
    def bucket(self) -> str:
        start = datetime.fromtimestamp(self.start_ms / 1000, UTC)
        return f"date={start:%Y-%m-%d}"

    @property
    def label(self) -> str:
        return f"{self.start_ms}-{self.end_ms}"


@dataclass(frozen=True)
class CacheManifest:
    table: str
    endpoint: str
    market_key: dict[str, Any]
    start_ms: int
    end_ms: int
    status: str
    row_count: int
    source_error: str | None
    created_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CachedShardResult:
    shard_label: str
    partition_label: str
    status: str
    db_rows: int
    source_rows: int
    differences: int
    report_path: str | None
    diff_path: str | None
    message: str | None


@dataclass
class CachedRunResult:
    shards: list[CachedShardResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.shards) and all(
            shard.status in {"passed", "skipped_empty"} for shard in self.shards
        )

    def summary_text(self) -> str:
        passed = sum(1 for shard in self.shards if shard.status == "passed")
        skipped = sum(1 for shard in self.shards if shard.status.startswith("skipped"))
        failed = len(self.shards) - passed - skipped
        db_rows = sum(shard.db_rows for shard in self.shards)
        source_rows = sum(shard.source_rows for shard in self.shards)
        differences = sum(shard.differences for shard in self.shards)
        return "\n".join(
            [
                f"shards={len(self.shards)}",
                f"passed={passed}",
                f"failed={failed}",
                f"skipped={skipped}",
                f"db_rows={db_rows}",
                f"source_rows={source_rows}",
                f"differences={differences}",
            ]
        )


def _path_value(value: Any) -> str:
    text = str(value)
    return text.replace("/", "_").replace(" ", "_")
