"""DB accuracy 数据模型。

本模块定义 DB accuracy direct 与 cached 模式共享的数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TableSpec:
    table: str
    kind: str
    endpoint: str
    key_fields: tuple[str, ...]
    time_fields: tuple[str, ...]
    interval_field: str | None
    compare_fields: tuple[str, ...]
    request_limit: int
    optional_compare_fields: tuple[str, ...] = ()
    fixed_interval: str | None = None
    contract_type_field: str | None = None
    pair_field: str | None = None
    symbol_field: str | None = "symbol"
    source_time_field: str | None = None


@dataclass(frozen=True)
class ResolvedTableSpec:
    spec: TableSpec
    columns: tuple[str, ...]
    time_field: str | None
    interval_field: str | None
    compare_fields: tuple[str, ...]
    key_fields: tuple[str, ...]


@dataclass(frozen=True)
class ValidationKey:
    values: dict[str, Any]

    def label(self) -> str:
        return ",".join(f"{key}={self.values[key]}" for key in sorted(self.values))


@dataclass(frozen=True)
class ValidationWindow:
    table: str
    key: ValidationKey
    start_ms: int | None
    end_ms: int | None


@dataclass(frozen=True)
class KeyTimeRange:
    table: str
    key: ValidationKey
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class SourceRow:
    key: Any
    fields: dict[str, Any]


@dataclass(frozen=True)
class MarketLifecycle:
    is_known: bool
    status: str | None
    onboard_ms: int | None
    delivery_ms: int | None


@dataclass(frozen=True)
class Difference:
    table: str
    key_label: str
    row_key: Any
    field: str
    db_value: Any
    source_value: Any
    reason: str


@dataclass
class TableRunResult:
    table: str
    windows_checked: int = 0
    db_rows_checked: int = 0
    source_rows_checked: int = 0
    differences: list[Difference] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.differences


@dataclass
class AccuracyRunResult:
    tables: list[TableRunResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(table.passed for table in self.tables)

    def summary_text(self) -> str:
        table_count = len(self.tables)
        diff_count = sum(len(table.differences) for table in self.tables)
        row_count = sum(table.db_rows_checked for table in self.tables)
        window_count = sum(table.windows_checked for table in self.tables)
        lines = [
            f"tables={table_count}",
            f"windows_checked={window_count}",
            f"db_rows_checked={row_count}",
            f"differences={diff_count}",
        ]
        for table in self.tables:
            lines.append(
                f"{table.table}: windows={table.windows_checked}, "
                f"db_rows={table.db_rows_checked}, "
                f"source_rows={table.source_rows_checked}, "
                f"differences={len(table.differences)}"
            )
        return "\n".join(lines)
