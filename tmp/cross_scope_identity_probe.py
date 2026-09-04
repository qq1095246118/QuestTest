#!/usr/bin/env python3
"""Cross-check Factor Data MCP scopes, filters, batches, and cursors.

The probe is deliberately read-only.  It discovers metric and publication
identities from the test database, calls only MCP read tools, and stores
sanitized request/response artifacts for each assertion.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

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
    scalar_equal,
    successful,
)


MCP_URL = os.environ.get(
    "MCP_URL", "https://test-factor-frontend.questvector.ai/mcp/factor-data"
)
TOKEN = os.environ.get("MCP_TOKEN") or os.environ.get("FACTOR4_MCP_TOKEN")
UNKNOWN_REF = "sub_factor:999999999"


def db_connection() -> pymysql.connections.Connection:
    """Open a test-database connection for one read-only snapshot."""

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


def json_default(value: Any) -> str:
    """Serialize database-native values in the compact report."""

    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def api_datetime(value: Any) -> str:
    """Serialize a database timestamp with the explicit UTC offset required by MCP."""

    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    return str(value)


def compact_call(call: dict[str, Any]) -> dict[str, Any]:
    """Return transport and business identity without large payloads."""

    business = call.get("business") or {}
    payload = business.get("data") if isinstance(business.get("data"), dict) else {}
    items = rows(call)
    if not items:
        for key in ("ic_summaries", "performance_summaries", "results", "tags"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = [item for item in candidate if isinstance(item, dict)]
                break
    return {
        "http_status": call.get("http_status"),
        "is_error": call.get("is_error"),
        "error_code": error_code(call),
        "success": successful(call),
        "data_keys": sorted(payload),
        "item_count": len(items),
        "item_refs": [item.get("factor_ref") for item in items],
        "item_ids": [item.get("id") for item in items],
        "next_cursor": bool(meta(call).get("next_cursor")),
        "request_id": meta(call).get("request_id"),
    }


def metric_args(row: dict[str, Any], *, factor_ref: str | None = None, symbol: str | None = None) -> dict[str, Any]:
    """Build an exact metric request from a discovered summary row."""

    return {
        "factor_ref": factor_ref or f"sub_factor:{row['factor_id']}",
        "ic_scope": row["ic_scope"],
        "calculation_mode": row["calculation_mode"],
        "universe_key": row["universe_key"],
        "window_scope": row["window_scope"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scoring_version": row["scoring_version"],
        "symbol": row.get("symbol") if symbol is None else symbol,
        "run_id": row["run_id"],
    }


def same_metric_identity(api: dict[str, Any], db: dict[str, Any]) -> list[str]:
    """List identity/value fields that differ between one API and DB row."""

    mapping = {
        "id": "id",
        "run_id": "run_id",
        "factor_id": "factor_id",
        "ic_scope": "ic_scope",
        "calculation_mode": "calculation_mode",
        "factor_bar_interval": "factor_bar_interval",
        "factor_window_bars": "factor_window_bars",
        "return_bar_interval": "return_bar_interval",
        "forward_return_bars": "forward_return_bars",
        "universe_key": "universe_key",
        "symbol": "symbol",
        "window_scope": "window_scope",
        "scoring_version": "scoring_version",
        "mean_ic": "mean_ic",
        "mean_rank_ic": "mean_rank_ic",
        "icir": "icir",
        "rank_icir": "rank_icir",
        "final_score": "final_score",
    }
    mismatches: list[str] = []
    for api_key, db_key in mapping.items():
        if not scalar_equal(api.get(api_key), db.get(db_key)):
            mismatches.append(api_key)
    return mismatches


def same_slice_identity(api: dict[str, Any], db: dict[str, Any]) -> list[str]:
    """List slice fields that differ after API/DB timestamp normalization."""

    fields = (
        "id",
        "run_id",
        "factor_id",
        "ic_scope",
        "calculation_mode",
        "factor_bar_interval",
        "factor_window_bars",
        "return_bar_interval",
        "forward_return_bars",
        "universe_key",
        "symbol",
        "window_scope",
        "sample_segment",
        "slice_start",
        "slice_end",
        "as_of_time",
        "sample_count",
        "ic",
        "rank_ic",
        "icir",
        "rank_icir",
    )
    return [field for field in fields if not scalar_equal(api.get(field), db.get(field))]


def main() -> None:
    """Execute cross-scope checks and print a compact result summary."""

    if not TOKEN:
        raise SystemExit("MCP_TOKEN or FACTOR4_MCP_TOKEN is required")
    if not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("test MCP host gate failed")

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{run_stamp}-cross-scope-identity"
    output.mkdir(parents=True, exist_ok=False)
    client = MCPClient(TOKEN, output)
    cases: list[dict[str, Any]] = []

    def record(case_id: str, title: str, passed: bool, actual: Any, call: dict[str, Any] | None = None, *, blocked: str | None = None, duplicate_of: str | None = None) -> None:
        """Record one assertion while preserving duplicate/blocked semantics."""

        status = "BLOCKED" if blocked else ("PASS" if passed else "FAIL")
        item = {
            "case_id": case_id,
            "module": "mcp.cross_scope_identity",
            "mode": "READ_ONLY",
            "status": status,
            "title": title,
            "actual": actual,
            "severity": None if status != "FAIL" else "P1",
            "failure_class": None if status != "FAIL" else "FAIL_DATA",
        }
        if call is not None:
            item["call"] = compact_call(call)
        if blocked:
            item["blocking_reason"] = blocked
        if duplicate_of:
            item["duplicate_of"] = duplicate_of
        cases.append(item)

    # Discover all fixtures from one consistent DB snapshot.
    db = db_connection()
    try:
        with db.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                """
                SELECT s.*
                FROM factor_ic_summary_metrics s
                JOIN factor_ic_runs r ON r.run_id=s.run_id AND r.status='completed'
                WHERE s.is_sub_factor_id=1 AND COALESCE(s.symbol,'')=''
                  AND s.ic_scope='time_series' AND s.calculation_mode='direct'
                  AND EXISTS (
                    SELECT 1
                    FROM factor_ic_summary_metrics c
                    WHERE c.factor_id=s.factor_id AND c.is_sub_factor_id=1
                      AND c.run_id=s.run_id AND c.ic_scope='cross_sectional'
                      AND c.calculation_mode=s.calculation_mode
                      AND c.universe_key=s.universe_key
                      AND c.window_scope=s.window_scope
                      AND c.factor_bar_interval=s.factor_bar_interval
                      AND c.factor_window_bars=s.factor_window_bars
                      AND c.return_bar_interval=s.return_bar_interval
                      AND c.forward_return_bars=s.forward_return_bars
                      AND COALESCE(c.symbol,'')=COALESCE(s.symbol,'')
                      AND c.scoring_version <=> s.scoring_version
                  )
                  AND EXISTS (
                    SELECT 1
                    FROM factor_ic_summary_metrics d
                    WHERE d.factor_id<>s.factor_id AND d.is_sub_factor_id=1
                      AND d.run_id=s.run_id AND d.ic_scope='time_series'
                      AND d.calculation_mode=s.calculation_mode
                      AND d.universe_key=s.universe_key
                      AND d.window_scope=s.window_scope
                      AND d.factor_bar_interval=s.factor_bar_interval
                      AND d.factor_window_bars=s.factor_window_bars
                      AND d.return_bar_interval=s.return_bar_interval
                      AND d.forward_return_bars=s.forward_return_bars
                      AND COALESCE(d.symbol,'')=COALESCE(s.symbol,'')
                      AND d.scoring_version <=> s.scoring_version
                  )
                ORDER BY r.completed_at DESC, s.updated_at DESC, s.id DESC
                LIMIT 100
                """
            )
            ts_candidates = [dict(row) for row in cursor.fetchall()]
            selected_ts: dict[str, Any] | None = None
            selected_cs: dict[str, Any] | None = None
            selected_second: dict[str, Any] | None = None
            for candidate in ts_candidates:
                cursor.execute(
                    """
                    SELECT * FROM factor_ic_summary_metrics
                    WHERE factor_id=%s AND is_sub_factor_id=1 AND run_id=%s
                      AND ic_scope='cross_sectional' AND calculation_mode=%s
                      AND universe_key=%s AND window_scope=%s
                      AND factor_bar_interval=%s AND factor_window_bars=%s
                      AND return_bar_interval=%s AND forward_return_bars=%s
                      AND symbol=''
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        candidate["factor_id"], candidate["run_id"], candidate["calculation_mode"],
                        candidate["universe_key"], candidate["window_scope"], candidate["factor_bar_interval"],
                        candidate["factor_window_bars"], candidate["return_bar_interval"], candidate["forward_return_bars"],
                    ),
                )
                cross = cursor.fetchone()
                if not cross:
                    continue
                cursor.execute(
                    """
                    SELECT * FROM factor_ic_summary_metrics
                    WHERE factor_id<>%s AND is_sub_factor_id=1 AND run_id=%s
                      AND ic_scope='time_series' AND calculation_mode=%s
                      AND universe_key=%s AND window_scope=%s
                      AND factor_bar_interval=%s AND factor_window_bars=%s
                      AND return_bar_interval=%s AND forward_return_bars=%s
                      AND symbol=''
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        candidate["factor_id"], candidate["run_id"], candidate["calculation_mode"],
                        candidate["universe_key"], candidate["window_scope"], candidate["factor_bar_interval"],
                        candidate["factor_window_bars"], candidate["return_bar_interval"], candidate["forward_return_bars"],
                    ),
                )
                second = cursor.fetchone()
                if second:
                    selected_ts, selected_cs, selected_second = candidate, dict(cross), dict(second)
                    break
            # Active publication/metric rows for the environment cross-check.
            cursor.execute(
                "SELECT * FROM market_environment_factor_route WHERE is_active=1 ORDER BY rank_no,id LIMIT 1"
            )
            route = dict(cursor.fetchone() or {})
            env_factor = route.get("factor_ref")
            cursor.execute(
                """
                SELECT * FROM market_environment_factor_metric
                WHERE eval_batch_id=%s AND factor_ref=%s
                ORDER BY label_code,evaluation_type,id
                """,
                (route.get("eval_batch_id"), env_factor),
            )
            env_metrics = [dict(row) for row in cursor.fetchall()]
            # Slice rows are selected after the metric fixture is known.
            slices: list[dict[str, Any]] = []
            if selected_ts:
                cursor.execute(
                    """
                    SELECT * FROM factor_ic_slice_metrics
                    WHERE factor_id=%s AND is_sub_factor_id=1 AND run_id=%s
                      AND ic_scope=%s AND calculation_mode=%s AND universe_key=%s
                      AND window_scope=%s AND factor_bar_interval=%s
                      AND factor_window_bars=%s AND return_bar_interval=%s
                      AND forward_return_bars=%s AND symbol=''
                    ORDER BY id LIMIT 30
                    """,
                    (
                        selected_ts["factor_id"], selected_ts["run_id"], selected_ts["ic_scope"],
                        selected_ts["calculation_mode"], selected_ts["universe_key"], selected_ts["window_scope"],
                        selected_ts["factor_bar_interval"], selected_ts["factor_window_bars"], selected_ts["return_bar_interval"],
                        selected_ts["forward_return_bars"],
                    ),
                )
                slices = [dict(row) for row in cursor.fetchall()]
            # Capture a symbol-specific row for the same factor if available.
            symbol_row: dict[str, Any] | None = None
            if selected_ts:
                cursor.execute(
                    """
                    SELECT * FROM factor_ic_summary_metrics
                    WHERE factor_id=%s AND is_sub_factor_id=1 AND run_id=%s
                      AND ic_scope='time_series' AND calculation_mode=%s
                      AND universe_key=%s AND window_scope=%s
                      AND factor_bar_interval=%s AND factor_window_bars=%s
                      AND return_bar_interval=%s AND forward_return_bars=%s
                      AND symbol<>''
                      AND scoring_version=%s
                    ORDER BY id LIMIT 1
                    """,
                    (
                        selected_ts["factor_id"], selected_ts["run_id"], selected_ts["calculation_mode"],
                        selected_ts["universe_key"], selected_ts["window_scope"], selected_ts["factor_bar_interval"],
                        selected_ts["factor_window_bars"], selected_ts["return_bar_interval"], selected_ts["forward_return_bars"],
                        selected_ts["scoring_version"],
                    ),
                )
                value = cursor.fetchone()
                symbol_row = dict(value) if value else None
    finally:
        db.rollback()
        db.close()

    init = client.request(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-cross-scope", "version": "1.0"},
        },
    )
    protocol = ((init.get("envelope") or {}).get("result") or {}).get("protocolVersion")
    client.protocol_version = str(protocol or "") or None
    client.request("MCP-NOTIFY", "notifications/initialized", {})
    if init.get("http_status") != 200 or not client.protocol_version:
        record("MCP-INIT", "MCP handshake", False, compact_call(init), init, blocked="MCP_INIT_FAILED")
        print(json.dumps({"output": str(output), "counts": {"BLOCKED": 1}}, ensure_ascii=False))
        return

    if not selected_ts or not selected_cs or not selected_second:
        record("METRIC-FIXTURE", "same-run TS/CS and second-factor fixture", False, {}, blocked="NO_CROSS_SCOPE_FIXTURE")
    else:
        ts_ref = f"sub_factor:{selected_ts['factor_id']}"
        second_ref = f"sub_factor:{selected_second['factor_id']}"
        # Single TS and CS calls must remain on their requested scope/run.
        ts_call = client.tool("METRIC-TS", "factor_get_metrics", metric_args(selected_ts))
        ts_items = data(ts_call).get("ic_summaries") or []
        ts_item = next((item for item in ts_items if isinstance(item, dict)), {})
        record(
            "METRIC-TS-EXACT",
            "single TS metric preserves exact run and scope",
            successful(ts_call) and len(ts_items) == 1 and not same_metric_identity(ts_item, selected_ts),
            {"requested": ts_ref, "returned": compact_call(ts_call), "mismatches": same_metric_identity(ts_item, selected_ts)},
            ts_call,
        )
        cs_args = metric_args(selected_cs)
        cs_call = client.tool("METRIC-CS", "factor_get_metrics", cs_args)
        cs_items = data(cs_call).get("ic_summaries") or []
        cs_item = next((item for item in cs_items if isinstance(item, dict)), {})
        record(
            "METRIC-CS-EXACT",
            "single CS metric does not fall back to TS",
            successful(cs_call) and len(cs_items) == 1 and not same_metric_identity(cs_item, selected_cs),
            {"requested": f"sub_factor:{selected_cs['factor_id']}", "returned": compact_call(cs_call), "mismatches": same_metric_identity(cs_item, selected_cs)},
            cs_call,
        )
        # Batch response must preserve per-factor identity and match singles/DB.
        batch_args = metric_args(selected_ts)
        batch_args.pop("factor_ref")
        batch_args["factor_refs"] = [ts_ref, second_ref, UNKNOWN_REF]
        batch_call = client.tool("METRIC-BATCH-CROSS", "factor_get_metrics_batch", batch_args)
        batch_items = data(batch_call).get("items") or []
        batch_map = {item.get("factor_ref"): item for item in batch_items if isinstance(item, dict)}
        first = batch_map.get(ts_ref, {}).get("data") or {}
        second = batch_map.get(second_ref, {}).get("data") or {}
        unknown = batch_map.get(UNKNOWN_REF, {})
        batch_ok = (
            successful(batch_call)
            and batch_map.get(ts_ref, {}).get("success") is True
            and batch_map.get(second_ref, {}).get("success") is True
            and batch_map.get(UNKNOWN_REF, {}).get("success") is False
            and (unknown.get("error") or {}).get("code") == "FACTOR_NOT_FOUND"
            and not same_metric_identity(first, selected_ts)
            and not same_metric_identity(second, selected_second)
        )
        record(
            "METRIC-BATCH-CROSS",
            "mixed-factor metric batch keeps scope and item-level not-found",
            batch_ok,
            {"refs": [ts_ref, second_ref, UNKNOWN_REF], "items": compact_call(batch_call), "ts_mismatches": same_metric_identity(first, selected_ts), "second_mismatches": same_metric_identity(second, selected_second), "unknown": unknown.get("error")},
            batch_call,
        )
        # A symbol filter is a cross-dimensional check, not an aggregate lookup.
        if symbol_row:
            symbol_args = metric_args(selected_ts, symbol=symbol_row.get("symbol"))
            symbol_call = client.tool("METRIC-SYMBOL-FILTER", "factor_get_metrics", symbol_args)
            symbol_items = data(symbol_call).get("ic_summaries") or []
            symbol_item = next((item for item in symbol_items if isinstance(item, dict)), {})
            record(
                "METRIC-SYMBOL-FILTER",
                "symbol filter returns symbol row rather than aggregate row",
                successful(symbol_call) and len(symbol_items) == 1 and not same_metric_identity(symbol_item, symbol_row),
                {"symbol": symbol_row.get("symbol"), "returned": compact_call(symbol_call), "mismatches": same_metric_identity(symbol_item, symbol_row)},
                symbol_call,
            )
        else:
            record("METRIC-SYMBOL-FILTER", "symbol-specific metric fixture", False, {}, blocked="NO_SYMBOL_FIXTURE")

        # Cursor continuation and a changed symbol query must not cross datasets.
        if slices:
            first_slice, last_slice = slices[0], slices[-1]
            slice_args = {
                "factor_ref": ts_ref,
                "ic_scope": selected_ts["ic_scope"],
                "calculation_mode": selected_ts["calculation_mode"],
                "universe_key": selected_ts["universe_key"],
                "interval": selected_ts["factor_bar_interval"],
                "factor_window_bars": selected_ts["factor_window_bars"],
                "return_bar_interval": selected_ts["return_bar_interval"],
                "forward_return_bars": int(selected_ts["forward_return_bars"]),
                "window_scope": selected_ts["window_scope"],
                "as_of": datetime.now(timezone.utc).isoformat(),
                "scoring_version": selected_ts["scoring_version"],
                "start_time": api_datetime(selected_ts["period_start"]),
                "end_time": api_datetime(selected_ts["period_end"]),
                "symbol": "",
                "run_id": selected_ts["run_id"],
                "limit": 3,
            }
            page1 = client.tool("SLICES-PAGE-1", "factor_get_metric_slices", slice_args)
            page_items = rows(page1)
            page_mismatches: dict[str, list[str]] = {}
            for item in page_items:
                match = next((db_row for db_row in slices if int(db_row["id"]) == int(item.get("id"))), None)
                if match:
                    mismatch = same_slice_identity(item, match)
                    if mismatch:
                        page_mismatches[str(item.get("id"))] = mismatch
                else:
                    page_mismatches[str(item.get("id"))] = ["missing_db_row"]
            record(
                "SLICES-PAGE-IDENTITY",
                "slice page rows match exact factor/run/scope DB rows",
                successful(page1) and len(page_items) == min(3, len(slices)) and not page_mismatches,
                {"returned": compact_call(page1), "mismatches": page_mismatches},
                page1,
            )
            cursor_token = meta(page1).get("next_cursor")
            if cursor_token:
                page2_args = dict(slice_args)
                page2_args["cursor"] = cursor_token
                page2 = client.tool("SLICES-PAGE-2", "factor_get_metric_slices", page2_args)
                ids = [item.get("id") for item in page_items + rows(page2)]
                record(
                    "SLICES-CURSOR-CONTINUE",
                    "slice cursor continuation is monotonic and non-overlapping",
                    successful(page2) and bool(rows(page2)) and len(ids) == len(set(ids)) and ids == sorted(ids),
                    {"page1": [item.get("id") for item in page_items], "page2": [item.get("id") for item in rows(page2)]},
                    page2,
                )
                changed = dict(page2_args)
                changed["symbol"] = "ARUSDT"
                changed_call = client.tool("SLICES-CURSOR-CHANGED-SYMBOL", "factor_get_metric_slices", changed)
                changed_rejected = changed_call.get("is_error") is True or error_code(changed_call) == "INVALID_ARGUMENT" or not rows(changed_call)
                record(
                    "SLICES-CURSOR-BINDING",
                    "slice cursor cannot be reused with a changed symbol filter",
                    changed_rejected,
                    compact_call(changed_call),
                    changed_call,
                )
            else:
                record("SLICES-CURSOR-CONTINUE", "slice cursor continuation", False, {}, blocked="NO_CURSOR")
                record("SLICES-CURSOR-BINDING", "slice cursor binding", False, {}, blocked="NO_CURSOR")
            # Exact one-slice boundary: start at the first slice and end at its end.
            boundary = dict(slice_args)
            boundary["start_time"] = api_datetime(first_slice["slice_start"])
            boundary["end_time"] = api_datetime(first_slice["slice_end"])
            boundary["limit"] = 10
            boundary_call = client.tool("SLICES-BOUNDARY-ONE", "factor_get_metric_slices", boundary)
            boundary_ids = [item.get("id") for item in rows(boundary_call)]
            record(
                "SLICES-BOUNDARY-ONE",
                "slice start/end boundary selects the expected slice only",
                successful(boundary_call) and boundary_ids == [first_slice["id"]],
                {"expected_id": first_slice["id"], "returned_ids": boundary_ids},
                boundary_call,
            )
        else:
            for case_id in ("SLICES-PAGE-IDENTITY", "SLICES-CURSOR-CONTINUE", "SLICES-CURSOR-BINDING", "SLICES-BOUNDARY-ONE"):
                record(case_id, "slice fixture", False, {}, blocked="NO_SLICE_FIXTURE")

    # Environment metrics: compare all label/type combinations to the same DB batch.
    if route and env_metrics:
        env_base = {
            "factor_ref": env_factor,
            "market_scope": route.get("market_scope"),
            "route_profile_key": "default",
            "batch_uid": None,
            "limit": 100,
        }
        env_call = client.tool("ENV-METRICS-ALL", "factor_get_environment_metrics", env_base)
        api_env = rows(env_call)
        db_by_key = {(str(item.get("label_code")), str(item.get("evaluation_type"))): item for item in env_metrics}
        api_by_key = {(str(item.get("label_code")), str(item.get("evaluation_type"))): item for item in api_env}
        env_mismatches: dict[str, Any] = {}
        for key, db_row in db_by_key.items():
            api_row = api_by_key.get(key)
            if api_row is None:
                env_mismatches[str(key)] = "missing"
                continue
            for field in ("id", "factor_ref", "factor_id", "label_code", "evaluation_type", "eval_batch_id", "market_scope", "factor_version", "metric_status", "is_valid", "time_series_score", "cross_sectional_score", "routing_score"):
                db_field = db_row.get(field)
                if not scalar_equal(api_row.get(field), db_field):
                    env_mismatches.setdefault(str(key), []).append(field)
        record(
            "ENV-METRICS-CROSS-MATRIX",
            "environment metrics preserve every label/type identity",
            successful(env_call) and set(api_by_key) == set(db_by_key) and not env_mismatches,
            {"returned": compact_call(env_call), "db_key_count": len(db_by_key), "mismatches": env_mismatches},
            env_call,
        )
        # Filtered calls for one success and one insufficient/invalid label.
        for idx, key in enumerate(sorted(db_by_key)[:4]):
            label, evaluation_type = key
            filtered_args = dict(env_base)
            filtered_args.update({"label_code": label, "evaluation_type": evaluation_type, "limit": 2})
            filtered = client.tool(f"ENV-METRICS-FILTER-{idx}", "factor_get_environment_metrics", filtered_args)
            filtered_rows = rows(filtered)
            ok = successful(filtered) and len(filtered_rows) == 1 and all(
                item.get("label_code") == label and item.get("evaluation_type") == evaluation_type and str(item.get("eval_batch_id")) == str(route.get("eval_batch_id"))
                for item in filtered_rows
            )
            record(
                f"ENV-METRICS-FILTER-{idx}",
                "label/evaluation filter is exact",
                ok,
                {"requested": {"label_code": label, "evaluation_type": evaluation_type}, "returned": compact_call(filtered)},
                filtered,
            )
        # Route tag metric_id must be one of the exact environment metrics.
        tags = client.tool("ENV-TAGS-CROSS", "factor_get_environment_tags", {"factor_ref": env_factor, "market_scope": route.get("market_scope"), "route_profile_key": "default"})
        tag_rows = rows(tags)
        tag = next((item for item in tag_rows if str(item.get("id")) == str(route.get("id"))), None)
        tag_metric = next((item for item in env_metrics if str(item.get("id")) == str(route.get("metric_id"))), None)
        tag_ok = successful(tags) and tag is not None and tag_metric is not None and str(tag.get("metric_id")) == str(tag_metric.get("id")) and str(tag.get("publication_uid")) == str(route.get("publication_uid"))
        record(
            "ENV-TAGS-METRIC-LINK",
            "active tag metric_id links to the same batch metric",
            tag_ok,
            {"route_id": route.get("id"), "tag_metric_id": tag.get("metric_id") if tag else None, "db_metric_id": route.get("metric_id"), "returned": compact_call(tags)},
            tags,
        )
    else:
        record("ENV-METRICS-CROSS-MATRIX", "environment metric fixture", False, {}, blocked="NO_ACTIVE_ENV_METRIC")

    # The string-limit coercion is already tracked as OPEN-MCP-LIMIT-COERCION.
    # Keep this cross-scope run focused on independent identity invariants; a
    # caller can opt into the duplicate recheck when explicitly needed.
    if os.environ.get("RUN_KNOWN_RECHECKS") == "1":
        string_daily = client.tool(
            "RECHECK-DAILY-STRING-LIMIT",
            "environment_get_daily",
            {"label_kind": "fact", "limit": "1"},
        )
        record(
            "RECHECK-DAILY-STRING-LIMIT",
            "string limit validation recheck",
            string_daily.get("is_error") is True or not rows(string_daily),
            compact_call(string_daily),
            string_daily,
            duplicate_of="OPEN-MCP-LIMIT-COERCION",
        )

    counts = {status: sum(case["status"] == status for case in cases) for status in ("PASS", "FAIL", "BLOCKED")}
    report = {
        "run_id": run_stamp,
        "environment": "test",
        "mode": "READ_ONLY",
        "mcp_url": MCP_URL,
        "counts": counts,
        "cases": cases,
        "fixture": {
            "ts_factor_ref": f"sub_factor:{selected_ts['factor_id']}" if selected_ts else None,
            "cs_factor_ref": f"sub_factor:{selected_cs['factor_id']}" if selected_cs else None,
            "second_factor_ref": f"sub_factor:{selected_second['factor_id']}" if selected_second else None,
            "environment_factor_ref": env_factor,
            "environment_metric_count": len(env_metrics),
        },
        "known_rechecks_included": os.environ.get("RUN_KNOWN_RECHECKS") == "1",
    }
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")
    lines = ["# Cross-scope identity probe", "", f"PASS={counts['PASS']} / FAIL={counts['FAIL']} / BLOCKED={counts['BLOCKED']}", "", "| Case | Status | Title |", "|---|---|---|"]
    for case in cases:
        lines.append(f"| {case['case_id']} | {case['status']} | {case['title']} |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "counts": counts, "fixture": report["fixture"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
