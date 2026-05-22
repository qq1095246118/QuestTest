"""统一分区 DB accuracy 数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class AccuracyMode(StrEnum):
    DIRECT = "direct"
    CACHED = "cached"


class CacheSide(StrEnum):
    DB = "db"
    SOURCE = "source"


class CacheStatus(StrEnum):
    COMPLETE = "complete"
    EMPTY = "empty"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def reusable(self) -> bool:
        return self in {CacheStatus.COMPLETE, CacheStatus.EMPTY}


class CompareStatus(StrEnum):
    PASSED = "passed"
    FAILED_WITH_DIFFERENCES = "failed_with_differences"
    FAILED_OPERATIONAL = "failed_operational"
    CANCELLED = "cancelled"

    @property
    def complete(self) -> bool:
        return self in {
            CompareStatus.PASSED,
            CompareStatus.FAILED_WITH_DIFFERENCES,
        }


class RunStatus(StrEnum):
    PASSED = "passed"
    COMPLETED_WITH_DIFFERENCES = "completed_with_differences"
    PAUSED = "paused"
    FAILED = "failed"


@dataclass(frozen=True)
class CachePolicy:
    use_db_cache: bool = True
    use_source_cache: bool = True


@dataclass(frozen=True)
class ExecutionOptions:
    workers: int = 8
    source_retries: int = 5
    source_retry_backoff_ms: int = 1000
    stop_on_source_failure: bool = True


@dataclass(frozen=True)
class PartitionedAccuracyRequest:
    mode: AccuracyMode
    tables: tuple[str, ...]
    cache_root: Path
    symbols: tuple[str, ...] = ()
    pairs: tuple[str, ...] = ()
    contract_types: tuple[str, ...] = ()
    intervals: tuple[str, ...] = ()
    start_ms: int | None = None
    end_ms: int | None = None
    partition_days: int = 1
    max_shards: int = 100
    safety_hours: int = 24
    cache_policy: CachePolicy = field(default_factory=CachePolicy)
    execution: ExecutionOptions = field(default_factory=ExecutionOptions)


@dataclass(frozen=True)
class PartitionTask:
    table: str
    kind: str
    endpoint: str
    key_values: dict[str, Any]
    time_field: str | None
    source_time_field: str | None
    compare_fields: tuple[str, ...]
    request_limit: int
    start_ms: int | None
    end_ms: int | None
    partition_label: str
    partition_bucket: str
    is_registry: bool = False
    key_fields: tuple[str, ...] = ()
    interval_field: str | None = None
    fixed_interval: str | None = None
    symbol_field: str | None = "symbol"
    pair_field: str | None = None
    contract_type_field: str | None = None

    @property
    def key_columns(self) -> tuple[str, ...]:
        return tuple(self.key_values.keys()) or self.key_fields

    @property
    def join_columns(self) -> tuple[str, ...]:
        if self.time_field is None:
            return self.key_columns
        return (*self.key_columns, self.time_field)

    @property
    def label(self) -> str:
        parts = [f"table={self.table}"]
        parts.extend(f"{key}={self.key_values[key]}" for key in self.key_values)
        parts.append(self.partition_label)
        return ",".join(parts)

    @property
    def path_parts(self) -> tuple[str, ...]:
        parts = [f"table={self.table}"]
        parts.extend(f"{key}={_path_value(self.key_values[key])}" for key in self.key_values)
        parts.append(self.partition_bucket)
        return tuple(parts)


@dataclass(frozen=True)
class DataFingerprint:
    row_count: int
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CacheManifest:
    schema_version: int
    side: CacheSide
    table: str
    endpoint: str
    market_key: dict[str, Any]
    start_ms: int | None
    end_ms: int | None
    status: CacheStatus
    row_count: int
    fingerprint: DataFingerprint | None
    error_type: str | None
    error_message: str | None
    artifact_path: str | None
    created_at_utc: str

    def reusable_for(self, task: PartitionTask) -> bool:
        if self.schema_version != SCHEMA_VERSION:
            return False
        if not self.status.reusable:
            return False
        if self.table != task.table or self.endpoint != task.endpoint:
            return False
        if self.market_key != task.key_values:
            return False
        if task.is_registry:
            return self.start_ms is None and self.end_ms is None
        if self.start_ms is None or self.end_ms is None:
            return False
        if task.start_ms is None or task.end_ms is None:
            return False
        return self.start_ms <= task.start_ms and self.end_ms >= task.end_ms

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["side"] = self.side.value
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CacheManifest:
        data = dict(payload)
        data["side"] = CacheSide(data["side"])
        data["status"] = CacheStatus(data["status"])
        fingerprint = data.get("fingerprint")
        data["fingerprint"] = DataFingerprint(**fingerprint) if fingerprint else None
        return cls(**data)


@dataclass(frozen=True)
class CompareManifest:
    schema_version: int
    table: str
    endpoint: str
    market_key: dict[str, Any]
    start_ms: int | None
    end_ms: int | None
    status: CompareStatus
    db_fingerprint: DataFingerprint | None
    source_fingerprint: DataFingerprint | None
    db_rows: int
    source_rows: int
    differences: int
    report_path: str | None
    diff_path: str | None
    message: str | None
    created_at_utc: str

    @property
    def complete(self) -> bool:
        return self.status.complete

    def reusable_for(
        self,
        task: PartitionTask,
        db_fingerprint: DataFingerprint | None,
        source_fingerprint: DataFingerprint | None,
    ) -> bool:
        return (
            self.schema_version == SCHEMA_VERSION
            and self.complete
            and self.table == task.table
            and self.endpoint == task.endpoint
            and self.market_key == task.key_values
            and self.start_ms == task.start_ms
            and self.end_ms == task.end_ms
            and self.db_fingerprint == db_fingerprint
            and self.source_fingerprint == source_fingerprint
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CompareManifest:
        data = dict(payload)
        data["status"] = CompareStatus(data["status"])
        db_fingerprint = data.get("db_fingerprint")
        source_fingerprint = data.get("source_fingerprint")
        data["db_fingerprint"] = DataFingerprint(**db_fingerprint) if db_fingerprint else None
        data["source_fingerprint"] = (
            DataFingerprint(**source_fingerprint) if source_fingerprint else None
        )
        return cls(**data)


@dataclass(frozen=True)
class PartitionExecutionResult:
    task: PartitionTask
    compare_manifest: CompareManifest | None
    status: CompareStatus
    message: str | None = None


@dataclass(frozen=True)
class RunPauseReason:
    reason: str
    task_label: str
    message: str


@dataclass
class PartitionedRunResult:
    status: RunStatus
    tasks_total: int
    tasks_compared: int
    tasks_with_differences: int
    db_rows: int
    source_rows: int
    differences: int
    summary_text: str
    details: dict[str, Any]
    pause_reason: RunPauseReason | None = None

    @property
    def passed(self) -> bool:
        return self.status == RunStatus.PASSED


def _path_value(value: Any) -> str:
    return str(value).replace("/", "_").replace(" ", "_")
