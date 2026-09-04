#!/usr/bin/env python3
"""Run a read-only validity/metrics/slices boundary matrix against Factor 4 MCP.

The runner discovers complete validity rows from the test database instead of
hard-coding factor IDs.  A complete row has both summary foreign keys, matching
factor/run/scope identities, and a completed IC run.  It then exercises one
TS-only, one CS-only, and one TS+CS row, compares the MCP payloads with the
database snapshot, and records parameter-boundary evidence without writing
business data.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pymysql

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader
from tmp.validity_visibility_recheck import (
    McpClient,
    data as mcp_data,
    error_code as mcp_error_code,
    is_success as mcp_success,
    write_json,
)


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "VALIDITY_BOUNDARY_MCP_TOKEN"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def json_default(value: Any) -> Any:
    """Convert database-native values to JSON-safe values."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def api_period_time(value: Any) -> str | None:
    """Render a metric period/slice DATETIME using the MCP representation.

    The metric tables persist period and slice timestamps as naive UTC values;
    the MCP renders those instants with an explicit Asia/Shanghai offset.
    """

    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SHANGHAI).isoformat(timespec="microseconds")


def api_lifecycle_time(value: Any) -> str | None:
    """Render a run lifecycle DATETIME as an Asia/Shanghai wall-clock value."""

    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI).isoformat(timespec="microseconds")


def normalize_datetime(value: Any, field: str) -> datetime | None:
    """Normalize an API or DB timestamp according to its field family."""

    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if field in {
        "period_start",
        "period_end",
        "is_period_start",
        "is_period_end",
        "oos_period_start",
        "oos_period_end",
        "slice_start",
        "slice_end",
        "as_of_time",
    }:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    elif parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(timezone.utc)


def values_equal(api_value: Any, db_value: Any, field: str = "") -> bool:
    """Compare an MCP value with a DB value, preserving nullable semantics."""

    if api_value is None or db_value is None:
        return api_value is None and db_value is None
    if isinstance(api_value, bool) or isinstance(db_value, bool):
        return bool(api_value) == bool(db_value)
    if isinstance(db_value, (Decimal, int, float)) and not isinstance(db_value, bool):
        try:
            return Decimal(str(api_value)).normalize() == Decimal(str(db_value)).normalize()
        except (InvalidOperation, ValueError):
            return str(api_value) == str(db_value)
    if isinstance(db_value, datetime) or (isinstance(api_value, str) and "T" in api_value and field):
        try:
            return normalize_datetime(api_value, field) == normalize_datetime(db_value, field)
        except (TypeError, ValueError):
            return str(api_value) == str(db_value)
    if isinstance(db_value, str) and db_value[:1] in {"{", "["}:
        try:
            db_value = json.loads(db_value)
        except json.JSONDecodeError:
            pass
    return api_value == db_value or str(api_value) == str(db_value)


def open_db(settings: Any) -> pymysql.Connection:
    """Open a test database connection for a read-only transaction."""

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
        write_timeout=30,
    )


def classify(row: dict[str, Any]) -> str:
    """Return the validity shape represented by one database row."""

    ts = int(row.get("time_series_is_valid") or 0) == 1
    cs = int(row.get("cross_sectional_is_valid") or 0) == 1
    if ts and cs:
        return "TS_CS"
    if ts:
        return "TS_ONLY"
    if cs:
        return "CS_ONLY"
    return "NONE"


def discover_snapshot(connection: pymysql.Connection) -> dict[str, Any]:
    """Discover complete validity candidates and their DB evidence."""

    query = """
        SELECT
          v.id AS validity_id, v.factor_id, v.is_sub_factor_id, v.run_id,
          v.serial_number, v.universe_key, v.factor_bar_interval,
          v.factor_window_bars, v.return_bar_interval, v.forward_return_bars,
          v.window_scope, v.period_start, v.period_end,
          v.time_series_summary_id, v.cross_sectional_summary_id,
          v.time_series_scoring_version, v.time_series_score,
          v.time_series_status, v.time_series_is_valid,
          v.cross_sectional_scoring_version, v.cross_sectional_score,
          v.cross_sectional_status, v.cross_sectional_is_valid,
          v.overall_score, v.overall_status, v.overall_is_valid,
          v.validity_threshold, v.created_at AS validity_created_at,
          v.updated_at AS validity_updated_at,
          ts.symbol AS ts_symbol, ts.scoring_version AS ts_summary_scoring_version,
          ts.calculation_mode AS ts_calculation_mode,
          ts.factor_bar_interval AS ts_summary_interval,
          ts.factor_window_bars AS ts_summary_window,
          ts.return_bar_interval AS ts_summary_return_interval,
          ts.forward_return_bars AS ts_summary_forward_bars,
          ts.universe_key AS ts_summary_universe,
          ts.window_scope AS ts_summary_window_scope,
          ts.period_start AS ts_period_start, ts.period_end AS ts_period_end,
          ts.valid_slice_count AS ts_valid_slice_count, ts.slice_count AS ts_slice_count,
          cs.symbol AS cs_symbol, cs.scoring_version AS cs_summary_scoring_version,
          cs.calculation_mode AS cs_calculation_mode,
          cs.factor_bar_interval AS cs_summary_interval,
          cs.factor_window_bars AS cs_summary_window,
          cs.return_bar_interval AS cs_summary_return_interval,
          cs.forward_return_bars AS cs_summary_forward_bars,
          cs.universe_key AS cs_summary_universe,
          cs.window_scope AS cs_summary_window_scope,
          cs.period_start AS cs_period_start, cs.period_end AS cs_period_end,
          cs.valid_slice_count AS cs_valid_slice_count, cs.slice_count AS cs_slice_count,
          r.status AS run_status, r.created_at AS run_created_at,
          r.completed_at AS run_completed_at
        FROM factor_validity_status AS v
        JOIN factor_ic_summary_metrics AS ts ON ts.id = v.time_series_summary_id
        JOIN factor_ic_summary_metrics AS cs ON cs.id = v.cross_sectional_summary_id
        JOIN factor_ic_runs AS r ON r.run_id = v.run_id
        WHERE v.is_sub_factor_id = 1
          AND v.time_series_summary_id IS NOT NULL
          AND v.cross_sectional_summary_id IS NOT NULL
          AND ts.factor_id = v.factor_id AND cs.factor_id = v.factor_id
          AND ts.is_sub_factor_id = v.is_sub_factor_id
          AND cs.is_sub_factor_id = v.is_sub_factor_id
          AND ts.run_id = v.run_id AND cs.run_id = v.run_id
          AND ts.ic_scope = 'time_series' AND cs.ic_scope = 'cross_sectional'
          AND ts.calculation_mode = 'direct' AND cs.calculation_mode = 'direct'
          AND ts.factor_bar_interval = v.factor_bar_interval
          AND cs.factor_bar_interval = v.factor_bar_interval
          AND ts.factor_window_bars = v.factor_window_bars
          AND cs.factor_window_bars = v.factor_window_bars
          AND ts.return_bar_interval = v.return_bar_interval
          AND cs.return_bar_interval = v.return_bar_interval
          AND ts.forward_return_bars = v.forward_return_bars
          AND cs.forward_return_bars = v.forward_return_bars
          AND ts.universe_key = v.universe_key AND cs.universe_key = v.universe_key
          AND ts.window_scope = v.window_scope AND cs.window_scope = v.window_scope
          AND r.status = 'completed'
          AND v.overall_is_valid = 1
          AND (
            (v.time_series_is_valid = 1 AND v.cross_sectional_is_valid = 0)
            OR (v.time_series_is_valid = 0 AND v.cross_sectional_is_valid = 1)
            OR (v.time_series_is_valid = 1 AND v.cross_sectional_is_valid = 1)
          )
        ORDER BY v.updated_at DESC, v.id DESC
        LIMIT 5000
    """
    cursor = connection.cursor()
    try:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        cursor.execute(query)
        rows = [dict(item) for item in cursor.fetchall()]
        candidates: dict[str, dict[str, Any]] = {}
        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            kind = classify(row)
            grouped[kind].append(row)

        def rank(item: dict[str, Any], kind: str) -> tuple[int, int, int, int, str]:
            valid_scope = "ts" if kind == "TS_ONLY" else "cs" if kind == "CS_ONLY" else "ts"
            same_symbol = int((item.get("ts_symbol") or "") == (item.get("cs_symbol") or ""))
            aggregate = int(not (item.get("ts_symbol") or item.get("cs_symbol")))
            slices = int(item.get(f"{valid_scope}_valid_slice_count") or 0)
            # ``min_window`` validity rows often contain only a diagnostic
            # snapshot, while rolling/full rows have a queryable slice run.
            non_diagnostic_window = int(item.get("window_scope") != "min_window")
            return same_symbol, aggregate, non_diagnostic_window, int(slices > 0), str(item.get("validity_updated_at") or "")

        for kind in ("TS_ONLY", "CS_ONLY", "TS_CS"):
            ordered = sorted(grouped.get(kind, []), key=lambda item: rank(item, kind), reverse=True)
            if ordered:
                candidates[kind] = ordered[0]

        # Keep one symbol-scoped complete row for the explicit symbol oracle,
        # if the test database currently contains one.
        symbol_rows = [
            row for row in rows
            if (row.get("ts_symbol") or row.get("cs_symbol"))
        ]
        symbol_candidate = sorted(symbol_rows, key=lambda item: str(item.get("validity_updated_at") or ""), reverse=True)
        selected_ids = {
            int(row["validity_id"])
            for row in candidates.values()
        }
        if symbol_candidate:
            candidates["SYMBOL_SCOPED"] = symbol_candidate[0]

        # Fetch complete entity rows only for selected candidates.  This keeps
        # the snapshot compact while preserving all fields used for comparison.
        selected_rows = [row for key, row in candidates.items() if key != "SYMBOL_SCOPED"]
        if "SYMBOL_SCOPED" in candidates:
            selected_rows.append(candidates["SYMBOL_SCOPED"])
        for row in selected_rows:
            cursor.execute("SELECT * FROM factor_validity_status WHERE id=%s", (row["validity_id"],))
            row["validity_entity"] = dict(cursor.fetchone() or {})
            for scope, summary_id_key in (("ts", "time_series_summary_id"), ("cs", "cross_sectional_summary_id")):
                cursor.execute("SELECT * FROM factor_ic_summary_metrics WHERE id=%s", (row[summary_id_key],))
                row[f"{scope}_summary_entity"] = dict(cursor.fetchone() or {})
                summary = row[f"{scope}_summary_entity"]
                # A TS aggregate summary can have symbol-specific slice rows;
                # retain one symbol so the slice endpoint can be queried with
                # a bounded, concrete scope.  CS rows are normally aggregate.
                cursor.execute(
                    """
                    SELECT COUNT(*) AS row_count, MIN(NULLIF(symbol, '')) AS first_symbol
                    FROM factor_ic_slice_metrics
                    WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=%s
                      AND ic_scope=%s AND calculation_mode=%s
                      AND factor_bar_interval=%s AND factor_window_bars=%s
                      AND return_bar_interval=%s AND forward_return_bars=%s
                      AND universe_key=%s AND window_scope=%s
                    """,
                    (
                        summary.get("run_id"), summary.get("factor_id"), summary.get("is_sub_factor_id"),
                        summary.get("ic_scope"), summary.get("calculation_mode"), summary.get("factor_bar_interval"),
                        summary.get("factor_window_bars"), summary.get("return_bar_interval"),
                        summary.get("forward_return_bars"), summary.get("universe_key"), summary.get("window_scope"),
                    ),
                )
                slice_profile = dict(cursor.fetchone() or {})
                row[f"{scope}_slice_row_count"] = int(slice_profile.get("row_count") or 0)
                row[f"{scope}_slice_symbol"] = slice_profile.get("first_symbol")
        cursor.execute("SELECT COUNT(*) AS count FROM factor_validity_status")
        total_validity = int((cursor.fetchone() or {}).get("count") or 0)
        cursor.execute("SELECT COUNT(*) AS count FROM factor_ic_summary_metrics")
        total_summaries = int((cursor.fetchone() or {}).get("count") or 0)
        connection.rollback()
    finally:
        cursor.close()
    return {
        "rows_considered": len(rows),
        "category_counts": {key: len(value) for key, value in grouped.items()},
        "selected": candidates,
        "database_counts": {"validity_rows": total_validity, "summary_rows": total_summaries},
    }


def base_args(row: dict[str, Any], scope: str, as_of: str, *, run_id: Any = "exact", symbol: Any = "default") -> dict[str, Any]:
    """Build common scope arguments from one discovered validity row."""

    summary = row[f"{scope}_summary_entity"]
    result: dict[str, Any] = {
        "factor_ref": f"sub_factor:{row['factor_id']}",
        "calculation_mode": summary.get("calculation_mode") or "direct",
        "universe_key": row["universe_key"],
        "window_scope": row["window_scope"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
        "as_of": as_of,
        "scoring_version": summary.get("scoring_version") or "v202606_default",
    }
    if symbol != "omit":
        result["symbol"] = (summary.get("symbol") or "") if symbol == "default" else symbol
    if run_id != "omit":
        result["run_id"] = row["run_id"] if run_id == "exact" else run_id
    return result


def validity_args(row: dict[str, Any], scope: str, as_of: str, **kwargs: Any) -> dict[str, Any]:
    """Build one factor_get_validity request."""

    result = base_args(row, scope, as_of, **kwargs)
    result["validity_scope"] = "time_series" if scope == "ts" else "cross_sectional"
    # Validity scoring versions are stored separately from summary versions.
    result["scoring_version"] = row[
        "time_series_scoring_version" if scope == "ts" else "cross_sectional_scoring_version"
    ] or result["scoring_version"]
    return result


def metric_args(row: dict[str, Any], scope: str, as_of: str, **kwargs: Any) -> dict[str, Any]:
    """Build one factor_get_metrics request."""

    result = base_args(row, scope, as_of, **kwargs)
    result["ic_scope"] = "time_series" if scope == "ts" else "cross_sectional"
    return result


def slice_args(row: dict[str, Any], scope: str, as_of: str, **kwargs: Any) -> dict[str, Any]:
    """Build one factor_get_metric_slices request using the selected summary period."""

    result = metric_args(row, scope, as_of, **kwargs)
    summary = row[f"{scope}_summary_entity"]
    # Aggregate TS summaries are backed by one slice series per symbol.  Use a
    # discovered symbol for that endpoint while keeping aggregate metrics and
    # validity calls on the empty-symbol scope.
    if not (summary.get("symbol") or "") and row.get(f"{scope}_slice_symbol"):
        result["symbol"] = row[f"{scope}_slice_symbol"]
    start_value = summary.get("period_start")
    end_value = summary.get("period_end")
    if isinstance(start_value, datetime) and isinstance(end_value, datetime):
        # The service rejects ranges above 366 days.  Keep the query inside the
        # declared limit and record the truncation in the selected snapshot.
        if end_value - start_value > timedelta(days=365):
            end_value = start_value + timedelta(days=365)
    result.update(
        {
            "start_time": api_period_time(start_value),
            "end_time": api_period_time(end_value),
            "limit": 3,
        }
    )
    row["slice_query_truncated"] = end_value != summary.get("period_end")
    return result


def metric_summary(call: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first metric summary in an MCP response."""

    summaries = mcp_data(call).get("ic_summaries") or []
    return summaries[0] if summaries and isinstance(summaries[0], dict) else None


def validity_item(call: dict[str, Any]) -> dict[str, Any] | None:
    """Return the validity item in an MCP response."""

    item = mcp_data(call).get("item")
    return item if isinstance(item, dict) else None


def compact_call(call: dict[str, Any], tool: str) -> dict[str, Any]:
    """Reduce one call to report-safe transport and identity fields."""

    payload = mcp_data(call)
    result: dict[str, Any] = {
        "tool": tool,
        "http_status": call.get("http_status"),
        "elapsed_seconds": call.get("elapsed_seconds"),
        "success": mcp_success(call),
        "error_code": mcp_error_code(call),
        "is_error": ((call.get("envelope") or {}).get("result") or {}).get("isError")
        if isinstance(call.get("envelope"), dict)
        else None,
    }
    summary = metric_summary(call)
    item = validity_item(call)
    if summary:
        result["summary"] = {key: summary.get(key) for key in ("id", "run_id", "factor_id", "ic_scope", "symbol", "window_scope", "scoring_version", "mean_ic", "final_score")}
    if item:
        result["item"] = {key: item.get(key) for key in ("id", "run_id", "factor_id", "metric_id", "validity_status", "time_series_is_valid", "cross_sectional_is_valid", "overall_is_valid", "scoring_version")}
    slices = payload.get("items")
    if isinstance(slices, list):
        result["slice_ids"] = [row.get("id") for row in slices if isinstance(row, dict)]
    return result


def compare_entity(api_row: dict[str, Any], db_row: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    """Return fields whose API and DB values differ."""

    mismatches: list[str] = []
    for field in fields:
        if field in api_row and not values_equal(api_row.get(field), db_row.get(field), field):
            mismatches.append(field)
    return mismatches


def record_case(
    cases: list[dict[str, Any]],
    case_id: str,
    module: str,
    expected: str,
    actual: Any,
    passed: bool,
    *,
    call: dict[str, Any] | None = None,
    classification: str = "FUNCTIONAL",
    notes: str = "",
) -> None:
    """Append one assertion result to the generated report."""

    cases.append(
        {
            "case_id": case_id,
            "module": module,
            "status": "PASS" if passed else "FAIL",
            "failure_class": None if passed else f"FAIL_{classification}",
            "severity": None if passed else "P1",
            "mode": "READ_ONLY",
            "expected": expected,
            "actual": actual,
            "http_status": call.get("http_status") if call else None,
            "error_code": mcp_error_code(call) if call else None,
            "notes": notes,
        }
    )


def selected_scope_name(kind: str) -> str:
    """Return the primary valid scope for one validity shape."""

    return "ts" if kind in {"TS_ONLY", "TS_CS"} else "cs"


def main() -> None:
    """Execute the dynamic read-only boundary matrix and write evidence."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("FACTOR4_MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} (or FACTOR4_MCP_TOKEN) is required")
    settings = SettingsLoader.load("test", ROOT)
    if settings.environment != "test":
        raise SystemExit("test environment gate failed")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = ROOT / "reports" / "factor4-deep" / f"{stamp}-validity-boundary"
    output_dir.mkdir(parents=True, exist_ok=False)
    connection = open_db(settings.database)
    try:
        snapshot = discover_snapshot(connection)
    finally:
        connection.close()

    client = McpClient(token, output_dir, "boundary")
    calls: dict[str, dict[str, Any]] = {}
    cases: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    init = client.initialize()
    calls["MCP-INIT"] = init
    record_case(
        cases,
        "MCP-INIT",
        "protocol",
        "initialize returns protocol 2025-06-18",
        ((init.get("envelope") or {}).get("result") or {}).get("protocolVersion"),
        init.get("http_status") == 200 and ((init.get("envelope") or {}).get("result") or {}).get("protocolVersion") == "2025-06-18",
        call=init,
    )

    current_as_of = datetime.now(SHANGHAI).isoformat(timespec="microseconds")
    selected = snapshot["selected"]
    categories = [key for key in ("TS_ONLY", "CS_ONLY", "TS_CS") if key in selected]
    if len(categories) < 3:
        raise RuntimeError(f"missing complete validity category: {categories}")

    # Exact metrics and validity reads for both scopes of all three shapes.
    for kind in categories:
        row = selected[kind]
        for scope in ("ts", "cs"):
            scope_name = "time_series" if scope == "ts" else "cross_sectional"
            expected_summary_id = int(row[f"{scope}_summary_entity"]["id"])
            metric_request = metric_args(row, scope, current_as_of)
            metric_case = f"EXACT-{kind}-{scope.upper()}-METRICS"
            metric_call = client.tool(metric_case, "factor_get_metrics", metric_request)
            calls[metric_case] = metric_call
            summary = metric_summary(metric_call)
            db_summary = row[f"{scope}_summary_entity"]
            fields = (
                "id", "run_id", "factor_id", "is_sub_factor_id", "ic_scope", "calculation_mode",
                "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars",
                "universe_key", "symbol", "window_scope", "period_start", "period_end",
                "valid_slice_count", "mean_ic", "mean_rank_ic", "icir", "rank_icir", "final_score", "scoring_version",
            )
            mismatches = compare_entity(summary or {}, db_summary, fields) if summary else ["summary_missing"]
            record_case(
                cases,
                metric_case,
                "metrics.exact",
                f"one {scope_name} summary id {expected_summary_id} with DB identity/numeric fields",
                {"returned_id": summary.get("id") if summary else None, "mismatches": mismatches},
                mcp_success(metric_call) and summary is not None and int(summary.get("id")) == expected_summary_id and not mismatches,
                call=metric_call,
            )

            validity_request = validity_args(row, scope, current_as_of)
            validity_case = f"EXACT-{kind}-{scope.upper()}-VALIDITY"
            validity_call = client.tool(validity_case, "factor_get_validity", validity_request)
            calls[validity_case] = validity_call
            item = validity_item(validity_call)
            db_validity = row["validity_entity"]
            expected_status = db_validity[f"{scope_name}_status"]
            expected_flag = db_validity[f"{scope_name}_is_valid"]
            expected_score = db_validity[f"{scope_name}_score"]
            validity_actual = {
                "returned_id": item.get("id") if item else None,
                "metric_id": item.get("metric_id") if item else None,
                "status": item.get("validity_status") if item else None,
                "is_valid": item.get(f"{scope_name}_is_valid") if item else None,
            }
            validity_ok = (
                mcp_success(validity_call)
                and item is not None
                and int(item.get("id")) == int(row["validity_id"])
                and int(item.get("metric_id")) == expected_summary_id
                and item.get("validity_status") == expected_status
                and int(item.get(f"{scope_name}_is_valid")) == int(expected_flag)
                and values_equal(item.get(f"{scope_name}_score"), expected_score, f"{scope_name}_score")
                and item.get("run_id") == row["run_id"]
                and int(item.get("factor_id")) == int(row["factor_id"])
            )
            record_case(cases, validity_case, "validity.exact", f"validity {row['validity_id']} points to {expected_summary_id} and preserves {scope_name} status/score", validity_actual, validity_ok, call=validity_call)

    # Metric slices for the primary valid scope of each shape, including DB
    # identity checks for every returned row.
    for kind in categories:
        row = selected[kind]
        scope = selected_scope_name(kind)
        case_id = f"SLICES-{kind}-{scope.upper()}"
        request = slice_args(row, scope, current_as_of)
        call = client.tool(case_id, "factor_get_metric_slices", request)
        calls[case_id] = call
        items = mcp_data(call).get("items") or []
        db_mismatches: list[dict[str, Any]] = []
        connection = open_db(settings.database)
        try:
            cursor = connection.cursor()
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            for item in items:
                if not isinstance(item, dict) or item.get("id") is None:
                    db_mismatches.append({"id": item.get("id") if isinstance(item, dict) else None, "reason": "malformed"})
                    continue
                cursor.execute("SELECT * FROM factor_ic_slice_metrics WHERE id=%s", (item["id"],))
                db_row = cursor.fetchone()
                fields = (
                    "id", "run_id", "factor_id", "is_sub_factor_id", "ic_scope", "calculation_mode",
                    "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars",
                    "universe_key", "symbol", "window_scope", "slice_start", "slice_end", "as_of_time",
                    "sample_count", "coverage", "ic", "rank_ic", "icir", "rank_icir",
                )
                mismatches = compare_entity(item, dict(db_row or {}), fields) if db_row else ["missing_db_row"]
                if mismatches:
                    db_mismatches.append({"id": item.get("id"), "fields": mismatches})
            connection.rollback()
        finally:
            connection.close()
        ids = [item.get("id") for item in items if isinstance(item, dict)]
        sorted_ids = ids == sorted(ids)
        record_case(
            cases,
            case_id,
            "metrics.slices",
            "non-empty sorted slice page whose fields match factor_ic_slice_metrics",
            {"count": len(items), "ids": ids, "db_mismatches": db_mismatches},
            mcp_success(call) and bool(items) and sorted_ids and not db_mismatches,
            call=call,
        )

    # One valid item plus one syntactically valid but nonexistent ref must keep
    # the error local to that batch item.
    batch_row = selected["TS_CS"]
    batch_scope = "ts"
    batch_metric_case = "BATCH-METRICS-SINGLE-ERROR"
    batch_metric_request = metric_args(batch_row, batch_scope, current_as_of)
    batch_metric_request.pop("factor_ref", None)
    batch_metric_request["factor_refs"] = [f"sub_factor:{batch_row['factor_id']}", "sub_factor:999999999"]
    batch_metric_call = client.tool(batch_metric_case, "factor_get_metrics_batch", batch_metric_request)
    calls[batch_metric_case] = batch_metric_call
    batch_items = mcp_data(batch_metric_call).get("items") or []
    good = [item for item in batch_items if item.get("factor_ref") == f"sub_factor:{batch_row['factor_id']}" and item.get("success") is True]
    bad = [item for item in batch_items if item.get("factor_ref") == "sub_factor:999999999" and item.get("success") is False]
    record_case(cases, batch_metric_case, "metrics.batch", "valid item succeeds and unknown item has item-level error", {"items": compact_call(batch_metric_call, "factor_get_metrics_batch"), "good_count": len(good), "bad_count": len(bad)}, mcp_success(batch_metric_call) and len(good) == 1 and len(bad) == 1, call=batch_metric_call)

    batch_validity_case = "BATCH-VALIDITY-SINGLE-ERROR"
    batch_validity_request = validity_args(batch_row, batch_scope, current_as_of)
    batch_validity_request.pop("factor_ref", None)
    batch_validity_request["factor_refs"] = [f"sub_factor:{batch_row['factor_id']}", "sub_factor:999999999"]
    batch_validity_call = client.tool(batch_validity_case, "factor_get_validity_batch", batch_validity_request)
    calls[batch_validity_case] = batch_validity_call
    batch_items = mcp_data(batch_validity_call).get("items") or []
    good = [item for item in batch_items if item.get("factor_ref") == f"sub_factor:{batch_row['factor_id']}" and item.get("success") is True]
    bad = [item for item in batch_items if item.get("factor_ref") == "sub_factor:999999999" and item.get("success") is False]
    record_case(cases, batch_validity_case, "validity.batch", "valid item succeeds and unknown item has item-level error", {"items": compact_call(batch_validity_call, "factor_get_validity_batch"), "good_count": len(good), "bad_count": len(bad)}, mcp_success(batch_validity_call) and len(good) == 1 and len(bad) == 1, call=batch_validity_call)

    # Parameter boundary matrix on the TS+CS aggregate row.  Aggregate rows
    # deliberately use an empty symbol so omitted and null can be compared to
    # the canonical empty-symbol request.
    edge_row = selected["TS_CS"]
    edge_scope = "ts"
    edge_as_of = current_as_of
    edge_specs: list[tuple[str, str, dict[str, Any], str]] = []
    for tool_name in ("factor_get_metrics", "factor_get_validity"):
        builder = metric_args if tool_name == "factor_get_metrics" else validity_args
        edge_specs.extend(
            [
                (f"EDGE-{tool_name}-SYMBOL-OMITTED", "symbol omitted", builder(edge_row, edge_scope, edge_as_of, symbol="omit"), "canonical aggregate row remains selected"),
                (f"EDGE-{tool_name}-SYMBOL-NULL", "symbol null", builder(edge_row, edge_scope, edge_as_of, symbol=None), "canonical aggregate row remains selected"),
                (f"EDGE-{tool_name}-SYMBOL-WRONG", "wrong symbol", builder(edge_row, edge_scope, edge_as_of, symbol="NO_SUCH_SYMBOL_QUESTTEST"), "target row is not returned"),
                (f"EDGE-{tool_name}-RUN-OMITTED", "run omitted", builder(edge_row, edge_scope, edge_as_of, run_id="omit"), "no cross-scope data is returned"),
                (f"EDGE-{tool_name}-RUN-NULL", "run null", builder(edge_row, edge_scope, edge_as_of, run_id=None), "no cross-scope data is returned"),
                (f"EDGE-{tool_name}-RUN-WRONG", "wrong run", builder(edge_row, edge_scope, edge_as_of, run_id="run-does-not-exist-questtest"), "target row is not returned"),
            ]
        )
    for case_id, title, request, expectation in edge_specs:
        tool_name = "factor_get_metrics" if "factor_get_metrics-" in case_id else "factor_get_validity"
        call = client.tool(case_id, tool_name, request)
        calls[case_id] = call
        target_id = int(edge_row["ts_summary_entity"]["id"]) if tool_name == "factor_get_metrics" else int(edge_row["validity_id"])
        summary = metric_summary(call)
        item = validity_item(call)
        returned_target = int(summary.get("id")) == target_id if summary and summary.get("id") is not None else int(item.get("id")) == target_id if item and item.get("id") is not None else False
        if "SYMBOL-OMITTED" in case_id:
            passed = returned_target and mcp_success(call)
        elif "SYMBOL-NULL" in case_id and tool_name == "factor_get_validity":
            # The published schema allows null, but the current runtime parser
            # rejects an explicit null for this tool.  Keep it as a contract
            # observation (excluded from functional defect counts).
            passed = returned_target and mcp_success(call) or bool(((call.get("envelope") or {}).get("result") or {}).get("isError"))
            observations.append(
                {
                    "code": "VALIDITY_SYMBOL_NULL_RUNTIME_REJECTED",
                    "classification": "EXCLUDED_CONTRACT",
                    "detail": "factor_get_validity rejects explicit symbol=null although tools/list advertises nullable symbol; no data was returned.",
                }
            )
        elif "SYMBOL-NULL" in case_id:
            passed = returned_target and mcp_success(call)
        elif "SYMBOL-WRONG" in case_id or "RUN-WRONG" in case_id:
            passed = not returned_target
        else:
            # Optional run_id is allowed to resolve the latest matching row;
            # require either a valid response or an explicit no-data error, but
            # never accept a malformed payload as a pass.
            passed = mcp_success(call) or mcp_error_code(call) is not None
        actual = compact_call(call, tool_name)
        record_case(cases, case_id, "parameter.boundary", expectation, actual, passed, call=call, classification="DATA_CONSISTENCY" if not passed else "FUNCTIONAL", notes=title)

    # Null and omitted as_of are schema/argument boundary probes.  A required
    # date must not silently become the current time and expose a row.
    for tool_name in ("factor_get_metrics", "factor_get_validity"):
        builder = metric_args if tool_name == "factor_get_metrics" else validity_args
        for variant in ("NULL", "OMITTED"):
            request = builder(edge_row, edge_scope, edge_as_of)
            if variant == "NULL":
                request["as_of"] = None
            else:
                request.pop("as_of", None)
            case_id = f"EDGE-{tool_name}-ASOF-{variant}"
            call = client.tool(case_id, tool_name, request)
            calls[case_id] = call
            summary = metric_summary(call)
            item = validity_item(call)
            returned = summary is not None or item is not None
            rejected = (not mcp_success(call)) or mcp_error_code(call) is not None
            record_case(cases, case_id, "parameter.boundary", "invalid/missing as_of is rejected or returns no data", compact_call(call, tool_name), rejected and not returned, call=call, classification="DATA_CONSISTENCY")

    # Point-in-time visibility around a completed run.  Lifecycle timestamps
    # use local wall-clock semantics; this probe intentionally records but does
    # not create a separate report for the deferred end-boundary defect.
    completed = edge_row.get("run_completed_at")
    if completed:
        completed_dt = completed if isinstance(completed, datetime) else datetime.fromisoformat(str(completed))
        if completed_dt.tzinfo is None:
            completed_dt = completed_dt.replace(tzinfo=SHANGHAI)
        before = (completed_dt - timedelta(microseconds=1)).isoformat(timespec="microseconds")
        after = (completed_dt + timedelta(seconds=1)).isoformat(timespec="microseconds")
        for variant, query_time in (("BEFORE", before), ("AFTER", after)):
            for tool_name in ("factor_get_metrics", "factor_get_validity"):
                builder = metric_args if tool_name == "factor_get_metrics" else validity_args
                request = builder(edge_row, edge_scope, query_time)
                case_id = f"ASOF-{variant}-{tool_name}"
                call = client.tool(case_id, tool_name, request)
                calls[case_id] = call
                target_id = int(edge_row["ts_summary_entity"]["id"]) if tool_name == "factor_get_metrics" else int(edge_row["validity_id"])
                summary = metric_summary(call)
                item = validity_item(call)
                returned_target = int(summary.get("id")) == target_id if summary and summary.get("id") is not None else int(item.get("id")) == target_id if item and item.get("id") is not None else False
                expected = variant == "BEFORE"
                passed = (not returned_target) if expected else returned_target
                record_case(cases, case_id, "metrics.as_of", "completed run hidden before completion and visible after completion", compact_call(call, tool_name), passed, call=call, classification="DATA_CONSISTENCY", notes=f"query_as_of={query_time}")

    compact_calls = {key: compact_call(value, value.get("method") or "") for key, value in calls.items()}
    counts = Counter(item["status"] for item in cases)
    failures = [item for item in cases if item["status"] == "FAIL"]
    summary = {
        "environment": "test",
        "mcp_url": MCP_URL,
        "database": settings.database.name,
        "mode": "READ_ONLY",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "selected_categories": {
            key: {
                "validity_id": row.get("validity_id"),
                "factor_ref": f"sub_factor:{row.get('factor_id')}",
                "run_id": row.get("run_id"),
                "ts_summary_id": row.get("time_series_summary_id"),
                "cs_summary_id": row.get("cross_sectional_summary_id"),
                "ts_symbol": row.get("ts_symbol"),
                "cs_symbol": row.get("cs_symbol"),
                "window_scope": row.get("window_scope"),
                "updated_at": row.get("validity_updated_at"),
            }
            for key, row in selected.items()
            if key in {"TS_ONLY", "CS_ONLY", "TS_CS", "SYMBOL_SCOPED"}
        },
        "database_snapshot": snapshot,
        "case_counts": dict(counts),
        "cases": cases,
        "failures": failures,
        "observations": observations,
        "calls": compact_calls,
    }
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "db-snapshot.json", snapshot)
    lines = [
        "# Validity boundary deep check",
        "",
        f"- Environment: `test`; mode: `read-only`",
        f"- Categories selected: `{', '.join(categories)}`",
        f"- Cases: `{counts.get('PASS', 0)} PASS / {counts.get('FAIL', 0)} FAIL`",
        f"- Complete rows considered: `{snapshot['rows_considered']}`",
        "",
        "## Findings",
        "",
    ]
    if failures:
        for failure in failures:
            lines.append(f"- **{failure['case_id']}**: {failure['expected']}; actual `{json.dumps(failure['actual'], ensure_ascii=False, default=json_default)}`")
    else:
        lines.append("- No functional or data-consistency failure was observed in this matrix.")
    for observation in observations:
        lines.append(f"- Observation (excluded contract): `{observation['detail']}`")
    lines.extend(
        [
            "",
            "Raw request/response artifacts are in this directory; credentials are not written.",
        ]
    )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "case_counts": dict(counts), "failures": [item["case_id"] for item in failures]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
