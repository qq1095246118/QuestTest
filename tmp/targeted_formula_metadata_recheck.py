#!/usr/bin/env python3
"""Reconcile two suspected formula-window metadata mismatches read-only.

The probe compares the canonical detail row, ``sub_factors.metadata``, immutable
formula evidence, active environment metrics, and all three MCP detail levels.
It never writes to the database and redacts credentials from captured artifacts.
"""

from __future__ import annotations

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
TARGET_IDS = (160386, 161385)
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)
TOKEN_TEXT = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.I)


def json_default(value: Any) -> Any:
    """Serialize database-native values into JSON-safe scalar values."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Recursively redact credentials and token-like text from evidence."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return TOKEN_TEXT.sub("<redacted>", value)
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one recursively redacted JSON artifact."""

    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def parse_mcp(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse an ordinary JSON or one-event SSE MCP response."""

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
        raise ValueError(f"expected one SSE event, got {len(events)}")
    return events[0]


def business(envelope: dict[str, Any] | None) -> dict[str, Any]:
    """Extract structured business content from an MCP envelope."""

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


def error_code(call: dict[str, Any]) -> str | None:
    """Return a business or JSON-RPC error code from one call."""

    value = (call.get("business") or {}).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    value = (call.get("envelope") or {}).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    return None


def call_success(call: dict[str, Any]) -> bool:
    """Return whether a call has an HTTP and business-level success."""

    return (
        call.get("http_status") == 200
        and call.get("parse_error") is None
        and call.get("is_error") is not True
        and error_code(call) is None
        and isinstance(call.get("business"), dict)
    )


class McpClient:
    """Minimal authenticated MCP client with sanitized evidence capture."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize one client session for the test endpoint."""

        self.token = token
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.sequence = 0

    def request(self, case_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one MCP request and save credential-free request/response artifacts."""

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
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/139 Safari/537.36",
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
        status: int | None = None
        raw = b""
        response_headers: dict[str, str] = {}
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                status = response.status
                raw = response.read()
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
        parse_error: str | None = None
        envelope: dict[str, Any] | None = None
        try:
            envelope = parse_mcp(raw, response_headers.get("content-type", ""))
        except Exception as exc:  # preserve diagnostics
            parse_error = f"{type(exc).__name__}: {exc}"
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        call = {
            "case_id": case_id,
            "method": method,
            "http_status": status,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "content_type": response_headers.get("content-type"),
            "parse_error": parse_error,
            "envelope": envelope,
            "business": business(envelope),
            "is_error": bool((envelope or {}).get("result", {}).get("isError")) if isinstance(envelope, dict) else True,
        }
        write_json(self.output / f"{self.sequence:02d}-{case_id}.request.json", payload)
        if envelope is not None:
            write_json(self.output / f"{self.sequence:02d}-{case_id}.response.json", envelope)
        else:
            (self.output / f"{self.sequence:02d}-{case_id}.response.txt").write_text(raw.decode("utf-8", "replace"), encoding="utf-8")
        return call

    def tool(self, case_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one named MCP tool."""

        return self.request(case_id, "tools/call", {"name": name, "arguments": arguments})


def decode_json(value: Any) -> Any:
    """Decode a JSON column while preserving already-decoded values."""

    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def db_snapshot(db: DatabaseClient) -> dict[str, Any]:
    """Read target definitions, evidence, summaries, routes, and metrics."""

    placeholders = ",".join(["%s"] * len(TARGET_IDS))
    refs = tuple(f"sub_factor:{factor_id}" for factor_id in TARGET_IDS)
    with db.transaction() as tx:
        details = tx.fetch_all(
            f"""SELECT id,factor_id,is_sub_factor_id,calc_logic,params,status,updated_at
                FROM factors_details WHERE is_sub_factor_id=1 AND factor_id IN ({placeholders}) ORDER BY factor_id,id""",
            TARGET_IDS,
        )
        definitions = tx.fetch_all(
            f"""SELECT id,sub_factor_name,`window`,factor_bar_interval,formula_summary,metadata,updated_at
                FROM sub_factors WHERE id IN ({placeholders}) ORDER BY id""",
            TARGET_IDS,
        )
        evidence = tx.fetch_all(
            f"""SELECT e.id,e.run_id,e.factor_id,e.is_sub_factor_id,e.calculation_mode,
                       e.factor_bar_interval,e.factor_window_bars,e.return_bar_interval,
                       e.forward_return_bars,e.expression,e.formula_hash,e.source_detail_id,
                       e.required_fields,e.recorded_at,r.status AS run_status,r.completed_at
                FROM factor_ic_run_formula_evidence e
                LEFT JOIN factor_ic_runs r ON r.run_id=e.run_id
                WHERE e.is_sub_factor_id=1 AND e.factor_id IN ({placeholders})
                ORDER BY e.factor_id,e.recorded_at DESC,e.id DESC""",
            TARGET_IDS,
        )
        summaries = tx.fetch_all(
            f"""SELECT id,run_id,factor_id,is_sub_factor_id,ic_scope,calculation_mode,
                       factor_bar_interval,factor_window_bars,return_bar_interval,
                       forward_return_bars,universe_key,symbol,window_scope,scoring_version,
                       mean_ic,rank_icir,final_score,period_start,period_end,updated_at
                FROM factor_ic_summary_metrics
                WHERE is_sub_factor_id=1 AND factor_id IN ({placeholders})
                ORDER BY factor_id,updated_at DESC,id DESC""",
            TARGET_IDS,
        )
        routes = tx.fetch_all(
            f"""SELECT id,metric_id,factor_ref,factor_type,factor_id,publication_uid,eval_batch_id,
                       market_scope,label_kind,label_code,as_of_time,is_eligible,is_active
                FROM market_environment_factor_route
                WHERE factor_ref IN ({','.join(['%s'] * len(refs))}) ORDER BY id""",
            refs,
        )
        metrics = tx.fetch_all(
            f"""SELECT id,eval_batch_id,factor_ref,factor_type,factor_id,market_scope,label_kind,
                       label_code,evaluation_type,`interval`,return_bar_interval,forward_return_bars,
                       window_scope,metric_status,is_valid,scoring_version,metric_payload,updated_at
                FROM market_environment_factor_metric
                WHERE factor_ref IN ({','.join(['%s'] * len(refs))}) ORDER BY id DESC""",
            refs,
        )
    latest_evidence: dict[int, dict[str, Any]] = {}
    for row in evidence:
        factor_id = int(row["factor_id"])
        if factor_id not in latest_evidence and str(row.get("run_status")) == "completed":
            latest_evidence[factor_id] = row
    return {
        "details": details,
        "definitions": definitions,
        "evidence": latest_evidence,
        "all_evidence_counts": {str(fid): sum(int(row["factor_id"]) == fid for row in evidence) for fid in TARGET_IDS},
        "summaries": summaries,
        "routes": routes,
        "metrics": metrics,
    }


def compact_metric(row: dict[str, Any]) -> dict[str, Any]:
    """Keep metric identity and omit large diagnostic payloads."""

    payload = decode_json(row.get("metric_payload"))
    identity = payload.get("metric_identity") if isinstance(payload, dict) else None
    return {
        key: row.get(key)
        for key in (
            "id", "eval_batch_id", "factor_ref", "factor_type", "factor_id", "market_scope",
            "label_kind", "label_code", "evaluation_type", "interval", "return_bar_interval",
            "forward_return_bars", "window_scope", "metric_status", "is_valid", "scoring_version", "updated_at",
        )
    } | {"metric_identity": identity}


def formula_args(row: dict[str, Any]) -> dict[str, Any]:
    """Build the exact immutable formula identity from one evidence row."""

    return {
        "factor_ref": f"sub_factor:{int(row['factor_id'])}",
        "run_id": row["run_id"],
        "calculation_mode": row["calculation_mode"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
    }


def main() -> None:
    """Run the read-only MCP/DB reconciliation and print its report path."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    settings = SettingsLoader.load("test", ROOT)
    db = DatabaseClient.from_settings(settings.database)
    snapshot = db_snapshot(db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-formula-metadata-recheck"
    client = McpClient(token, output)

    init = client.request(
        "INIT", "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "QuestTest-formula-metadata-recheck", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    client.protocol_version = init_result.get("protocolVersion")
    client.request("NOTIFY", "notifications/initialized", {})
    report: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "environment": "test",
        "mcp_url": MCP_URL,
        "read_only": True,
        "targets": list(TARGET_IDS),
        "init": {"http_status": init.get("http_status"), "protocol_version": client.protocol_version, "server_info": init_result.get("serverInfo")},
        "db": {
            "details": snapshot["details"],
            "definitions": [
                {**row, "metadata": decode_json(row.get("metadata"))} for row in snapshot["definitions"]
            ],
            "latest_evidence": snapshot["evidence"],
            "all_evidence_counts": snapshot["all_evidence_counts"],
            "summaries_count": {str(fid): sum(int(row["factor_id"]) == fid for row in snapshot["summaries"]) for fid in TARGET_IDS},
            "routes": snapshot["routes"],
            "metrics": [compact_metric(row) for row in snapshot["metrics"]],
        },
        "mcp": {},
    }
    if not client.protocol_version:
        report["status"] = "BLOCKED_AUTH_OR_PROTOCOL"
        write_json(output / "report.json", report)
        print(json.dumps({"output": str(output), "status": report["status"]}, ensure_ascii=False))
        return

    for factor_id in TARGET_IDS:
        ref = f"sub_factor:{factor_id}"
        levels: dict[str, dict[str, Any]] = {}
        for level in ("summary", "definition", "executable"):
            call = client.tool(f"DETAIL-{factor_id}-{level}", "factor_get_detail", {"factor_ref": ref, "detail_level": level})
            data = (call.get("business") or {}).get("data")
            levels[level] = {
                "http_status": call.get("http_status"),
                "success": call_success(call),
                "error_code": error_code(call),
                "data": data if isinstance(data, dict) else {},
            }
        evidence = snapshot["evidence"].get(factor_id)
        formula_call: dict[str, Any] | None = None
        if evidence:
            formula_call = client.tool(f"FORMULA-{factor_id}", "factor_get_formula", formula_args(evidence))
        report["mcp"][ref] = {
            "detail_levels": levels,
            "formula": {
                "request": formula_args(evidence) if evidence else None,
                "http_status": formula_call.get("http_status") if formula_call else None,
                "success": call_success(formula_call) if formula_call else False,
                "error_code": error_code(formula_call) if formula_call else None,
                "data": ((formula_call or {}).get("business") or {}).get("data") or {},
            },
        }

    # Summarize deterministic comparisons in a small, easy-to-review section.
    comparisons: list[dict[str, Any]] = []
    details_by_factor = {int(row["factor_id"]): row for row in snapshot["details"]}
    definitions_by_factor = {int(row["id"]): row for row in snapshot["definitions"]}
    for factor_id in TARGET_IDS:
        detail = details_by_factor.get(factor_id, {})
        definition = definitions_by_factor.get(factor_id, {})
        metadata = decode_json(definition.get("metadata"))
        metadata_formula = metadata.get("formula") if isinstance(metadata, dict) else None
        evidence = snapshot["evidence"].get(factor_id) or {}
        mcp_data = report["mcp"].get(f"sub_factor:{factor_id}", {})
        executable = ((mcp_data.get("detail_levels") or {}).get("executable") or {}).get("data") or {}
        formula_data = mcp_data.get("formula", {}).get("data") or {}
        comparisons.append({
            "factor_ref": f"sub_factor:{factor_id}",
            "detail_id": detail.get("id"),
            "detail_calc_logic": detail.get("calc_logic"),
            "metadata_formula": metadata_formula,
            "metadata_window": metadata.get("window") if isinstance(metadata, dict) else None,
            "detail_params": decode_json(detail.get("params")),
            "evidence": {
                "id": evidence.get("id"), "run_id": evidence.get("run_id"),
                "source_detail_id": evidence.get("source_detail_id"),
                "expression": evidence.get("expression"), "formula_hash": evidence.get("formula_hash"),
                "factor_window_bars": evidence.get("factor_window_bars"), "run_status": evidence.get("run_status"),
            },
            "mcp_definition": {
                "calc_logic": executable.get("calc_logic"),
                "metadata_formula": ((executable.get("metadata") or {}).get("formula") if isinstance(executable.get("metadata"), dict) else None),
                "source_detail_id": executable.get("source_detail_id"),
            },
            "mcp_formula": {
                "expression": formula_data.get("expression"),
                "formula_hash": formula_data.get("formula_hash"),
                "source_detail_id": formula_data.get("source_detail_id"),
                "metric_identity": formula_data.get("metric_identity"),
            },
            "active_metric_count": sum(1 for row in snapshot["metrics"] if row.get("factor_ref") == f"sub_factor:{factor_id}"),
            "active_route_count": sum(1 for row in snapshot["routes"] if row.get("factor_ref") == f"sub_factor:{factor_id}" and int(row.get("is_active") or 0) == 1),
        })
    report["comparisons"] = comparisons
    report["status"] = "COMPLETE"
    write_json(output / "report.json", report)
    print(json.dumps({"output": str(output), "status": report["status"], "targets": len(comparisons)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
