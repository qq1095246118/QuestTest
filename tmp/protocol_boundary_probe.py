#!/usr/bin/env python3
"""Probe read-only MCP protocol and input-boundary behavior.

The probe keeps the business surface deliberately small.  It discovers the
tool schemas first, uses only catalog/schema/universe reads, and records raw
protocol observations separately from product failures.  No write-capable
tool or database mutation is attempted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402
from tmp import catalog_deep_readonly as transport  # noqa: E402


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ERROR_KEY_RE = re.compile(r"(authorization|token|password|secret|signature)", re.I)


def _safe(value: Any) -> Any:
    """Redact credentials recursively before writing evidence."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if ERROR_KEY_RE.search(str(key)) else _safe(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def _write(path: Path, value: Any) -> None:
    """Write a JSON evidence artifact with credential-like keys removed."""

    path.write_text(
        json.dumps(_safe(value), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _parse_body(raw: bytes, content_type: str) -> Any:
    """Parse a JSON or single-event SSE response."""

    text = raw.decode("utf-8", "replace")
    if "text/event-stream" not in content_type.lower():
        return json.loads(text) if text else None
    events: list[Any] = []
    for block in re.split(r"\r?\n\r?\n", text):
        lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if lines:
            events.append(json.loads("\n".join(lines)))
    return events[0] if len(events) == 1 else {"sse_event_count": len(events)}


def _raw(
    payload: dict[str, Any] | bytes,
    token: str | None,
    *,
    accept: str = "application/json, text/event-stream",
    session_id: str | None = None,
    protocol_version: str | None = None,
    request_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send one low-level request and return sanitized transport diagnostics."""

    raw_payload = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": accept,
        "User-Agent": CHROME_UA,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if session_id:
        headers["MCP-Session-Id"] = session_id
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    if request_headers:
        headers.update(request_headers)
    request = urllib.request.Request(MCP_URL, data=raw_payload, headers=headers, method="POST")
    started = time.monotonic()
    response_headers: dict[str, str] = {}
    status: int | None = None
    raw_response = b""
    transport_error: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            raw_response = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_headers = {key.lower(): value for key, value in exc.headers.items()}
        raw_response = exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        transport_error = f"{type(exc).__name__}: {exc}"
    content_type = response_headers.get("content-type", "")
    try:
        envelope = _parse_body(raw_response, content_type)
        parse_error = None
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        envelope = None
        parse_error = f"{type(exc).__name__}: {exc}"
    return {
        "http_status": status,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "content_type": content_type,
        "response_headers": {
            key: value
            for key, value in response_headers.items()
            if key in {"content-type", "mcp-session-id", "mcp-protocol-version", "x-request-id", "x-trace-id"}
        },
        "envelope": envelope,
        "parse_error": parse_error,
        "transport_error": transport_error,
    }


def _result(call: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON-RPC result object, if present."""

    envelope = call.get("envelope")
    return envelope.get("result", {}) if isinstance(envelope, dict) and isinstance(envelope.get("result"), dict) else {}


def _business(call: dict[str, Any]) -> dict[str, Any]:
    """Extract structured or text business content from one response."""

    result = _result(call)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _error_code(call: dict[str, Any]) -> str | None:
    """Extract a structured business or JSON-RPC error code."""

    business = _business(call)
    error = business.get("error")
    if isinstance(error, dict) and error.get("code") is not None:
        return str(error["code"])
    envelope = call.get("envelope")
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        value = envelope["error"].get("code")
        return str(value) if value is not None else None
    return None


def _data(call: dict[str, Any]) -> dict[str, Any]:
    """Extract the business data object."""

    value = _business(call).get("data")
    return value if isinstance(value, dict) else {}


def _hash_business(call: dict[str, Any]) -> str:
    """Hash a normalized business object for repeatability checks."""

    encoded = json.dumps(_business(call), ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parity(call: dict[str, Any]) -> bool | None:
    """Compare structuredContent with the first JSON content text block."""

    result = _result(call)
    structured = result.get("structuredContent")
    content = result.get("content")
    if not isinstance(structured, dict) or not isinstance(content, list) or not content:
        return None
    text = content[0].get("text") if isinstance(content[0], dict) else None
    if not isinstance(text, str):
        return None
    try:
        return structured == json.loads(text)
    except json.JSONDecodeError:
        return False


def _record(cases: list[dict[str, Any]], case_id: str, title: str, status: str, reason: str, evidence: Any) -> None:
    """Append one compact case verdict."""

    cases.append({"case_id": case_id, "title": title, "status": status, "reason": reason, "evidence": evidence})


def _db_state(db: DatabaseClient) -> dict[str, Any]:
    """Read counts and update markers for relevant business tables."""

    tables = (
        ("market_environment_daily", "updated_at"),
        ("market_environment_eval_batch", "updated_at"),
        ("market_environment_factor_metric", "updated_at"),
        ("market_environment_factor_route", "activated_at"),
        ("factor_ic_runs", "created_at"),
    )
    state: dict[str, Any] = {}
    with db.transaction() as tx:
        for table, marker in tables:
            state[table] = tx.fetch_one(f"SELECT COUNT(*) AS row_count, MAX(id) AS max_id, MAX({marker}) AS max_marker FROM `{table}`") or {}
    return state


def _tool_call(
    runner: transport.Runner,
    output: Path,
    case_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Invoke a normal tool through the shared evidence-capturing Runner."""

    call = runner.tool(case_id, name, arguments)
    _write(output / f"normalized-{case_id}.json", {
        "case_id": case_id,
        "tool": name,
        "arguments": arguments,
        "http_status": call.get("http_status"),
        "error_code": _error_code(call),
        "business": _business(call),
        "representations_equal": _parity(call),
    })
    return call


def main() -> int:
    """Execute protocol and boundary checks and write a summary."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} or MCP_TOKEN is required")
    settings = SettingsLoader.load("test", ROOT)
    if settings.environment != "test" or not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("test environment gate failed")
    db = DatabaseClient.from_settings(settings.database)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-protocol-boundaries"
    output.mkdir(parents=True, exist_ok=False)
    before = _db_state(db)
    runner = transport.Runner(token, output, db)
    cases: list[dict[str, Any]] = []

    init = runner.request(
        "INIT",
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest-boundaries", "version": "1.0"}},
    )
    init_result = _result(init)
    runner.protocol_version = init_result.get("protocolVersion")
    runner.session_id = (init.get("response_headers") or {}).get("mcp-session-id")
    _record(
        cases,
        "MCP-INIT",
        "initialize negotiates the supported protocol",
        "PASS" if init.get("http_status") == 200 and runner.protocol_version == "2025-06-18" else "FAIL",
        "protocol version negotiated" if init.get("http_status") == 200 and runner.protocol_version == "2025-06-18" else "initialization did not negotiate 2025-06-18",
        {"http_status": init.get("http_status"), "protocol_version": runner.protocol_version, "session_present": bool(runner.session_id)},
    )
    notify = runner.notify_initialized("NOTIFY")
    _record(cases, "MCP-NOTIFY", "initialized notification is accepted", "PASS" if notify.get("http_status") in {200, 202, 204} else "FAIL", "notification accepted" if notify.get("http_status") in {200, 202, 204} else "notification rejected", {"http_status": notify.get("http_status")})
    tools_call = runner.request("TOOLS", "tools/list", {})
    listed = _result(tools_call).get("tools") or []
    tools = {row.get("name"): row for row in listed if isinstance(row, dict) and row.get("name")}
    required = {"factor_search", "factor_catalog_stats", "schema_get_factor_fields", "universe_list_symbols"}
    _record(cases, "MCP-TOOLS", "required read-only tools are listed", "PASS" if required <= tools.keys() else "FAIL", "required tools present" if required <= tools.keys() else "required tool missing", {"tool_count": len(tools), "missing": sorted(required - tools.keys())})

    # Content negotiation is tested in independent sessions to avoid relying
    # on a session surviving a client-level Accept change.
    accept_results: dict[str, Any] = {}
    for label, accept in (("JSON", "application/json"), ("SSE", "text/event-stream")):
        init_payload = {"jsonrpc": "2.0", "id": f"accept-init-{label.lower()}", "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Chrome", "version": "1"}}}
        init_raw = _raw(init_payload, token, accept=accept)
        ir = _result(init_raw)
        session = (init_raw.get("response_headers") or {}).get("mcp-session-id")
        version = ir.get("protocolVersion")
        tool_payload = {"jsonrpc": "2.0", "id": f"accept-tool-{label.lower()}", "method": "tools/call", "params": {"name": "schema_get_factor_fields", "arguments": {}}}
        tool_raw = _raw(tool_payload, token, accept=accept, session_id=session, protocol_version=version)
        business = _business(tool_raw)
        accept_results[label] = {"init": {"http_status": init_raw.get("http_status"), "content_type": init_raw.get("content_type")}, "tool": {"http_status": tool_raw.get("http_status"), "content_type": tool_raw.get("content_type"), "parse_error": tool_raw.get("parse_error"), "has_data": bool(_data(tool_raw)), "error_code": _error_code(tool_raw)}}
    accept_ok = all(row["tool"]["http_status"] == 200 and row["tool"]["has_data"] and row["tool"]["parse_error"] is None for row in accept_results.values())
    _record(cases, "MCP-ACCEPT", "JSON and SSE content negotiation both return readable results", "PASS" if accept_ok else "FAIL", "both response formats parsed as business data" if accept_ok else "one content type failed to produce parseable data", accept_results)

    # Boundary values are judged against the discovered schema, not a copied
    # cross-tool limit.  A rejection is required for values outside min/max;
    # no silent normalization is accepted.
    factor_schema = (tools.get("factor_search") or {}).get("inputSchema") or {}
    limit_schema = factor_schema.get("properties", {}).get("limit") or {}
    min_limit = limit_schema.get("minimum", 1)
    max_limit = limit_schema.get("maximum", 500)
    boundary_values = [0, -1, int(max_limit) + 1, "1"]
    boundary_results: list[dict[str, Any]] = []
    for index, value in enumerate(boundary_values, start=1):
        call = _tool_call(runner, output, f"LIMIT-{index}", "factor_search", {"limit": value})
        rows = _data(call).get("items")
        is_error = bool(_result(call).get("isError")) or _error_code(call) is not None or isinstance((call.get("envelope") or {}).get("error"), dict)
        boundary_results.append({"value": value, "http_status": call.get("http_status"), "error_code": _error_code(call), "is_error": is_error, "returned_count": len(rows) if isinstance(rows, list) else None})
    boundary_ok = all(row["is_error"] for row in boundary_results)
    _record(cases, "MCP-LIMIT-BOUNDARY", "factor_search rejects values outside its declared limit type/range", "PASS" if boundary_ok else "FAIL", "all invalid limit values were rejected" if boundary_ok else "an invalid limit was silently normalized or accepted", {"schema": {"minimum": min_limit, "maximum": max_limit}, "results": boundary_results})

    # Protocol-level malformed/unknown method and duplicate-id behavior.
    malformed = _raw(b'{"jsonrpc":"2.0",', token)
    malformed_ok = malformed.get("http_status", 0) >= 400 or _error_code(malformed) is not None or isinstance((malformed.get("envelope") or {}).get("error"), dict)
    _record(cases, "MCP-MALFORMED", "malformed JSON is rejected before dispatch", "PASS" if malformed_ok else "FAIL", "protocol error returned" if malformed_ok else "malformed body reached a business success", {key: malformed.get(key) for key in ("http_status", "parse_error", "transport_error", "content_type")})

    unknown_method = _raw({"jsonrpc": "2.0", "id": "unknown-method", "method": "method_that_does_not_exist", "params": {}}, token, session_id=runner.session_id, protocol_version=runner.protocol_version)
    unknown_ok = _error_code(unknown_method) is not None or isinstance((unknown_method.get("envelope") or {}).get("error"), dict) or bool(_result(unknown_method).get("isError"))
    _record(cases, "MCP-UNKNOWN-METHOD", "unknown JSON-RPC method returns an explicit error", "PASS" if unknown_ok else "FAIL", "unknown method rejected" if unknown_ok else "unknown method returned success", {"http_status": unknown_method.get("http_status"), "error_code": _error_code(unknown_method)})

    duplicate_payload = {"jsonrpc": "2.0", "id": "duplicate-id", "method": "tools/call", "params": {"name": "schema_get_factor_fields", "arguments": {}}}
    duplicate_first = _raw(duplicate_payload, token, session_id=runner.session_id, protocol_version=runner.protocol_version)
    duplicate_second = _raw(duplicate_payload, token, session_id=runner.session_id, protocol_version=runner.protocol_version)
    ids = [((item.get("envelope") or {}).get("id")) for item in (duplicate_first, duplicate_second)]
    duplicate_ok = all(item.get("http_status") == 200 and _data(item) for item in (duplicate_first, duplicate_second)) and ids == ["duplicate-id", "duplicate-id"]
    _record(cases, "MCP-DUPLICATE-ID", "sequential duplicate JSON-RPC ids remain independently valid", "PASS" if duplicate_ok else "FAIL", "both duplicate-id reads returned their own valid envelope" if duplicate_ok else "duplicate id corrupted or dropped a response", {"statuses": [duplicate_first.get("http_status"), duplicate_second.get("http_status")], "ids": ids, "hashes": [_hash_business(duplicate_first), _hash_business(duplicate_second)]})

    # Unknown protocol negotiation is recorded as a contract observation: the
    # server may reject it or choose a supported version according to MCP.
    unsupported = _raw({"jsonrpc": "2.0", "id": "unsupported-version", "method": "initialize", "params": {"protocolVersion": "1900-01-01", "capabilities": {}, "clientInfo": {"name": "Chrome", "version": "1"}}}, token)
    unsupported_version = _result(unsupported).get("protocolVersion")
    unsupported_error = _error_code(unsupported) or ((unsupported.get("envelope") or {}).get("error") or {}).get("code") if isinstance((unsupported.get("envelope") or {}).get("error"), dict) else _error_code(unsupported)
    unsupported_status = "PASS" if unsupported_error is not None or unsupported_version in {None, "2025-06-18"} else "FAIL"
    _record(cases, "MCP-VERSION-UNKNOWN", "unsupported protocol version is rejected or explicitly negotiated", unsupported_status, "server rejected or selected its supported version" if unsupported_status == "PASS" else "server claimed an unadvertised protocol version", {"http_status": unsupported.get("http_status"), "negotiated_version": unsupported_version, "error_code": unsupported_error})

    # Three independent sessions perform the same schema read concurrently.
    def concurrent_read(index: int) -> dict[str, Any]:
        """Initialize one session and read the approved schema."""

        init_call = _raw({"jsonrpc": "2.0", "id": f"concurrent-init-{index}", "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Chrome", "version": "1"}}}, token)
        version = _result(init_call).get("protocolVersion")
        session = (init_call.get("response_headers") or {}).get("mcp-session-id")
        read_call = _raw({"jsonrpc": "2.0", "id": f"concurrent-read-{index}", "method": "tools/call", "params": {"name": "schema_get_factor_fields", "arguments": {}}}, token, session_id=session, protocol_version=version)
        return {"http_status": read_call.get("http_status"), "error_code": _error_code(read_call), "hash": _hash_business(read_call), "has_data": bool(_data(read_call)), "elapsed_seconds": read_call.get("elapsed_seconds")}

    with ThreadPoolExecutor(max_workers=3) as pool:
        concurrent = list(pool.map(concurrent_read, range(3)))
    concurrent_ok = all(row["http_status"] == 200 and row["has_data"] for row in concurrent) and len({row["hash"] for row in concurrent}) == 1
    _record(cases, "MCP-CONCURRENT-READ", "independent concurrent read sessions return identical business data", "PASS" if concurrent_ok else "FAIL", "all concurrent reads succeeded with one payload hash" if concurrent_ok else "concurrent reads diverged or failed", {"results": concurrent})

    after = _db_state(db)
    read_only_ok = before == after
    _record(cases, "MCP-READ-ONLY", "protocol and boundary reads do not mutate business tables", "PASS" if read_only_ok else "FAIL", "database state unchanged" if read_only_ok else "business-table state changed", {"before": before, "after": after})

    counts: dict[str, int] = {}
    for case in cases:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    summary = {
        "run_id": stamp,
        "environment": "test",
        "mcp_host": "test-factor-frontend.questvector.ai",
        "mode": "READ_ONLY",
        "user_agent_class": "standard Chrome",
        "case_counts": counts,
        "cases": cases,
        "request_count": len(runner.calls),
        "failed_cases": [case["case_id"] for case in cases if case["status"] == "FAIL"],
        "writes_attempted": False,
    }
    _write(output / "summary.json", summary)
    lines = ["# MCP protocol and boundary probe", "", f"- Run: `{stamp}`", f"- Counts: `{counts}`", "- Mode: `READ_ONLY`", "", "| Case | Status | Reason |", "| --- | --- | --- |"]
    lines.extend(f"| `{case['case_id']}` | `{case['status']}` | {case['reason']} |" for case in cases)
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "case_counts": counts, "failed_cases": summary["failed_cases"]}, ensure_ascii=False))
    return 0 if not summary["failed_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
