#!/usr/bin/env python3
"""Reconcile active ATR routes with their formula and runtime metric records."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from config.settings import SettingsLoader
from db.client import DatabaseClient


ROOT = Path(__file__).resolve().parents[1]
FACTOR_IDS = (160458, 160461, 160463)
TOKEN_ENV = "FACTOR4_MCP_TOKEN"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tmp.targeted_5921_recheck import McpClient, business, error_code, write_json


def _json_default(value: Any) -> str:
    """Serialize database-native values for evidence files."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def _parse_json(value: Any) -> Any:
    """Decode a JSON column while preserving null and already-decoded values."""

    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _db_snapshot() -> dict[str, Any]:
    """Read active route, detail, evidence, and metric identity rows."""

    settings = SettingsLoader.load("test", ROOT)
    db = DatabaseClient.from_settings(settings.database)
    with db.transaction() as tx:
        ids = ",".join(["%s"] * len(FACTOR_IDS))
        routes = tx.fetch_all(
            f"""
            SELECT r.id AS route_id, r.factor_ref, r.factor_id, r.factor_version,
                   r.metric_id, r.label_code, r.market_scope, r.eval_batch_id,
                   r.publication_uid, r.publish_version, r.is_active, r.is_eligible,
                   r.routing_score, r.time_series_score, r.cross_sectional_score,
                   m.metric_status, m.is_valid, m.evaluation_type, m.interval,
                   m.return_bar_interval, m.forward_return_bars, m.window_scope,
                   m.scoring_version, b.batch_uid, b.route_profile_key,
                   b.as_of_time, b.published_at, b.status AS batch_status,
                   b.publish_status
            FROM market_environment_factor_route r
            JOIN market_environment_factor_metric m ON m.id = r.metric_id
            JOIN market_environment_eval_batch b ON b.id = r.eval_batch_id
            WHERE r.factor_id IN ({ids}) AND r.is_active=1 AND r.is_eligible=1
            ORDER BY r.factor_id
            """,
            FACTOR_IDS,
        )
        details = tx.fetch_all(
            f"""
            SELECT id, factor_id, name, calc_logic, params, data_source_metadata,
                   status, is_sub_factor_id, updated_at
            FROM factors_details
            WHERE factor_id IN ({ids}) AND is_sub_factor_id=1
            ORDER BY factor_id, id DESC
            """,
            FACTOR_IDS,
        )
        evidence = tx.fetch_all(
            f"""
            SELECT e.id, e.run_id, e.factor_id, e.factor_bar_interval,
                   e.factor_window_bars, e.return_bar_interval,
                   e.forward_return_bars, e.formula_hash, e.expression,
                   e.required_fields, e.lookback_json, e.source_detail_id,
                   e.metadata_complete, e.metadata_warnings, e.recorded_at,
                   r.status AS run_status, r.completed_at
            FROM factor_ic_run_formula_evidence e
            LEFT JOIN factor_ic_runs r ON r.run_id=e.run_id
            WHERE e.factor_id IN ({ids}) AND e.is_sub_factor_id=1
            ORDER BY e.factor_id, e.recorded_at DESC
            """,
            FACTOR_IDS,
        )
        summaries = tx.fetch_all(
            f"""
            SELECT id, run_id, factor_id, ic_scope, calculation_mode,
                   factor_bar_interval, factor_window_bars, return_bar_interval,
                   forward_return_bars, interval_value, universe_key, symbol,
                   window_scope, mean_ic, final_score, valid_slice_count,
                   coverage_mean, period_start, period_end, created_at
            FROM factor_ic_summary_metrics
            WHERE factor_id IN ({ids}) AND is_sub_factor_id=1
            ORDER BY factor_id, created_at DESC
            LIMIT 300
            """,
            FACTOR_IDS,
        )
        validity = tx.fetch_all(
            f"""
            SELECT id, run_id, factor_id, factor_bar_interval, factor_window_bars,
                   window_scope, time_series_status, time_series_is_valid,
                   cross_sectional_status, cross_sectional_is_valid,
                   overall_status, overall_is_valid, time_series_summary_id,
                   cross_sectional_summary_id, updated_at
            FROM factor_validity_status
            WHERE factor_id IN ({ids}) AND is_sub_factor_id=1
            ORDER BY factor_id, updated_at DESC
            LIMIT 100
            """,
            FACTOR_IDS,
        )
    return {
        "database": settings.database.name,
        "factor_ids": list(FACTOR_IDS),
        "routes": routes,
        "details": details,
        "evidence": evidence,
        "summaries": summaries,
        "validity": validity,
    }


def _first_by(rows: list[dict[str, Any]], key: str, value: Any) -> dict[str, Any] | None:
    """Return the first row matching one field value."""

    return next((row for row in rows if row.get(key) == value), None)


def _formula_args(route: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Build an exact formula evidence lookup from a completed DB run."""

    return {
        "factor_ref": route["factor_ref"],
        "run_id": evidence["run_id"],
        "calculation_mode": "direct",
        "interval": evidence["factor_bar_interval"],
        "factor_window_bars": evidence["factor_window_bars"],
        "return_bar_interval": evidence["return_bar_interval"],
        "forward_return_bars": int(evidence["forward_return_bars"]),
    }


def _metric_args(route: dict[str, Any]) -> dict[str, Any]:
    """Build an exact active-route environment metric lookup."""

    return {
        "factor_ref": route["factor_ref"],
        "market_scope": route["market_scope"],
        "route_profile_key": route["route_profile_key"],
        "batch_uid": route["batch_uid"],
        "label_code": route["label_code"],
        "evaluation_type": route["evaluation_type"],
        "limit": 100,
    }


def main() -> None:
    """Execute read-only MCP and DB checks for the three active ATR routes."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-truerange-active"
    output.mkdir(parents=True, exist_ok=True)
    snapshot = _db_snapshot()
    write_json(output / "db-snapshot.json", snapshot)
    client = McpClient(token, output)
    init = client.initialize()
    checks: list[dict[str, Any]] = []
    route_by_id = {int(row["factor_id"]): row for row in snapshot["routes"]}
    detail_by_id = {int(row["factor_id"]): row for row in snapshot["details"]}
    evidence_by_id: dict[int, dict[str, Any]] = {}
    for row in snapshot["evidence"]:
        factor_id = int(row["factor_id"])
        if factor_id not in evidence_by_id and row.get("run_status") == "completed":
            evidence_by_id[factor_id] = row

    for factor_id in FACTOR_IDS:
        route = route_by_id.get(factor_id)
        detail = detail_by_id.get(factor_id)
        evidence = evidence_by_id.get(factor_id)
        item: dict[str, Any] = {
            "factor_id": factor_id,
            "factor_ref": route.get("factor_ref") if route else f"sub_factor:{factor_id}",
            "active_route_present": route is not None,
            "completed_evidence_present": evidence is not None,
            "detail": {
                "detail_id": detail.get("id") if detail else None,
                "calc_logic": detail.get("calc_logic") if detail else None,
                "params_fields": _parse_json(detail.get("params") if detail else None).get("fields", [])
                if isinstance(_parse_json(detail.get("params") if detail else None), dict)
                else [],
                "params_derived_fields": _parse_json(detail.get("params") if detail else None).get("derived_fields", [])
                if isinstance(_parse_json(detail.get("params") if detail else None), dict)
                else [],
            },
        }
        if route is None or evidence is None:
            item["status"] = "BLOCKED_DB_PRECONDITION"
            checks.append(item)
            continue
        formula = client.tool(f"FORMULA-{factor_id}", "factor_get_formula", _formula_args(route, evidence))
        metrics = client.tool(f"METRICS-{factor_id}", "factor_get_environment_metrics", _metric_args(route))
        formula_data = business(formula).get("data") or {}
        metric_data = business(metrics).get("data") or {}
        metric_items = metric_data.get("items") if isinstance(metric_data.get("items"), list) else []
        matching_metric = next((row for row in metric_items if row.get("id") == route["metric_id"]), None)
        item.update(
            {
                "formula": {
                    "http_status": formula.get("http_status"),
                    "error_code": error_code(formula),
                    "returned_factor_ref": formula_data.get("factor_ref"),
                    "returned_run_id": formula_data.get("run_id"),
                    "returned_expression": formula_data.get("expression"),
                    "returned_required_fields": formula_data.get("required_fields"),
                    "returned_hash": formula_data.get("formula_hash"),
                    "db_expression": evidence.get("expression"),
                    "db_required_fields": _parse_json(evidence.get("required_fields")),
                    "db_hash": evidence.get("formula_hash"),
                    "db_source_detail_id": evidence.get("source_detail_id"),
                    "returned_source_detail_id": formula_data.get("source_detail_id"),
                },
                "metrics": {
                    "http_status": metrics.get("http_status"),
                    "error_code": error_code(metrics),
                    "returned_count": len(metric_items),
                    "returned_metric_id": matching_metric.get("id") if matching_metric else None,
                    "returned_factor_ref": matching_metric.get("factor_ref") if matching_metric else None,
                    "returned_metric_status": matching_metric.get("metric_status") if matching_metric else None,
                    "returned_is_valid": matching_metric.get("is_valid") if matching_metric else None,
                    "returned_interval": matching_metric.get("interval") if matching_metric else None,
                },
            }
        )
        formula_ok = (
            formula.get("http_status") == 200
            and error_code(formula) is None
            and formula_data.get("factor_ref") == route["factor_ref"]
            and formula_data.get("run_id") == evidence["run_id"]
            and formula_data.get("expression") == evidence["expression"]
            and formula_data.get("formula_hash") == evidence["formula_hash"]
            and formula_data.get("source_detail_id") == evidence["source_detail_id"]
        )
        metric_ok = (
            metrics.get("http_status") == 200
            and error_code(metrics) is None
            and matching_metric is not None
            and matching_metric.get("factor_ref") == route["factor_ref"]
            and matching_metric.get("metric_status") == route["metric_status"]
            and int(matching_metric.get("is_valid")) == int(route["is_valid"])
            and matching_metric.get("interval") == route["interval"]
        )
        item["formula_ok"] = formula_ok
        item["metrics_ok"] = metric_ok
        item["status"] = "PASS" if formula_ok and metric_ok else "FAIL"
        checks.append(item)

    report = {
        "environment": "test",
        "read_only": True,
        "mcp_url": "https://test-factor-frontend.questvector.ai/mcp/factor-data",
        "initialization": {
            "http_status": init.get("http_status"),
            "protocol_version": client.protocol_version,
        },
        "checks": checks,
        "failures": [row["factor_id"] for row in checks if row.get("status") == "FAIL"],
        "blocked": [row["factor_id"] for row in checks if row.get("status", "").startswith("BLOCKED")],
    }
    write_json(output / "summary.json", report)
    (output / "summary.md").write_text(
        "# Active ATR truerange recheck\n\n"
        f"- Environment: `test`; mode: `read-only`\n"
        f"- Initialization: `{report['initialization']}`\n"
        f"- PASS: `{sum(row.get('status') == 'PASS' for row in checks)}`; "
        f"FAIL: `{len(report['failures'])}`; BLOCKED: `{len(report['blocked'])}`\n\n"
        "The generated detail wrapper is inspected separately from canonical field metadata; a derived field is not treated as a raw input.\n",
        encoding="utf-8",
    )
    print(output)
    print(json.dumps({"failures": report["failures"], "blocked": report["blocked"]}))


if __name__ == "__main__":
    main()
