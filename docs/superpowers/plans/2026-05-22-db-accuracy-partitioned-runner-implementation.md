# DB Accuracy Partitioned Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一次性交付 DB accuracy 的统一分区任务执行器，让 direct 和 cached 模式都支持 DB/source 双缓存、断点续跑、先取数后对比、并发执行、失败清理和聚合报告。

**Architecture:** 新增 `services/db_accuracy/partitioned/` 包承载统一模型、规划、缓存、数据准备、对比、聚合和运行编排；pytest 入口只负责把 CLI 参数转换为 `PartitionedAccuracyRequest` 并调用新 runner。旧 direct/cached 的业务能力迁移到统一 runner，低层复用现有 `DBAccuracyReaderService`、`BinanceSourceService`、`rows_to_normalized_frame()` 和 `DataComPyCompareService`。

**Tech Stack:** Python 3.12、pytest、Polars/Parquet、DataComPy、现有 QuestTest DB accuracy services、pytest CLI hooks、Allure attachments。

---

## 执行前约束

- 先运行 `git status --short`，记录已有用户改动。当前工作树可能已有 `AGENTS.md`、`README.md`、历史 plan 删除和 `reports/` 目录变化，不要回滚这些无关改动。
- 不修改 `infrastructure/`。DB 查询继续通过现有 `infrastructure.database.db_client.DBClient` 间接使用。
- 使用 `/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12` 跑测试。
- 使用 `apply_patch` 做手工代码编辑。
- 本计划要求一次性交付完整用户可见能力，但实现按任务小步提交，避免并发、缓存和报告逻辑混在一个不可审查的大改里。
- 长期保留目录只允许是 `.cache/binance_accuracy/db/`、`.cache/binance_accuracy/source/`、`.cache/binance_accuracy/compare/`、`.cache/binance_accuracy/runs/`。临时目录 `_tmp/` 在单分区完成、失败、取消、运行结束、下次启动时都要清理。

## 文件结构

### 新增文件

- `services/db_accuracy/partitioned/__init__.py`
  - 暴露统一 runner 相关服务。
- `services/db_accuracy/partitioned/models.py`
  - 定义 `PartitionedAccuracyRequest`、`ExecutionOptions`、`PartitionTask`、`CachePolicy`、`CacheManifest`、`CompareManifest`、`PartitionExecutionResult`、`PartitionedRunResult`、`RunPauseReason`。
- `services/db_accuracy/partitioned/cache_store_service.py`
  - 负责 DB/source/compare/runs 路径、manifest 读写、Parquet 读写、原子写入、临时目录清理、缓存覆盖判断、范围过滤。
- `services/db_accuracy/partitioned/planner_service.py`
  - 把 direct/cached 请求转换为 `PartitionTask` 列表；支持 direct 自动 DB 范围发现、显式 start/end、market filters、registry 特殊分区。
- `services/db_accuracy/partitioned/db_data_service.py`
  - 准备 DB 分区数据：命中缓存则读取并按本次范围过滤，未命中则查 DB 并写缓存。
- `services/db_accuracy/partitioned/source_data_service.py`
  - 准备 Binance source 分区数据：命中缓存则读取并过滤，未命中则按 request window 拉取，带 5 次重试，网络失败清理正式/临时文件并抛出暂停信号。
- `services/db_accuracy/partitioned/compare_data_service.py`
  - 读取 DB/source frame，在数据全部准备完成后执行 DataComPy，对比结果写入 compare artifact 和 manifest；范围完全一致且输入 fingerprint 匹配时复用 compare。
- `services/db_accuracy/partitioned/aggregation_service.py`
  - 聚合历史和本次 compare manifest，生成 run `summary.txt` / `summary.json`，并构造 Allure JSON payload。
- `services/db_accuracy/partitioned/runner_service.py`
  - 三阶段编排：规划任务 -> 准备所有 DB/source 数据 -> 统一对比 -> 聚合报告；支持并发、source 失败取消、运行状态返回。

### 修改文件

- `tests/conftest.py`
  - 删除 `--db-accuracy-refresh-cache`。
  - 新增 `--db-accuracy-use-db-cache true|false`、`--db-accuracy-use-source-cache true|false`、`--db-accuracy-workers`、`--db-accuracy-source-retries`、`--db-accuracy-source-retry-backoff-ms`、`--db-accuracy-stop-on-source-failure true|false`。
- `tests/db_accuracy/integration/test_binance_db_accuracy.py`
  - 使用 `PartitionedAccuracyService` 替代 direct/cached 分支里的 `DirectAccuracyService` 和 `CachedAccuracyService`。
  - 构造 `PartitionedAccuracyRequest`。
  - Allure 附件统一使用 partitioned run summary/details。
- `services/db_accuracy/direct/accuracy_service.py`
  - 保留 `compare_db_and_source_rows()`、`compare_registry_rows()` 等纯比较函数。
  - `DirectAccuracyService` 不再作为 pytest 主入口，可改为轻量 wrapper 或保留给旧单元测试。
- `services/db_accuracy/cached/cached_accuracy_service.py`
  - 不再作为 pytest 主入口。现有低层 helper 可保留；旧 runner 测试改写到 partitioned runner。
- `services/db_accuracy/cached/cache_models.py`
  - 保留现有低层测试所需模型，或者把仍有价值的 `MarketShard`、`TimePartition` 迁移/复用到 `partitioned.models`。最终避免新代码依赖 `refresh_cache` 字段。
- `services/db_accuracy/reporting/result_serializer_service.py`
  - 新增 `partitioned_to_json(result)`，保留 direct/cached serializer 供历史测试使用。
- `docs/binance_db_accuracy_validation.md`
  - 更新新参数、新执行流程、缓存命中规则、断点续跑、失败清理、报告路径。
- `README.md`
  - 更新 DB accuracy 快速命令中的参数说明。

### 新增测试文件

- `tests/db_accuracy/services/test_partitioned_cache_store_service.py`
- `tests/db_accuracy/services/test_partitioned_planner_service.py`
- `tests/db_accuracy/services/test_partitioned_db_data_service.py`
- `tests/db_accuracy/services/test_partitioned_source_data_service.py`
- `tests/db_accuracy/services/test_partitioned_compare_data_service.py`
- `tests/db_accuracy/services/test_partitioned_runner_service.py`
- `tests/db_accuracy/services/test_partitioned_aggregation_service.py`

### 修改测试文件

- `tests/db_accuracy/services/test_cli_options.py`
  - 旧 refresh-cache 断言改为新 cache booleans 和并发/重试参数。
  - 增加旧参数 unknown option 测试。
- `tests/db_accuracy/services/test_cached_accuracy_service.py`
  - 将覆盖 runner 行为的测试迁移到 `test_partitioned_runner_service.py`。
- `tests/db_accuracy/services/test_cached_source_service.py`
  - 只保留仍用于低层 source cache helper 的测试；新增的 retry/cleanup 语义放到 partitioned source data service 测试。
- `tests/db_accuracy/services/test_cache_store_service.py`
  - 保留现有 cached store 测试或改为覆盖新 partitioned store。

---

## Task 1: 定义统一模型与缓存路径

**Files:**
- Create: `services/db_accuracy/partitioned/__init__.py`
- Create: `services/db_accuracy/partitioned/models.py`
- Create: `services/db_accuracy/partitioned/cache_store_service.py`
- Create: `tests/db_accuracy/services/test_partitioned_cache_store_service.py`

- [ ] **Step 1: 写缓存路径和 manifest 的失败测试**

Create `tests/db_accuracy/services/test_partitioned_cache_store_service.py` with these tests:

```python
from __future__ import annotations

from pathlib import Path

import polars as pl

from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService
from services.db_accuracy.partitioned.models import (
    CacheManifest,
    CacheSide,
    CacheStatus,
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


def _manifest(start_ms: int, end_ms: int, row_count: int = 1) -> CacheManifest:
    return CacheManifest(
        schema_version=1,
        side=CacheSide.DB,
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        market_key={"symbol": "BTCUSDT", "interval": "1m"},
        start_ms=start_ms,
        end_ms=end_ms,
        status=CacheStatus.COMPLETE,
        row_count=row_count,
        fingerprint=DataFingerprint(row_count=row_count, content_hash="abc"),
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

    store.write_data_frame(paths, frame, _manifest(large_task.start_ms, large_task.end_ms, row_count=3))

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
    store.write_data_frame(paths, frame, _manifest(cached_task.start_ms, cached_task.end_ms))

    assert store.find_covering_data_cache(CacheSide.DB, requested_task) is None


def test_store_cleans_tmp_files_without_removing_formal_cache(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    task = _task()
    paths = store.data_paths(CacheSide.DB, task)
    frame = pl.DataFrame({"timestamp": ["1704067200000"]})
    store.write_data_frame(paths, frame, _manifest(task.start_ms, task.end_ms))
    tmp_file = store.tmp_root / "run-1" / "leftover.tmp"
    tmp_file.parent.mkdir(parents=True)
    tmp_file.write_text("partial", encoding="utf-8")

    store.cleanup_tmp()

    assert not tmp_file.exists()
    assert paths.data_path.exists()
    assert paths.manifest_path.exists()
```

- [ ] **Step 2: Run the failing cache store tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_cache_store_service.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'services.db_accuracy.partitioned'`.

- [ ] **Step 3: Implement `models.py`**

Create `services/db_accuracy/partitioned/models.py`:

```python
"""统一分区 DB accuracy 执行模型。"""

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
    def from_dict(cls, payload: dict[str, Any]) -> "CacheManifest":
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
    def from_dict(cls, payload: dict[str, Any]) -> "CompareManifest":
        data = dict(payload)
        data["status"] = CompareStatus(data["status"])
        db_fingerprint = data.get("db_fingerprint")
        source_fingerprint = data.get("source_fingerprint")
        data["db_fingerprint"] = DataFingerprint(**db_fingerprint) if db_fingerprint else None
        data["source_fingerprint"] = DataFingerprint(**source_fingerprint) if source_fingerprint else None
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
```

- [ ] **Step 4: Implement `cache_store_service.py`**

Create `services/db_accuracy/partitioned/cache_store_service.py`:

```python
"""统一分区缓存读写服务。"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import polars as pl

from services.db_accuracy.partitioned.models import (
    CacheManifest,
    CacheSide,
    CacheStatus,
    CompareManifest,
    DataFingerprint,
    PartitionTask,
)


@dataclass(frozen=True)
class DataCachePaths:
    data_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class CompareCachePaths:
    report_path: Path
    diff_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class DataCacheHit:
    paths: DataCachePaths
    manifest: CacheManifest


class PartitionedCacheStoreService:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.tmp_root = self.root / "_tmp"

    def data_paths(self, side: CacheSide, task: PartitionTask) -> DataCachePaths:
        directory = self.root / side.value
        for part in task.path_parts:
            directory = directory / part
        return DataCachePaths(
            data_path=directory / "data.parquet",
            manifest_path=directory / "manifest.json",
        )

    def compare_paths(self, task: PartitionTask) -> CompareCachePaths:
        directory = self.root / "compare"
        for part in task.path_parts:
            directory = directory / part
        return CompareCachePaths(
            report_path=directory / "report.txt",
            diff_path=directory / "diff.json",
            manifest_path=directory / "manifest.json",
        )

    def run_root(self, run_id: str) -> Path:
        return self.root / "runs" / f"run_id={run_id}"

    def read_data_manifest(self, paths: DataCachePaths) -> CacheManifest | None:
        if not paths.manifest_path.exists():
            return None
        payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        return CacheManifest.from_dict(payload)

    def read_compare_manifest(self, paths: CompareCachePaths) -> CompareManifest | None:
        if not paths.manifest_path.exists():
            return None
        payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        return CompareManifest.from_dict(payload)

    def find_covering_data_cache(self, side: CacheSide, task: PartitionTask) -> DataCacheHit | None:
        exact_paths = self.data_paths(side, task)
        exact_manifest = self.read_data_manifest(exact_paths)
        if exact_manifest is not None and exact_manifest.reusable_for(task):
            if exact_manifest.status == CacheStatus.COMPLETE and not exact_paths.data_path.exists():
                return None
            return DataCacheHit(paths=exact_paths, manifest=exact_manifest)

        base = self.root / side.value
        if not base.exists():
            return None
        for manifest_path in sorted(base.glob("**/manifest.json")):
            paths = DataCachePaths(
                data_path=manifest_path.parent / "data.parquet",
                manifest_path=manifest_path,
            )
            manifest = self.read_data_manifest(paths)
            if manifest is None or not manifest.reusable_for(task):
                continue
            if manifest.status == CacheStatus.COMPLETE and not paths.data_path.exists():
                continue
            return DataCacheHit(paths=paths, manifest=manifest)
        return None

    def read_data_frame(
        self,
        paths: DataCachePaths,
        task: PartitionTask,
        time_field: str | None,
    ) -> pl.DataFrame:
        manifest = self.read_data_manifest(paths)
        if manifest is not None and manifest.status == CacheStatus.EMPTY:
            return pl.DataFrame()
        if not paths.data_path.exists():
            return pl.DataFrame()
        frame = pl.read_parquet(paths.data_path)
        if task.is_registry or time_field is None or frame.is_empty():
            return frame
        return frame.filter(
            pl.col(time_field).cast(pl.Int64) >= int(task.start_ms),
            pl.col(time_field).cast(pl.Int64) <= int(task.end_ms),
        )

    def write_data_frame(
        self,
        paths: DataCachePaths,
        frame: pl.DataFrame,
        manifest: CacheManifest,
    ) -> None:
        paths.data_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = self._tmp_dir()
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_data = tmp_dir / "data.parquet"
        tmp_manifest = tmp_dir / "manifest.json"
        try:
            if manifest.status == CacheStatus.COMPLETE:
                frame.write_parquet(tmp_data)
                tmp_data.replace(paths.data_path)
            elif paths.data_path.exists():
                paths.data_path.unlink()
            tmp_manifest.write_text(
                json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_manifest.replace(paths.manifest_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def write_compare_artifacts(
        self,
        paths: CompareCachePaths,
        report_text: str,
        diff_json: str,
        manifest: CompareManifest,
    ) -> None:
        paths.report_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = self._tmp_dir()
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_report = tmp_dir / "report.txt"
        tmp_diff = tmp_dir / "diff.json"
        tmp_manifest = tmp_dir / "manifest.json"
        try:
            tmp_report.write_text(report_text, encoding="utf-8")
            tmp_diff.write_text(diff_json, encoding="utf-8")
            tmp_manifest.write_text(
                json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_report.replace(paths.report_path)
            tmp_diff.replace(paths.diff_path)
            tmp_manifest.replace(paths.manifest_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def clear_data_cache(self, side: CacheSide, task: PartitionTask) -> None:
        paths = self.data_paths(side, task)
        for path in (paths.data_path, paths.manifest_path):
            if path.exists():
                path.unlink()

    def cleanup_tmp(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def relative_to_root(self, path: Path | None) -> str | None:
        if path is None:
            return None
        return str(path.relative_to(self.root))

    def _tmp_dir(self) -> Path:
        return self.tmp_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") / uuid4().hex


def fingerprint_frame(frame: pl.DataFrame) -> DataFingerprint:
    payload = frame.write_json()
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return DataFingerprint(row_count=frame.height, content_hash=digest)
```

- [ ] **Step 5: Add package exports**

Create `services/db_accuracy/partitioned/__init__.py`:

```python
"""统一分区 DB accuracy runner。"""
```

- [ ] **Step 6: Run cache store tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_cache_store_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add services/db_accuracy/partitioned/__init__.py \
  services/db_accuracy/partitioned/models.py \
  services/db_accuracy/partitioned/cache_store_service.py \
  tests/db_accuracy/services/test_partitioned_cache_store_service.py
git commit -m "feat: add partitioned accuracy cache models"
```

---

## Task 2: 实现分区任务规划

**Files:**
- Create: `services/db_accuracy/partitioned/planner_service.py`
- Create: `tests/db_accuracy/services/test_partitioned_planner_service.py`

- [ ] **Step 1: Write planner tests**

Create `tests/db_accuracy/services/test_partitioned_planner_service.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.db_accuracy.models import TableSpec
from services.db_accuracy.partitioned.models import AccuracyMode, PartitionedAccuracyRequest
from services.db_accuracy.partitioned.planner_service import PartitionPlannerService


class FakeDB:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        if sql.startswith("SHOW COLUMNS"):
            return [
                {"Field": "symbol"},
                {"Field": "interval"},
                {"Field": "timestamp"},
                {"Field": "open"},
                {"Field": "close"},
            ]
        if "MIN(" in sql and "MAX(" in sql:
            return [
                {
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "min_time_ms": 1704067200000,
                    "max_time_ms": 1704239999999,
                }
            ]
        if "GROUP BY" in sql:
            return [{"symbol": "BTCUSDT", "interval": "1m"}]
        return []


def _kline_spec() -> TableSpec:
    return TableSpec(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
    )


def _registry_spec() -> TableSpec:
    return TableSpec(
        table="binance_futures_symbols",
        kind="registry",
        endpoint="usdm_exchange_info",
        key_fields=("symbol",),
        time_fields=(),
        interval_field=None,
        compare_fields=("symbol", "status"),
        request_limit=1000,
    )


def test_direct_without_range_discovers_db_ranges_and_splits_partitions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    planner = PartitionPlannerService(FakeDB())

    tasks = planner.plan(
        PartitionedAccuracyRequest(
            mode=AccuracyMode.DIRECT,
            tables=("binance_kline_all_future_raw",),
            cache_root=tmp_path,
            partition_days=1,
        )
    )

    assert [(task.start_ms, task.end_ms, task.partition_bucket) for task in tasks] == [
        (1704067200000, 1704153599999, "date=2024-01-01"),
        (1704153600000, 1704239999999, "date=2024-01-02"),
    ]
    assert tasks[0].key_values == {"symbol": "BTCUSDT", "interval": "1m"}


def test_direct_with_explicit_range_and_filters_uses_discovery_filters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = FakeDB()
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    planner = PartitionPlannerService(db)

    tasks = planner.plan(
        PartitionedAccuracyRequest(
            mode=AccuracyMode.DIRECT,
            tables=("binance_kline_all_future_raw",),
            cache_root=tmp_path,
            symbols=("BTCUSDT",),
            intervals=("1m",),
            start_ms=1704110400000,
            end_ms=1704113999999,
        )
    )

    assert len(tasks) == 1
    assert tasks[0].start_ms == 1704110400000
    assert tasks[0].end_ms == 1704113999999
    assert any("GROUP BY `symbol`, `interval`" in sql for sql, _ in db.queries)


def test_cached_mode_requires_single_table_and_explicit_range(tmp_path: Path) -> None:
    planner = PartitionPlannerService(FakeDB())

    with pytest.raises(ValueError, match="cached mode requires exactly one table"):
        planner.plan(
            PartitionedAccuracyRequest(
                mode=AccuracyMode.CACHED,
                tables=(),
                cache_root=tmp_path,
                start_ms=1704067200000,
                end_ms=1704153599999,
            )
        )

    with pytest.raises(ValueError, match="cached mode requires start_ms and end_ms"):
        planner.plan(
            PartitionedAccuracyRequest(
                mode=AccuracyMode.CACHED,
                tables=("binance_kline_all_future_raw",),
                cache_root=tmp_path,
            )
        )


def test_registry_table_becomes_single_registry_partition(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [_registry_spec()],
    )
    planner = PartitionPlannerService(FakeDB())

    tasks = planner.plan(
        PartitionedAccuracyRequest(
            mode=AccuracyMode.DIRECT,
            tables=("binance_futures_symbols",),
            cache_root=tmp_path,
        )
    )

    assert len(tasks) == 1
    assert tasks[0].is_registry is True
    assert tasks[0].start_ms is None
    assert tasks[0].end_ms is None
    assert tasks[0].partition_bucket == "registry"
```

- [ ] **Step 2: Run failing planner tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_planner_service.py -q
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `planner_service`.

- [ ] **Step 3: Implement planner service**

Create `services/db_accuracy/partitioned/planner_service.py`:

```python
"""统一分区任务规划服务。"""

from __future__ import annotations

import time
from typing import Any

from services.db_accuracy.cached.shard_planner_service import split_time_partitions
from services.db_accuracy.db_reader_service import DBAccuracyReaderService
from services.db_accuracy.models import ResolvedTableSpec, TableSpec
from services.db_accuracy.partitioned.models import AccuracyMode, PartitionTask, PartitionedAccuracyRequest
from services.db_accuracy.table_specs import load_table_specs, resolve_spec
from services.db_accuracy.cached.cached_db_reader_service import CachedDBReaderService


class PartitionPlannerService:
    def __init__(self, db: Any):
        self.db = db
        self.reader = DBAccuracyReaderService(db)
        self.cached_reader = CachedDBReaderService(db)

    def plan(self, request: PartitionedAccuracyRequest) -> list[PartitionTask]:
        _validate_request(request)
        specs = _selected_specs(request.tables)
        tasks: list[PartitionTask] = []
        stable_before_ms = int(time.time() * 1000) - request.safety_hours * 3_600_000
        for spec in specs:
            columns = self.reader.table_columns(spec.table)
            resolved = resolve_spec(spec, columns)
            if spec.kind == "registry":
                tasks.append(_registry_task(resolved))
                continue
            if resolved.time_field is None:
                raise ValueError(f"{spec.table} has no time field for partitioned comparison")
            tasks.extend(self._time_series_tasks(resolved, request, stable_before_ms))
        if not tasks:
            raise ValueError("no partition tasks planned")
        return tasks

    def _time_series_tasks(
        self,
        resolved: ResolvedTableSpec,
        request: PartitionedAccuracyRequest,
        stable_before_ms: int,
    ) -> list[PartitionTask]:
        if request.start_ms is None or request.end_ms is None:
            ranges = self.reader.key_ranges(resolved, stable_before_ms)
            ranges = [
                item
                for item in ranges
                if _matches_request_filters(item.key.values, request)
            ][: request.max_shards]
        else:
            keys = self.cached_reader.discover_market_keys(
                table=resolved.spec.table,
                key_fields=resolved.key_fields,
                time_field=_required_time_field(resolved),
                start_ms=request.start_ms,
                end_ms=request.end_ms,
                filters=_discovery_filters(resolved, request),
                limit=request.max_shards,
            )
            ranges = [
                _Range(key_values=key, start_ms=request.start_ms, end_ms=request.end_ms)
                for key in keys
            ]

        tasks: list[PartitionTask] = []
        for item in ranges:
            for partition in split_time_partitions(item.start_ms, item.end_ms, request.partition_days):
                tasks.append(
                    PartitionTask(
                        table=resolved.spec.table,
                        kind=resolved.spec.kind,
                        endpoint=resolved.spec.endpoint,
                        key_values=dict(item.key.values if hasattr(item, "key") else item.key_values),
                        time_field=_required_time_field(resolved),
                        source_time_field=resolved.spec.source_time_field or _required_time_field(resolved),
                        compare_fields=resolved.compare_fields,
                        request_limit=resolved.spec.request_limit,
                        start_ms=partition.start_ms,
                        end_ms=partition.end_ms,
                        partition_label=partition.label,
                        partition_bucket=partition.bucket,
                        is_registry=False,
                        key_fields=resolved.key_fields,
                        interval_field=resolved.interval_field,
                        fixed_interval=resolved.spec.fixed_interval,
                        symbol_field=resolved.spec.symbol_field,
                        pair_field=resolved.spec.pair_field,
                        contract_type_field=resolved.spec.contract_type_field,
                    )
                )
        return tasks


class _Range:
    def __init__(self, key_values: dict[str, Any], start_ms: int, end_ms: int) -> None:
        self.key_values = key_values
        self.start_ms = start_ms
        self.end_ms = end_ms


def _validate_request(request: PartitionedAccuracyRequest) -> None:
    if request.mode == AccuracyMode.CACHED:
        if len(request.tables) != 1:
            raise ValueError("cached mode requires exactly one table")
        if request.start_ms is None or request.end_ms is None:
            raise ValueError("cached mode requires start_ms and end_ms")
    if request.partition_days < 1:
        raise ValueError("partition_days must be >= 1")
    if request.max_shards < 1:
        raise ValueError("max_shards must be >= 1")
    if request.start_ms is not None and request.end_ms is not None and request.end_ms < request.start_ms:
        raise ValueError("end_ms must be greater than or equal to start_ms")


def _selected_specs(tables: tuple[str, ...]) -> list[TableSpec]:
    specs = load_table_specs()
    if not tables:
        return specs
    selected = set(tables)
    configured = {spec.table for spec in specs}
    unknown = sorted(selected - configured)
    if unknown:
        raise ValueError(f"unknown db accuracy table: {','.join(unknown)}")
    return [spec for spec in specs if spec.table in selected]


def _registry_task(resolved: ResolvedTableSpec) -> PartitionTask:
    return PartitionTask(
        table=resolved.spec.table,
        kind=resolved.spec.kind,
        endpoint=resolved.spec.endpoint,
        key_values={},
        time_field=None,
        source_time_field=None,
        compare_fields=resolved.compare_fields,
        request_limit=resolved.spec.request_limit,
        start_ms=None,
        end_ms=None,
        partition_label="registry",
        partition_bucket="registry",
        is_registry=True,
        key_fields=resolved.key_fields,
        symbol_field=resolved.spec.symbol_field,
    )


def _required_time_field(resolved: ResolvedTableSpec) -> str:
    if resolved.time_field is None:
        raise ValueError(f"{resolved.spec.table} has no time field")
    return resolved.time_field


def _matches_request_filters(values: dict[str, Any], request: PartitionedAccuracyRequest) -> bool:
    filters = {
        "symbol": request.symbols,
        "pair": request.pairs,
        "contract_type": request.contract_types,
        "interval": request.intervals,
    }
    for field, candidates in filters.items():
        if candidates and str(values.get(field)) not in {str(item) for item in candidates}:
            return False
    return True


def _discovery_filters(
    resolved: ResolvedTableSpec,
    request: PartitionedAccuracyRequest,
) -> dict[str, Any]:
    candidates = {
        "symbol": request.symbols,
        "pair": request.pairs,
        "contract_type": request.contract_types,
        "interval": request.intervals,
    }
    filters: dict[str, Any] = {}
    for field, values in candidates.items():
        if field not in resolved.key_fields or not values:
            continue
        if len(values) > 1:
            raise ValueError(f"multi-value discovery filters are unsupported: {field}")
        filters[field] = values[0]
    return filters
```

- [ ] **Step 4: Run planner tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_planner_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add services/db_accuracy/partitioned/planner_service.py \
  tests/db_accuracy/services/test_partitioned_planner_service.py
git commit -m "feat: plan partitioned db accuracy tasks"
```

---

## Task 3: 准备 DB 分区缓存

**Files:**
- Create: `services/db_accuracy/partitioned/db_data_service.py`
- Create: `tests/db_accuracy/services/test_partitioned_db_data_service.py`

- [ ] **Step 1: Write DB data service tests**

Create `tests/db_accuracy/services/test_partitioned_db_data_service.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService
from services.db_accuracy.partitioned.db_data_service import PartitionedDBDataService
from services.db_accuracy.partitioned.models import CachePolicy, CacheSide, PartitionTask


class FakeDB:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        if "FROM `binance_kline_all_future_raw`" in sql:
            return [
                {
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "timestamp": 1704067200000,
                    "open": "1",
                    "close": "2",
                }
            ]
        if "FROM `binance_futures_symbols`" in sql:
            return [{"symbol": "BTCUSDT", "status": "TRADING"}]
        return []


def _task() -> PartitionTask:
    return PartitionTask(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_values={"symbol": "BTCUSDT", "interval": "1m"},
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        start_ms=1704067200000,
        end_ms=1704153599999,
        partition_label="1704067200000-1704153599999",
        partition_bucket="date=2024-01-01",
    )


def test_db_service_fetches_missing_partition_and_writes_cache(tmp_path: Path) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedDBDataService(db=db, store=store)

    frame, manifest = service.ensure_db_frame(_task(), CachePolicy(use_db_cache=True))

    assert frame.to_dict(as_series=False)["timestamp"] == ["1704067200000"]
    assert manifest.status.value == "complete"
    assert manifest.row_count == 1
    assert db.queries
    paths = store.data_paths(CacheSide.DB, _task())
    assert paths.data_path.exists()
    assert paths.manifest_path.exists()


def test_db_service_reuses_cache_when_policy_allows(tmp_path: Path) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedDBDataService(db=db, store=store)
    task = _task()
    service.ensure_db_frame(task, CachePolicy(use_db_cache=True))
    db.queries.clear()

    frame, manifest = service.ensure_db_frame(task, CachePolicy(use_db_cache=True))

    assert frame.height == 1
    assert manifest.row_count == 1
    assert db.queries == []


def test_db_service_ignores_cache_when_policy_disallows_reuse(tmp_path: Path) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedDBDataService(db=db, store=store)
    task = _task()
    service.ensure_db_frame(task, CachePolicy(use_db_cache=True))
    db.queries.clear()

    service.ensure_db_frame(task, CachePolicy(use_db_cache=False))

    assert db.queries


def test_db_service_reuses_larger_cache_and_filters_requested_range(tmp_path: Path) -> None:
    db = FakeDB()
    store = PartitionedCacheStoreService(tmp_path)
    large_task = _task()
    small_task = PartitionTask(
        **{
            **large_task.__dict__,
            "start_ms": 1704067200000,
            "end_ms": 1704067200000,
            "partition_label": "1704067200000-1704067200000",
        }
    )
    paths = store.data_paths(CacheSide.DB, large_task)
    frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "interval": ["1m", "1m"],
            "timestamp": ["1704067200000", "1704067260000"],
            "timestamp__compare": ["1704067200000", "1704067260000"],
            "open": ["1", "3"],
            "close": ["2", "4"],
        }
    )
    first_frame, first_manifest = service.ensure_db_frame(large_task, CachePolicy(use_db_cache=True))
    store.write_data_frame(paths, frame, first_manifest)
    db.queries.clear()

    filtered, manifest = service.ensure_db_frame(small_task, CachePolicy(use_db_cache=True))

    assert manifest.row_count == 1
    assert filtered.to_dict(as_series=False)["timestamp"] == ["1704067200000"]
    assert db.queries == []
```

- [ ] **Step 2: Run failing DB data tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_db_data_service.py -q
```

Expected: FAIL with missing `db_data_service`.

- [ ] **Step 3: Implement DB data service**

Create `services/db_accuracy/partitioned/db_data_service.py`:

```python
"""DB 分区数据准备服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl

from services.db_accuracy.cached.cache_models import MarketShard, TimePartition
from services.db_accuracy.cached.cached_db_reader_service import CachedDBReaderService
from services.db_accuracy.cached.frame_normalizer_service import rows_to_normalized_frame
from services.db_accuracy.db_reader_service import DBAccuracyReaderService
from services.db_accuracy.models import ResolvedTableSpec, TableSpec
from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService, fingerprint_frame
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
        self.reader = CachedDBReaderService(db)
        self.registry_reader = DBAccuracyReaderService(db)

    def ensure_db_frame(
        self,
        task: PartitionTask,
        policy: CachePolicy,
    ) -> tuple[pl.DataFrame, CacheManifest]:
        if policy.use_db_cache:
            hit = self.store.find_covering_data_cache(CacheSide.DB, task)
            if hit is not None:
                frame = self.store.read_data_frame(hit.paths, task=task, time_field=task.time_field)
                return frame, _manifest_from_frame(task, frame, hit.manifest.status)

        self.store.clear_data_cache(CacheSide.DB, task)
        frame = self._fetch_db_frame(task)
        status = CacheStatus.EMPTY if frame.is_empty() else CacheStatus.COMPLETE
        manifest = _manifest_from_frame(task, frame, status)
        self.store.write_data_frame(self.store.data_paths(CacheSide.DB, task), frame, manifest)
        return frame, manifest

    def _fetch_db_frame(self, task: PartitionTask) -> pl.DataFrame:
        if task.is_registry:
            spec = ResolvedTableSpec(
                spec=TableSpec(
                    table=task.table,
                    kind=task.kind,
                    endpoint=task.endpoint,
                    key_fields=task.key_columns,
                    time_fields=(),
                    interval_field=None,
                    compare_fields=task.compare_fields,
                    request_limit=task.request_limit,
                    symbol_field=task.symbol_field,
                ),
                columns=(),
                time_field=None,
                interval_field=None,
                compare_fields=task.compare_fields,
                key_fields=task.key_columns,
            )
            return pl.DataFrame(self.registry_reader.registry_rows(spec))

        rows = self.reader.rows_for_partition(_market_shard(task), _time_partition(task))
        return rows_to_normalized_frame(_market_shard(task), rows)


def _market_shard(task: PartitionTask) -> MarketShard:
    if task.time_field is None:
        raise ValueError(f"{task.table} has no time field for time-series DB fetch")
    return MarketShard(
        table=task.table,
        endpoint=task.endpoint,
        kind=task.kind,
        key_values=dict(task.key_values),
        time_field=task.time_field,
        source_time_field=task.source_time_field or task.time_field,
        compare_fields=task.compare_fields,
        request_limit=task.request_limit,
    )


def _time_partition(task: PartitionTask) -> TimePartition:
    if task.start_ms is None or task.end_ms is None:
        raise ValueError(f"{task.table} has no time range for time-series DB fetch")
    return TimePartition(start_ms=task.start_ms, end_ms=task.end_ms)


def _manifest_from_frame(
    task: PartitionTask,
    frame: pl.DataFrame,
    status: CacheStatus,
) -> CacheManifest:
    artifact_path = None if status == CacheStatus.EMPTY else _artifact_path(CacheSide.DB, task)
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
        fingerprint=fingerprint_frame(frame),
        error_type=None,
        error_message=None,
        artifact_path=artifact_path,
        created_at_utc=datetime.now(UTC).isoformat(),
    )


def _artifact_path(side: CacheSide, task: PartitionTask) -> str:
    parts = [side.value, *task.path_parts, "data.parquet"]
    return "/".join(parts)
```

- [ ] **Step 4: Run DB data tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_db_data_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add services/db_accuracy/partitioned/db_data_service.py \
  tests/db_accuracy/services/test_partitioned_db_data_service.py
git commit -m "feat: cache partitioned db data"
```

---

## Task 4: 准备 source 分区缓存、重试和失败清理

**Files:**
- Create: `services/db_accuracy/partitioned/source_data_service.py`
- Create: `tests/db_accuracy/services/test_partitioned_source_data_service.py`

- [ ] **Step 1: Write source service tests**

Create `tests/db_accuracy/services/test_partitioned_source_data_service.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from services.db_accuracy.models import SourceRow, TableSpec
from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService
from services.db_accuracy.partitioned.models import CachePolicy, CacheSide, ExecutionOptions, PartitionTask
from services.db_accuracy.partitioned.source_data_service import PartitionedSourceDataService, SourceRequestFailed


class FlakySource:
    def __init__(self, failures_before_success: int, error: Exception | None = None) -> None:
        self.failures_before_success = failures_before_success
        self.error = error or RuntimeError("network down")
        self.calls = 0

    def fetch_rows(self, spec: TableSpec, key, start_ms: int, end_ms: int) -> list[SourceRow]:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise self.error
        return [
            SourceRow(
                key=start_ms,
                fields={"timestamp": start_ms, "open": "1", "close": "2"},
            )
        ]


def _task() -> PartitionTask:
    return PartitionTask(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_values={"symbol": "BTCUSDT", "interval": "1m"},
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        start_ms=1704067200000,
        end_ms=1704067259999,
        partition_label="1704067200000-1704067259999",
        partition_bucket="date=2024-01-01",
    )


def test_source_service_retries_and_succeeds_on_fifth_attempt(tmp_path: Path) -> None:
    source = FlakySource(failures_before_success=4)
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedSourceDataService(store=store, source=source)

    frame, manifest = service.ensure_source_frame(
        _task(),
        CachePolicy(use_source_cache=True),
        ExecutionOptions(source_retries=5, source_retry_backoff_ms=0),
    )

    assert source.calls == 5
    assert manifest.status.value == "complete"
    assert frame.height == 1
    assert store.data_paths(CacheSide.SOURCE, _task()).data_path.exists()


def test_source_service_clears_formal_cache_and_raises_after_retries(tmp_path: Path) -> None:
    source = FlakySource(failures_before_success=99)
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedSourceDataService(store=store, source=source)
    task = _task()

    with pytest.raises(SourceRequestFailed, match="network down"):
        service.ensure_source_frame(
            task,
            CachePolicy(use_source_cache=True),
            ExecutionOptions(source_retries=5, source_retry_backoff_ms=0),
        )

    paths = store.data_paths(CacheSide.SOURCE, task)
    assert source.calls == 5
    assert not paths.data_path.exists()
    assert not paths.manifest_path.exists()


def test_source_service_reuses_complete_cache_when_policy_allows(tmp_path: Path) -> None:
    source = FlakySource(failures_before_success=0)
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedSourceDataService(store=store, source=source)
    task = _task()
    service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=True),
        ExecutionOptions(source_retries=5, source_retry_backoff_ms=0),
    )
    source.calls = 0

    frame, manifest = service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=True),
        ExecutionOptions(source_retries=5, source_retry_backoff_ms=0),
    )

    assert frame.height == 1
    assert manifest.status.value == "complete"
    assert source.calls == 0


def test_source_service_refetches_when_policy_disallows_reuse(tmp_path: Path) -> None:
    source = FlakySource(failures_before_success=0)
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedSourceDataService(store=store, source=source)
    task = _task()
    service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=True),
        ExecutionOptions(source_retries=5, source_retry_backoff_ms=0),
    )
    source.calls = 0

    service.ensure_source_frame(
        task,
        CachePolicy(use_source_cache=False),
        ExecutionOptions(source_retries=5, source_retry_backoff_ms=0),
    )

    assert source.calls == 1
```

- [ ] **Step 2: Run failing source tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_source_data_service.py -q
```

Expected: FAIL with missing `source_data_service`.

- [ ] **Step 3: Implement source service**

Create `services/db_accuracy/partitioned/source_data_service.py`:

```python
"""Binance source 分区数据准备服务。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import polars as pl

from services.db_accuracy.cached.cache_models import MarketShard, TimePartition
from services.db_accuracy.cached.frame_normalizer_service import source_rows_to_normalized_frame
from services.db_accuracy.db_reader_service import DBAccuracyReaderService
from services.db_accuracy.models import KeyTimeRange, ResolvedTableSpec, TableSpec, ValidationKey
from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService, fingerprint_frame
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
    def __init__(self, task: PartitionTask, message: str):
        super().__init__(message)
        self.task = task


class PartitionedSourceDataService:
    def __init__(self, store: PartitionedCacheStoreService, source: Any = None):
        self.store = store
        self.source = source if source is not None else BinanceSourceService()

    def ensure_source_frame(
        self,
        task: PartitionTask,
        policy: CachePolicy,
        execution: ExecutionOptions,
    ) -> tuple[pl.DataFrame, CacheManifest]:
        if policy.use_source_cache:
            hit = self.store.find_covering_data_cache(CacheSide.SOURCE, task)
            if hit is not None:
                frame = self.store.read_data_frame(hit.paths, task=task, time_field=task.time_field)
                return frame, _manifest_from_frame(task, frame, hit.manifest.status)

        self.store.clear_data_cache(CacheSide.SOURCE, task)
        try:
            frame = self._fetch_source_frame(task, execution)
        except Exception as exc:
            self.store.clear_data_cache(CacheSide.SOURCE, task)
            self.store.cleanup_tmp()
            raise SourceRequestFailed(task, str(exc)) from exc

        status = CacheStatus.EMPTY if frame.is_empty() else CacheStatus.COMPLETE
        manifest = _manifest_from_frame(task, frame, status)
        self.store.write_data_frame(self.store.data_paths(CacheSide.SOURCE, task), frame, manifest)
        return frame, manifest

    def _fetch_source_frame(
        self,
        task: PartitionTask,
        execution: ExecutionOptions,
    ) -> pl.DataFrame:
        if task.is_registry:
            rows = _fetch_with_retries(
                lambda: self.source.fetch_registry_rows(_table_spec(task)),
                retries=execution.source_retries,
                backoff_ms=execution.source_retry_backoff_ms,
            )
            return pl.DataFrame([row.fields for row in rows])

        rows = []
        spec = _table_spec(task)
        key = ValidationKey(dict(task.key_values))
        for start_ms, end_ms in _source_windows(spec, task, key):
            rows.extend(
                _fetch_with_retries(
                    lambda start=start_ms, end=end_ms: self.source.fetch_rows(spec, key, start, end),
                    retries=execution.source_retries,
                    backoff_ms=execution.source_retry_backoff_ms,
                )
            )
        if not rows:
            return _empty_frame(task)
        return source_rows_to_normalized_frame(_market_shard(task), rows)


def _fetch_with_retries(call, retries: int, backoff_ms: int):
    attempts = max(retries, 1)
    last_exc: Exception | None = None
    for index in range(attempts):
        try:
            return call()
        except Exception as exc:
            last_exc = exc
            if index < attempts - 1 and backoff_ms > 0:
                time.sleep((backoff_ms / 1000) * (index + 1))
    if last_exc is None:
        raise RuntimeError("source request failed without exception")
    raise last_exc


def _table_spec(task: PartitionTask) -> TableSpec:
    return TableSpec(
        table=task.table,
        kind=task.kind,
        endpoint=task.endpoint,
        key_fields=task.key_columns,
        time_fields=tuple(field for field in (task.time_field,) if field),
        interval_field=task.interval_field,
        compare_fields=task.compare_fields,
        request_limit=task.request_limit,
        fixed_interval=task.fixed_interval,
        symbol_field=task.symbol_field,
        pair_field=task.pair_field,
        contract_type_field=task.contract_type_field,
        source_time_field=task.source_time_field,
    )


def _market_shard(task: PartitionTask) -> MarketShard:
    if task.time_field is None:
        raise ValueError(f"{task.table} has no time field for source fetch")
    return MarketShard(
        table=task.table,
        endpoint=task.endpoint,
        kind=task.kind,
        key_values=dict(task.key_values),
        time_field=task.time_field,
        source_time_field=task.source_time_field or task.time_field,
        compare_fields=task.compare_fields,
        request_limit=task.request_limit,
    )


def _source_windows(
    spec: TableSpec,
    task: PartitionTask,
    key: ValidationKey,
) -> list[tuple[int, int]]:
    if task.time_field is None or task.start_ms is None or task.end_ms is None:
        raise ValueError(f"{task.table} has no time range for source fetch")
    resolved = ResolvedTableSpec(
        spec=spec,
        columns=(),
        time_field=task.time_field,
        interval_field=spec.interval_field,
        compare_fields=task.compare_fields,
        key_fields=task.key_columns,
    )
    time_range = KeyTimeRange(
        table=task.table,
        key=key,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
    )
    return [
        (window.start_ms, window.end_ms)
        for window in DBAccuracyReaderService(db=None).build_windows(resolved, time_range)
    ]


def _empty_frame(task: PartitionTask) -> pl.DataFrame:
    return pl.DataFrame(schema={column: pl.String for column in task.join_columns})


def _manifest_from_frame(
    task: PartitionTask,
    frame: pl.DataFrame,
    status: CacheStatus,
) -> CacheManifest:
    artifact_path = None if status == CacheStatus.EMPTY else "/".join([CacheSide.SOURCE.value, *task.path_parts, "data.parquet"])
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
        fingerprint=fingerprint_frame(frame),
        error_type=None,
        error_message=None,
        artifact_path=artifact_path,
        created_at_utc=datetime.now(UTC).isoformat(),
    )
```

- [ ] **Step 4: Run source tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_source_data_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add services/db_accuracy/partitioned/source_data_service.py \
  tests/db_accuracy/services/test_partitioned_source_data_service.py
git commit -m "feat: cache source partitions with retry cleanup"
```

---

## Task 5: 实现 compare 缓存和聚合报告

**Files:**
- Create: `services/db_accuracy/partitioned/compare_data_service.py`
- Create: `services/db_accuracy/partitioned/aggregation_service.py`
- Create: `tests/db_accuracy/services/test_partitioned_compare_data_service.py`
- Create: `tests/db_accuracy/services/test_partitioned_aggregation_service.py`
- Modify: `services/db_accuracy/reporting/result_serializer_service.py`

- [ ] **Step 1: Write compare service tests**

Create `tests/db_accuracy/services/test_partitioned_compare_data_service.py`:

```python
from __future__ import annotations

from pathlib import Path

import polars as pl

from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService, fingerprint_frame
from services.db_accuracy.partitioned.compare_data_service import PartitionedCompareDataService
from services.db_accuracy.partitioned.models import PartitionTask


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
    )


def _frame(close: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "timestamp__compare": ["1704067200000"],
            "open": ["1"],
            "close": [close],
        }
    )


def test_compare_service_writes_passed_manifest_and_artifacts(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedCompareDataService(store)
    task = _task()
    db_frame = _frame("2")
    source_frame = _frame("2")

    manifest = service.ensure_compare(task, db_frame, source_frame, fingerprint_frame(db_frame), fingerprint_frame(source_frame))

    paths = store.compare_paths(task)
    assert manifest.status.value == "passed"
    assert manifest.differences == 0
    assert paths.report_path.exists()
    assert paths.diff_path.exists()
    assert paths.manifest_path.exists()


def test_compare_service_writes_difference_manifest_as_complete_result(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedCompareDataService(store)
    task = _task()
    db_frame = _frame("2")
    source_frame = _frame("3")

    manifest = service.ensure_compare(task, db_frame, source_frame, fingerprint_frame(db_frame), fingerprint_frame(source_frame))

    assert manifest.status.value == "failed_with_differences"
    assert manifest.differences == 1
    assert manifest.complete is True


def test_compare_service_reuses_only_exact_range_and_fingerprint(tmp_path: Path) -> None:
    store = PartitionedCacheStoreService(tmp_path)
    service = PartitionedCompareDataService(store)
    task = _task()
    db_frame = _frame("2")
    source_frame = _frame("2")
    db_fp = fingerprint_frame(db_frame)
    source_fp = fingerprint_frame(source_frame)
    first = service.ensure_compare(task, db_frame, source_frame, db_fp, source_fp)
    changed_task = _task(start_ms=1704067200000, end_ms=1704067200000)

    second = service.ensure_compare(changed_task, db_frame, source_frame, db_fp, source_fp)

    assert first.start_ms != second.end_ms
    assert second.start_ms == changed_task.start_ms
    assert second.end_ms == changed_task.end_ms
```

- [ ] **Step 2: Write aggregation tests**

Create `tests/db_accuracy/services/test_partitioned_aggregation_service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from services.db_accuracy.partitioned.aggregation_service import PartitionedAggregationService
from services.db_accuracy.partitioned.models import (
    SCHEMA_VERSION,
    CompareManifest,
    CompareStatus,
    DataFingerprint,
    PartitionTask,
)


def _task(symbol: str) -> PartitionTask:
    return PartitionTask(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_values={"symbol": symbol, "interval": "1m"},
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        start_ms=1704067200000,
        end_ms=1704153599999,
        partition_label="1704067200000-1704153599999",
        partition_bucket="date=2024-01-01",
    )


def _manifest(task: PartitionTask, differences: int) -> CompareManifest:
    return CompareManifest(
        schema_version=SCHEMA_VERSION,
        table=task.table,
        endpoint=task.endpoint,
        market_key=dict(task.key_values),
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        status=CompareStatus.PASSED if differences == 0 else CompareStatus.FAILED_WITH_DIFFERENCES,
        db_fingerprint=DataFingerprint(row_count=1, content_hash=f"db-{task.label}"),
        source_fingerprint=DataFingerprint(row_count=1, content_hash=f"source-{task.label}"),
        db_rows=1,
        source_rows=1,
        differences=differences,
        report_path=f"compare/{task.label}/report.txt",
        diff_path=f"compare/{task.label}/diff.json",
        message=None,
        created_at_utc=datetime.now(UTC).isoformat(),
    )


def test_aggregation_includes_all_complete_manifests_for_current_tasks(tmp_path: Path) -> None:
    service = PartitionedAggregationService(tmp_path)
    tasks = [_task("BTCUSDT"), _task("ETHUSDT")]
    manifests = [_manifest(tasks[0], 0), _manifest(tasks[1], 2)]

    result = service.aggregate(run_id="run-1", tasks=tasks, manifests=manifests, pause_reason=None)

    assert result.status.value == "completed_with_differences"
    assert result.tasks_total == 2
    assert result.tasks_compared == 2
    assert result.tasks_with_differences == 1
    assert result.differences == 2
    assert "differences=2" in result.summary_text
    assert (tmp_path / "runs" / "run_id=run-1" / "summary.json").exists()
    assert (tmp_path / "runs" / "run_id=run-1" / "summary.txt").exists()
```

- [ ] **Step 3: Run failing compare/aggregation tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/db_accuracy/services/test_partitioned_compare_data_service.py \
  tests/db_accuracy/services/test_partitioned_aggregation_service.py \
  -q
```

Expected: FAIL with missing services.

- [ ] **Step 4: Implement compare service**

Create `services/db_accuracy/partitioned/compare_data_service.py`:

```python
"""分区对比缓存服务。"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from services.db_accuracy.cached.datacompy_service import DataComPyCompareService
from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService
from services.db_accuracy.partitioned.models import (
    SCHEMA_VERSION,
    CompareManifest,
    CompareStatus,
    DataFingerprint,
    PartitionTask,
)


class PartitionedCompareDataService:
    def __init__(self, store: PartitionedCacheStoreService):
        self.store = store

    def ensure_compare(
        self,
        task: PartitionTask,
        db_frame: pl.DataFrame,
        source_frame: pl.DataFrame,
        db_fingerprint: DataFingerprint | None,
        source_fingerprint: DataFingerprint | None,
    ) -> CompareManifest:
        paths = self.store.compare_paths(task)
        existing = self.store.read_compare_manifest(paths)
        if existing is not None and existing.reusable_for(task, db_fingerprint, source_fingerprint):
            return existing

        engine = DataComPyCompareService(report_root=paths.report_path.parent)
        shard_result = engine.compare(
            shard_label=task.label,
            partition_label=task.partition_label,
            db_frame=db_frame,
            source_frame=source_frame,
            join_columns=task.join_columns,
        )
        generated_report = paths.report_path.parent / str(shard_result.report_path)
        generated_diff = paths.diff_path.parent / str(shard_result.diff_path)
        status = (
            CompareStatus.PASSED
            if shard_result.differences == 0
            else CompareStatus.FAILED_WITH_DIFFERENCES
        )
        manifest = CompareManifest(
            schema_version=SCHEMA_VERSION,
            table=task.table,
            endpoint=task.endpoint,
            market_key=dict(task.key_values),
            start_ms=task.start_ms,
            end_ms=task.end_ms,
            status=status,
            db_fingerprint=db_fingerprint,
            source_fingerprint=source_fingerprint,
            db_rows=shard_result.db_rows,
            source_rows=shard_result.source_rows,
            differences=shard_result.differences,
            report_path=self.store.relative_to_root(paths.report_path),
            diff_path=self.store.relative_to_root(paths.diff_path),
            message=shard_result.message,
            created_at_utc=datetime.now(UTC).isoformat(),
        )
        report_text = generated_report.read_text(encoding="utf-8")
        diff_json = generated_diff.read_text(encoding="utf-8")
        self.store.write_compare_artifacts(paths, report_text, diff_json, manifest)
        if generated_report != paths.report_path and generated_report.exists():
            generated_report.unlink()
        if generated_diff != paths.diff_path and generated_diff.exists():
            generated_diff.unlink()
        return manifest
```

- [ ] **Step 5: Implement aggregation service**

Create `services/db_accuracy/partitioned/aggregation_service.py`:

```python
"""分区运行聚合报告服务。"""

from __future__ import annotations

import json
from pathlib import Path

from services.db_accuracy.partitioned.models import (
    CompareManifest,
    CompareStatus,
    PartitionTask,
    PartitionedRunResult,
    RunPauseReason,
    RunStatus,
)


class PartitionedAggregationService:
    def __init__(self, cache_root: Path):
        self.cache_root = Path(cache_root)

    def aggregate(
        self,
        run_id: str,
        tasks: list[PartitionTask],
        manifests: list[CompareManifest],
        pause_reason: RunPauseReason | None,
    ) -> PartitionedRunResult:
        compared = [manifest for manifest in manifests if manifest.complete]
        diff_manifests = [
            manifest
            for manifest in compared
            if manifest.status == CompareStatus.FAILED_WITH_DIFFERENCES
        ]
        differences = sum(manifest.differences for manifest in compared)
        status = _run_status(tasks, compared, diff_manifests, pause_reason)
        details = {
            "status": status.value,
            "tasks_total": len(tasks),
            "tasks_compared": len(compared),
            "tasks_with_differences": len(diff_manifests),
            "db_rows": sum(manifest.db_rows for manifest in compared),
            "source_rows": sum(manifest.source_rows for manifest in compared),
            "differences": differences,
            "pause_reason": None if pause_reason is None else pause_reason.__dict__,
            "partitions": [manifest.to_dict() for manifest in compared],
        }
        summary = _summary_text(details)
        run_root = self.cache_root / "runs" / f"run_id={run_id}"
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "summary.json").write_text(
            json.dumps(details, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (run_root / "summary.txt").write_text(summary, encoding="utf-8")
        return PartitionedRunResult(
            status=status,
            tasks_total=len(tasks),
            tasks_compared=len(compared),
            tasks_with_differences=len(diff_manifests),
            db_rows=details["db_rows"],
            source_rows=details["source_rows"],
            differences=differences,
            summary_text=summary,
            details=details,
            pause_reason=pause_reason,
        )


def _run_status(
    tasks: list[PartitionTask],
    compared: list[CompareManifest],
    diff_manifests: list[CompareManifest],
    pause_reason: RunPauseReason | None,
) -> RunStatus:
    if pause_reason is not None:
        return RunStatus.PAUSED
    if len(compared) < len(tasks):
        return RunStatus.FAILED
    if diff_manifests:
        return RunStatus.COMPLETED_WITH_DIFFERENCES
    return RunStatus.PASSED


def _summary_text(details: dict) -> str:
    pause = details["pause_reason"]
    lines = [
        f"status={details['status']}",
        f"tasks_total={details['tasks_total']}",
        f"tasks_compared={details['tasks_compared']}",
        f"tasks_with_differences={details['tasks_with_differences']}",
        f"db_rows={details['db_rows']}",
        f"source_rows={details['source_rows']}",
        f"differences={details['differences']}",
    ]
    if pause is not None:
        lines.append(f"paused_reason={pause['reason']}")
        lines.append(f"paused_task={pause['task_label']}")
        lines.append(f"paused_message={pause['message']}")
    return "\n".join(lines)
```

- [ ] **Step 6: Add partitioned serializer**

Modify `services/db_accuracy/reporting/result_serializer_service.py` by importing `PartitionedRunResult` and adding:

```python
    @staticmethod
    def partitioned_to_json(result: PartitionedRunResult) -> str:
        """把统一分区 runner 结果转换为稳定的 JSON 字符串。"""
        return json.dumps(result.details, ensure_ascii=False, indent=2, default=str)
```

The import section should include:

```python
from services.db_accuracy.partitioned.models import PartitionedRunResult
```

- [ ] **Step 7: Run compare and aggregation tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/db_accuracy/services/test_partitioned_compare_data_service.py \
  tests/db_accuracy/services/test_partitioned_aggregation_service.py \
  -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

```bash
git add services/db_accuracy/partitioned/compare_data_service.py \
  services/db_accuracy/partitioned/aggregation_service.py \
  services/db_accuracy/reporting/result_serializer_service.py \
  tests/db_accuracy/services/test_partitioned_compare_data_service.py \
  tests/db_accuracy/services/test_partitioned_aggregation_service.py
git commit -m "feat: compare and aggregate partitioned accuracy results"
```

---

## Task 6: 实现三阶段 runner 和并发暂停

**Files:**
- Create: `services/db_accuracy/partitioned/runner_service.py`
- Create: `tests/db_accuracy/services/test_partitioned_runner_service.py`

- [ ] **Step 1: Write runner tests**

Create `tests/db_accuracy/services/test_partitioned_runner_service.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.db_accuracy.models import SourceRow, TableSpec
from services.db_accuracy.partitioned.models import (
    AccuracyMode,
    CachePolicy,
    ExecutionOptions,
    PartitionedAccuracyRequest,
)
from services.db_accuracy.partitioned.runner_service import PartitionedAccuracyService


class FakeDB:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        if sql.startswith("SHOW COLUMNS"):
            return [
                {"Field": "symbol"},
                {"Field": "interval"},
                {"Field": "timestamp"},
                {"Field": "open"},
                {"Field": "close"},
            ]
        if "MIN(" in sql and "MAX(" in sql:
            return [
                {
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "min_time_ms": 1704067200000,
                    "max_time_ms": 1704067259999,
                }
            ]
        if "GROUP BY" in sql:
            return [{"symbol": "BTCUSDT", "interval": "1m"}]
        if "FROM `binance_kline_all_future_raw`" in sql:
            return [
                {
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "timestamp": 1704067200000,
                    "open": "1",
                    "close": "2",
                }
            ]
        return []


class GoodSource:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_rows(self, spec: TableSpec, key, start_ms: int, end_ms: int) -> list[SourceRow]:
        self.calls += 1
        return [
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "1", "close": "2"},
            )
        ]


class FailingSource:
    def fetch_rows(self, spec: TableSpec, key, start_ms: int, end_ms: int) -> list[SourceRow]:
        raise RuntimeError("network down")


def _request(tmp_path: Path, source_retries: int = 5) -> PartitionedAccuracyRequest:
    return PartitionedAccuracyRequest(
        mode=AccuracyMode.DIRECT,
        tables=("binance_kline_all_future_raw",),
        cache_root=tmp_path,
        start_ms=1704067200000,
        end_ms=1704067259999,
        cache_policy=CachePolicy(use_db_cache=True, use_source_cache=True),
        execution=ExecutionOptions(
            workers=4,
            source_retries=source_retries,
            source_retry_backoff_ms=0,
            stop_on_source_failure=True,
        ),
    )


def test_runner_prepares_data_then_compares_successfully(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [
            TableSpec(
                table="binance_kline_all_future_raw",
                kind="kline",
                endpoint="usdm_klines",
                key_fields=("symbol", "interval"),
                time_fields=("timestamp",),
                interval_field="interval",
                compare_fields=("timestamp", "open", "close"),
                request_limit=1000,
            )
        ],
    )
    runner = PartitionedAccuracyService(db=FakeDB(), source=GoodSource())

    result = runner.run(_request(tmp_path))

    assert result.passed
    assert result.tasks_total == 1
    assert result.tasks_compared == 1
    assert result.differences == 0
    assert (tmp_path / "runs").exists()


def test_runner_pauses_on_source_failure_and_leaves_no_source_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [
            TableSpec(
                table="binance_kline_all_future_raw",
                kind="kline",
                endpoint="usdm_klines",
                key_fields=("symbol", "interval"),
                time_fields=("timestamp",),
                interval_field="interval",
                compare_fields=("timestamp", "open", "close"),
                request_limit=1000,
            )
        ],
    )
    runner = PartitionedAccuracyService(db=FakeDB(), source=FailingSource())

    result = runner.run(_request(tmp_path, source_retries=2))

    assert result.status.value == "paused"
    assert result.pause_reason is not None
    assert result.pause_reason.reason == "source_request_failed"
    assert not list((tmp_path / "source").glob("**/data.parquet"))
    assert not list((tmp_path / "source").glob("**/manifest.json"))


def test_runner_reuses_existing_complete_cache_on_second_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.partitioned.planner_service.load_table_specs",
        lambda: [
            TableSpec(
                table="binance_kline_all_future_raw",
                kind="kline",
                endpoint="usdm_klines",
                key_fields=("symbol", "interval"),
                time_fields=("timestamp",),
                interval_field="interval",
                compare_fields=("timestamp", "open", "close"),
                request_limit=1000,
            )
        ],
    )
    db = FakeDB()
    source = GoodSource()
    runner = PartitionedAccuracyService(db=db, source=source)
    runner.run(_request(tmp_path))
    db.queries.clear()
    source.calls = 0

    result = runner.run(_request(tmp_path))

    assert result.passed
    assert source.calls == 0
    assert not any("FROM `binance_kline_all_future_raw`" in sql for sql, _ in db.queries)
```

- [ ] **Step 2: Run failing runner tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_runner_service.py -q
```

Expected: FAIL with missing `runner_service`.

- [ ] **Step 3: Implement runner service**

Create `services/db_accuracy/partitioned/runner_service.py`:

```python
"""统一分区 DB accuracy runner。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from services.db_accuracy.partitioned.aggregation_service import PartitionedAggregationService
from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService
from services.db_accuracy.partitioned.compare_data_service import PartitionedCompareDataService
from services.db_accuracy.partitioned.db_data_service import PartitionedDBDataService
from services.db_accuracy.partitioned.models import (
    CacheManifest,
    CompareManifest,
    PartitionTask,
    PartitionedAccuracyRequest,
    RunPauseReason,
)
from services.db_accuracy.partitioned.planner_service import PartitionPlannerService
from services.db_accuracy.partitioned.source_data_service import PartitionedSourceDataService, SourceRequestFailed


class PartitionedAccuracyService:
    def __init__(self, db: Any = None, source: Any = None):
        if db is None:
            from infrastructure.database.db_client import DBClient

            db = DBClient()
        self.db = db
        self.source = source

    def run(self, request: PartitionedAccuracyRequest):
        store = PartitionedCacheStoreService(request.cache_root)
        store.cleanup_tmp()
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        planner = PartitionPlannerService(self.db)
        tasks = planner.plan(request)
        db_service = PartitionedDBDataService(self.db, store)
        source_service = PartitionedSourceDataService(store, self.source)
        compare_service = PartitionedCompareDataService(store)
        aggregation = PartitionedAggregationService(request.cache_root)

        prepared: dict[str, tuple[PartitionTask, CacheManifest, CacheManifest]] = {}
        pause_reason: RunPauseReason | None = None

        try:
            db_manifests = self._prepare_db(tasks, db_service, request)
            source_manifests, pause_reason = self._prepare_source(tasks, source_service, request)
            if pause_reason is None:
                for task in tasks:
                    prepared[task.label] = (task, db_manifests[task.label], source_manifests[task.label])
                compare_manifests = self._compare_all(prepared, store, compare_service, request)
            else:
                compare_manifests = _historical_complete_manifests(store, tasks)
        finally:
            store.cleanup_tmp()

        all_manifests = _dedupe_compare_manifests([*_historical_complete_manifests(store, tasks), *compare_manifests])
        return aggregation.aggregate(
            run_id=run_id,
            tasks=tasks,
            manifests=all_manifests,
            pause_reason=pause_reason,
        )

    def _prepare_db(
        self,
        tasks: list[PartitionTask],
        db_service: PartitionedDBDataService,
        request: PartitionedAccuracyRequest,
    ) -> dict[str, CacheManifest]:
        output: dict[str, CacheManifest] = {}
        with ThreadPoolExecutor(max_workers=request.execution.workers) as executor:
            futures = {
                executor.submit(db_service.ensure_db_frame, task, request.cache_policy): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                _, manifest = future.result()
                output[task.label] = manifest
        return output

    def _prepare_source(
        self,
        tasks: list[PartitionTask],
        source_service: PartitionedSourceDataService,
        request: PartitionedAccuracyRequest,
    ) -> tuple[dict[str, CacheManifest], RunPauseReason | None]:
        output: dict[str, CacheManifest] = {}
        with ThreadPoolExecutor(max_workers=request.execution.workers) as executor:
            futures = {
                executor.submit(
                    source_service.ensure_source_frame,
                    task,
                    request.cache_policy,
                    request.execution,
                ): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    _, manifest = future.result()
                except SourceRequestFailed as exc:
                    for pending in futures:
                        pending.cancel()
                    return output, RunPauseReason(
                        reason="source_request_failed",
                        task_label=task.label,
                        message=str(exc),
                    )
                output[task.label] = manifest
        return output, None

    def _compare_all(
        self,
        prepared: dict[str, tuple[PartitionTask, CacheManifest, CacheManifest]],
        store: PartitionedCacheStoreService,
        compare_service: PartitionedCompareDataService,
        request: PartitionedAccuracyRequest,
    ) -> list[CompareManifest]:
        manifests: list[CompareManifest] = []
        with ThreadPoolExecutor(max_workers=request.execution.workers) as executor:
            futures = {}
            for task, db_manifest, source_manifest in prepared.values():
                db_hit = store.find_covering_data_cache(db_manifest.side, task)
                source_hit = store.find_covering_data_cache(source_manifest.side, task)
                if db_hit is None or source_hit is None:
                    raise RuntimeError(f"prepared data cache disappeared for {task.label}")
                db_frame = store.read_data_frame(db_hit.paths, task=task, time_field=task.time_field)
                source_frame = store.read_data_frame(source_hit.paths, task=task, time_field=task.time_field)
                futures[
                    executor.submit(
                        compare_service.ensure_compare,
                        task,
                        db_frame,
                        source_frame,
                        db_manifest.fingerprint,
                        source_manifest.fingerprint,
                    )
                ] = task
            for future in as_completed(futures):
                manifests.append(future.result())
        return manifests


def _historical_complete_manifests(
    store: PartitionedCacheStoreService,
    tasks: list[PartitionTask],
) -> list[CompareManifest]:
    manifests: list[CompareManifest] = []
    for task in tasks:
        paths = store.compare_paths(task)
        manifest = store.read_compare_manifest(paths)
        if manifest is not None and manifest.complete:
            manifests.append(manifest)
    return manifests


def _dedupe_compare_manifests(manifests: list[CompareManifest]) -> list[CompareManifest]:
    output: dict[tuple[str, tuple[tuple[str, object], ...], int | None, int | None], CompareManifest] = {}
    for manifest in manifests:
        key = (
            manifest.table,
            tuple(sorted(manifest.market_key.items())),
            manifest.start_ms,
            manifest.end_ms,
        )
        output[key] = manifest
    return list(output.values())
```

- [ ] **Step 4: Run runner tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_runner_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Run partitioned service suite**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/db_accuracy/services/test_partitioned_cache_store_service.py \
  tests/db_accuracy/services/test_partitioned_planner_service.py \
  tests/db_accuracy/services/test_partitioned_db_data_service.py \
  tests/db_accuracy/services/test_partitioned_source_data_service.py \
  tests/db_accuracy/services/test_partitioned_compare_data_service.py \
  tests/db_accuracy/services/test_partitioned_aggregation_service.py \
  tests/db_accuracy/services/test_partitioned_runner_service.py \
  -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add services/db_accuracy/partitioned/runner_service.py \
  tests/db_accuracy/services/test_partitioned_runner_service.py
git commit -m "feat: orchestrate partitioned accuracy runner"
```

---

## Task 7: 更新 pytest CLI 和入口

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/db_accuracy/integration/test_binance_db_accuracy.py`
- Modify: `tests/db_accuracy/services/test_cli_options.py`

- [ ] **Step 1: Update CLI tests first**

In `tests/db_accuracy/services/test_cli_options.py`, replace `test_cached_db_accuracy_options_are_registered` with:

```python
def test_partitioned_db_accuracy_options_are_registered(pytester):
    _install_project_cli_options(pytester)
    pytester.makepyfile(
        """
        def test_options(request):
            assert request.config.getoption("--db-accuracy-mode") == "cached"
            assert request.config.getoption("--db-accuracy-cache-root") == ".cache/custom"
            assert request.config.getoption("--db-accuracy-symbol") == ["BTCUSDT"]
            assert request.config.getoption("--db-accuracy-pair") == ["BTCUSD"]
            assert request.config.getoption("--db-accuracy-contract-type") == ["CURRENT_QUARTER"]
            assert request.config.getoption("--db-accuracy-interval") == ["1m"]
            assert request.config.getoption("--db-accuracy-start-ms") == 1704067200000
            assert request.config.getoption("--db-accuracy-end-ms") == 1704153599999
            assert request.config.getoption("--db-accuracy-partition-days") == 2
            assert request.config.getoption("--db-accuracy-use-db-cache") is False
            assert request.config.getoption("--db-accuracy-use-source-cache") is False
            assert request.config.getoption("--db-accuracy-workers") == 12
            assert request.config.getoption("--db-accuracy-source-retries") == 5
            assert request.config.getoption("--db-accuracy-source-retry-backoff-ms") == 500
            assert request.config.getoption("--db-accuracy-stop-on-source-failure") is True
            assert request.config.getoption("--db-accuracy-max-shards") == 20
        """
    )

    result = pytester.runpytest(
        "--db-accuracy-mode",
        "cached",
        "--db-accuracy-cache-root",
        ".cache/custom",
        "--db-accuracy-symbol",
        "BTCUSDT",
        "--db-accuracy-pair",
        "BTCUSD",
        "--db-accuracy-contract-type",
        "CURRENT_QUARTER",
        "--db-accuracy-interval",
        "1m",
        "--db-accuracy-start-ms",
        "1704067200000",
        "--db-accuracy-end-ms",
        "1704153599999",
        "--db-accuracy-partition-days",
        "2",
        "--db-accuracy-use-db-cache",
        "false",
        "--db-accuracy-use-source-cache",
        "false",
        "--db-accuracy-workers",
        "12",
        "--db-accuracy-source-retries",
        "5",
        "--db-accuracy-source-retry-backoff-ms",
        "500",
        "--db-accuracy-stop-on-source-failure",
        "true",
        "--db-accuracy-max-shards",
        "20",
    )

    result.assert_outcomes(passed=1)
```

Replace the default options test cache assertions with:

```python
            assert request.config.getoption("--db-accuracy-use-db-cache") is True
            assert request.config.getoption("--db-accuracy-use-source-cache") is True
            assert request.config.getoption("--db-accuracy-workers") == 8
            assert request.config.getoption("--db-accuracy-source-retries") == 5
            assert request.config.getoption("--db-accuracy-source-retry-backoff-ms") == 1000
            assert request.config.getoption("--db-accuracy-stop-on-source-failure") is True
```

Add this test:

```python
def test_old_refresh_cache_option_is_removed(pytester):
    _install_project_cli_options(pytester)
    pytester.makepyfile("def test_placeholder(): pass")

    result = pytester.runpytest("--db-accuracy-refresh-cache")

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*unrecognized arguments: --db-accuracy-refresh-cache*"])
```

- [ ] **Step 2: Run failing CLI tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_cli_options.py -q
```

Expected: FAIL because new options are not registered and old option is still accepted.

- [ ] **Step 3: Modify `tests/conftest.py` options**

In `pytest_addoption`, delete the `--db-accuracy-refresh-cache` option block.

Add this helper near `_safe_path_component`:

```python
def _parse_bool_option(value: str) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    raise pytest.UsageError(f"expected true or false, got: {value}")
```

Add these options:

```python
    parser.addoption(
        "--db-accuracy-use-db-cache",
        action="store",
        type=_parse_bool_option,
        default=True,
        help="Reuse local DB partition cache when it covers the requested range",
    )
    parser.addoption(
        "--db-accuracy-use-source-cache",
        action="store",
        type=_parse_bool_option,
        default=True,
        help="Reuse local Binance source partition cache when it covers the requested range",
    )
    parser.addoption(
        "--db-accuracy-workers",
        action="store",
        type=int,
        default=8,
        help="Maximum concurrent partition workers for DB/source/compare stages",
    )
    parser.addoption(
        "--db-accuracy-source-retries",
        action="store",
        type=int,
        default=5,
        help="Retries for each Binance request window before pausing the run",
    )
    parser.addoption(
        "--db-accuracy-source-retry-backoff-ms",
        action="store",
        type=int,
        default=1000,
        help="Linear retry backoff base in milliseconds for Binance requests",
    )
    parser.addoption(
        "--db-accuracy-stop-on-source-failure",
        action="store",
        type=_parse_bool_option,
        default=True,
        help="Pause the run after a source request exhausts retries",
    )
```

- [ ] **Step 4: Modify integration entrypoint**

In `tests/db_accuracy/integration/test_binance_db_accuracy.py`, replace imports:

```python
from services.db_accuracy.partitioned.models import (
    AccuracyMode,
    CachePolicy,
    ExecutionOptions,
    PartitionedAccuracyRequest,
)
from services.db_accuracy.partitioned.runner_service import PartitionedAccuracyService
from services.db_accuracy.reporting.result_serializer_service import ResultSerializerService
```

Replace the body of `test_binance_raw_and_metadata_db_accuracy` with:

```python
    partitioned_request = _partitioned_compare_request(request.config)
    result = PartitionedAccuracyService().run(partitioned_request)

    allure.attach(
        result.summary_text,
        name="db_accuracy_partitioned_summary",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(
        ResultSerializerService.partitioned_to_json(result),
        name="db_accuracy_partitioned_details",
        attachment_type=allure.attachment_type.JSON,
    )

    assert result.passed, result.summary_text
```

Replace `_cached_compare_request` and `_single_table` with:

```python
def _partitioned_compare_request(config) -> PartitionedAccuracyRequest:
    return PartitionedAccuracyRequest(
        mode=AccuracyMode(config.getoption("--db-accuracy-mode")),
        tables=tuple(config.getoption("--db-accuracy-table")),
        start_ms=config.getoption("--db-accuracy-start-ms"),
        end_ms=config.getoption("--db-accuracy-end-ms"),
        cache_root=Path(config.getoption("--db-accuracy-cache-root")),
        symbols=tuple(config.getoption("--db-accuracy-symbol")),
        pairs=tuple(config.getoption("--db-accuracy-pair")),
        contract_types=tuple(config.getoption("--db-accuracy-contract-type")),
        intervals=tuple(config.getoption("--db-accuracy-interval")),
        partition_days=config.getoption("--db-accuracy-partition-days"),
        max_shards=config.getoption("--db-accuracy-max-shards"),
        safety_hours=config.getoption("--db-accuracy-safety-hours"),
        cache_policy=CachePolicy(
            use_db_cache=config.getoption("--db-accuracy-use-db-cache"),
            use_source_cache=config.getoption("--db-accuracy-use-source-cache"),
        ),
        execution=ExecutionOptions(
            workers=config.getoption("--db-accuracy-workers"),
            source_retries=config.getoption("--db-accuracy-source-retries"),
            source_retry_backoff_ms=config.getoption("--db-accuracy-source-retry-backoff-ms"),
            stop_on_source_failure=config.getoption("--db-accuracy-stop-on-source-failure"),
        ),
    )
```

- [ ] **Step 5: Update CLI fake defaults in tests**

In `_fake_request()` and `_fake_config()` defaults inside `tests/db_accuracy/services/test_cli_options.py`, remove `--db-accuracy-refresh-cache` and add:

```python
        "--db-accuracy-use-db-cache": True,
        "--db-accuracy-use-source-cache": True,
        "--db-accuracy-workers": 8,
        "--db-accuracy-source-retries": 5,
        "--db-accuracy-source-retry-backoff-ms": 1000,
        "--db-accuracy-stop-on-source-failure": True,
```

Update tests named `test_cached_mode_validates_single_table_before_runner_construction` and `test_cached_mode_validates_time_range_before_runner_construction` to monkeypatch `PartitionedAccuracyService` only if still needed. Preferred replacement:

```python
def test_partitioned_request_builder_populates_cache_and_execution_options():
    request = _fake_request(
        {
            "--db-accuracy-mode": "cached",
            "--db-accuracy-table": ["binance_kline_all_future_raw"],
            "--db-accuracy-start-ms": 1704067200000,
            "--db-accuracy-end-ms": 1704153599999,
            "--db-accuracy-use-db-cache": False,
            "--db-accuracy-use-source-cache": True,
            "--db-accuracy-workers": 12,
        }
    )

    built = db_accuracy_entry._partitioned_compare_request(request.config)

    assert built.mode.value == "cached"
    assert built.tables == ("binance_kline_all_future_raw",)
    assert built.cache_policy.use_db_cache is False
    assert built.cache_policy.use_source_cache is True
    assert built.execution.workers == 12
```

- [ ] **Step 6: Run CLI and entrypoint tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_cli_options.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 7**

```bash
git add tests/conftest.py \
  tests/db_accuracy/integration/test_binance_db_accuracy.py \
  tests/db_accuracy/services/test_cli_options.py
git commit -m "feat: wire partitioned runner to db accuracy cli"
```

---

## Task 8: 更新旧 cached/direct runner 测试并删除旧 refresh 语义

**Files:**
- Modify: `tests/db_accuracy/services/test_cached_accuracy_service.py`
- Modify: `tests/db_accuracy/services/test_cached_source_service.py`
- Modify: `services/db_accuracy/cached/cache_models.py`
- Modify: `tests/db_accuracy/services/test_cache_models.py`

- [ ] **Step 1: Run existing DB accuracy service tests to reveal breakage**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services -q
```

Expected: FAIL in tests that assert `refresh_cache` or old runner behavior.

- [ ] **Step 2: Remove old runner behavior assertions**

In `tests/db_accuracy/services/test_cached_accuracy_service.py`, keep only tests that still cover reusable low-level helpers. Move runner-level expectations to `test_partitioned_runner_service.py`. Delete imports of `CachedCompareRequest` and `CachedAccuracyService` if every runner behavior has moved.

If the file becomes empty after moving tests, delete `tests/db_accuracy/services/test_cached_accuracy_service.py`.

- [ ] **Step 3: Keep source cache helper tests aligned**

In `tests/db_accuracy/services/test_cached_source_service.py`, do not add retry/cleanup expectations. Those now live in `test_partitioned_source_data_service.py`. Keep tests for:

```text
ensure_partition fetches missing source partition
ensure_partition splits by request limit
ensure_partition reuses complete partition
ensure_partition records empty partition
```

Remove tests that assert `source_request_failed` manifests are reusable. The new partitioned source service deletes failed source cache.

- [ ] **Step 4: Remove `refresh_cache` from old model if no longer referenced**

Run:

```bash
rg "refresh_cache|db-accuracy-refresh-cache" .
```

Expected after edits: no hits in implementation code, tests, README, or `docs/binance_db_accuracy_validation.md`. It is acceptable for this implementation plan itself to mention the removed option because it documents the migration.

- [ ] **Step 5: Run DB accuracy service tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 8**

```bash
git add services/db_accuracy/cached/cache_models.py \
  tests/db_accuracy/services/test_cached_accuracy_service.py \
  tests/db_accuracy/services/test_cached_source_service.py \
  tests/db_accuracy/services/test_cache_models.py
git commit -m "refactor: remove legacy refresh cache semantics"
```

If `test_cached_accuracy_service.py` is deleted, stage it with:

```bash
git add -u tests/db_accuracy/services/test_cached_accuracy_service.py
```

---

## Task 9: 文档更新

**Files:**
- Modify: `docs/binance_db_accuracy_validation.md`
- Modify: `README.md`

- [ ] **Step 1: Update docs with new execution flow**

In `docs/binance_db_accuracy_validation.md`, replace the cached mode description with this flow:

```markdown
统一分区 runner 的执行顺序是：

1. 规划 `table + market shard + time partition` 任务。
2. 准备 DB 分区缓存。
3. 准备 Binance source 分区缓存。
4. 只有所有需要的 DB/source 分区都准备完成后，才进入对比阶段。
5. 对比结果按分区写入 `compare/report.txt`、`compare/diff.json` 和 `compare/manifest.json`。
6. 本次 run 汇总历史完成分区和本次完成分区，生成 `runs/run_id=.../summary.txt` 与 `summary.json`。
```

- [ ] **Step 2: Document cache hit rules**

Add this section to `docs/binance_db_accuracy_validation.md`:

```markdown
### 缓存命中规则

`--db-accuracy-use-db-cache true` 和 `--db-accuracy-use-source-cache true` 表示允许复用本地缓存，不表示无条件复用。

- 缓存必须匹配 table、market shard、data side、schema version，并覆盖本次请求的时间分区，才算命中。
- 本次范围比缓存小：DB/source 数据可以复用较大缓存，读取时按本次范围过滤；compare artifact 只有范围完全一致且输入 fingerprint 一致时才复用。
- 本次范围比缓存大：复用已完整覆盖的分区，只补拉缺失分区。
- 缓存覆盖不足、manifest 缺失、schema version 不匹配或状态不是 `complete/empty` 时，该分区视为未命中。
- `--db-accuracy-use-db-cache false` 会强制重新查询 DB 并覆盖 DB 缓存。
- `--db-accuracy-use-source-cache false` 会强制重新请求 Binance source 并覆盖 source 缓存。
- DB/source 任一侧强制刷新后，相关 compare 结果失效，需要重新对比。
```

- [ ] **Step 3: Document failure cleanup**

Add this section:

```markdown
### 网络失败与断点续跑

每个 Binance 请求窗口默认重试 5 次。重试耗尽后：

1. 当前 source 分区判定为 `source_request_failed`。
2. 当前分区的 source 临时文件、正式 parquet、manifest 都会被删除。
3. runner 触发暂停并尽快取消未完成任务。
4. 已完成的 DB/source/compare 缓存会保留。
5. 下次执行默认复用已完成缓存，从缺失分区继续。

数据不一致不是运行失败，不触发暂停。发现差异的 compare 分区仍是完成态，会纳入后续聚合报告。
```

- [ ] **Step 4: Update parameter table**

Remove `--db-accuracy-refresh-cache`. Add:

```markdown
| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--db-accuracy-use-db-cache` | `true` | 是否允许复用本地 DB 分区缓存。传 `false` 时重新查询 DB 并覆盖缓存。 |
| `--db-accuracy-use-source-cache` | `true` | 是否允许复用本地 Binance source 分区缓存。传 `false` 时重新请求接口并覆盖缓存。 |
| `--db-accuracy-workers` | `8` | DB/source/compare 阶段的分区并发数。 |
| `--db-accuracy-source-retries` | `5` | 每个 Binance 请求窗口的重试次数。 |
| `--db-accuracy-source-retry-backoff-ms` | `1000` | Binance 请求重试退避基准毫秒数。 |
| `--db-accuracy-stop-on-source-failure` | `true` | source 请求重试耗尽后是否暂停整次任务。 |
```

- [ ] **Step 5: Update README quick command**

In `README.md`, update the DB accuracy cached command example to include:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/integration/test_binance_db_accuracy.py -v \
  --run-db-accuracy \
  --db-accuracy-mode cached \
  --db-accuracy-table binance_kline_all_future_raw \
  --db-accuracy-symbol BTCUSDT \
  --db-accuracy-interval 1m \
  --db-accuracy-start-ms 1704067200000 \
  --db-accuracy-end-ms 1704153599999 \
  --db-accuracy-use-db-cache true \
  --db-accuracy-use-source-cache true \
  --db-accuracy-workers 8
```

- [ ] **Step 6: Run doc grep checks**

Run:

```bash
rg "db-accuracy-refresh-cache|refresh_cache" README.md docs/binance_db_accuracy_validation.md tests services
```

Expected: no output.

- [ ] **Step 7: Commit Task 9**

```bash
git add docs/binance_db_accuracy_validation.md README.md
git commit -m "docs: describe partitioned db accuracy runner"
```

---

## Task 10: 全量验证与收尾

**Files:**
- Test only

- [ ] **Step 1: Run focused partitioned suite**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/db_accuracy/services/test_partitioned_cache_store_service.py \
  tests/db_accuracy/services/test_partitioned_planner_service.py \
  tests/db_accuracy/services/test_partitioned_db_data_service.py \
  tests/db_accuracy/services/test_partitioned_source_data_service.py \
  tests/db_accuracy/services/test_partitioned_compare_data_service.py \
  tests/db_accuracy/services/test_partitioned_aggregation_service.py \
  tests/db_accuracy/services/test_partitioned_runner_service.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run all DB accuracy service/tool tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services tests/db_accuracy/tools -q
```

Expected: PASS.

- [ ] **Step 3: Run collection**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest --collect-only -q
```

Expected: collection succeeds. DB accuracy integration test remains collected but skipped unless `--run-db-accuracy` is supplied.

- [ ] **Step 4: Run a no-network fake-backed smoke through unit tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services/test_partitioned_runner_service.py::test_runner_reuses_existing_complete_cache_on_second_run -q
```

Expected: PASS and confirms second run reuses cached DB/source/compare state.

- [ ] **Step 5: Inspect git diff for protected paths**

Run:

```bash
git diff --name-only
```

Expected: no files under `infrastructure/`.

- [ ] **Step 6: Commit final verification adjustments if needed**

If verification required small fixes, stage only files touched by this feature:

```bash
git add services/db_accuracy tests/db_accuracy docs/binance_db_accuracy_validation.md README.md tests/conftest.py
git commit -m "test: verify partitioned db accuracy runner"
```

Skip this commit if Task 1-9 commits already contain all final fixes and the working tree is clean except unrelated user changes.

---

## Acceptance Criteria

- `--db-accuracy-refresh-cache` is removed and fails as an unknown pytest option.
- `--db-accuracy-use-db-cache true|false` and `--db-accuracy-use-source-cache true|false` are the only cache reuse controls exposed to users.
- direct and cached pytest modes both use `PartitionedAccuracyService`.
- direct without start/end discovers DB ranges and splits by `--db-accuracy-partition-days`.
- cached requires exactly one table plus start/end.
- The runner plans `table + market shard + time partition` tasks.
- The runner prepares all DB/source data before starting compare.
- `use_cache=true` reuses only cache that covers the requested range.
- If requested range is smaller than a DB/source cache, the frame is filtered to the requested range.
- If requested range is larger than existing cache, covered partitions are reused and missing partitions are fetched.
- Compare cache is reused only for exact range and matching DB/source fingerprints.
- Source request windows retry 5 times by default.
- Source retry exhaustion deletes that partition's source parquet and manifest, cleans temp files, pauses the run, and keeps completed historical caches.
- Data differences produce `failed_with_differences` compare manifests and do not pause execution.
- Aggregated run summaries include historical completed compare manifests plus new completed manifests.
- Long-lived files are limited to `db/`, `source/`, `compare/`, and `runs/`; `_tmp/` is cleaned after success, failure, cancellation, and next startup.
- No code under `infrastructure/` is modified.

## Self-Review

- Spec coverage: covered unified runner, direct/cached support, DB/source dual cache, cache hit rules for smaller/larger ranges, network retry and cleanup, stop/resume behavior, prepare-before-compare staging, aggressive configurable concurrency, compare aggregation, registry partition, CLI deletion of old refresh parameter, and docs.
- Placeholder scan: no task contains deferred implementation markers. Each code-edit task includes concrete test functions, concrete service signatures, and exact commands.
- Type consistency: `PartitionedAccuracyRequest`, `CachePolicy`, `ExecutionOptions`, `PartitionTask`, `CacheManifest`, `CompareManifest`, `PartitionedRunResult`, and service method names are consistent across tasks.
