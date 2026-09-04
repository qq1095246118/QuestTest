#!/usr/bin/env python3
"""Recheck latest-run selection when ``run_id`` is omitted.

This is a read-only regression for the historical "parent metric selects an
old cycle" defect.  It discovers a factor/configuration with at least two
completed, aggregate validity rows, reads the newest row without ``run_id``,
and also reads the older row with an explicit run to prove that selection is
intentional rather than a silent fallback.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from tmp.validity_boundary_deep import (  # noqa: E402
    McpClient,
    api_period_time,
    base_args,
    compact_call,
    metric_args,
    metric_summary,
    open_db,
    slice_args,
    validity_args,
    validity_item,
    write_json,
)


TOKEN_ENV = "VALIDITY_BOUNDARY_MCP_TOKEN"


def discover_pair(connection: pymysql.Connection) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return newest and older complete rows for one identical factor scope."""

    group_query = """
        SELECT v.factor_id, v.universe_key, v.factor_bar_interval,
               v.factor_window_bars, v.return_bar_interval,
               v.forward_return_bars, v.window_scope,
               MAX(v.updated_at) AS latest_update
        FROM factor_validity_status v
        JOIN factor_ic_summary_metrics ts ON ts.id=v.time_series_summary_id
        JOIN factor_ic_summary_metrics cs ON cs.id=v.cross_sectional_summary_id
        JOIN factor_ic_runs r ON r.run_id=v.run_id
        WHERE v.is_sub_factor_id=1 AND v.overall_is_valid=1 AND r.status='completed'
          AND ts.factor_id=v.factor_id AND cs.factor_id=v.factor_id
          AND ts.is_sub_factor_id=v.is_sub_factor_id AND cs.is_sub_factor_id=v.is_sub_factor_id
          AND ts.run_id=v.run_id AND cs.run_id=v.run_id
          AND ts.ic_scope='time_series' AND cs.ic_scope='cross_sectional'
          AND ts.calculation_mode='direct' AND cs.calculation_mode='direct'
          AND ts.factor_bar_interval=v.factor_bar_interval AND cs.factor_bar_interval=v.factor_bar_interval
          AND ts.factor_window_bars=v.factor_window_bars AND cs.factor_window_bars=v.factor_window_bars
          AND ts.return_bar_interval=v.return_bar_interval AND cs.return_bar_interval=v.return_bar_interval
          AND ts.forward_return_bars=v.forward_return_bars AND cs.forward_return_bars=v.forward_return_bars
          AND ts.universe_key=v.universe_key AND cs.universe_key=v.universe_key
          AND ts.window_scope=v.window_scope AND cs.window_scope=v.window_scope
          AND COALESCE(ts.symbol,'')='' AND COALESCE(cs.symbol,'')=''
        GROUP BY v.factor_id, v.universe_key, v.factor_bar_interval,
                 v.factor_window_bars, v.return_bar_interval,
                 v.forward_return_bars, v.window_scope
        HAVING COUNT(DISTINCT v.run_id) >= 2
        ORDER BY latest_update DESC
        LIMIT 1
    """
    row_query = """
        SELECT v.id AS validity_id, v.factor_id, v.is_sub_factor_id, v.run_id,
               v.serial_number, v.universe_key, v.factor_bar_interval,
               v.factor_window_bars, v.return_bar_interval, v.forward_return_bars,
               v.window_scope, v.period_start, v.period_end,
               v.time_series_summary_id, v.cross_sectional_summary_id,
               v.time_series_scoring_version, v.time_series_score,
               v.time_series_status, v.time_series_is_valid,
               v.cross_sectional_scoring_version, v.cross_sectional_score,
               v.cross_sectional_status, v.cross_sectional_is_valid,
               v.overall_score, v.overall_status, v.overall_is_valid,
               v.created_at AS validity_created_at, v.updated_at AS validity_updated_at,
               ts.symbol AS ts_symbol, cs.symbol AS cs_symbol,
               r.created_at AS run_created_at, r.completed_at AS run_completed_at
        FROM factor_validity_status v
        JOIN factor_ic_summary_metrics ts ON ts.id=v.time_series_summary_id
        JOIN factor_ic_summary_metrics cs ON cs.id=v.cross_sectional_summary_id
        JOIN factor_ic_runs r ON r.run_id=v.run_id
        WHERE v.is_sub_factor_id=1 AND v.overall_is_valid=1 AND r.status='completed'
          AND ts.factor_id=v.factor_id AND cs.factor_id=v.factor_id
          AND ts.is_sub_factor_id=v.is_sub_factor_id AND cs.is_sub_factor_id=v.is_sub_factor_id
          AND ts.run_id=v.run_id AND cs.run_id=v.run_id
          AND ts.ic_scope='time_series' AND cs.ic_scope='cross_sectional'
          AND ts.calculation_mode='direct' AND cs.calculation_mode='direct'
          AND ts.factor_bar_interval=v.factor_bar_interval AND cs.factor_bar_interval=v.factor_bar_interval
          AND ts.factor_window_bars=v.factor_window_bars AND cs.factor_window_bars=v.factor_window_bars
          AND ts.return_bar_interval=v.return_bar_interval AND cs.return_bar_interval=v.return_bar_interval
          AND ts.forward_return_bars=v.forward_return_bars AND cs.forward_return_bars=v.forward_return_bars
          AND ts.universe_key=v.universe_key AND cs.universe_key=v.universe_key
          AND ts.window_scope=v.window_scope AND cs.window_scope=v.window_scope
          AND COALESCE(ts.symbol,'')='' AND COALESCE(cs.symbol,'')=''
          AND v.factor_id=%s AND v.universe_key=%s AND v.factor_bar_interval=%s
          AND v.factor_window_bars=%s AND v.return_bar_interval=%s
          AND v.forward_return_bars=%s AND v.window_scope=%s
        ORDER BY v.updated_at DESC, v.id DESC
        LIMIT 2
    """
    cursor = connection.cursor()
    try:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        cursor.execute(group_query)
        group = cursor.fetchone()
        if not group:
            connection.rollback()
            return None
        cursor.execute(
            row_query,
            (
                group["factor_id"], group["universe_key"], group["factor_bar_interval"],
                group["factor_window_bars"], group["return_bar_interval"],
                group["forward_return_bars"], group["window_scope"],
            ),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        pair: tuple[dict[str, Any], dict[str, Any]] | None = tuple(rows) if len(rows) >= 2 else None  # type: ignore[assignment]
        if pair is None:
            connection.rollback()
            return None
        for row in pair:
            cursor.execute("SELECT * FROM factor_validity_status WHERE id=%s", (row["validity_id"],))
            row["validity_entity"] = dict(cursor.fetchone() or {})
            for scope, key in (("ts", "time_series_summary_id"), ("cs", "cross_sectional_summary_id")):
                cursor.execute("SELECT * FROM factor_ic_summary_metrics WHERE id=%s", (row[key],))
                row[f"{scope}_summary_entity"] = dict(cursor.fetchone() or {})
                summary = row[f"{scope}_summary_entity"]
                cursor.execute(
                    """
                    SELECT COUNT(*) AS row_count, MIN(NULLIF(symbol,'')) AS first_symbol
                    FROM factor_ic_slice_metrics
                    WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=%s
                      AND ic_scope=%s AND calculation_mode=%s AND factor_bar_interval=%s
                      AND factor_window_bars=%s AND return_bar_interval=%s
                      AND forward_return_bars=%s AND universe_key=%s AND window_scope=%s
                    """,
                    (
                        summary.get("run_id"), summary.get("factor_id"), summary.get("is_sub_factor_id"),
                        summary.get("ic_scope"), summary.get("calculation_mode"), summary.get("factor_bar_interval"),
                        summary.get("factor_window_bars"), summary.get("return_bar_interval"),
                        summary.get("forward_return_bars"), summary.get("universe_key"), summary.get("window_scope"),
                    ),
                )
                profile = dict(cursor.fetchone() or {})
                row[f"{scope}_slice_row_count"] = int(profile.get("row_count") or 0)
                row[f"{scope}_slice_symbol"] = profile.get("first_symbol")
        connection.rollback()
        return pair
    finally:
        cursor.close()


def main() -> None:
    """Execute and persist the latest/older run selection assertions."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("FACTOR4_MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} (or FACTOR4_MCP_TOKEN) is required")
    settings = SettingsLoader.load("test", ROOT)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = ROOT / "reports" / "factor4-deep" / f"{stamp}-auto-run-selection"
    output_dir.mkdir(parents=True, exist_ok=False)
    connection = open_db(settings.database)
    try:
        pair = discover_pair(connection)
    finally:
        connection.close()
    if pair is None:
        raise SystemExit("No complete multi-run aggregate scope was found")
    latest, older = pair
    as_of = datetime.now().astimezone().isoformat(timespec="microseconds")
    client = McpClient(token, output_dir, "auto-run")
    calls: dict[str, dict[str, Any]] = {}
    init = client.initialize()
    calls["MCP-INIT"] = init
    results: list[dict[str, Any]] = []

    def run(case_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Invoke one tool and retain its raw artifact."""

        call = client.tool(case_id, tool, args)
        calls[case_id] = call
        return call

    def add(case_id: str, expected: str, actual: Any, passed: bool, call: dict[str, Any]) -> None:
        """Append one selection assertion."""

        results.append({
            "case_id": case_id,
            "status": "PASS" if passed else "FAIL",
            "expected": expected,
            "actual": actual,
            "http_status": call.get("http_status"),
            "error_code": (call.get("business") or {}).get("error", {}).get("code") if isinstance((call.get("business") or {}).get("error"), dict) else None,
        })

    for scope in ("ts", "cs"):
        scope_name = "time_series" if scope == "ts" else "cross_sectional"
        latest_summary_id = int(latest[f"{scope}_summary_entity"]["id"])
        old_summary_id = int(older[f"{scope}_summary_entity"]["id"])
        metric_omitted_case = f"AUTO-LATEST-{scope.upper()}-METRICS"
        metric_call = run(metric_omitted_case, "factor_get_metrics", metric_args(latest, scope, as_of, run_id="omit"))
        metric = metric_summary(metric_call)
        add(metric_omitted_case, f"omitted run_id selects newest summary {latest_summary_id}", {"returned_id": metric.get("id") if metric else None, "returned_run": metric.get("run_id") if metric else None}, bool(metric and int(metric.get("id")) == latest_summary_id), metric_call)

        validity_omitted_case = f"AUTO-LATEST-{scope.upper()}-VALIDITY"
        validity_call = run(validity_omitted_case, "factor_get_validity", validity_args(latest, scope, as_of, run_id="omit"))
        validity = validity_item(validity_call)
        add(validity_omitted_case, f"omitted run_id selects newest validity {latest['validity_id']}", {"returned_id": validity.get("id") if validity else None, "returned_run": validity.get("run_id") if validity else None}, bool(validity and int(validity.get("id")) == int(latest["validity_id"])), validity_call)

        old_metric_case = f"AUTO-EXPLICIT-OLD-{scope.upper()}-METRICS"
        old_metric_call = run(old_metric_case, "factor_get_metrics", metric_args(older, scope, as_of))
        old_metric = metric_summary(old_metric_call)
        add(old_metric_case, f"explicit old run returns old summary {old_summary_id}", {"returned_id": old_metric.get("id") if old_metric else None, "returned_run": old_metric.get("run_id") if old_metric else None}, bool(old_metric and int(old_metric.get("id")) == old_summary_id), old_metric_call)

        old_validity_case = f"AUTO-EXPLICIT-OLD-{scope.upper()}-VALIDITY"
        old_validity_call = run(old_validity_case, "factor_get_validity", validity_args(older, scope, as_of))
        old_validity = validity_item(old_validity_call)
        add(old_validity_case, f"explicit old run returns old validity {older['validity_id']}", {"returned_id": old_validity.get("id") if old_validity else None, "returned_run": old_validity.get("run_id") if old_validity else None}, bool(old_validity and int(old_validity.get("id")) == int(older["validity_id"])), old_validity_call)

    # Slice selection is tested on the newest TS scope if a slice profile exists.
    if int(latest.get("ts_slice_row_count") or 0) > 0:
        slice_request = slice_args(latest, "ts", as_of, run_id="omit")
        slice_case = "AUTO-LATEST-TS-SLICES"
        slice_call = run(slice_case, "factor_get_metric_slices", slice_request)
        items = (slice_call.get("business") or {}).get("data", {}).get("items") or []
        wrong_runs = [item.get("run_id") for item in items if item.get("run_id") != latest["run_id"]]
        add(slice_case, "omitted run_id slices belong to newest run", {"count": len(items), "wrong_runs": wrong_runs, "ids": [item.get("id") for item in items]}, bool(items) and not wrong_runs, slice_call)

    summary = {
        "environment": "test",
        "mode": "READ_ONLY",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "selected_latest": {key: latest.get(key) for key in ("validity_id", "factor_id", "run_id", "window_scope", "time_series_summary_id", "cross_sectional_summary_id", "validity_updated_at")},
        "selected_older": {key: older.get(key) for key in ("validity_id", "factor_id", "run_id", "window_scope", "time_series_summary_id", "cross_sectional_summary_id", "validity_updated_at")},
        "results": results,
        "calls": {key: compact_call(value, value.get("method") or "") for key, value in calls.items()},
    }
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "db-pair.json", {"latest": latest, "older": older})
    failures = [item for item in results if item["status"] == "FAIL"]
    lines = [
        "# Automatic run selection recheck",
        "",
        f"- Results: `{len(results) - len(failures)} PASS / {len(failures)} FAIL`",
        f"- Latest validity: `{latest['validity_id']}`; older validity: `{older['validity_id']}`",
        "",
        "## Findings",
        "",
    ]
    if failures:
        lines.extend(f"- **{item['case_id']}**: {item['expected']}; actual `{json.dumps(item['actual'], ensure_ascii=False, default=str)}`" for item in failures)
    else:
        lines.append("- No old-cycle selection or cross-run leakage was observed.")
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "results": len(results), "failures": [item["case_id"] for item in failures]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
