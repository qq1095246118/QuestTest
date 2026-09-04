#!/usr/bin/env python3
"""Reconcile active Factor 4 formula projections and semantic invariants.

This read-only probe covers every active and eligible route in the test
environment. It compares MCP batch detail projections with database detail
rows, checks approved raw-field references, and records only independent
semantic candidates after excluding known formula families.
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from config.settings import SettingsLoader
from db.client import DatabaseClient


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)
TOKEN_TEXT = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.I)
KNOWN_FAMILIES = ("topup", "funding", "long_short", "vwap", "iv_rv", "implied_vol")
DERIVED_FIELDS = {"returns", "log_return", "log_returns", "vwap", "truerange", "rsi", "taker_volume", "buy", "sell"}
FUNCTION_NAMES = {
    "abs", "clip", "corr", "correlation", "diff", "ewm", "exp", "log", "max", "mean", "min",
    "np", "pd", "pct_change", "replace", "rolling", "rolling_vwap", "shift", "std", "sum", "truerange",
}


def decode(value: Any) -> Any:
    """Decode JSON-like database values without changing native scalars."""

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


def json_safe(value: Any) -> Any:
    """Convert database-native values into JSON-safe values."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def redact(value: Any) -> Any:
    """Recursively redact credentials and token-like strings."""

    if isinstance(value, dict):
        return {str(k): "<redacted>" if SENSITIVE_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return TOKEN_TEXT.sub("<redacted>", value)
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one recursively redacted JSON artifact."""

    path.write_text(json.dumps(redact(value), ensure_ascii=False, indent=2, default=json_safe) + "\n", encoding="utf-8")


def parse_response(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse JSON or a single MCP SSE event."""

    text = raw.decode("utf-8", "replace")
    if "text/event-stream" not in content_type.lower():
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    events = []
    for block in re.split(r"\r?\n\r?\n", text):
        lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if lines:
            value = json.loads("\n".join(lines))
            if isinstance(value, dict):
                events.append(value)
    if len(events) != 1:
        raise ValueError(f"expected one MCP event, got {len(events)}")
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
    """Return a business or JSON-RPC error code from a call."""

    value = (call.get("business") or {}).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    value = (call.get("envelope") or {}).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    return None


class Client:
    """Minimal authenticated MCP client for read-only requests."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize a client and sanitized artifact directory."""

        self.token = token
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.sequence = 0

    def request(self, case_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one MCP request and persist its redacted evidence."""

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
        request = urllib.request.Request(MCP_URL, data=json.dumps(payload, separators=(",", ":")).encode(), headers=headers, method="POST")
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
            envelope = parse_response(raw, response_headers.get("content-type", ""))
        except Exception as exc:
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
        write_json(self.output / f"{self.sequence:03d}-{case_id}.request.json", payload)
        if envelope is not None:
            write_json(self.output / f"{self.sequence:03d}-{case_id}.response.json", envelope)
        else:
            (self.output / f"{self.sequence:03d}-{case_id}.response.txt").write_text(raw.decode("utf-8", "replace"), encoding="utf-8")
        return call

    def tool(self, case_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one MCP tool."""

        return self.request(case_id, "tools/call", {"name": name, "arguments": arguments})


def db_snapshot(db: DatabaseClient) -> dict[str, Any]:
    """Read all active route identities, details, evidence and metric payloads."""

    with db.transaction() as tx:
        routes = tx.fetch_all(
            """SELECT id route_id,factor_ref,factor_type,factor_id,metric_id,market_scope,label_code
               FROM market_environment_factor_route WHERE is_active=1 AND is_eligible=1 ORDER BY id"""
        )
        details = tx.fetch_all(
            """SELECT id,factor_id,is_sub_factor_id,calc_logic,params,description,data_source_metadata,updated_at
               FROM factors_details WHERE is_sub_factor_id=1 ORDER BY id"""
        )
        definitions = tx.fetch_all(
            """SELECT id,sub_factor_name,`window`,factor_bar_interval,formula_summary,metadata,updated_at
               FROM sub_factors ORDER BY id"""
        )
        evidence = tx.fetch_all(
            """SELECT id,run_id,factor_id,is_sub_factor_id,calculation_mode,factor_bar_interval,
                      factor_window_bars,return_bar_interval,forward_return_bars,expression,formula_hash,
                      source_detail_id,required_fields,recorded_at
               FROM factor_ic_run_formula_evidence ORDER BY factor_id,recorded_at DESC,id DESC"""
        )
        metrics = tx.fetch_all(
            """SELECT id,eval_batch_id,factor_ref,factor_type,factor_id,`interval`,return_bar_interval,
                      forward_return_bars,metric_status,is_valid,metric_payload,updated_at
               FROM market_environment_factor_metric WHERE id IN
                 (SELECT metric_id FROM market_environment_factor_route WHERE is_active=1 AND is_eligible=1)
               ORDER BY id"""
        )
    return {"routes": routes, "details": details, "definitions": definitions, "evidence": evidence, "metrics": metrics}


def ast_names(expression: str | None) -> set[str]:
    """Extract probable data variable names from one formula expression."""

    if not expression:
        return set()
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return set()
    return {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in FUNCTION_NAMES and node.id not in {"window", "min_periods", "True", "False", "None", "nan", "inf"}
    }


def ast_calls(expression: str | None) -> list[tuple[str, list[Any]]]:
    """Extract function names and simple positional arguments."""

    if not expression:
        return []
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return []
    result: list[tuple[str, list[Any]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id.lower()
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr.lower()
        else:
            name = ""
        values: list[Any] = []
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                values.append(arg.value)
            elif isinstance(arg, ast.Name):
                values.append(f"${arg.id}")
            else:
                values.append(ast.unparse(arg))
        result.append((name, values))
    return result


def compare(snapshot: dict[str, Any], mcp_items: list[dict[str, Any]], raw_schema: dict[str, Any]) -> dict[str, Any]:
    """Build deterministic projection, field and semantic comparison results."""

    routes = snapshot["routes"]
    route_ids = {str(row["factor_ref"]) for row in routes}
    details = {int(row["factor_id"]): row for row in snapshot["details"]}
    definitions = {int(row["id"]): row for row in snapshot["definitions"]}
    latest_evidence: dict[int, dict[str, Any]] = {}
    for row in snapshot["evidence"]:
        fid = int(row["factor_id"])
        if fid not in latest_evidence:
            latest_evidence[fid] = row
    by_ref = {str(item.get("factor_ref")): item for item in mcp_items if isinstance(item, dict)}
    approved: set[str] = set()
    for key in ("mappings", "field_resolutions"):
        for row in (raw_schema.get("data") or {}).get(key, []) or []:
            if isinstance(row, dict):
                value = row.get("field_name") or row.get("canonical_field_name")
                if value:
                    approved.add(str(value))
    projection_mismatches: list[dict[str, Any]] = []
    field_mismatches: list[dict[str, Any]] = []
    independent_candidates: list[dict[str, Any]] = []
    no_evidence: list[dict[str, Any]] = []
    for route in routes:
        fid = int(route["factor_id"])
        ref = str(route["factor_ref"])
        detail = details.get(fid, {})
        definition = definitions.get(fid, {})
        params = decode(detail.get("params")) or {}
        metadata = decode(definition.get("metadata")) or {}
        mcp_item = by_ref.get(ref)
        mcp_data = (mcp_item or {}).get("data") or {}
        mcp_logic = mcp_data.get("calc_logic")
        if mcp_logic != detail.get("calc_logic"):
            projection_mismatches.append({"factor_ref": ref, "kind": "calc_logic", "db": detail.get("calc_logic"), "mcp": mcp_logic})
        source_detail_id = mcp_data.get("source_detail_id")
        if source_detail_id is not None and int(source_detail_id) != int(detail.get("id")):
            projection_mismatches.append({"factor_ref": ref, "kind": "source_detail_id", "db": detail.get("id"), "mcp": source_detail_id})
        expression = str(detail.get("calc_logic") or "")
        names = ast_names(expression)
        declared = {str(value) for value in params.get("fields", []) if value}
        metadata_fields = {str(value) for value in metadata.get("fields", []) if value}
        unresolved = sorted(names - declared - metadata_fields - approved - DERIVED_FIELDS)
        if unresolved:
            field_mismatches.append({"factor_ref": ref, "kind": "unapproved_formula_names", "names": unresolved, "declared": sorted(declared), "approved_hits": sorted(names & approved)})
        name = str(definition.get("sub_factor_name") or "").lower()
        if not any(family in name for family in KNOWN_FAMILIES):
            explicit = [(func, vals) for func, vals in ast_calls(expression) if func in {"rolling", "pct_change", "shift", "diff", "ewm"} and any(isinstance(value, (int, float)) for value in vals)]
            # These two constants have an explicit interpretation in their definitions:
            # breakout excludes the current bar; OI momentum uses 24h returns inside a 72h window.
            if explicit and not ("breakout" in name or "oi_momentum_corr" in name):
                independent_candidates.append({"factor_ref": ref, "kind": "fixed_temporal_constant", "name": name, "window": params.get("window"), "calls": explicit, "expression": expression})
        if fid not in latest_evidence:
            no_evidence.append({"factor_ref": ref, "route_id": route["route_id"], "metric_id": route["metric_id"], "detail_id": detail.get("id")})
    return {
        "route_count": len(routes),
        "mcp_item_count": len(mcp_items),
        "mcp_refs_missing": sorted(route_ids - set(by_ref)),
        "mcp_extra_refs": sorted(set(by_ref) - route_ids),
        "duplicate_mcp_refs": [ref for ref, count in Counter(str(item.get("factor_ref")) for item in mcp_items).items() if count > 1],
        "projection_mismatches": projection_mismatches,
        "field_mismatches": field_mismatches,
        "independent_semantic_candidates": independent_candidates,
        "active_routes_without_formula_evidence": no_evidence,
        "approved_raw_field_count": len(approved),
        "approved_raw_fields": sorted(approved),
    }


def main() -> None:
    """Run the active-route reconciliation and print the report location."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    settings = SettingsLoader.load("test", ROOT)
    db = DatabaseClient.from_settings(settings.database)
    snapshot = db_snapshot(db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-active-formula-semantic-recheck"
    client = Client(token, output)
    init = client.request("INIT", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest-active-formula-semantic", "version": "1.0"}})
    init_result = ((init.get("envelope") or {}).get("result") or {})
    client.protocol_version = init_result.get("protocolVersion")
    client.request("NOTIFY", "notifications/initialized", {})
    refs = [str(row["factor_ref"]) for row in snapshot["routes"]]
    batches = [refs[index:index + 50] for index in range(0, len(refs), 50)]
    detail_calls = []
    all_items: list[dict[str, Any]] = []
    for index, batch in enumerate(batches, 1):
        call = client.tool(f"DETAIL-BATCH-{index}", "factor_get_details_batch", {"factor_refs": batch, "detail_level": "executable"})
        detail_calls.append({"http_status": call.get("http_status"), "error_code": error_code(call), "is_error": call.get("is_error"), "data": (call.get("business") or {}).get("data") or {}})
        items = ((call.get("business") or {}).get("data") or {}).get("items") or []
        if isinstance(items, list):
            all_items.extend(item for item in items if isinstance(item, dict))
    raw_call = client.tool("RAW-SCHEMA", "schema_get_raw_data", {})
    raw_data = (raw_call.get("business") or {}).get("data") or {}
    comparisons = compare(snapshot, all_items, {"data": raw_data})
    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "environment": "test",
        "mcp_url": MCP_URL,
        "read_only": True,
        "initialization": {"http_status": init.get("http_status"), "protocol_version": client.protocol_version, "server_info": init_result.get("serverInfo")},
        "detail_batches": detail_calls,
        "raw_schema": {"http_status": raw_call.get("http_status"), "error_code": error_code(raw_call), "schema_version": raw_data.get("schema_version"), "schema_hash": raw_data.get("schema_hash"), "status": raw_data.get("status")},
        "comparisons": comparisons,
        "db_route_refs": refs,
        "status": "COMPLETE" if client.protocol_version and not comparisons["projection_mismatches"] and not comparisons["field_mismatches"] and not comparisons["independent_semantic_candidates"] else "REVIEW",
    }
    write_json(output / "report.json", report)
    print(json.dumps({"output": str(output), "status": report["status"], "routes": comparisons["route_count"], "mcp_items": comparisons["mcp_item_count"], "projection_mismatches": len(comparisons["projection_mismatches"]), "field_mismatches": len(comparisons["field_mismatches"]), "independent_candidates": len(comparisons["independent_semantic_candidates"]), "no_formula_evidence": len(comparisons["active_routes_without_formula_evidence"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
