#!/usr/bin/env python3
"""Run a read-only, database-backed regression for metric slice retrieval.

The probe discovers a completed rolling slice scope from the test database,
uses the same scope for MCP requests, and records field-level evidence.  It
does not create, update, or delete any database data.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pymysql

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from tmp.critical_readonly_gap_probe import (  # noqa: E402
    MCPClient,
    data,
    error_code,
    meta,
    rows,
    successful,
)


MCP_URL = os.environ.get(
    "MCP_URL", "https://test-factor-frontend.questvector.ai/mcp/factor-data"
)
TOKEN = os.environ.get("MCP_TOKEN") or os.environ.get("FACTOR4_MCP_TOKEN")
TEST_HOST_PREFIX = "https://test-factor-frontend.questvector.ai/"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc

SLICE_FIELDS = (
    "id",
    "run_id",
    "factor_id",
    "is_sub_factor_id",
    "ic_scope",
    "calculation_mode",
    "factor_bar_interval",
    "factor_window_bars",
    "return_bar_interval",
    "forward_return_bars",
    "interval_value",
    "forward_return_horizon",
    "universe_key",
    "symbol",
    "window_scope",
    "metric_window_bars",
    "metric_window_days",
    "sample_segment",
    "slice_start",
    "slice_end",
    "as_of_time",
    "sample_count",
    "coverage",
    "ic",
    "rank_ic",
    "ic_abs",
    "rank_ic_abs",
    "ic_p_value",
    "rank_ic_p_value",
    "ic_t_stat",
    "rank_ic_t_stat",
    "top_quantile_return",
    "bottom_quantile_return",
    "long_short_return",
    "long_short_annual_return",
    "ic_score",
    "rank_ic_score",
    "icir_score",
    "rank_icir_score",
    "t_stat_score",
    "monotonicity_score",
    "long_short_score",
    "slice_score",
    "created_at",
    "icir",
    "rank_icir",
    "monotonicity_ratio",
    "stratification",
)


def json_default(value: Any) -> str:
    """Serialize database-native values in generated evidence."""

    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def write_json(path: Path, value: Any) -> None:
    """Write a JSON evidence artifact without credentials."""

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def db_connection() -> pymysql.connections.Connection:
    """Open a test database connection used only for a read-only snapshot."""

    settings = SettingsLoader.load("test", ROOT).database
    return pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.username,
        password=settings.password,
        database=settings.name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=120,
    )


def parse_time(value: Any, *, db_value: bool = False) -> datetime | None:
    """Normalize API and database timestamps to timezone-aware UTC values."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ if db_value else UTC)
    return parsed.astimezone(UTC)


def equal(left: Any, right: Any) -> bool:
    """Compare nullable scalar values with timestamp and decimal normalization."""

    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    left_time = parse_time(left)
    right_time = parse_time(right, db_value=isinstance(right, datetime))
    if left_time is not None and right_time is not None:
        return left_time == right_time
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


def local_iso(value: datetime) -> str:
    """Serialize a database DATETIME as an explicit Asia/Shanghai timestamp."""

    aware = value if value.tzinfo is not None else value.replace(tzinfo=LOCAL_TZ)
    return aware.isoformat()


def period_iso(value: datetime) -> str:
    """Serialize a metric-period DATETIME as an explicit UTC timestamp."""

    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def db_snapshot_rows(scope: dict[str, Any]) -> list[dict[str, Any]]:
    """Read every slice row in one exact discovered scope."""

    connection = db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                """
                SELECT *
                FROM factor_ic_slice_metrics
                WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=1
                  AND ic_scope=%s AND calculation_mode=%s
                  AND factor_bar_interval=%s AND factor_window_bars=%s
                  AND return_bar_interval=%s AND forward_return_bars=%s
                  AND universe_key=%s AND symbol=%s AND window_scope=%s
                ORDER BY as_of_time ASC, id ASC
                """,
                (
                    scope["run_id"],
                    scope["factor_id"],
                    scope["ic_scope"],
                    scope["calculation_mode"],
                    scope["factor_bar_interval"],
                    scope["factor_window_bars"],
                    scope["return_bar_interval"],
                    scope["forward_return_bars"],
                    scope["universe_key"],
                    scope["symbol"],
                    scope["window_scope"],
                ),
            )
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.rollback()
        connection.close()


def discover_scope() -> dict[str, Any] | None:
    """Select the confirmed completed rolling fixture without a full-table group."""

    connection = db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                """
                SELECT s.run_id,s.factor_id,s.ic_scope,s.calculation_mode,
                       s.factor_bar_interval,s.factor_window_bars,
                       s.return_bar_interval,s.forward_return_bars,
                       s.universe_key,s.symbol,s.window_scope,
                       COUNT(*) AS row_count,MAX(s.as_of_time) AS max_as_of
                FROM factor_ic_slice_metrics s
                JOIN factor_ic_runs r ON r.run_id=s.run_id AND r.status='completed'
                WHERE s.run_id=%s AND s.factor_id=%s AND s.is_sub_factor_id=1
                  AND s.ic_scope='time_series' AND s.calculation_mode='direct'
                  AND s.window_scope='rolling' AND s.symbol=%s
                GROUP BY s.run_id,s.factor_id,s.ic_scope,s.calculation_mode,
                         s.factor_bar_interval,s.factor_window_bars,
                         s.return_bar_interval,s.forward_return_bars,
                         s.universe_key,s.symbol,s.window_scope
                HAVING COUNT(*) >= 10
                LIMIT 1
                """,
                (
                    "combo_refresh_03913f3b6b51452763be0f1a317d644b_all_8015_equal_weight_1h_rolling_summary",
                    1632921,
                    "TAOUSDT",
                ),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    finally:
        connection.rollback()
        connection.close()


def table_watermark(scope: dict[str, Any]) -> dict[str, Any]:
    """Capture a targeted count and creation watermark for one slice scope."""

    connection = db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                """
                SELECT COUNT(*) AS row_count, MAX(created_at) AS max_created
                FROM factor_ic_slice_metrics
                WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=1
                  AND ic_scope=%s AND calculation_mode=%s
                  AND factor_bar_interval=%s AND factor_window_bars=%s
                  AND return_bar_interval=%s AND forward_return_bars=%s
                  AND universe_key=%s AND symbol=%s AND window_scope=%s
                """,
                (
                    scope["run_id"], scope["factor_id"], scope["ic_scope"],
                    scope["calculation_mode"], scope["factor_bar_interval"],
                    scope["factor_window_bars"], scope["return_bar_interval"],
                    scope["forward_return_bars"], scope["universe_key"],
                    scope["symbol"], scope["window_scope"],
                ),
            )
            return dict(cursor.fetchone() or {})
    finally:
        connection.rollback()
        connection.close()


def scope_args(scope: dict[str, Any], rows_for_scope: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    """Build exact MCP arguments from a discovered database scope."""

    return {
        "factor_ref": f"sub_factor:{scope['factor_id']}",
        "ic_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": str(scope["factor_window_bars"]),
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "as_of": datetime.now(UTC).isoformat(),
        "scoring_version": "v202606_default",
        "run_id": scope["run_id"],
        "symbol": scope["symbol"],
        "start_time": period_iso(rows_for_scope[0]["slice_start"]),
        "end_time": period_iso(rows_for_scope[-1]["slice_end"]),
        "limit": limit,
    }


def identity_mismatches(api_row: dict[str, Any], db_row: dict[str, Any]) -> list[str]:
    """Return DB-backed fields that differ for one API slice row."""

    period_fields = {"slice_start", "slice_end", "as_of_time"}
    mismatches: list[str] = []
    for field in SLICE_FIELDS:
        if field not in db_row:
            continue
        if field in period_fields:
            left = parse_time(api_row.get(field), db_value=False)
            right = parse_time(db_row.get(field), db_value=False)
            same = left == right
        else:
            same = equal(api_row.get(field), db_row.get(field))
        if not same:
            mismatches.append(field)
    return mismatches


def compact_call(call: dict[str, Any] | None) -> dict[str, Any]:
    """Extract transport and identity details without retaining large payloads."""

    if not call:
        return {}
    payload = data(call)
    return {
        "http_status": call.get("http_status"),
        "is_error": call.get("is_error"),
        "error_code": error_code(call),
        "success": successful(call),
        "item_count": len(rows(call)),
        "item_ids": [item.get("id") for item in rows(call)],
        "next_cursor": bool(meta(call).get("next_cursor")),
        "data_keys": sorted(payload),
    }


def main() -> None:
    """Execute the slice reconciliation and write a credential-free report."""

    if not TOKEN:
        raise SystemExit("MCP_TOKEN or FACTOR4_MCP_TOKEN is required")
    if not MCP_URL.startswith(TEST_HOST_PREFIX):
        raise SystemExit("test MCP host gate failed")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-slice-reconcile-{uuid4().hex[:8]}"
    output.mkdir(parents=True, exist_ok=False)
    cases: list[dict[str, Any]] = []

    def record(
        case_id: str,
        status: str,
        expected: str,
        actual: Any,
        call: dict[str, Any] | None = None,
        note: str = "",
    ) -> None:
        """Append one compact case verdict."""

        cases.append(
            {
                "case_id": case_id,
                "module": "factor.metrics.slices",
                "status": status,
                "expected": expected,
                "actual": actual,
                "call": compact_call(call),
                "note": note,
            }
        )

    scope = discover_scope()
    if not scope:
        record("FIXTURE-DISCOVERY", "BLOCKED", "a completed rolling symbol scope with >=10 rows", None)
        summary = {"status_counts": {"BLOCKED": 1}, "cases": cases, "db_unchanged": None}
        write_json(output / "summary.json", summary)
        print(json.dumps({"output_dir": str(output), "status_counts": summary["status_counts"]}))
        return
    before_watermark = table_watermark(scope)
    db_rows = db_snapshot_rows(scope)
    write_json(output / "fixture.json", {"scope": scope, "row_count": len(db_rows), "ids": [row["id"] for row in db_rows]})
    if len(db_rows) < 10:
        record("FIXTURE-DISCOVERY", "BLOCKED", "at least 10 rows in the selected scope", len(db_rows))
        summary = {"status_counts": {"BLOCKED": 1}, "cases": cases, "db_unchanged": None}
        write_json(output / "summary.json", summary)
        print(json.dumps({"output_dir": str(output), "status_counts": summary["status_counts"]}))
        return

    client = MCPClient(TOKEN, output)
    init = client.request(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-slice-reconcile", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    client.protocol_version = str(init_result.get("protocolVersion") or "") or None
    init_ok = successful(init) and client.protocol_version is not None
    record(
        "MCP-INIT",
        "PASS" if init_ok else "BLOCKED",
        "latest test token initializes successfully",
        {"protocol_version": client.protocol_version, "server": init_result.get("serverInfo")},
        init,
        "All MCP cases are blocked when initialization fails.",
    )
    if not init_ok:
        after_watermark = table_watermark(scope)
        summary = {
            "status_counts": {"BLOCKED": 1},
            "cases": cases,
            "db_before": before_watermark,
            "db_after": after_watermark,
            "db_unchanged": before_watermark == after_watermark,
        }
        write_json(output / "summary.json", summary)
        print(json.dumps({"output_dir": str(output), "status_counts": summary["status_counts"]}))
        return

    notify = client.request("MCP-NOTIFY", "notifications/initialized", {})
    record(
        "MCP-NOTIFY",
        "PASS" if notify.get("http_status") in {200, 202, 204} else "FAIL",
        "initialized notification is accepted",
        {"http_status": notify.get("http_status"), "parse_error": notify.get("parse_error")},
        notify,
    )

    db_by_id = {str(row["id"]): row for row in db_rows}
    base = scope_args(scope, db_rows, 5)
    page1 = client.tool("SLICES-PAGE-1", "factor_get_metric_slices", base)
    page1_items = rows(page1)
    expected_page1 = db_rows[:5]
    page1_mismatches = []
    for item in page1_items:
        db_row = db_by_id.get(str(item.get("id")))
        page1_mismatches.append({"id": item.get("id"), "fields": ["missing_db_row"] if not db_row else identity_mismatches(item, db_row)})
    page1_mismatches = [item for item in page1_mismatches if item["fields"]]
    api_scope = data(page1).get("resolved_scope") if isinstance(data(page1).get("resolved_scope"), dict) else {}
    scope_fields = ("ic_scope", "calculation_mode", "interval", "factor_window_bars", "return_bar_interval", "forward_return_bars", "universe_key", "window_scope", "symbol", "scoring_version")
    scope_mismatch = [field for field in scope_fields if not equal(api_scope.get(field), {
        "ic_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": str(scope["factor_window_bars"]),
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "symbol": scope["symbol"],
        "scoring_version": "v202606_default",
    }[field])]
    page1_ok = (
        successful(page1)
        and [item.get("id") for item in page1_items] == [row["id"] for row in expected_page1]
        and len(page1_items) == 5
        and bool(meta(page1).get("next_cursor"))
        and not page1_mismatches
        and not scope_mismatch
    )
    record(
        "SLICES-PAGE-1",
        "PASS" if page1_ok else "FAIL",
        "first page is bounded, ordered, scope-resolved, and field-identical to DB",
        {"returned_ids": [item.get("id") for item in page1_items], "expected_ids": [row["id"] for row in expected_page1], "field_mismatches": page1_mismatches, "scope_mismatches": scope_mismatch},
        page1,
    )

    cursor = meta(page1).get("next_cursor")
    walk_args = scope_args(scope, db_rows, 7)
    walk_page = client.tool("SLICES-WALK-1", "factor_get_metric_slices", walk_args)
    walked = rows(walk_page)
    cursors_seen: set[str] = set()
    pages = [walked]
    cursor = meta(walk_page).get("next_cursor")
    if cursor:
        cursors_seen.add(str(cursor))
    while cursor and len(pages) < 20:
        next_call = client.tool(f"SLICES-WALK-{len(pages) + 1}", "factor_get_metric_slices", {**walk_args, "cursor": cursor})
        next_rows = rows(next_call)
        pages.append(next_rows)
        next_cursor = meta(next_call).get("next_cursor")
        if next_cursor and str(next_cursor) in cursors_seen:
            record("SLICES-CURSOR-LOOP", "FAIL", "cursor continuation must advance", {"cursor_repeated": True}, next_call)
            break
        if next_cursor:
            cursors_seen.add(str(next_cursor))
        cursor = next_cursor
        if not successful(next_call):
            break
    walked_items = [item for page in pages for item in page]
    walked_ids = [item.get("id") for item in walked_items]
    expected_ids = [row["id"] for row in db_rows]
    field_failures = []
    for item in walked_items:
        db_row = db_by_id.get(str(item.get("id")))
        mismatch = ["missing_db_row"] if not db_row else identity_mismatches(item, db_row)
        if mismatch:
            field_failures.append({"id": item.get("id"), "fields": mismatch})
    monotonic = all(
        (parse_time(left.get("as_of_time")) or datetime.min.replace(tzinfo=UTC), left.get("id"))
        <= (parse_time(right.get("as_of_time")) or datetime.min.replace(tzinfo=UTC), right.get("id"))
        for left, right in zip(walked_items, walked_items[1:])
    )
    walk_ok = successful(walk_page) and walked_ids == expected_ids and len(walked_ids) == len(set(walked_ids)) and monotonic and not field_failures
    record(
        "SLICES-CURSOR-WALK",
        "PASS" if walk_ok else "FAIL",
        "cursor continuation returns every DB row once, in ascending time/id order",
        {"page_counts": [len(page) for page in pages], "returned_count": len(walked_ids), "expected_count": len(expected_ids), "duplicate_ids": len(walked_ids) - len(set(walked_ids)), "monotonic": monotonic, "field_failures": field_failures[:10]},
        walk_page,
        "Known end-time boundary recheck: the selected end instant is currently treated as exclusive; a missing final row is not a new cursor defect.",
    )

    # A control range ending after the final row separates cursor behavior
    # from the known end-time boundary behavior of the selected API contract.
    extended_args = scope_args(scope, db_rows, 7)
    extended_args["end_time"] = period_iso(db_rows[-1]["slice_end"] + timedelta(days=1))
    extended_page = client.tool("SLICES-CURSOR-WALK-EXTENDED-1", "factor_get_metric_slices", extended_args)
    extended_items = rows(extended_page)
    extended_pages = [extended_items]
    extended_cursor = meta(extended_page).get("next_cursor")
    while extended_cursor and len(extended_pages) < 20:
        next_extended = client.tool(
            f"SLICES-CURSOR-WALK-EXTENDED-{len(extended_pages) + 1}",
            "factor_get_metric_slices",
            {**extended_args, "cursor": extended_cursor},
        )
        extended_pages.append(rows(next_extended))
        extended_cursor = meta(next_extended).get("next_cursor")
        if not successful(next_extended):
            break
    extended_items = [item for page in extended_pages for item in page]
    extended_ids = [item.get("id") for item in extended_items]
    extended_ok = successful(extended_page) and extended_ids == expected_ids and len(extended_ids) == len(set(extended_ids))
    record(
        "SLICES-CURSOR-WALK-EXTENDED",
        "PASS" if extended_ok else "FAIL",
        "when the end instant is after the final row, cursor continuation returns all DB rows exactly once",
        {"page_counts": [len(page) for page in extended_pages], "returned_count": len(extended_ids), "expected_count": len(expected_ids), "returned_ids": extended_ids},
        extended_page,
    )

    first_walk_cursor = next(iter(cursors_seen), None)
    if first_walk_cursor is None:
        # The walk should have produced at least one continuation for >=10 rows.
        record("SLICES-CURSOR-PRECONDITION", "BLOCKED", "a continuation cursor exists", None)
    else:
        # Use the first continuation cursor captured from the first walk page.
        first_cursor = first_walk_cursor
        if first_cursor:
            changed_limit = client.tool("SLICES-CURSOR-LIMIT-BIND", "factor_get_metric_slices", {**walk_args, "cursor": first_cursor, "limit": 6})
            record("SLICES-CURSOR-LIMIT-BIND", "PASS" if not successful(changed_limit) or not rows(changed_limit) else "FAIL", "changing limit cannot reuse a signed cursor", compact_call(changed_limit), changed_limit)
            changed_symbol = client.tool("SLICES-CURSOR-SYMBOL-BIND", "factor_get_metric_slices", {**walk_args, "cursor": first_cursor, "symbol": ""})
            record("SLICES-CURSOR-SYMBOL-BIND", "PASS" if not successful(changed_symbol) or not rows(changed_symbol) else "FAIL", "changing symbol cannot reuse a signed cursor", compact_call(changed_symbol), changed_symbol)
            tampered = str(first_cursor)[:-1] + ("A" if str(first_cursor)[-1:] != "A" else "B")
            tampered_call = client.tool("SLICES-CURSOR-TAMPER", "factor_get_metric_slices", {**walk_args, "cursor": tampered})
            record("SLICES-CURSOR-TAMPER", "PASS" if not successful(tampered_call) or not rows(tampered_call) else "FAIL", "tampered cursor is rejected", compact_call(tampered_call), tampered_call)

    first = db_rows[0]
    second = db_rows[1]
    exact_first_args = {**base, "start_time": period_iso(first["slice_start"]), "end_time": period_iso(first["slice_end"]), "limit": 10}
    exact_first = client.tool("SLICES-BOUNDARY-FIRST", "factor_get_metric_slices", exact_first_args)
    exact_ids = [item.get("id") for item in rows(exact_first)]
    record("SLICES-BOUNDARY-FIRST", "PASS" if successful(exact_first) and first["id"] in exact_ids else "FAIL", "exact first slice boundaries include the first row", {"expected_id": first["id"], "returned_ids": exact_ids}, exact_first)

    before_end_args = {**exact_first_args, "end_time": period_iso(first["slice_end"] - timedelta(microseconds=1))}
    before_end = client.tool("SLICES-BOUNDARY-END-BEFORE", "factor_get_metric_slices", before_end_args)
    before_ids = [item.get("id") for item in rows(before_end)]
    record("SLICES-BOUNDARY-END-BEFORE", "PASS" if successful(before_end) and first["id"] not in before_ids else "FAIL", "an end instant immediately before slice end excludes that slice", {"excluded_id": first["id"], "returned_ids": before_ids}, before_end, "Boundary recheck; a failure would reproduce the known end-time boundary defect.")

    second_range_args = {**base, "start_time": period_iso(second["slice_start"]), "end_time": period_iso(second["slice_end"]), "limit": 10}
    second_range = client.tool("SLICES-BOUNDARY-SECOND", "factor_get_metric_slices", second_range_args)
    second_ids = [item.get("id") for item in rows(second_range)]
    record("SLICES-BOUNDARY-SECOND", "PASS" if successful(second_range) and second["id"] in second_ids and first["id"] not in second_ids else "FAIL", "a range starting at the second slice excludes the first and includes the second", {"first_id": first["id"], "second_id": second["id"], "returned_ids": second_ids}, second_range)

    reversed_range = client.tool("SLICES-INVALID-RANGE", "factor_get_metric_slices", {**base, "start_time": period_iso(second["slice_end"]), "end_time": period_iso(first["slice_start"]), "limit": 5})
    record("SLICES-INVALID-RANGE", "PASS" if not successful(reversed_range) or not rows(reversed_range) else "FAIL", "end before start is rejected or returns no rows", compact_call(reversed_range), reversed_range)

    # Verify the same run and factor in aggregate TS scope when available.
    aggregate_scope = dict(scope)
    aggregate_scope["symbol"] = ""
    aggregate_rows = db_snapshot_rows(aggregate_scope)
    if aggregate_rows:
        aggregate_args = scope_args(aggregate_scope, aggregate_rows, 3)
        aggregate_call = client.tool("SLICES-AGGREGATE-SCOPE", "factor_get_metric_slices", aggregate_args)
        aggregate_ok = successful(aggregate_call) and all(str(item.get("symbol") or "") == "" for item in rows(aggregate_call))
        record("SLICES-AGGREGATE-SCOPE", "PASS" if aggregate_ok else "FAIL", "empty symbol selects aggregate rows in the same run/scope", {"returned_ids": [item.get("id") for item in rows(aggregate_call)], "returned_symbols": [item.get("symbol") for item in rows(aggregate_call)]}, aggregate_call)
    else:
        record("SLICES-AGGREGATE-SCOPE", "BLOCKED", "aggregate rows exist for the selected run/factor", "no aggregate fixture")

    after_watermark = table_watermark(scope)
    db_unchanged = before_watermark == after_watermark
    record("DB-READONLY-SNAPSHOT", "PASS" if db_unchanged else "FAIL", "MCP read calls do not mutate the slice table", {"before": before_watermark, "after": after_watermark, "unchanged": db_unchanged})
    counts = {status: sum(1 for case in cases if case["status"] == status) for status in ("PASS", "FAIL", "BLOCKED")}
    summary = {
        "run_id": stamp,
        "environment": "test",
        "mcp_url": MCP_URL,
        "mode": "READ_ONLY",
        "scope": scope,
        "db_before": before_watermark,
        "db_after": after_watermark,
        "db_unchanged": db_unchanged,
        "status_counts": counts,
        "cases": cases,
        "sensitive_values_written": False,
    }
    write_json(output / "summary.json", summary)
    lines = [
        "# Metric slice reconciliation",
        "",
        f"- Status: PASS={counts['PASS']} / FAIL={counts['FAIL']} / BLOCKED={counts['BLOCKED']}",
        f"- DB unchanged: `{db_unchanged}`",
        f"- Scope: `{scope['run_id']}` / factor `{scope['factor_id']}` / symbol `{scope['symbol']}`",
        "",
        "| Case | Status | Expected | Actual |",
        "|---|---|---|---|",
    ]
    for case in cases:
        actual = json.dumps(case.get("actual"), ensure_ascii=False, separators=(",", ":"), default=json_default)
        lines.append(f"| {case['case_id']} | {case['status']} | {case['expected']} | `{actual}` |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "status_counts": counts, "db_unchanged": db_unchanged}, ensure_ascii=False))


if __name__ == "__main__":
    main()
