#!/usr/bin/env python3
"""Read-only reconciliation for factors declared as IV-minus-realized-volatility.

The probe compares the catalog/detail declaration, executable formula evidence,
MCP formula/detail projections, approved raw-field schema, and active route
metrics. It never writes to the database and redacts token-like values in files.
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from config.settings import SettingsLoader
from db.client import DatabaseClient


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TARGET_IDS = (161628, 161629, 161630)
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)
TOKEN_TEXT = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.I)


def decode(value: Any) -> Any:
    """Decode JSON-like database values while preserving native scalars."""

    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def safe(value: Any) -> Any:
    """Convert database-native values into JSON-safe values."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def redact(value: Any) -> Any:
    """Recursively remove credentials from an evidence object."""

    if isinstance(value, dict):
        return {str(k): "<redacted>" if SENSITIVE_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return TOKEN_TEXT.sub("<redacted>", value)
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one redacted JSON artifact."""

    path.write_text(json.dumps(redact(value), ensure_ascii=False, indent=2, default=safe) + "\n", encoding="utf-8")


def parse_envelope(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse JSON or a single JSON event from an MCP SSE response."""

    text = raw.decode("utf-8", "replace")
    if "text/event-stream" not in content_type.lower():
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    events = []
    for block in re.split(r"\r?\n\r?\n", text):
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if data_lines:
            value = json.loads("\n".join(data_lines))
            if isinstance(value, dict):
                events.append(value)
    if len(events) != 1:
        raise ValueError(f"expected one MCP event, got {len(events)}")
    return events[0]


def business(envelope: dict[str, Any] | None) -> dict[str, Any]:
    """Extract structured business data from an MCP JSON-RPC envelope."""

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
    """Return a business or JSON-RPC error code from a captured call."""

    value = (call.get("business") or {}).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    value = (call.get("envelope") or {}).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    return None


class Client:
    """Minimal stateful MCP client used only by this read-only probe."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize an authenticated client and artifact directory."""

        self.token = token
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.sequence = 0

    def request(self, case_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one MCP request and capture a sanitized request/response pair."""

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
            "User-Agent": "QuestTest-iv-rv-recheck/1.0",
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        request = urllib.request.Request(MCP_URL, data=json.dumps(payload, separators=(",", ":")).encode(), headers=headers, method="POST")
        started = time.monotonic()
        status: int | None = None
        raw = b""
        response_headers: dict[str, str] = {}
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                status = response.status
                raw = response.read()
                response_headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            response_headers = {k.lower(): v for k, v in exc.headers.items()}
        parse_error: str | None = None
        envelope: dict[str, Any] | None = None
        try:
            envelope = parse_envelope(raw, response_headers.get("content-type", ""))
        except Exception as exc:  # preserve transport diagnostics
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
        """Invoke one MCP tool by name."""

        return self.request(case_id, "tools/call", {"name": name, "arguments": arguments})


def db_snapshot(db: DatabaseClient) -> dict[str, Any]:
    """Read target details, metadata, formula evidence, schema and active metrics."""

    placeholders = ",".join(["%s"] * len(TARGET_IDS))
    refs = tuple(f"sub_factor:{factor_id}" for factor_id in TARGET_IDS)
    with db.transaction() as tx:
        details = tx.fetch_all(
            f"""SELECT id,factor_id,is_sub_factor_id,calc_logic,params,description,data_source_metadata,status,updated_at
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
        # The field contract is stored in formula evidence; this query makes the
        # approved raw-field universe explicit for the target factors.
        field_rows = tx.fetch_all(
            f"""SELECT id,run_id,factor_id,source_detail_id,required_fields,expression,formula_hash
                FROM factor_ic_run_formula_evidence
                WHERE is_sub_factor_id=1 AND factor_id IN ({placeholders})
                ORDER BY factor_id,recorded_at DESC,id DESC""",
            TARGET_IDS,
        )
    latest: dict[int, dict[str, Any]] = {}
    for row in evidence:
        fid = int(row["factor_id"])
        if fid not in latest and str(row.get("run_status")) == "completed":
            latest[fid] = row
    return {"details": details, "definitions": definitions, "evidence": latest, "all_evidence": evidence, "routes": routes, "metrics": metrics, "field_evidence": field_rows}


def compact_metric(row: dict[str, Any]) -> dict[str, Any]:
    """Remove large metric payloads while retaining identity and status."""

    payload = decode(row.get("metric_payload"))
    identity = payload.get("metric_identity") if isinstance(payload, dict) else None
    keys = ("id", "eval_batch_id", "factor_ref", "factor_type", "factor_id", "market_scope", "label_kind", "label_code", "evaluation_type", "interval", "return_bar_interval", "forward_return_bars", "window_scope", "metric_status", "is_valid", "scoring_version", "updated_at")
    return {key: row.get(key) for key in keys} | {"metric_identity": identity}


def fields_from_expression(expression: str | None) -> list[str]:
    """Extract likely data variable names from a Python-like formula."""

    if not expression:
        return []
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return []
    ignored = {"window", "min_periods", "np", "pd", "True", "False", "None"}
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in ignored and node.id.isidentifier():
            names.add(node.id)
    return sorted(names)


def main() -> None:
    """Run the read-only MCP/database reconciliation and print its report path."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    settings = SettingsLoader.load("test", ROOT)
    db = DatabaseClient.from_settings(settings.database)
    snapshot = db_snapshot(db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-iv-rv-definition-recheck"
    client = Client(token, output)
    init = client.request("INIT", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest-iv-rv-recheck", "version": "1.0"}})
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
            "details": [{**row, "params": decode(row.get("params")), "data_source_metadata": decode(row.get("data_source_metadata"))} for row in snapshot["details"]],
            "definitions": [{**row, "metadata": decode(row.get("metadata"))} for row in snapshot["definitions"]],
            "latest_evidence": [{**row, "required_fields": decode(row.get("required_fields"))} for row in snapshot["evidence"].values()],
            "evidence_count": {str(fid): sum(int(row["factor_id"]) == fid for row in snapshot["all_evidence"]) for fid in TARGET_IDS},
            "routes": snapshot["routes"],
            "metrics": [compact_metric(row) for row in snapshot["metrics"]],
            "field_evidence": [{**row, "required_fields": decode(row.get("required_fields"))} for row in snapshot["field_evidence"]],
        },
        "mcp": {},
    }
    raw_schema_call = client.tool("RAW-SCHEMA", "schema_get_raw_data", {})
    report["mcp"]["raw_schema"] = {"http_status": raw_schema_call.get("http_status"), "error_code": error_code(raw_schema_call), "data": (raw_schema_call.get("business") or {}).get("data") or {}}
    for fid in TARGET_IDS:
        ref = f"sub_factor:{fid}"
        levels: dict[str, Any] = {}
        for level in ("summary", "definition", "executable"):
            call = client.tool(f"DETAIL-{fid}-{level}", "factor_get_detail", {"factor_ref": ref, "detail_level": level})
            levels[level] = {"http_status": call.get("http_status"), "error_code": error_code(call), "is_error": call.get("is_error"), "data": (call.get("business") or {}).get("data") or {}}
        evidence = snapshot["evidence"].get(fid)
        formula = None
        if evidence:
            args = {"factor_ref": ref, "run_id": evidence["run_id"], "calculation_mode": evidence["calculation_mode"], "interval": evidence["factor_bar_interval"], "factor_window_bars": evidence["factor_window_bars"], "return_bar_interval": evidence["return_bar_interval"], "forward_return_bars": int(evidence["forward_return_bars"])}
            formula = client.tool(f"FORMULA-{fid}", "factor_get_formula", args)
        report["mcp"][ref] = {"detail_levels": levels, "formula": {"http_status": formula.get("http_status") if formula else None, "error_code": error_code(formula) if formula else None, "data": (formula.get("business") or {}).get("data") if formula else {}}}

    details = {int(row["factor_id"]): row for row in snapshot["details"]}
    definitions = {int(row["id"]): row for row in snapshot["definitions"]}
    comparisons = []
    for fid in TARGET_IDS:
        detail = details.get(fid, {})
        definition = definitions.get(fid, {})
        metadata = decode(definition.get("metadata"))
        evidence = snapshot["evidence"].get(fid, {})
        mcp = report["mcp"].get(f"sub_factor:{fid}", {})
        executable = ((mcp.get("detail_levels") or {}).get("executable") or {}).get("data") or {}
        formula = (mcp.get("formula") or {}).get("data") or {}
        declared = (decode(detail.get("params")) or {}).get("fields", []) if isinstance(decode(detail.get("params")), dict) else []
        expression = str(evidence.get("expression") or detail.get("calc_logic") or "")
        comparisons.append({
            "factor_ref": f"sub_factor:{fid}",
            "detail_id": detail.get("id"),
            "description": detail.get("description"),
            "formula_summary": definition.get("formula_summary"),
            "declared_data_source": detail.get("data_source_metadata"),
            "metadata": metadata,
            "detail_calc_logic": detail.get("calc_logic"),
            "detail_params_fields": declared,
            "expression_fields": fields_from_expression(expression),
            "evidence": {"id": evidence.get("id"), "run_id": evidence.get("run_id"), "source_detail_id": evidence.get("source_detail_id"), "expression": evidence.get("expression"), "formula_hash": evidence.get("formula_hash"), "required_fields": decode(evidence.get("required_fields"))},
            "mcp_executable": {"calc_logic": executable.get("calc_logic"), "metadata": executable.get("metadata"), "source_detail_id": executable.get("source_detail_id"), "data_source_metadata": executable.get("data_source_metadata")},
            "mcp_formula": {"expression": formula.get("expression"), "formula_hash": formula.get("formula_hash"), "source_detail_id": formula.get("source_detail_id"), "required_fields": formula.get("required_fields"), "field_resolution": formula.get("field_resolution"), "metric_identity": formula.get("metric_identity")},
            "active_routes": [row for row in snapshot["routes"] if row.get("factor_ref") == f"sub_factor:{fid}" and int(row.get("is_active") or 0) == 1],
            "active_metrics": [compact_metric(row) for row in snapshot["metrics"] if row.get("factor_ref") == f"sub_factor:{fid}"],
        })
    report["comparisons"] = comparisons
    report["status"] = "COMPLETE"
    write_json(output / "report.json", report)
    print(json.dumps({"output": str(output), "status": report["status"], "targets": len(comparisons)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
