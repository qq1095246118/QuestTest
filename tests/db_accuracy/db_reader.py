from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime
import re
from typing import Any

from tests.db_accuracy.models import KeyTimeRange, ResolvedTableSpec, ValidationKey, ValidationWindow


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return f"`{identifier}`"


def interval_to_ms(interval: str) -> int:
    if len(interval) < 2:
        raise ValueError(f"Unsupported interval: {interval}")

    amount_text = interval[:-1]
    unit = interval[-1]
    if not amount_text.isdigit() or unit not in {"s", "m", "h", "d", "w"}:
        raise ValueError(f"Unsupported Binance interval: {interval}")

    amount = int(amount_text)
    if amount <= 0:
        raise ValueError(f"Unsupported Binance interval: {interval}")

    multipliers = {
        "s": 1_000,
        "m": 60_000,
        "h": 3_600_000,
        "d": 86_400_000,
        "w": 604_800_000,
    }
    return amount * multipliers[unit]


class DBAccuracyReader:
    def __init__(self, db: Any):
        self.db = db

    def table_columns(self, table: str) -> set[str]:
        rows = self.db.query(f"SHOW COLUMNS FROM {quote_identifier(table)}")
        return {str(row["Field"]) for row in rows}

    def key_ranges(self, spec: ResolvedTableSpec, stable_before_ms: int) -> list[KeyTimeRange]:
        if spec.time_field is None:
            return []

        table = quote_identifier(spec.spec.table)
        time_field = quote_identifier(spec.time_field)
        key_fields = list(spec.key_fields)
        selected_keys = ", ".join(quote_identifier(field) for field in key_fields)
        group_by = ", ".join(quote_identifier(field) for field in key_fields)
        sql = (
            f"SELECT {selected_keys}, MIN({time_field}) AS min_time_ms, "
            f"MAX({time_field}) AS max_time_ms "
            f"FROM {table} "
            f"WHERE {time_field} < %s "
            f"GROUP BY {group_by} "
            f"ORDER BY {group_by}"
        )
        rows = self.db.query(sql, (stable_before_ms,))

        ranges: list[KeyTimeRange] = []
        for row in rows:
            ranges.append(
                KeyTimeRange(
                    table=spec.spec.table,
                    key=ValidationKey({field: row[field] for field in key_fields}),
                    start_ms=int(row["min_time_ms"]),
                    end_ms=int(row["max_time_ms"]),
                )
            )
        return ranges

    def rows_for_window(
        self,
        spec: ResolvedTableSpec,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        if spec.time_field is None:
            return []

        fields = _dedupe((*spec.key_fields, spec.time_field, *spec.compare_fields))
        select_fields = ", ".join(quote_identifier(field) for field in fields)
        table = quote_identifier(spec.spec.table)
        time_field = quote_identifier(spec.time_field)
        where_parts = [f"{time_field} >= %s", f"{time_field} <= %s"]
        params: list[Any] = [start_ms, end_ms]
        for field in spec.key_fields:
            where_parts.append(f"{quote_identifier(field)} = %s")
            params.append(key.values[field])

        sql = (
            f"SELECT {select_fields} "
            f"FROM {table} "
            f"WHERE {' AND '.join(where_parts)} "
            f"ORDER BY {time_field} ASC"
        )
        return list(self.db.query(sql, tuple(params)))

    def registry_rows(self, spec: ResolvedTableSpec) -> list[dict[str, Any]]:
        fields = _dedupe((*spec.key_fields, *spec.compare_fields))
        select_fields = ", ".join(quote_identifier(field) for field in fields)
        table = quote_identifier(spec.spec.table)
        order_by = ", ".join(quote_identifier(field) for field in spec.key_fields)
        sql = f"SELECT {select_fields} FROM {table} ORDER BY {order_by}"
        return list(self.db.query(sql))

    def build_windows(
        self,
        spec: ResolvedTableSpec,
        time_range: KeyTimeRange,
    ) -> list[ValidationWindow]:
        if spec.spec.kind == "funding":
            window_end = lambda start_ms: start_ms + 90 * 86_400_000
        else:
            if spec.spec.request_limit < 1:
                raise ValueError("request_limit must be >= 1")
            interval = spec.spec.fixed_interval
            if interval is None:
                if spec.interval_field is None:
                    raise ValueError(
                        f"{spec.spec.table} requires fixed_interval or interval_field to build windows"
                    )
                interval = str(time_range.key.values[spec.interval_field])
            interval_count = spec.spec.request_limit
            window_end = _kline_window_end(spec.spec.endpoint, interval, interval_count)

        windows: list[ValidationWindow] = []
        start_ms = time_range.start_ms
        while start_ms <= time_range.end_ms:
            end_ms = min(window_end(start_ms), time_range.end_ms)
            windows.append(
                ValidationWindow(
                    table=spec.spec.table,
                    key=time_range.key,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
            )
            start_ms = end_ms + 1
        return windows


def _dedupe(fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(fields))


def _fixed_window_end(window_span_ms: int):
    def window_end(start_ms: int) -> int:
        return start_ms + window_span_ms - 1

    return window_end


def _kline_window_end(endpoint: str, interval: str, interval_count: int):
    max_span_ms = 200 * 86_400_000 if endpoint.startswith("coinm_") else None
    if _is_month_interval(interval):
        month_count = int(interval[:-1]) * interval_count

        def window_end(start_ms: int) -> int:
            end_ms = _add_months_ms(start_ms, month_count) - 1
            if max_span_ms is not None:
                end_ms = min(end_ms, start_ms + max_span_ms)
            return end_ms

        return window_end

    window_span_ms = interval_to_ms(interval) * interval_count
    if max_span_ms is not None:
        window_span_ms = min(window_span_ms, max_span_ms)
    return _fixed_window_end(window_span_ms)


def _is_month_interval(interval: str) -> bool:
    return len(interval) >= 2 and interval[:-1].isdigit() and int(interval[:-1]) > 0 and interval[-1] == "M"


def _add_months_ms(timestamp_ms: int, months: int) -> int:
    if months == 0:
        return timestamp_ms

    timestamp = datetime.fromtimestamp(timestamp_ms / 1000, UTC)
    month_index = timestamp.month - 1 + months
    year = timestamp.year + month_index // 12
    month = month_index % 12 + 1
    day = min(timestamp.day, monthrange(year, month)[1])
    shifted = timestamp.replace(year=year, month=month, day=day)
    return int(shifted.timestamp() * 1000)
