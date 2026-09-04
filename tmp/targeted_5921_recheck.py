#!/usr/bin/env python3
"""Run a bounded, read-only MCP/DB reconciliation for sub-factor 5921.

The script is intentionally independent of the catalog ranking budget.  It uses
the metrics bucket for exact formula, metric, and validity lookups and records a
catalog quota response as a blocked precondition rather than a product failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from config.settings import SettingsLoader
from db.client import DatabaseClient


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
FACTOR_REF = "sub_factor:5921"
FACTOR_ID = 5921
DETAIL_ID = 5376
RUN_ID = "prod_nostatus_g11_20260728_034007_20260727_19400_all_long_short_oi_leverage_m_1h_min_window_diag_d5e85b3153"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)


def json_default(value: Any) -> Any:
    """Convert database-native scalar values into JSON-safe values."""

    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def redact(value: Any) -> Any:
    """Recursively remove credentials from an evidence object."""

    if isinstance(value, dict):
        return {
            key: "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one sanitized JSON artifact."""

    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def parse_body(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Decode either an ordinary JSON or one MCP SSE data event."""

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
        raise ValueError(f"expected one MCP event, got {len(events)}")
    return events[0]


class McpClient:
    """Minimal MCP HTTP client that persists sanitized request/response pairs."""

    def __init__(self, token: str, output_dir: Path) -> None:
        """Initialize a client for one test token and output directory."""

        self.token = token
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.sequence = 0

    def call(self, case_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one JSON-RPC request and return transport plus parsed envelope."""

        self.sequence += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": case_id, "method": method}
        if params is not None:
            payload["params"] = params
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "QuestTest-targeted-5921/1.0",
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
        parse_error: str | None = None
        try:
            envelope = parse_body(raw, response_headers.get("content-type", ""))
        except Exception as exc:  # preserve transport evidence even for malformed payloads
            envelope = None
            parse_error = f"{type(exc).__name__}: {exc}"
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        result = envelope.get("result") if isinstance(envelope, dict) else None
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        text_value: dict[str, Any] | None = None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                    text_value = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    text_value = None
        business = structured if isinstance(structured, dict) else text_value
        call = {
            "case_id": case_id,
            "method": method,
            "http_status": status,
            "elapsed_seconds": elapsed,
            "content_type": response_headers.get("content-type"),
            "parse_error": parse_error,
            "envelope": envelope,
            "business": business if isinstance(business, dict) else {},
            "representations_equal": structured == text_value if structured is not None and text_value is not None else None,
        }
        write_json(self.output_dir / f"{self.sequence:02d}-{case_id}.request.json", payload)
        if envelope is not None:
            write_json(self.output_dir / f"{self.sequence:02d}-{case_id}.response.json", envelope)
        else:
            (self.output_dir / f"{self.sequence:02d}-{case_id}.response.txt").write_text(
                raw.decode("utf-8", "replace"), encoding="utf-8"
            )
        return call

    def initialize(self) -> dict[str, Any]:
        """Negotiate the MCP protocol and send the initialized notification."""

        call = self.call(
            "MCP-INIT",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "QuestTest-targeted-5921", "version": "1.0"},
            },
        )
        result = (call.get("envelope") or {}).get("result") or {}
        self.protocol_version = result.get("protocolVersion")
        self.call("MCP-NOTIFY", "notifications/initialized", {})
        return call

    def tool(self, case_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one named MCP tool."""

        return self.call(case_id, "tools/call", {"name": name, "arguments": arguments})


def business(call: dict[str, Any]) -> dict[str, Any]:
    """Return the structured business envelope of a call."""

    value = call.get("business")
    return value if isinstance(value, dict) else {}


def error_code(call: dict[str, Any]) -> str | None:
    """Return a structured business error code, if present."""

    error = business(call).get("error")
    return error.get("code") if isinstance(error, dict) else None


def db_snapshot() -> dict[str, Any]:
    """Read the selected factor's immutable definition and computation rows."""

    settings = SettingsLoader.load("test", ROOT)
    db = DatabaseClient.from_settings(settings.database)
    with db.transaction() as tx:
        detail = tx.fetch_one("SELECT * FROM factors_details WHERE id=%s", (DETAIL_ID,))
        evidence = tx.fetch_all(
            """SELECT id,run_id,factor_id,is_sub_factor_id,calculation_mode,
                      factor_bar_interval,factor_window_bars,return_bar_interval,
                      forward_return_bars,formula_version,formula_hash,expression,
                      required_fields,lookback_json,source_detail_id,recorded_at
               FROM factor_ic_run_formula_evidence
               WHERE factor_id=%s AND is_sub_factor_id=1 ORDER BY id""",
            (FACTOR_ID,),
        )
        summaries = tx.fetch_all(
            """SELECT id,run_id,factor_id,is_sub_factor_id,ic_scope,calculation_mode,
                      factor_bar_interval,factor_window_bars,return_bar_interval,
                      forward_return_bars,universe_key,symbol,window_scope,
                      scoring_version,period_start,period_end,valid_slice_count,
                      mean_ic,icir,is_icir,oos_icir
               FROM factor_ic_summary_metrics
               WHERE factor_id=%s AND is_sub_factor_id=1 ORDER BY id""",
            (FACTOR_ID,),
        )
        validity = tx.fetch_all(
            """SELECT id,run_id,factor_id,is_sub_factor_id,universe_key,
                      factor_bar_interval,factor_window_bars,window_scope,
                      time_series_summary_id,cross_sectional_summary_id,
                      time_series_status,time_series_is_valid,cross_sectional_status,
                      cross_sectional_is_valid,overall_status,overall_is_valid,
                      created_at,updated_at
               FROM factor_validity_status
               WHERE factor_id=%s AND is_sub_factor_id=1 ORDER BY id""",
            (FACTOR_ID,),
        )
        run = tx.fetch_one(
            "SELECT run_id,status,created_at,completed_at FROM factor_ic_runs WHERE run_id=%s",
            (RUN_ID,),
        )
    return {
        "database": settings.database.name,
        "factor_ref": FACTOR_REF,
        "detail": detail,
        "selected_run": run,
        "evidence": evidence,
        "summaries": summaries,
        "validity": validity,
    }


def formula_args(window: str) -> dict[str, Any]:
    """Build exact formula evidence arguments for the selected run."""

    return {
        "factor_ref": FACTOR_REF,
        "run_id": RUN_ID,
        "calculation_mode": "direct",
        "interval": "1h",
        "factor_window_bars": window,
        "return_bar_interval": "1h",
        "forward_return_bars": 1,
    }


def metric_args(symbol: str, scope: str) -> dict[str, Any]:
    """Build an exact metric request from the selected 5921 summary scope."""

    return {
        "factor_ref": FACTOR_REF,
        "ic_scope": scope,
        "calculation_mode": "direct",
        "universe_key": "all",
        "window_scope": "min_window",
        "interval": "1h",
        "factor_window_bars": "12H",
        "return_bar_interval": "1h",
        "forward_return_bars": 1,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scoring_version": "v202606_default",
        "symbol": symbol,
    }


def main() -> None:
    """Execute the read-only targeted check and emit a compact verdict."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = ROOT / "reports" / "factor4-deep" / f"{stamp}-5921-recheck"
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = db_snapshot()
    write_json(output_dir / "db-snapshot.json", snapshot)
    client = McpClient(token, output_dir)
    init = client.initialize()
    formula_12h = client.tool("FORMULA-12H", "factor_get_formula", formula_args("12H"))
    formula_24h = client.tool("FORMULA-24H", "factor_get_formula", formula_args("24H"))
    detail = client.tool("DETAIL-EXECUTABLE", "factor_get_detail", {"factor_ref": FACTOR_REF, "detail_level": "executable"})
    metric_ts = client.tool("METRIC-TS", "factor_get_metrics", metric_args("", "time_series"))
    metric_cs = client.tool("METRIC-CS", "factor_get_metrics", metric_args("", "cross_sectional"))
    validity = client.tool(
        "VALIDITY-TS",
        "factor_get_validity",
        {
            "factor_ref": FACTOR_REF,
            "validity_scope": "time_series",
            "calculation_mode": "direct",
            "universe_key": "all",
            "window_scope": "min_window",
            "interval": "1h",
            "factor_window_bars": "12H",
            "return_bar_interval": "1h",
            "forward_return_bars": 1,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "scoring_version": "v202606_default",
            "symbol": "",
            "run_id": RUN_ID,
        },
    )
    evidence = [row for row in snapshot["evidence"] if row["run_id"] == RUN_ID]
    selected = evidence[0] if evidence else {}
    f12 = business(formula_12h).get("data") or {}
    f24_error = error_code(formula_24h)
    detail_error = error_code(detail)
    ts_data = business(metric_ts).get("data") or {}
    cs_data = business(metric_cs).get("data") or {}
    validity_data = business(validity).get("data") or {}
    report = {
        "run_id": stamp,
        "environment": "test",
        "read_only": True,
        "mcp_url": MCP_URL,
        "token_label": "provided test token (redacted)",
        "db_snapshot_at": snapshot.get("selected_run", {}).get("completed_at"),
        "checks": {
            "initialize": {
                "http_status": init.get("http_status"),
                "protocol_version": client.protocol_version,
                "representations_equal": init.get("representations_equal"),
            },
            "formula_12h": {
                "http_status": formula_12h.get("http_status"),
                "error_code": error_code(formula_12h),
                "success": bool(f12) and f12.get("run_id") == RUN_ID,
                "returned_window": f12.get("metric_identity", {}).get("factor_window_bars"),
                "returned_lookback": f12.get("lookback"),
                "db_window": selected.get("factor_window_bars"),
                "db_lookback": selected.get("lookback_json"),
                "hash_matches_db": f12.get("formula_hash") == selected.get("formula_hash"),
                "source_detail_matches": f12.get("source_detail_id") == DETAIL_ID,
            },
            "formula_24h": {
                "http_status": formula_24h.get("http_status"),
                "error_code": f24_error,
                "expected_not_found": f24_error == "FORMULA_EVIDENCE_NOT_FOUND",
                "returned_window": (business(formula_24h).get("data") or {}).get("metric_identity", {}).get("factor_window_bars"),
            },
            "detail": {
                "http_status": detail.get("http_status"),
                "error_code": detail_error,
                "blocked_by_catalog_quota": detail_error == "EXPORT_BUDGET_EXCEEDED",
                "returned_window": (business(detail).get("data") or {}).get("window"),
                "returned_formula": (business(detail).get("data") or {}).get("calc_logic"),
            },
            "metrics_ts": {
                "http_status": metric_ts.get("http_status"),
                "error_code": error_code(metric_ts),
                "summary_ids": [row.get("id") for row in ts_data.get("ic_summaries", [])],
                "all_12h": all(row.get("factor_window_bars") == "12H" for row in ts_data.get("ic_summaries", [])),
            },
            "metrics_cs": {
                "http_status": metric_cs.get("http_status"),
                "error_code": error_code(metric_cs),
                "summary_ids": [row.get("id") for row in cs_data.get("ic_summaries", [])],
                "all_12h": all(row.get("factor_window_bars") == "12H" for row in cs_data.get("ic_summaries", [])),
            },
            "validity_ts": {
                "http_status": validity.get("http_status"),
                "error_code": error_code(validity),
                "summary_id": validity_data.get("time_series_summary_id"),
                "status": validity_data.get("time_series_status"),
                "is_valid": validity_data.get("time_series_is_valid"),
                "returned_window": validity_data.get("factor_window_bars"),
            },
        },
        "database_counts": {
            "evidence_5921": len(snapshot["evidence"]),
            "summary_5921": len(snapshot["summaries"]),
            "validity_5921": len(snapshot["validity"]),
            "completed_selected_run": snapshot.get("selected_run", {}).get("status"),
        },
    }
    write_json(output_dir / "summary.json", report)
    (output_dir / "summary.md").write_text(
        "# 5921 targeted recheck\n\n"
        f"- Run: `{stamp}`\n- Environment: `test`\n- Mode: `READ_ONLY`\n\n"
        f"- Formula 12H exact match: `{report['checks']['formula_12h']['success']}`\n"
        f"- Formula 24H rejected as not found: `{report['checks']['formula_24h']['expected_not_found']}`\n"
        f"- Detail error: `{detail_error or 'none'}`\n"
        f"- TS metrics error: `{error_code(metric_ts) or 'none'}`\n"
        f"- CS metrics error: `{error_code(metric_cs) or 'none'}`\n"
        f"- TS validity error: `{error_code(validity) or 'none'}`\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(output_dir), "summary": report}, ensure_ascii=False, default=json_default))


if __name__ == "__main__":
    main()
