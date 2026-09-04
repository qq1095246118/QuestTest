#!/usr/bin/env python3
"""Reconcile suspected unbounded aggregate formulas through MCP and the test DB.

This runner is deliberately read-only.  It discovers the latest completed
formula-evidence row for the two candidates found by the static audit, calls
the exact MCP formula and metric scopes, and records enough identity fields to
separate an MCP projection defect from an underlying factor-definition defect.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
TARGET_IDS = (1336092, 1482924)
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)


def json_default(value: Any) -> Any:
    """Convert database-native values to JSON-safe scalar values."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def redact(value: Any) -> Any:
    """Recursively remove credential-like keys from captured evidence."""

    if isinstance(value, dict):
        return {
            key: "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one sanitized JSON artifact to ``path``."""

    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def parse_body(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse an ordinary JSON MCP response or one SSE data event."""

    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" not in content_type.lower():
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    events: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if lines:
            value = json.loads("\n".join(lines))
            if isinstance(value, dict):
                events.append(value)
    if len(events) != 1:
        raise ValueError(f"expected one MCP SSE event, got {len(events)}")
    return events[0]


def business(envelope: dict[str, Any] | None) -> dict[str, Any]:
    """Extract structured business content, falling back to text content."""

    result = envelope.get("result") if isinstance(envelope, dict) else None
    if not isinstance(result, dict):
        return {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def error_code(value: dict[str, Any]) -> str | None:
    """Return a structured business or JSON-RPC error code."""

    err = value.get("error")
    if isinstance(err, dict) and err.get("code") is not None:
        return str(err["code"])
    return None


class McpClient:
    """Minimal authenticated MCP client that persists redacted call evidence."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize a client for one read-only test session."""

        self.token = token
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.sequence = 0

    def call(self, case_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one JSON-RPC request and return transport plus business data."""

        self.sequence += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": case_id, "method": method}
        if params is not None:
            payload["params"] = params
        if method == "notifications/initialized":
            payload.pop("id", None)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "QuestTest-aggregate-window-recheck/1.0",
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        request = urllib.request.Request(
            MCP_URL,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        raw = b""
        status = 0
        response_headers: dict[str, str] = {}
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
                status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
        elapsed = round(time.monotonic() - started, 3)
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        parse_error: str | None = None
        envelope: dict[str, Any] | None = None
        try:
            envelope = parse_body(raw, response_headers.get("content-type", ""))
        except Exception as exc:  # preserve transport evidence for malformed responses
            parse_error = f"{type(exc).__name__}: {exc}"
        result = {
            "case_id": case_id,
            "method": method,
            "http_status": status,
            "elapsed_seconds": elapsed,
            "content_type": response_headers.get("content-type"),
            "parse_error": parse_error,
            "envelope": envelope,
            "business": business(envelope),
        }
        write_json(self.output / f"{self.sequence:02d}-{case_id}.request.json", payload)
        if envelope is not None:
            write_json(self.output / f"{self.sequence:02d}-{case_id}.response.json", envelope)
        else:
            (self.output / f"{self.sequence:02d}-{case_id}.response.txt").write_text(
                raw.decode("utf-8", "replace"), encoding="utf-8"
            )
        return result


def db_snapshot(db: DatabaseClient) -> dict[str, Any]:
    """Read candidate definitions, evidence, summaries, and routes."""

    placeholders = ",".join(["%s"] * len(TARGET_IDS))
    with db.transaction() as tx:
        details = tx.fetch_all(
            f"SELECT id,factor_id,is_sub_factor_id,calc_logic,params FROM factors_details WHERE factor_id IN ({placeholders}) ORDER BY id",
            TARGET_IDS,
        )
        evidence = tx.fetch_all(
            f"""SELECT id,run_id,factor_id,is_sub_factor_id,calculation_mode,
                       factor_bar_interval,factor_window_bars,return_bar_interval,
                       forward_return_bars,formula_version,formula_hash,expression,
                       source_detail_id,recorded_at
                FROM factor_ic_run_formula_evidence
                WHERE factor_id IN ({placeholders}) AND is_sub_factor_id=1
                ORDER BY factor_id,id""",
            TARGET_IDS,
        )
        summaries = tx.fetch_all(
            f"""SELECT id,run_id,factor_id,is_sub_factor_id,ic_scope,
                       calculation_mode,factor_bar_interval,factor_window_bars,
                       return_bar_interval,forward_return_bars,universe_key,symbol,
                       window_scope,scoring_version,period_start,period_end,
                       valid_slice_count,mean_ic,mean_rank_ic,icir,oos_icir
                FROM factor_ic_summary_metrics
                WHERE factor_id IN ({placeholders}) AND is_sub_factor_id=1
                ORDER BY factor_id,id""",
            TARGET_IDS,
        )
        runs = tx.fetch_all(
            f"SELECT run_id,status,created_at,completed_at FROM factor_ic_runs WHERE run_id IN (SELECT DISTINCT run_id FROM factor_ic_run_formula_evidence WHERE factor_id IN ({placeholders}) AND is_sub_factor_id=1)",
            TARGET_IDS,
        )
        routes = tx.fetch_all(
            f"""SELECT id,factor_ref,factor_type,factor_id,metric_id,label_code,
                       publication_uid,eval_batch_id,is_eligible,is_active
                FROM market_environment_factor_route
                WHERE factor_id IN ({placeholders})""",
            TARGET_IDS,
        )
    return {"details": details, "evidence": evidence, "summaries": summaries, "runs": runs, "routes": routes}


def choose_evidence(snapshot: dict[str, Any], factor_id: int) -> dict[str, Any]:
    """Select the newest completed minimum-window evidence row for a factor."""

    statuses = {str(row["run_id"]): str(row.get("status")) for row in snapshot["runs"]}
    rows = [
        row
        for row in snapshot["evidence"]
        if int(row["factor_id"]) == factor_id and statuses.get(str(row["run_id"])) == "completed"
    ]
    rows.sort(key=lambda row: str(row.get("recorded_at") or ""), reverse=True)
    min_rows = [row for row in rows if "min_window" in str(row.get("run_id") or "")]
    return (min_rows or rows)[0] if (min_rows or rows) else {}


def formula_args(row: dict[str, Any]) -> dict[str, Any]:
    """Build an exact formula-evidence request from one DB evidence row."""

    return {
        "factor_ref": f"sub_factor:{int(row['factor_id'])}",
        "run_id": row["run_id"],
        "calculation_mode": row["calculation_mode"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
    }


def metric_args(row: dict[str, Any], scope: str) -> dict[str, Any]:
    """Build an exact metric-scope request for one DB evidence row."""

    return {
        "factor_ref": f"sub_factor:{int(row['factor_id'])}",
        "ic_scope": scope,
        "calculation_mode": row["calculation_mode"],
        "universe_key": "all",
        "window_scope": "min_window",
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scoring_version": "v202606_default" if scope == "time_series" else "v20260728_scope_split",
        "symbol": "",
    }


def aggregate_issues(expression: str) -> list[str]:
    """Detect method aggregates lacking a bounded rolling/ewm/expanding receiver."""

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return ["EXPRESSION_PARSE_ERROR"]

    def method_name(node: ast.Call) -> str:
        return node.func.attr.lower() if isinstance(node.func, ast.Attribute) else ""

    def receiver_chain(value: ast.AST) -> list[str]:
        chain: list[str] = []
        current: ast.AST | None = value
        while current is not None:
            if isinstance(current, ast.Call):
                if isinstance(current.func, ast.Attribute):
                    chain.append(current.func.attr.lower())
                    current = current.func.value
                else:
                    break
            elif isinstance(current, ast.Attribute):
                chain.append(current.attr.lower())
                current = current.value
            else:
                break
        return chain

    issues: set[str] = set()
    aggregate_names = {"mean", "std", "var", "median", "sum", "min", "max", "quantile", "cov", "corr"}
    bounded_names = {"rolling", "ewm", "expanding", "groupby", "resample"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name = method_name(node)
        if name in aggregate_names and not (set(receiver_chain(node.func.value)) & bounded_names):
            issues.add(f"UNBOUNDED_{name.upper()}")
    return sorted(issues)


def main() -> None:
    """Execute the read-only MCP/DB reconciliation and write a summary."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    settings = SettingsLoader.load("test", ROOT)
    db = DatabaseClient.from_settings(settings.database)
    snapshot = db_snapshot(db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-aggregate-window-recheck"
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "db-snapshot.json", snapshot)

    client = McpClient(token, output)
    init = client.call(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-aggregate-window-recheck", "version": "1.0"},
        },
    )
    init_result = (init.get("envelope") or {}).get("result") or {}
    client.protocol_version = str(init_result.get("protocolVersion") or "") or None
    client.call("MCP-NOTIFY", "notifications/initialized", {})

    checks: list[dict[str, Any]] = []
    for factor_id in TARGET_IDS:
        row = choose_evidence(snapshot, factor_id)
        if not row:
            checks.append({"factor_id": factor_id, "status": "BLOCKED", "reason": "no completed evidence"})
            continue
        formula = client.call(f"FORMULA-{factor_id}", "tools/call", {"name": "factor_get_formula", "arguments": formula_args(row)})
        formula_business = formula["business"]
        formula_data = formula_business.get("data") if isinstance(formula_business.get("data"), dict) else {}
        metric_results: dict[str, dict[str, Any]] = {}
        for scope in ("time_series", "cross_sectional"):
            # Some legacy runs only persisted symbol-level TS summaries.  Try
            # the aggregate scope first, then one symbol actually present in
            # the same DB snapshot; an empty aggregate is not a run failure.
            symbols = [
                str(item.get("symbol"))
                for item in snapshot["summaries"]
                if int(item.get("factor_id")) == factor_id
                and str(item.get("ic_scope")) == scope
                and item.get("symbol")
            ]
            requests: list[tuple[str, dict[str, Any]]] = [("aggregate", metric_args(row, scope))]
            if symbols:
                symbol_args = metric_args(row, scope)
                symbol_args["symbol"] = symbols[0]
                requests.append((f"symbol:{symbols[0]}", symbol_args))
            scope_results: dict[str, Any] = {}
            for label, arguments in requests:
                call = client.call(
                    f"METRIC-{factor_id}-{scope.upper()}-{label.replace(':', '-')}",
                    "tools/call",
                    {"name": "factor_get_metrics", "arguments": arguments},
                )
                scope_results[label] = {
                    "http_status": call["http_status"],
                    "error_code": error_code(call["business"]),
                    "data": call["business"].get("data") if isinstance(call["business"].get("data"), dict) else {},
                }
            metric_results[scope] = scope_results
        expression = str(row.get("expression") or "")
        returned_expression = str(formula_data.get("expression") or "")
        checks.append(
            {
                "factor_id": factor_id,
                "factor_ref": f"sub_factor:{factor_id}",
                "detail_id": row.get("source_detail_id"),
                "run_id": row.get("run_id"),
                "db_formula_hash": row.get("formula_hash"),
                "mcp_formula_hash": formula_data.get("formula_hash"),
                "formula_http_status": formula["http_status"],
                "formula_error_code": error_code(formula_business),
                "hash_matches": formula_data.get("formula_hash") == row.get("formula_hash"),
                "expression_matches": returned_expression == expression,
                "mcp_identity": formula_data.get("metric_identity"),
                "db_identity": {
                    "calculation_mode": row.get("calculation_mode"),
                    "factor_bar_interval": row.get("factor_bar_interval"),
                    "factor_window_bars": row.get("factor_window_bars"),
                    "return_bar_interval": row.get("return_bar_interval"),
                    "forward_return_bars": row.get("forward_return_bars"),
                },
                "aggregate_issues": aggregate_issues(expression),
                "expression": expression,
                "metrics": metric_results,
            }
        )

    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "environment": settings.environment,
        "mcp_url": MCP_URL,
        "read_only": True,
        "token_label": "provided test token (redacted)",
        "db_snapshot_sha256": hashlib.sha256(json.dumps(snapshot, default=json_default, sort_keys=True).encode()).hexdigest(),
        "initialize": {
            "http_status": init["http_status"],
            "protocol_version": client.protocol_version,
            "error_code": error_code(init["business"]),
        },
        "checks": checks,
    }
    write_json(output / "summary.json", report)
    lines = [
        "# Aggregate-window candidate recheck",
        "",
        f"- Captured: `{report['captured_at']}`",
        f"- Environment: `{settings.environment}`; mode: `READ_ONLY`",
        f"- MCP initialize: HTTP `{init['http_status']}`, protocol `{client.protocol_version}`",
        "",
    ]
    for check in checks:
        if check.get("status") == "BLOCKED":
            lines.append(f"- `{check['factor_id']}`: BLOCKED ({check['reason']})")
            continue
        ts_results = check["metrics"].get("time_series", {})
        cs_results = check["metrics"].get("cross_sectional", {})
        ts_text = "; ".join(
            f"{label}: HTTP `{value.get('http_status')}`, error `{value.get('error_code') or 'none'}`"
            for label, value in ts_results.items()
        ) or "none"
        cs_text = "; ".join(
            f"{label}: HTTP `{value.get('http_status')}`, error `{value.get('error_code') or 'none'}`"
            for label, value in cs_results.items()
        ) or "none"
        lines.extend(
            [
                f"- `{check['factor_id']}`: formula HTTP `{check['formula_http_status']}`, "
                f"hash match `{check['hash_matches']}`, expression match `{check['expression_matches']}`, "
                f"unbounded aggregate issues `{', '.join(check['aggregate_issues']) or 'none'}`.",
                f"  TS metrics: {ts_text}; CS metrics: {cs_text}.",
            ]
        )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "checks": checks}, ensure_ascii=False, default=json_default))


if __name__ == "__main__":
    main()
