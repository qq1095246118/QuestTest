#!/usr/bin/env python3
"""Read-only DPO formula audit for active Factor 4 routes.

The Factor 4 definitions identify these rows as ``-DPO(close,n)``.  This
probe compares the persisted expression with the usual DPO oracle
``close.shift(n/2+1) - close.rolling(n).mean()`` and records the active route
and formula-evidence linkage.  It never calls a write endpoint or mutates the
database.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from config.settings import SettingsLoader
from db.client import DatabaseClient, DatabaseTransaction
from tmp.catalog_deep_readonly import Runner, _data, _error_code, _success

TARGET_IDS = (161104, 161106, 161108)


def decode(value: Any) -> Any:
    """Decode a JSON database value while preserving native values."""

    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def json_default(value: Any) -> str:
    """Serialize database-native values for the diagnostic artifact."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def evaluate_persisted_expression(close: pd.Series, expression: str, window: int) -> pd.Series:
    """Evaluate the limited persisted DPO expression on a synthetic series."""

    tree = ast.parse(expression, mode="eval")

    def evaluate(node: ast.AST) -> Any:
        """Evaluate only the arithmetic, mean, and shift nodes used by DPO."""

        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id == "close":
            return close
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -evaluate(node.operand)
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "mean":
            if len(node.args) != 2 or evaluate(node.args[0]) is not close:
                raise ValueError("unsupported mean call in DPO expression")
            requested_window = int(evaluate(node.args[1]))
            if requested_window != window:
                raise ValueError(f"unexpected DPO window {requested_window}")
            return close.rolling(requested_window).mean()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "shift" or len(node.args) != 1:
                raise ValueError("unsupported series call in DPO expression")
            return evaluate(node.func.value).shift(int(evaluate(node.args[0])))
        raise ValueError(f"unexpected AST node in DPO expression: {type(node).__name__}")

    result = evaluate(tree)
    if not isinstance(result, pd.Series):
        raise ValueError("DPO expression did not produce a series")
    return result


def expected_negative_dpo(close: pd.Series, window: int) -> pd.Series:
    """Return ``-DPO`` using the standard displaced-price definition."""

    dpo = close.shift(window // 2 + 1) - close.rolling(window).mean()
    return -dpo


def main() -> None:
    """Run the read-only audit and write a redacted JSON report."""

    settings = SettingsLoader.load("test", ROOT)
    db = DatabaseClient.from_settings(settings.database)
    placeholders = ",".join(["%s"] * len(TARGET_IDS))
    connection = db._connection_factory()  # Explicit read-only rollback for this diagnostic.
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION")
        finally:
            cursor.close()
        tx = DatabaseTransaction(connection)
        routes = tx.fetch_all(
            f"""SELECT r.id route_id,r.factor_ref,r.factor_id,r.metric_id,
                       r.is_active,r.is_eligible,m.interval,m.metric_payload,
                       m.metric_status,m.is_valid
                FROM market_environment_factor_route r
                JOIN market_environment_factor_metric m ON m.id=r.metric_id
                WHERE r.factor_id IN ({placeholders})
                ORDER BY r.factor_id""",
            TARGET_IDS,
        )
        details = tx.fetch_all(
            f"""SELECT id detail_id,factor_id,name,calc_logic,params,
                       description,updated_at
                FROM factors_details
                WHERE is_sub_factor_id=1 AND factor_id IN ({placeholders})
                ORDER BY factor_id,id DESC""",
            TARGET_IDS,
        )
        evidence = tx.fetch_all(
            f"""SELECT e.id evidence_id,e.factor_id,e.run_id,e.calculation_mode,e.expression,
                       e.formula_hash,e.source_detail_id,e.factor_bar_interval,
                       e.factor_window_bars,e.return_bar_interval,
                       e.forward_return_bars,e.metadata_complete,e.recorded_at,
                       r.status run_status
                FROM factor_ic_run_formula_evidence e
                LEFT JOIN factor_ic_runs r ON r.run_id=e.run_id
                WHERE e.is_sub_factor_id=1 AND e.factor_id IN ({placeholders})
                ORDER BY e.factor_id,e.recorded_at DESC,e.id DESC""",
            TARGET_IDS,
        )
        persisted_stats = tx.fetch_all(
            f"""SELECT factor_id,COUNT(*) AS row_count,COUNT(DISTINCT run_id) AS run_count,
                       SUM(factor_value IS NOT NULL) AS factor_value_count,
                       SUM(adjusted_factor_value IS NOT NULL) AS adjusted_value_count,
                       MIN(as_of_time) AS first_as_of,MAX(as_of_time) AS last_as_of,
                       MAX(created_at) AS latest_created_at
                FROM factor_value_slice_metrics
                WHERE is_sub_factor_id=1 AND factor_id IN ({placeholders})
                GROUP BY factor_id ORDER BY factor_id""",
            TARGET_IDS,
        )
        latest_persisted: list[dict[str, Any]] = []
        for factor_id in TARGET_IDS:
            row = tx.fetch_one(
                """SELECT id,run_id,factor_id,factor_bar_interval,factor_window_bars,
                          symbol,as_of_time,factor_value,adjusted_factor_value,
                          weighting_method,created_at
                   FROM factor_value_slice_metrics
                   WHERE factor_id=%s AND is_sub_factor_id=1
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (factor_id,),
            )
            if row:
                latest_persisted.append(row)
    finally:
        connection.rollback()
        connection.close()

    route_by_id = {int(row["factor_id"]): row for row in routes}
    detail_by_id: dict[int, dict[str, Any]] = {}
    for row in details:
        detail_by_id.setdefault(int(row["factor_id"]), row)
    evidence_by_id: dict[int, dict[str, Any]] = {}
    evidence_by_run: dict[tuple[int, str], dict[str, Any]] = {}
    for row in evidence:
        fid = int(row["factor_id"])
        evidence_by_run[(fid, str(row["run_id"]))] = row
        if fid not in evidence_by_id and row.get("run_status") == "completed":
            evidence_by_id[fid] = row
    persisted_stats_by_id = {int(row["factor_id"]): row for row in persisted_stats}
    latest_persisted_by_id = {int(row["factor_id"]): row for row in latest_persisted}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-dpo-formula-recheck"
    output.mkdir(parents=True, exist_ok=False)
    token = os.environ.get("FACTOR4_MCP_TOKEN")
    runner: Runner | None = None
    mcp_initialized = False
    if token:
        runner = Runner(token, output, db)
        init = runner.request(
            "MCP-INIT",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "QuestTest-dpo-recheck", "version": "1.0"},
            },
        )
        runner.protocol_version = ((init.get("envelope") or {}).get("result") or {}).get(
            "protocolVersion"
        )
        mcp_initialized = init.get("http_status") == 200 and bool(runner.protocol_version)
        if mcp_initialized:
            runner.notify_initialized("MCP-NOTIFY")

    # A non-linear deterministic sequence prevents accidental equality caused
    # by a constant/linear input.  The first 60 values also establish the SMA.
    close = pd.Series([100.0 + (i * i) / 17.0 + (i % 7) * 0.13 for i in range(240)])
    rows: list[dict[str, Any]] = []
    for fid in TARGET_IDS:
        route = route_by_id.get(fid)
        detail = detail_by_id.get(fid)
        evidence_row = evidence_by_id.get(fid)
        persisted_stat = persisted_stats_by_id.get(fid) or {}
        persisted_row = latest_persisted_by_id.get(fid)
        persisted_evidence = (
            evidence_by_run.get((fid, str(persisted_row["run_id"])))
            if persisted_row
            else None
        )
        expression = str(detail.get("calc_logic") if detail else "")
        window = 60
        actual = evaluate_persisted_expression(close, expression, window)
        expected = expected_negative_dpo(close, window)
        diff = (actual - expected).abs()
        probe_index = 150
        mcp_formula: dict[str, Any] | None = None
        if runner and mcp_initialized and evidence_row:
            call = runner.tool(
                f"DPO-FORMULA-{fid}",
                "factor_get_formula",
                {
                    "factor_ref": f"sub_factor:{fid}",
                    "run_id": str(evidence_row["run_id"]),
                    "calculation_mode": evidence_row["calculation_mode"],
                    "interval": evidence_row["factor_bar_interval"],
                    "factor_window_bars": evidence_row["factor_window_bars"],
                    "return_bar_interval": evidence_row["return_bar_interval"],
                    "forward_return_bars": int(evidence_row["forward_return_bars"]),
                },
            )
            returned_formula = _data(call)
            mcp_formula = {
                "http_status": call.get("http_status"),
                "error_code": _error_code(call),
                "success": _success(call),
                "factor_ref_matches": returned_formula.get("factor_ref") == f"sub_factor:{fid}",
                "run_id_matches": str(returned_formula.get("run_id")) == str(evidence_row["run_id"]),
                "formula_hash_matches_db": returned_formula.get("formula_hash") == evidence_row.get("formula_hash"),
                "expression_matches_db": returned_formula.get("expression") == expression,
                "returned_expression": returned_formula.get("expression"),
            }
        rows.append(
            {
                "factor_id": fid,
                "factor_ref": route.get("factor_ref") if route else None,
                "active_eligible": bool(route and route.get("is_active") and route.get("is_eligible")),
                "route_metric_id": route.get("metric_id") if route else None,
                "route_interval": route.get("interval") if route else None,
                "route_window": (
                    (decode(route.get("metric_payload")) or {}).get("metric_identity", {}).get("factor_window_bars")
                    if route
                    else None
                ),
                "metric_status": route.get("metric_status") if route else None,
                "detail_id": detail.get("detail_id") if detail else None,
                "detail_name": detail.get("name") if detail else None,
                "detail_updated_at": detail.get("updated_at") if detail else None,
                "calc_logic": expression,
                "declared_params": decode(detail.get("params")) if detail else None,
                "evidence_id": evidence_row.get("evidence_id") if evidence_row else None,
                "evidence_run_id": evidence_row.get("run_id") if evidence_row else None,
                "evidence_run_status": evidence_row.get("run_status") if evidence_row else None,
                "evidence_expression_equals_detail": bool(
                    evidence_row and str(evidence_row.get("expression")) == expression
                ),
                "oracle": "-(close.shift(31) - close.rolling(60).mean())",
                "probe_index": probe_index,
                "stored_expression_value_on_synthetic_input": float(actual.iloc[probe_index]),
                "oracle_value": float(expected.iloc[probe_index]),
                "absolute_difference": float(diff.iloc[probe_index]),
                "mismatch_count_after_warmup": int(diff.iloc[60:].gt(1e-12).sum()),
                "persisted_value_rows": int(persisted_stat.get("row_count") or 0),
                "persisted_value_runs": int(persisted_stat.get("run_count") or 0),
                "persisted_factor_value_count": int(persisted_stat.get("factor_value_count") or 0),
                "persisted_adjusted_value_count": int(persisted_stat.get("adjusted_value_count") or 0),
                "persisted_first_as_of": persisted_stat.get("first_as_of"),
                "persisted_last_as_of": persisted_stat.get("last_as_of"),
                "latest_persisted_value": persisted_row,
                "latest_persisted_run_has_formula_evidence": persisted_evidence is not None,
                "latest_persisted_run_expression_equals_detail": bool(
                    persisted_evidence and str(persisted_evidence.get("expression")) == expression
                ),
                "mcp_formula": mcp_formula,
                "status": "CONFIRMED_FORMULA_MISMATCH"
                if diff.iloc[60:].gt(1e-12).any()
                else "PASS",
            }
        )

    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "environment": "test",
        "mode": "READ_ONLY_ROLLBACK",
        "mcp_formula_checked": bool(runner and mcp_initialized),
        "factor_ids": list(TARGET_IDS),
        "oracle_reference": "QuantConnect Lean DetrendedPriceOscillator: price[n/2+1] - SMA(n)",
        "synthetic_input": "close[i] = 100 + i^2/17 + 0.13*(i mod 7), 240 points",
        "checks": rows,
        "confirmed_count": sum(row["status"] == "CONFIRMED_FORMULA_MISMATCH" for row in rows),
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )
    (output / "summary.md").write_text(
        "# DPO formula recheck\n\n"
        "- Environment: `test`; mode: `read-only rollback`\n"
        f"- Active targets: `{len(rows)}`; confirmed mismatches: `{report['confirmed_count']}`\n"
        "- Stored shape: `SMA(close,n).shift(n/2+1) - close`\n"
        "- Standard negative DPO oracle: `SMA(close,n) - close.shift(n/2+1)`\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "confirmed_count": report["confirmed_count"]}))


if __name__ == "__main__":
    main()
