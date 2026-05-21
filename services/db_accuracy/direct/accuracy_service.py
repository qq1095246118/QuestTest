"""direct 模式 DB accuracy 编排服务。

本模块负责 direct 模式下表规格解析、DB/source 数据读取、生命周期过滤和差异汇总。
"""

from __future__ import annotations

import time
from typing import Any

from services.db_accuracy.source_service import BinanceSourceService
from services.db_accuracy.compare_service import compare_rows, normalize_value
from services.db_accuracy.db_reader_service import DBAccuracyReaderService
from services.db_accuracy.models import (
    AccuracyRunResult,
    Difference,
    MarketLifecycle,
    ResolvedTableSpec,
    SourceRow,
    TableRunResult,
    ValidationWindow,
)
from services.db_accuracy.table_specs import load_table_specs, resolve_spec


def compare_db_and_source_rows(
    table: str,
    key_label: str,
    row_key_field: str,
    compare_fields: tuple[str, ...],
    db_rows: list[dict[str, Any]],
    source_rows: list[SourceRow],
) -> list[Difference]:
    differences: list[Difference] = []
    db_by_key = _index_db_rows(
        table=table,
        key_label=key_label,
        row_key_field=row_key_field,
        rows=db_rows,
        differences=differences,
        normalize_key=True,
    )
    source_by_key = _index_source_rows(
        table=table,
        key_label=key_label,
        row_key_field=row_key_field,
        rows=source_rows,
        differences=differences,
        normalize_key=True,
    )

    for canonical_key, (row_key, db_row) in db_by_key.items():
        source_entry = source_by_key.get(canonical_key)
        if source_entry is None:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=row_key,
                    field=row_key_field,
                    db_value=db_row.get(row_key_field),
                    source_value=None,
                    reason="missing_source_row",
                )
            )
            continue

        _, source_row = source_entry
        differences.extend(
            compare_rows(
                table=table,
                key_label=key_label,
                row_key=row_key,
                db_row=db_row,
                source_row=source_row,
                fields=compare_fields,
            )
        )

    for canonical_key, (row_key, source_row) in source_by_key.items():
        if canonical_key not in db_by_key:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=row_key,
                    field=row_key_field,
                    db_value=None,
                    source_value=source_row.get(row_key_field, row_key),
                    reason="missing_db_row",
                )
            )

    return differences


def compare_registry_rows(
    table: str,
    compare_fields: tuple[str, ...],
    db_rows: list[dict[str, Any]],
    source_rows: list[SourceRow],
) -> list[Difference]:
    differences: list[Difference] = []
    source_by_symbol = _index_source_rows(
        table=table,
        key_label="registry",
        row_key_field="symbol",
        rows=source_rows,
        differences=differences,
        normalize_key=False,
    )
    db_by_symbol = _index_db_rows(
        table=table,
        key_label="registry",
        row_key_field="symbol",
        rows=db_rows,
        differences=differences,
        normalize_key=False,
    )

    for symbol, (_, db_row) in db_by_symbol.items():
        key_label = f"symbol={symbol}"
        source_entry = source_by_symbol.get(symbol)
        if source_entry is None:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=symbol,
                    field="symbol",
                    db_value=symbol,
                    source_value=None,
                    reason="missing_source_row",
                )
            )
            continue

        _, source_row = source_entry
        differences.extend(
            compare_rows(
                table=table,
                key_label=key_label,
                row_key=symbol,
                db_row=db_row,
                source_row=source_row,
                fields=compare_fields,
            )
        )

    for symbol, (_, source_row) in source_by_symbol.items():
        if symbol not in db_by_symbol:
            differences.append(
                Difference(
                    table=table,
                    key_label=f"symbol={symbol}",
                    row_key=symbol,
                    field="symbol",
                    db_value=None,
                    source_value=source_row.get("symbol", symbol),
                    reason="missing_db_row",
                )
            )

    return differences


def _index_db_rows(
    table: str,
    key_label: str,
    row_key_field: str,
    rows: list[dict[str, Any]],
    differences: list[Difference],
    normalize_key: bool,
) -> dict[Any, tuple[Any, dict[str, Any]]]:
    rows_by_key: dict[Any, tuple[Any, dict[str, Any]]] = {}
    for row in rows:
        if row_key_field not in row:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=None,
                    field=row_key_field,
                    db_value=None,
                    source_value=None,
                    reason="missing_db_row_key_field",
                )
            )
            continue

        row_key = row[row_key_field]
        if row_key is None:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=None,
                    field=row_key_field,
                    db_value=None,
                    source_value=None,
                    reason="null_db_row_key",
                )
            )
            continue

        canonical_key = normalize_value(row_key) if normalize_key else row_key
        if canonical_key in rows_by_key:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=row_key,
                    field=row_key_field,
                    db_value=row_key,
                    source_value=None,
                    reason="duplicate_db_row_key",
                )
            )
            continue

        rows_by_key[canonical_key] = (row_key, row)
    return rows_by_key


def _index_source_rows(
    table: str,
    key_label: str,
    row_key_field: str,
    rows: list[SourceRow],
    differences: list[Difference],
    normalize_key: bool,
) -> dict[Any, tuple[Any, dict[str, Any]]]:
    rows_by_key: dict[Any, tuple[Any, dict[str, Any]]] = {}
    for row in rows:
        row_key = row.key
        if row_key is None:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=None,
                    field=row_key_field,
                    db_value=None,
                    source_value=None,
                    reason="null_source_row_key",
                )
            )
            continue

        canonical_key = normalize_value(row_key) if normalize_key else row_key
        if canonical_key in rows_by_key:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=row_key,
                    field=row_key_field,
                    db_value=None,
                    source_value=row_key,
                    reason="duplicate_source_row_key",
                )
            )
            continue

        rows_by_key[canonical_key] = (row_key, row.fields)
    return rows_by_key


class DirectAccuracyService:
    def __init__(self, db: Any = None, source: BinanceSourceService | None = None):
        if db is None:
            from infrastructure.database.db_client import DBClient

            db = DBClient()

        self.db = db
        self.reader = DBAccuracyReaderService(db)
        self.source = source if source is not None else BinanceSourceService()

    def run(
        self,
        safety_hours: int,
        include_tables: list[str] | None = None,
    ) -> AccuracyRunResult:
        stable_before_ms = int(time.time() * 1000) - safety_hours * 3_600_000
        selected_tables = set(include_tables or [])
        specs = load_table_specs()
        configured_tables = {spec.table for spec in specs}
        result = AccuracyRunResult()

        for table in sorted(selected_tables - configured_tables):
            result.tables.append(
                TableRunResult(
                    table="table_selection",
                    differences=[
                        Difference(
                            table="table_selection",
                            key_label="table_selection",
                            row_key=table,
                            field="table",
                            db_value=None,
                            source_value=table,
                            reason="unknown_table",
                        )
                    ],
                )
            )

        for spec in specs:
            if selected_tables and spec.table not in selected_tables:
                continue

            table_result = TableRunResult(table=spec.table)
            try:
                columns = self.reader.table_columns(spec.table)
                resolved = resolve_spec(spec, columns)
                if spec.kind == "registry":
                    self._run_registry(resolved, table_result)
                else:
                    self._run_time_series(resolved, stable_before_ms, table_result)
            except Exception as exc:
                table_result.differences.append(
                    Difference(
                        table=spec.table,
                        key_label="table",
                        row_key="table",
                        field="table",
                        db_value=None,
                        source_value=None,
                        reason=f"table_error:{type(exc).__name__}:{exc}",
                    )
                )

            result.tables.append(table_result)

        if not result.tables:
            result.tables.append(
                TableRunResult(
                    table="table_selection",
                    differences=[
                        Difference(
                            table="table_selection",
                            key_label="table_selection",
                            row_key="table_selection",
                            field="table",
                            db_value=None,
                            source_value=None,
                            reason="no_tables_selected",
                        )
                    ],
                )
            )

        return result

    def _run_time_series(
        self,
        spec: ResolvedTableSpec,
        stable_before_ms: int,
        table_result: TableRunResult,
    ) -> None:
        if spec.time_field is None:
            raise ValueError(f"{spec.spec.table} has no resolved time field")

        key_ranges = self.reader.key_ranges(spec, stable_before_ms)
        if not key_ranges:
            table_result.differences.append(
                Difference(
                    table=spec.spec.table,
                    key_label="table",
                    row_key="table",
                    field=spec.time_field,
                    db_value=None,
                    source_value=None,
                    reason="no_stable_db_rows",
                )
            )
            return

        for key_range in key_ranges:
            try:
                windows = self.reader.build_windows(spec, key_range)
            except Exception as exc:
                table_result.differences.append(
                    Difference(
                        table=spec.spec.table,
                        key_label=key_range.key.label(),
                        row_key=key_range.key.label(),
                        field=spec.time_field,
                        db_value=None,
                        source_value=None,
                        reason=f"window_planning_error:{type(exc).__name__}:{exc}",
                    )
                )
                continue

            if not windows:
                table_result.differences.append(
                    Difference(
                        table=spec.spec.table,
                        key_label=key_range.key.label(),
                        row_key=key_range.key.label(),
                        field=spec.time_field,
                        db_value=None,
                        source_value=None,
                        reason="no_windows_checked",
                    )
                )
                continue

            try:
                lifecycle = self.source.market_lifecycle(spec.spec, key_range.key)
            except AttributeError:
                lifecycle = MarketLifecycle(
                    is_known=True,
                    status="TRADING",
                    onboard_ms=None,
                    delivery_ms=None,
                )

            if not _market_is_comparable(lifecycle):
                continue

            for window in windows:
                comparable_window = _comparable_window(window, lifecycle)
                if comparable_window is None:
                    continue
                table_result.windows_checked += 1
                try:
                    db_rows = self.reader.rows_for_window(
                        spec,
                        key_range.key,
                        comparable_window.start_ms,
                        comparable_window.end_ms,
                    )
                    source_rows = self.source.fetch_rows(
                        spec.spec,
                        key_range.key,
                        comparable_window.start_ms,
                        comparable_window.end_ms,
                    )
                    table_result.db_rows_checked += len(db_rows)
                    table_result.source_rows_checked += len(source_rows)
                    table_result.differences.extend(
                        compare_db_and_source_rows(
                            table=spec.spec.table,
                            key_label=key_range.key.label(),
                            row_key_field=spec.time_field,
                            compare_fields=spec.compare_fields,
                            db_rows=db_rows,
                            source_rows=source_rows,
                        )
                    )
                except Exception as exc:
                    table_result.differences.append(
                        Difference(
                            table=spec.spec.table,
                            key_label=key_range.key.label(),
                            row_key=f"{window.start_ms}-{window.end_ms}",
                            field=spec.time_field,
                            db_value=None,
                            source_value=None,
                            reason=f"window_error:{type(exc).__name__}:{exc}",
                        )
                    )

    def _run_registry(
        self,
        spec: ResolvedTableSpec,
        table_result: TableRunResult,
    ) -> None:
        db_rows = self.reader.registry_rows(spec)
        source_rows = self.source.fetch_registry_rows(spec.spec)
        table_result.windows_checked = 1
        table_result.db_rows_checked = len(db_rows)
        table_result.source_rows_checked = len(source_rows)
        table_result.differences.extend(
            compare_registry_rows(
                table=spec.spec.table,
                compare_fields=spec.compare_fields,
                db_rows=db_rows,
                source_rows=source_rows,
            )
        )


def _market_is_comparable(lifecycle: MarketLifecycle) -> bool:
    return lifecycle.is_known and lifecycle.status == "TRADING"


def _comparable_window(
    window: ValidationWindow,
    lifecycle: MarketLifecycle,
) -> ValidationWindow | None:
    start_ms = window.start_ms
    end_ms = window.end_ms
    if start_ms is None or end_ms is None:
        return None

    if lifecycle.onboard_ms is not None:
        start_ms = max(start_ms, lifecycle.onboard_ms)
    if lifecycle.delivery_ms is not None:
        end_ms = min(end_ms, lifecycle.delivery_ms)

    if start_ms > end_ms:
        return None
    return ValidationWindow(
        table=window.table,
        key=window.key,
        start_ms=start_ms,
        end_ms=end_ms,
    )
