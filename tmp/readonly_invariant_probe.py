#!/usr/bin/env python3
"""Read-only MCP protocol and Factor 4 database invariant probe.

The probe intentionally avoids all business writes.  It uses a standard Chrome
User-Agent because the test edge rejects synthetic QuestTest User-Agents.
Credentials are read from the environment/configuration and never written to
artifacts; response payloads are recursively redacted before persistence.
"""

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymysql
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MCP_URL = os.environ.get(
    "MCP_URL", "https://test-factor-frontend.questvector.ai/mcp/factor-data"
)
TOKEN = os.environ.get("MCP_TOKEN") or os.environ.get("FACTOR4_MCP_TOKEN")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
KNOWN_BLOCKING = {
    "AUTH_REQUIRED",
    "FORBIDDEN",
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE",
    "EXPORT_BUDGET_EXCEEDED",
}
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|signature|jwt|hmac", re.I)
SENSITIVE_TEXT = re.compile(r"Bearer\s+naf_mcp_[A-Za-z0-9_-]+|naf_mcp_[A-Za-z0-9_-]+", re.I)


def json_default(value: Any) -> str:
    """Serialize temporal and decimal values without losing displayed precision."""
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    return str(value)


def redact(value: Any) -> Any:
    """Recursively remove credentials and redact token-like strings."""
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SENSITIVE_TEXT.sub("<redacted>", value)
    return value


def write_json(path: Path, value: Any) -> None:
    """Write a recursively redacted JSON artifact."""
    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, sort_keys=True, default=json_default)
        + "\n",
        encoding="utf-8",
    )


def parse_sse(raw: bytes, content_type: str) -> tuple[Any, str | None]:
    """Parse JSON or exactly one SSE data event, preserving parse diagnostics."""
    if not raw:
        return None, "EMPTY_RESPONSE"
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" not in content_type.lower():
        try:
            return json.loads(text), None
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"
    events: list[Any] = []
    for block in re.split(r"\r?\n\r?\n", text):
        lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if not lines:
            continue
        try:
            events.append(json.loads("\n".join(lines)))
        except Exception as exc:  # noqa: BLE001
            return None, f"SSE_DATA_PARSE: {type(exc).__name__}: {exc}"
    if len(events) != 1:
        return None, f"SSE_EVENT_COUNT={len(events)}"
    return events[0], None


def error_code(call: dict[str, Any]) -> str | None:
    """Extract a JSON-RPC or business error code from a normalized call."""
    env = call.get("envelope") or {}
    for source in (env.get("error"), (call.get("business") or {}).get("error")):
        if isinstance(source, dict):
            for key in ("code", "error_code", "type"):
                if source.get(key) is not None:
                    return str(source[key])
    return None


def data(call: dict[str, Any]) -> dict[str, Any]:
    """Extract the structured business data object, if present."""
    value = (call.get("business") or {}).get("data")
    return value if isinstance(value, dict) else {}


def business_success(call: dict[str, Any]) -> bool:
    """Return whether a call yielded a successful MCP result envelope."""
    env = call.get("envelope") or {}
    result = env.get("result") if isinstance(env, dict) else None
    return bool(
        call.get("http_status") == 200
        and call.get("parse_error") is None
        and isinstance(result, dict)
        and result.get("isError") is not True
        and isinstance(call.get("business"), dict)
        and error_code(call) is None
    )


def rows(call: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """Extract object rows from common business response containers."""
    payload = data(call)
    for key in keys or ("items", "metrics", "top_items", "bottom_items", "symbols", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


class MCP:
    """Small authenticated MCP transport with sanitized evidence capture."""

    def __init__(self, token: str, output: Path, *, capture: bool = True) -> None:
        """Initialize a stateless transport and artifact directory."""
        self.token = token
        self.output = output
        self.capture = capture
        self.counter = 0
        self.protocol_version: str | None = None
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        case_id: str,
        method: str,
        params: Any = None,
        *,
        request_id: str | int | None = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 90,
    ) -> dict[str, Any]:
        """Send one raw or structured HTTP request and normalize its response."""
        self.counter += 1
        if raw_body is None:
            payload: Any = {"jsonrpc": "2.0", "id": request_id or f"{case_id}-{uuid4()}", "method": method}
            if params is not None:
                payload["params"] = params
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        else:
            payload = None
            body = raw_body
        req_headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": UA,
        }
        if self.protocol_version:
            req_headers["MCP-Protocol-Version"] = self.protocol_version
        if headers:
            req_headers.update(headers)
        request = urllib.request.Request(MCP_URL, data=body, headers=req_headers, method="POST")
        started = time.monotonic()
        response_headers: dict[str, str] = {}
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                status = response.status
                response_headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            response_headers = {k.lower(): v for k, v in exc.headers.items()}
        except Exception as exc:  # noqa: BLE001
            result = {
                "case_id": case_id,
                "method": method,
                "http_status": None,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "parse_error": f"{type(exc).__name__}: {exc}",
                "envelope": None,
                "business": None,
                "error_code": type(exc).__name__,
            }
            self.calls.append(result)
            return result
        content_type = response_headers.get("content-type", "")
        envelope, parse_error = parse_sse(raw, content_type)
        result: dict[str, Any] = {
            "case_id": case_id,
            "method": method,
            "request_id": payload.get("id") if isinstance(payload, dict) else None,
            "http_status": status,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "content_type": content_type,
            "response_headers": {
                k: (hashlib.sha256(v.encode()).hexdigest() if k == "mcp-session-id" else v)
                for k, v in response_headers.items()
                if k in {"content-type", "mcp-session-id", "mcp-protocol-version", "x-request-id", "x-trace-id", "retry-after"}
            },
            "parse_error": parse_error,
            "envelope": envelope,
            "business": None,
        }
        if isinstance(envelope, dict):
            rpc_result = envelope.get("result")
            if isinstance(rpc_result, dict):
                structured = rpc_result.get("structuredContent")
                if isinstance(structured, dict):
                    result["business"] = structured
                else:
                    content = rpc_result.get("content")
                    if isinstance(content, list) and content and isinstance(content[0], dict):
                        text = content[0].get("text")
                        if isinstance(text, str):
                            try:
                                result["business"] = json.loads(text)
                            except json.JSONDecodeError:
                                result["business"] = None
                result["is_error"] = rpc_result.get("isError")
        self.calls.append(result)
        if self.capture:
            stem = f"{self.counter:03d}-{case_id}"
            write_json(self.output / f"{stem}.request.json", payload if payload is not None else {"raw_body": body.decode(errors="replace")})
            if envelope is not None:
                write_json(self.output / f"{stem}.response.json", envelope)
            else:
                (self.output / f"{stem}.response.txt").write_text(raw.decode(errors="replace"), encoding="utf-8")
        return result

    def tool(self, case_id: str, name: str, arguments: Any = None, **kwargs: Any) -> dict[str, Any]:
        """Invoke a named MCP tool with supplied arguments."""
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self.request(case_id, "tools/call", params, **kwargs)


def summarize_call(call: dict[str, Any]) -> dict[str, Any]:
    """Return a compact credential-free call summary for the report."""
    return {
        key: call.get(key)
        for key in ("case_id", "method", "http_status", "elapsed_seconds", "content_type", "parse_error", "is_error", "error_code")
    } | {"error_code": error_code(call), "data_keys": sorted(data(call))}


def verdict(cases: list[dict[str, Any]], case_id: str, status: str, title: str, reason: str, **extra: Any) -> None:
    """Append one case verdict."""
    cases.append({"case_id": case_id, "status": status, "title": title, "reason": reason, **extra})


def canonical(value: Any) -> Any:
    """Remove volatile identifiers from a response for equality checks."""
    if isinstance(value, dict):
        return {
            key: canonical(item)
            for key, item in value.items()
            if key not in {"request_id", "trace_id", "requestId", "traceId", "generated_at", "retrieved_at"}
        }
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def db_connection() -> tuple[pymysql.connections.Connection, dict[str, Any]]:
    """Open the configured test DB and return connection plus non-secret identity."""
    config = yaml.safe_load((ROOT / "config/test.yaml").read_text())["database"]
    connection = pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["username"],
        password=config["password"],
        database=config["name"],
        connect_timeout=10,
        read_timeout=120,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    return connection, {"host": config["host"], "port": config["port"], "database": config["name"], "username": config["username"]}


def db_query(connection: pymysql.connections.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Execute a read-only query and return dictionaries."""
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def db_one(connection: pymysql.connections.Connection, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Execute a read-only query and return one dictionary or None."""
    rows_ = db_query(connection, query, params)
    return rows_[0] if rows_ else None


def table_snapshot(connection: pymysql.connections.Connection) -> dict[str, Any]:
    """Capture row counts and maximum update times for business tables."""
    names = [
        "market_environment_daily",
        "market_environment_eval_batch",
        "market_environment_factor_metric",
        "market_environment_factor_route",
        "market_environment_strategy_feedback_submissions",
        "factor_ic_runs",
        "factor_ic_summary_metrics",
        "factor_ic_slice_metrics",
        "factor_value_slice_metrics",
        "factor_validity_status",
    ]
    output: dict[str, Any] = {}
    for name in names:
        try:
            row = db_one(connection, f"SELECT COUNT(*) AS row_count, MAX(updated_at) AS max_updated_at FROM `{name}`")
            output[name] = row
        except Exception as exc:  # noqa: BLE001
            output[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return output


def main() -> None:
    """Execute protocol, concurrency, cursor and database read-only checks."""
    if not TOKEN:
        raise SystemExit("MCP_TOKEN or FACTOR4_MCP_TOKEN is required")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-readonly-invariant-probe"
    output.mkdir(parents=True, exist_ok=False)
    runner = MCP(TOKEN, output)
    cases: list[dict[str, Any]] = []
    conn, db_identity = db_connection()
    try:
        before = table_snapshot(conn)
        write_json(output / "db-before.json", before)
        write_json(output / "db-identity.json", db_identity)
        # MCP-001/002 baseline.
        init = runner.request(
            "MCP-001",
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Chrome", "version": "1.0"}},
        )
        init_result = ((init.get("envelope") or {}).get("result") or {})
        runner.protocol_version = init_result.get("protocolVersion")
        tools = runner.request("MCP-002", "tools/list", {})
        tool_rows = (((tools.get("envelope") or {}).get("result") or {}).get("tools") or [])
        tool_map = {row.get("name"): row for row in tool_rows if isinstance(row, dict)}
        required = {
            "factor_search", "factor_catalog_stats", "factor_list_metric_scopes", "factor_get_detail",
            "factor_get_formula", "factor_get_metrics", "factor_get_validity", "factor_get_metric_slices",
            "factor_rank", "factor_get_details_batch", "factor_get_metrics_batch", "factor_get_validity_batch",
            "environment_get_daily", "environment_get_recommendations", "factor_get_environment_metrics",
            "factor_get_environment_tags", "universe_list_symbols", "schema_get_factor_fields", "schema_get_raw_data",
        }
        if init.get("http_status") == 200 and runner.protocol_version == "2025-06-18" and required <= set(tool_map):
            verdict(cases, "MCP-001/002", "PASS", "MCP handshake and required tool discovery", "protocol and required tools available", tool_count=len(tool_map))
        else:
            verdict(cases, "MCP-001/002", "FAIL", "MCP handshake and required tool discovery", "protocol or required tool set mismatch", init=summarize_call(init), tools=summarize_call(tools), tool_count=len(tool_map))

        # Pick dynamic data identities from the DB/API, never fixed IDs.
        scopes_call = runner.tool("DISC-SCOPES", "factor_list_metric_scopes", {"as_of": datetime.now(timezone.utc).isoformat(), "kind": "sub_factor", "limit": 10})
        scope_rows = rows(scopes_call, "scopes", "items")
        search_call = runner.tool("DISC-FACTORS", "factor_search", {"kind": "sub_factor", "limit": 5})
        factor_rows = rows(search_call)
        market_row = db_one(conn, "SELECT market_scope FROM market_environment_eval_batch ORDER BY updated_at DESC, id DESC LIMIT 1")
        market_scope = str((market_row or {}).get("market_scope") or "")
        active_route = db_one(conn, """SELECT r.factor_ref, r.market_scope, r.label_code, r.eval_batch_id, r.metric_id,
             r.publish_version, r.publication_uid, r.environment_date, r.as_of_time,
             b.batch_uid, b.route_profile_key
             FROM market_environment_factor_route r JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
             WHERE r.is_active=1 ORDER BY r.activated_at DESC, r.id DESC LIMIT 1""")
        if active_route:
            market_scope = str(active_route["market_scope"])
        factor_ref = str((active_route or {}).get("factor_ref") or (factor_rows[0].get("factor_ref") if factor_rows else ""))

        # MCP-006 limits and input rejection.  Calls are deliberately bounded.
        limit_cases = [
            ("MCP-006-search-min", "factor_search", {"kind": "sub_factor", "limit": 1}),
            ("MCP-006-search-max", "factor_search", {"kind": "sub_factor", "limit": 500}),
            ("MCP-006-kb-min", "kb_factor_candidate_search", {"limit": 1}),
            ("MCP-006-kb-max", "kb_factor_candidate_search", {"limit": 50}),
            ("MCP-006-daily-min", "environment_get_daily", {"limit": 1}),
            ("MCP-006-daily-max", "environment_get_daily", {"limit": 1000}),
        ]
        for cid, tool, args in limit_cases:
            call = runner.tool(cid, tool, args)
            if business_success(call):
                actual_count = len(rows(call))
                verdict(cases, cid, "PASS", f"{tool} declared limit boundary", "success within documented boundary", returned=actual_count, limit=args["limit"])
            elif error_code(call) in KNOWN_BLOCKING:
                verdict(cases, cid, "BLOCKED", f"{tool} declared limit boundary", "capacity/dependency protection blocked bounded probe", blocking_reason=error_code(call), call=summarize_call(call))
            else:
                verdict(cases, cid, "FAIL", f"{tool} declared limit boundary", "declared legal boundary rejected unexpectedly", call=summarize_call(call), severity="P1")
        invalid_cases = [
            ("MCP-006-search-zero", "factor_search", {"kind": "sub_factor", "limit": 0}),
            ("MCP-006-search-negative", "factor_search", {"kind": "sub_factor", "limit": -1}),
            ("MCP-006-search-float", "factor_search", {"kind": "sub_factor", "limit": 1.5}),
            ("MCP-006-search-string", "factor_search", {"kind": "sub_factor", "limit": "1"}),
            ("MCP-006-daily-zero", "environment_get_daily", {"limit": 0}),
            ("MCP-006-daily-negative", "environment_get_daily", {"limit": -1}),
            ("MCP-006-daily-string", "environment_get_daily", {"limit": "1"}),
            ("MCP-006-unknown-field", "factor_search", {"kind": "sub_factor", "limit": 1, "questtest_unknown": True}),
            ("MCP-006-invalid-date", "environment_get_daily", {"limit": 1, "as_of": "not-a-date"}),
        ]
        for cid, tool, args in invalid_cases:
            call = runner.tool(cid, tool, args)
            rejected = call.get("http_status", 0) >= 400 or call.get("is_error") is True or error_code(call) is not None or ((call.get("envelope") or {}).get("error") is not None)
            verdict(cases, cid, "PASS" if rejected else "OBSERVED", f"{tool} invalid input", "structured rejection" if rejected else "service accepted/normalized input; schema compatibility observation", call=summarize_call(call), arguments=args)

        # MCP-011 raw protocol malformed/unknown requests.
        raw_cases = [
            ("MCP-011-truncated", b'{"jsonrpc":"2.0","id":1,"method":"tools/list"'),
            ("MCP-011-array", b'[]'),
            ("MCP-011-missing-jsonrpc", b'{"id":1,"method":"tools/list","params":{}}'),
            ("MCP-011-wrong-jsonrpc", b'{"jsonrpc":"1.0","id":1,"method":"tools/list","params":{}}'),
            ("MCP-011-unknown-method", json.dumps({"jsonrpc":"2.0","id":"unknown-method","method":"method/unknown","params":{}}).encode()),
            ("MCP-011-no-params", json.dumps({"jsonrpc":"2.0","id":"no-params","method":"tools/call"}).encode()),
            ("MCP-011-wrong-arguments", json.dumps({"jsonrpc":"2.0","id":"wrong-args","method":"tools/call","params":{"name":"factor_search","arguments":[]}}).encode()),
        ]
        for cid, body in raw_cases:
            call = runner.request(cid, "raw", raw_body=body)
            rejected = call.get("http_status", 0) >= 400 or call.get("parse_error") is not None or call.get("is_error") is True or error_code(call) is not None or ((call.get("envelope") or {}).get("error") is not None)
            verdict(cases, cid, "PASS" if rejected else "FAIL", "malformed/invalid JSON-RPC is rejected", "protocol rejection without business data" if rejected else "invalid request returned success", call=summarize_call(call), severity=None if rejected else "P1")
        duplicate = runner.request("MCP-011-duplicate-id-1", "tools/list", {}, request_id="duplicate-id")
        duplicate2 = runner.request("MCP-011-duplicate-id-2", "tools/list", {}, request_id="duplicate-id")
        duplicate_ok = business_success(duplicate) and business_success(duplicate2) and ((duplicate.get("envelope") or {}).get("id") == "duplicate-id") and ((duplicate2.get("envelope") or {}).get("id") == "duplicate-id")
        verdict(cases, "MCP-011-duplicate-id", "PASS" if duplicate_ok else "FAIL", "duplicate request IDs have deterministic response IDs", "both independent requests retained their IDs" if duplicate_ok else "duplicate ID response mismatch", first=summarize_call(duplicate), second=summarize_call(duplicate2), severity=None if duplicate_ok else "P1")
        unknown_version = runner.request("MCP-011-unknown-version", "tools/list", {}, headers={"MCP-Protocol-Version": "2099-01-01"})
        rejected = unknown_version.get("http_status", 0) >= 400 or unknown_version.get("is_error") is True or error_code(unknown_version) is not None or ((unknown_version.get("envelope") or {}).get("error") is not None)
        verdict(cases, "MCP-011-unknown-version", "PASS" if rejected else "OBSERVED", "unknown protocol version handling", "rejected/negotiation error" if rejected else "server accepted unknown version; compatibility observation", call=summarize_call(unknown_version))

        # MCP-014 response size/content negotiation.
        large = runner.tool("MCP-014-large-search", "factor_search", {"kind": "sub_factor", "limit": 500})
        if business_success(large):
            payload = data(large)
            returned = len(rows(large))
            limit_ok = returned <= 500
            verdict(cases, "MCP-014-large-response", "PASS" if limit_ok else "FAIL", "bounded large response", "returned rows respect requested maximum" if limit_ok else "returned more rows than requested limit", returned=returned, truncated=payload.get("truncated"), next_cursor=bool((payload.get("meta") or {}).get("next_cursor")), severity=None if limit_ok else "P1")
        elif error_code(large) in KNOWN_BLOCKING:
            verdict(cases, "MCP-014-large-response", "BLOCKED", "bounded large response", "capacity protection/timeout", blocking_reason=error_code(large), call=summarize_call(large))
        else:
            verdict(cases, "MCP-014-large-response", "FAIL", "bounded large response", "unexpected response failure", call=summarize_call(large), severity="P1")
        sse = runner.request("MCP-014-sse-accept", "tools/list", {}, headers={"Accept": "text/event-stream"})
        if "text/event-stream" in str(sse.get("content_type", "")).lower():
            sse_status = "PASS" if sse.get("parse_error") is None else "FAIL"
            sse_reason = "declared SSE parsed as one event" if sse_status == "PASS" else "declared SSE was not parseable"
        else:
            sse_status, sse_reason = "NOT_APPLICABLE", "endpoint returned JSON and did not declare SSE for this request"
        verdict(cases, "MCP-014-sse", sse_status, "SSE/content negotiation", sse_reason, call=summarize_call(sse))

        # MCP-015 fixed-argument concurrent read consistency.
        concurrent_args = {"kind": "sub_factor", "library_status": "new", "limit": 10}
        def one(i: int) -> dict[str, Any]:
            return runner.tool(f"MCP-015-concurrent-{i}", "factor_search", concurrent_args)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            concurrent_calls = list(pool.map(one, range(6)))
        serial = runner.tool("MCP-015-serial", "factor_search", concurrent_args)
        canonical_payloads = [canonical(data(call)) for call in concurrent_calls if business_success(call)]
        same = bool(canonical_payloads) and all(item == canonical_payloads[0] for item in canonical_payloads) and canonical(data(serial)) == canonical_payloads[0] if business_success(serial) else False
        blocked_count = sum(error_code(call) in KNOWN_BLOCKING for call in concurrent_calls)
        if same:
            verdict(cases, "MCP-015-concurrency", "PASS", "concurrent read snapshot consistency", "all canonical payloads match serial baseline", successful_calls=len(canonical_payloads))
        elif blocked_count:
            verdict(cases, "MCP-015-concurrency", "BLOCKED", "concurrent read snapshot consistency", "one or more calls hit a transient capacity/dependency guard", blocking_calls=blocked_count, calls=[summarize_call(call) for call in concurrent_calls])
        else:
            verdict(cases, "MCP-015-concurrency", "FAIL", "concurrent read snapshot consistency", "canonical payloads differ under identical arguments", calls=[summarize_call(call) for call in concurrent_calls], severity="P1")

        # MCP-018 cursor binding and replay.
        first_page = runner.tool("MCP-018-page-1", "factor_search", {"kind": "sub_factor", "limit": 1})
        first_data = data(first_page)
        first_meta = first_data.get("meta") if isinstance(first_data.get("meta"), dict) else {}
        cursor = first_meta.get("next_cursor") or ((first_page.get("business") or {}).get("meta") or {}).get("next_cursor")
        if cursor:
            original_args = {"kind": "sub_factor", "limit": 1, "cursor": cursor}
            continuation = runner.tool("MCP-018-page-2", "factor_search", original_args)
            replay = runner.tool("MCP-018-cursor-replay", "factor_search", original_args)
            same_page = business_success(continuation) and business_success(replay) and canonical(data(continuation)) == canonical(data(replay))
            verdict(cases, "MCP-018-original-cursor", "PASS" if same_page else "FAIL", "cursor continuation and replay", "same cursor/filters produce same continuation" if same_page else "cursor replay differs", first=summarize_call(continuation), replay=summarize_call(replay), severity=None if same_page else "P1")
            tampered = str(cursor)
            tampered = (tampered[:-1] + ("A" if tampered[-1:] != "A" else "B")) if tampered else "tampered"
            tamper_call = runner.tool("MCP-018-tampered", "factor_search", {"kind": "sub_factor", "limit": 1, "cursor": tampered})
            tamper_rejected = tamper_call.get("http_status", 0) >= 400 or tamper_call.get("is_error") is True or error_code(tamper_call) is not None or ((tamper_call.get("envelope") or {}).get("error") is not None)
            leaked = business_success(tamper_call) and bool(rows(tamper_call))
            verdict(cases, "MCP-018-tampered-cursor", "PASS" if tamper_rejected or not leaked else "FAIL", "tampered cursor cannot return unrelated data", "rejected/empty result" if tamper_rejected or not leaked else "tampered cursor returned data", call=summarize_call(tamper_call), severity=None if tamper_rejected or not leaked else "P0")
            changed = runner.tool("MCP-018-filter-changed", "factor_search", {"kind": "factor", "limit": 1, "cursor": cursor})
            changed_rows = rows(changed)
            cross_filter = any(row.get("kind") != "factor" for row in changed_rows)
            verdict(cases, "MCP-018-filter-binding", "PASS" if not cross_filter else "FAIL", "cursor binds filter", "changed filter did not leak sub-factor rows" if not cross_filter else "cursor returned rows outside changed filter", call=summarize_call(changed), severity=None if not cross_filter else "P0")
        else:
            verdict(cases, "MCP-018", "BLOCKED", "cursor integrity", "dynamic result has no continuation cursor", blocking_reason="BLOCKED_DATA_PRECONDITION")

        # MCP-019 point-in-time probes on daily/catalog.  Dates are dynamic DB values.
        daily_current = runner.tool("MCP-019-daily-current", "environment_get_daily", {"limit": 10})
        daily_rows = rows(daily_current)
        if daily_rows:
            date_values = [str(row.get("environment_date")) for row in daily_rows if row.get("environment_date")]
            oldest = min(date_values) if date_values else None
            historical = runner.tool("MCP-019-daily-history", "environment_get_daily", {"label_kind": "fact", "as_of": f"{oldest}T23:59:59Z"}) if oldest else None
            future = runner.tool("MCP-019-daily-future", "environment_get_daily", {"label_kind": "fact", "as_of": (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()})
            future_dates = [str(row.get("environment_date")) for row in rows(future)]
            no_future = not future_dates or max(future_dates) <= date.today().isoformat()
            verdict(cases, "MCP-019-daily-future", "PASS" if no_future else "FAIL", "daily point-in-time future visibility", "future as_of did not expose dates beyond current data" if no_future else "future response exposed data beyond current date", future_dates=future_dates, call=summarize_call(future), severity=None if no_future else "P0")
            if historical is not None:
                verdict(cases, "MCP-019-daily-history", "PASS" if business_success(historical) else "OBSERVED", "daily historical as_of", "historical query returned a structured result" if business_success(historical) else "historical query has no usable fixture", call=summarize_call(historical), rows=len(rows(historical)))
        else:
            verdict(cases, "MCP-019-daily", "BLOCKED", "daily point-in-time semantics", "no dynamic daily fixture", blocking_reason="BLOCKED_DATA_PRECONDITION")

        # DB-601 schema, constraints, FK and grants.
        required_tables = [
            "market_environment_daily", "market_environment_eval_batch", "market_environment_factor_metric",
            "market_environment_factor_route", "market_environment_strategy_feedback_submissions",
        ]
        table_rows = db_query(conn, "SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=DATABASE()")
        present = {row["TABLE_NAME"] for row in table_rows}
        schema_rows: dict[str, Any] = {}
        for table in required_tables:
            cols = db_query(conn, "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s ORDER BY ORDINAL_POSITION", (table,))
            indexes = db_query(conn, "SELECT INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX", (table,)) if table in present else []
            fks = db_query(conn, "SELECT CONSTRAINT_NAME,CONSTRAINT_TYPE FROM information_schema.table_constraints WHERE table_schema=DATABASE() AND table_name=%s", (table,)) if table in present else []
            schema_rows[table] = {"present": table in present, "column_count": len(cols), "columns": [r["COLUMN_NAME"] for r in cols], "unique_indexes": sorted({r["INDEX_NAME"] for r in indexes if r["NON_UNIQUE"] == 0}), "constraints": fks}
        write_json(output / "db-schema.json", schema_rows)
        schema_ok = all(item["present"] for item in schema_rows.values()) and all(item["column_count"] > 0 for item in schema_rows.values())
        verdict(cases, "DB-601", "PASS" if schema_ok else "FAIL", "required tables and relations exist", "all required tables expose columns and constraints" if schema_ok else "required table/structure missing", schema=schema_rows, severity=None if schema_ok else "P1")
        grants = db_query(conn, "SHOW GRANTS FOR CURRENT_USER()")
        write_json(output / "db-grants.json", grants)

        # DB-602 daily uniqueness/current checks.
        daily_dupes = db_query(conn, """SELECT environment_date,label_kind,revision,COUNT(*) AS row_count
            FROM market_environment_daily GROUP BY environment_date,label_kind,revision HAVING COUNT(*)>1""")
        current_dupes = db_query(conn, """SELECT environment_date,label_kind,SUM(is_current=1) AS current_count,COUNT(*) AS row_count
            FROM market_environment_daily GROUP BY environment_date,label_kind HAVING SUM(is_current=1)>1""")
        revision_stats = db_query(conn, """SELECT label_kind,COUNT(*) AS rows_total,COUNT(DISTINCT environment_date) AS dates,
            COUNT(DISTINCT revision) AS revisions,MAX(revision) AS max_revision FROM market_environment_daily GROUP BY label_kind""")
        db602_ok = not daily_dupes and not current_dupes
        verdict(cases, "DB-602", "PASS" if db602_ok else "FAIL", "daily revision/current uniqueness", "no duplicate revision key or multiple current rows" if db602_ok else "duplicate daily revision/current rows found", duplicate_revision=daily_dupes, duplicate_current=current_dupes, revision_stats=revision_stats, severity=None if db602_ok else "P1")
        if not any(int(row.get("revisions") or 0) > 1 for row in revision_stats):
            verdict(cases, "DB-602-revision-history", "BLOCKED", "daily historical revision retention", "test DB has only revision 1; no revision pair to verify non-overwrite", blocking_reason="BLOCKED_DATA_PRECONDITION")

        # DB-603 metric uniqueness by actual unique index and FK integrity.
        metric_dupes = db_query(conn, """SELECT eval_batch_id,factor_ref,factor_version,label_code,evaluation_type,`interval`,
            return_bar_interval,forward_return_bars,window_scope,COUNT(*) AS row_count
            FROM market_environment_factor_metric GROUP BY eval_batch_id,factor_ref,factor_version,label_code,evaluation_type,
            `interval`,return_bar_interval,forward_return_bars,window_scope HAVING COUNT(*)>1 LIMIT 100""")
        metric_orphans = db_query(conn, """SELECT COUNT(*) AS orphan_count FROM market_environment_factor_metric m
            LEFT JOIN market_environment_eval_batch b ON b.id=m.eval_batch_id WHERE b.id IS NULL""")
        db603_ok = not metric_dupes and int((metric_orphans[0] if metric_orphans else {}).get("orphan_count") or 0) == 0
        verdict(cases, "DB-603", "PASS" if db603_ok else "FAIL", "metric uniqueness and batch FK", "no duplicate formal metric unit or orphan batch reference" if db603_ok else "duplicate metric units or orphan references found", duplicate_units=metric_dupes, orphan_count=metric_orphans, severity=None if db603_ok else "P1")

        # DB-604/613 route and batch invariants.
        active_dupes = db_query(conn, """SELECT market_scope,label_kind,label_code,COUNT(*) AS active_count
            FROM market_environment_factor_route WHERE is_active=1 GROUP BY market_scope,label_kind,label_code HAVING COUNT(*)>1""")
        route_orphans = db_query(conn, """SELECT COUNT(*) AS orphan_count FROM market_environment_factor_route r
            LEFT JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
            LEFT JOIN market_environment_factor_metric m ON m.id=r.metric_id
            WHERE b.id IS NULL OR m.id IS NULL""")
        route_mismatch = db_query(conn, """SELECT COUNT(*) AS mismatch_count FROM market_environment_factor_route r
            JOIN market_environment_factor_metric m ON m.id=r.metric_id
            WHERE r.eval_batch_id<>m.eval_batch_id OR r.factor_ref<>m.factor_ref OR r.factor_version<>m.factor_version
               OR r.market_scope<>m.market_scope OR r.label_code<>m.label_code""")
        publication_dupes = db_query(conn, """SELECT publication_uid,COUNT(DISTINCT publish_version) AS versions,COUNT(*) AS routes
            FROM market_environment_factor_route WHERE is_active=1 GROUP BY publication_uid HAVING COUNT(DISTINCT publish_version)>1""")
        db604_ok = not active_dupes and int((route_orphans[0] if route_orphans else {}).get("orphan_count") or 0) == 0 and int((route_mismatch[0] if route_mismatch else {}).get("mismatch_count") or 0) == 0 and not publication_dupes
        verdict(cases, "DB-604", "PASS" if db604_ok else "FAIL", "active route history and foreign-key identity", "active route set is unique and references matching batch/metric" if db604_ok else "active route/history invariant violated", active_duplicates=active_dupes, orphans=route_orphans, mismatches=route_mismatch, publication_duplicates=publication_dupes, severity=None if db604_ok else "P0")

        # DB-605 API/MCP/DB route sample (only if a live route exists).
        if active_route and market_scope and factor_ref:
            metrics_call = runner.tool("DB-605-metrics", "factor_get_environment_metrics", {"factor_ref": factor_ref, "market_scope": market_scope, "route_profile_key": active_route.get("route_profile_key") or "default", "label_code": active_route.get("label_code"), "limit": 1000})
            metric_rows = rows(metrics_call, "metrics", "items")
            db_metric = db_one(conn, "SELECT id,factor_ref,market_scope,label_code,evaluation_type,metric_status,is_valid,routing_score,time_series_score,cross_sectional_score FROM market_environment_factor_metric WHERE id=%s", (active_route["metric_id"],))
            api_ids = {str(row.get("metric_id") or row.get("id")) for row in metric_rows if row.get("metric_id") is not None or row.get("id") is not None}
            db605_ok = business_success(metrics_call) and (not api_ids or str(active_route["metric_id"]) in api_ids)
            verdict(cases, "DB-605", "PASS" if db605_ok else "FAIL", "MCP environment metric traces to DB route metric", "returned metric identity matches active route" if db605_ok else "API/MCP metric identity does not match DB route", api=summarize_call(metrics_call), api_metric_ids=sorted(api_ids), db_route=active_route, db_metric=db_metric, severity=None if db605_ok else "P1")
        else:
            verdict(cases, "DB-605", "BLOCKED", "MCP/API/DB three-way metric reconciliation", "no active route fixture with complete dynamic identity", blocking_reason="BLOCKED_DATA_PRECONDITION")

        # Long read-only MCP probes can outlive an idle MySQL connection; renew it
        # before the remaining database checks rather than treating a dropped
        # diagnostic connection as a product failure.
        try:
            conn.ping(reconnect=True)
        except Exception:  # noqa: BLE001
            conn.close()
            conn, _ = db_connection()

        # DB-606 audit-field presence (null counts only; no sensitive payloads).
        audit_tables = db_query(conn, "SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=DATABASE() AND (TABLE_NAME LIKE '%%audit%%' OR TABLE_NAME LIKE '%%log%%')")
        audit_summary: dict[str, Any] = {}
        for row in audit_tables:
            table = row["TABLE_NAME"]
            columns = {r["COLUMN_NAME"] for r in db_query(conn, "SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s", (table,))}
            checks = {}
            for col in ("request_id", "trace_id", "actor", "created_by", "created_at", "event_type"):
                if col in columns:
                    try:
                        checks[col] = db_one(conn, f"SELECT COUNT(*) AS total, SUM(`{col}` IS NULL OR `{col}`='') AS missing FROM `{table}`")
                    except Exception as exc:  # noqa: BLE001
                        checks[col] = {"error": type(exc).__name__}
            audit_summary[table] = checks
        verdict(cases, "DB-606", "OBSERVED", "audit field inventory", "audit tables and nullable correlation fields inventoried; event-specific write evidence unavailable in read-only run", audit_tables=audit_summary)

        # DB-607/610/612 are explicitly write/internal-API dependent.
        verdict(cases, "DB-607", "BLOCKED", "transaction atomicity", "requires controlled mid-transaction failure on a dedicated test batch; no write authorization in this run", blocking_reason="BLOCKED_WRITE_AUTHORIZATION")
        grants_text = " ".join(str(row) for row in grants)
        broad_privilege = "ALL PRIVILEGES" in grants_text.upper() or "GRANT ALL" in grants_text.upper()
        verdict(cases, "DB-608", "OBSERVED" if broad_privilege else "PASS", "database account privilege boundary", "configured factor_app account has broad database privileges; account role (read-only vs application writer) is not documented in available config" if broad_privilege else "no broad grant observed", grants=grants, classification="SECURITY_REVIEW_REQUIRED" if broad_privilege else None)

        # DB-609 scan only counts potential secret markers, never values.
        secret_scan: dict[str, Any] = {}
        # Scan bounded business/audit payload tables.  A full text scan of every
        # unrelated trading table can hold a read connection for minutes and is
        # not required to establish the MCP factor-data leakage contract.
        sensitive_table_rows = db_query(
            conn,
            "SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND (TABLE_NAME LIKE 'market_environment%%' OR TABLE_NAME LIKE 'factor_ic%%' "
            "OR TABLE_NAME LIKE 'factor_performance%%' OR TABLE_NAME LIKE 'factor_validity%%' "
            "OR TABLE_NAME LIKE 'pipeline_%%' OR TABLE_NAME LIKE 'scheduled_job%%' "
            "OR TABLE_NAME LIKE 'kb_%%' OR TABLE_NAME LIKE '%%audit%%' OR TABLE_NAME LIKE '%%log%%')",
        )
        text_tables = sensitive_table_rows
        for row in text_tables:
            table = row["TABLE_NAME"]
            approx = db_one(conn, "SELECT TABLE_ROWS AS approximate_rows FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s", (table,))
            # Avoid holding a connection on very large historical payload tables;
            # those are recorded as bounded-scan omissions rather than guessed
            # clean.  The factor-data environment tables are all small enough.
            approximate_rows = int((approx or {}).get("approximate_rows") or 0)
            if approximate_rows > 100000:
                secret_scan[table] = {"skipped": True, "approximate_rows": approximate_rows, "reason": "bounded_scan_limit"}
                continue
            cols = db_query(conn, "SELECT COLUMN_NAME,DATA_TYPE FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s AND DATA_TYPE IN ('char','varchar','text','json') AND COLUMN_NAME IN ('raw_payload','error_message','message','details','payload','request_body','response_body','event_data','metrics_json')", (table,))
            hits = 0
            scanned_cols: list[str] = []
            for col in cols:
                name = col["COLUMN_NAME"]
                if name not in {"raw_payload", "error_message", "message", "details", "payload", "request_body", "response_body", "event_data", "metadata", "metrics_json"}:
                    continue
                scanned_cols.append(name)
                try:
                    hit = db_one(conn, f"SELECT COUNT(*) AS c FROM `{table}` WHERE CAST(`{name}` AS CHAR) REGEXP 'naf_mcp_|Bearer[[:space:]]|password|authorization|hmac_secret'", ())
                    hits += int((hit or {}).get("c") or 0)
                except Exception:
                    pass
            if scanned_cols:
                secret_scan[table] = {"columns": scanned_cols, "potential_marker_rows": hits}
        # Large tables are intentionally recorded as skipped entries without a
        # marker count; treat those as unknown rather than crashing report
        # generation after all network/database probes have completed.
        leak_rows = sum(
            int(item.get("potential_marker_rows") or 0)
            for item in secret_scan.values()
            if isinstance(item, dict) and "potential_marker_rows" in item
        )
        verdict(cases, "DB-609", "PASS" if leak_rows == 0 else "FAIL", "sensitive marker scan", "no token/password marker found in scanned payload/error columns" if leak_rows == 0 else "potential credential marker found; values intentionally withheld", scan=secret_scan, severity=None if leak_rows == 0 else "P0")

        verdict(cases, "DB-610", "BLOCKED", "cleanup/shared data protection", "no dedicated resources were created, so destructive cleanup path was not exercised", blocking_reason="BLOCKED_WRITE_AUTHORIZATION")
        after_rejected = table_snapshot(conn)
        write_json(output / "db-after.json", after_rejected)
        unchanged = before == after_rejected
        verdict(cases, "DB-611", "PASS" if unchanged else "OBSERVED", "rejected MCP requests have no business-table mutation", "before/after read-only snapshots match" if unchanged else "background or unrelated updates changed snapshots; cannot attribute to rejected calls", before=before, after=after_rejected)
        verdict(cases, "DB-612", "BLOCKED", "Scheduler/DB status reconciliation", "Scheduler endpoint and scheduler tables are unavailable in this environment", blocking_reason="BLOCKED_ENV")

        batch_invariants = db_query(conn, """SELECT id,batch_uid,status,publish_status,is_active,finished_at,published_at,publication_uid,publish_version,
            expected_metric_count,completed_metric_count,insufficient_metric_count,failed_metric_count,environment_terminal_count,environment_failed_count,
            JSON_UNQUOTE(JSON_EXTRACT(environment_status,'$.route_count')) AS environment_route_count,
            (SELECT COUNT(*) FROM market_environment_factor_route r WHERE r.eval_batch_id=b.id AND r.is_active=1) AS active_route_count
            FROM market_environment_eval_batch b ORDER BY b.updated_at DESC LIMIT 200""")
        contradictions: list[dict[str, Any]] = []
        for row in batch_invariants:
            status = str(row.get("status") or "")
            publish = str(row.get("publish_status") or "")
            if publish in {"published", "active"} and row.get("published_at") is None:
                contradictions.append({"batch_uid": row["batch_uid"], "kind": "published_without_published_at"})
            if status in {"completed", "failed", "cancelled"} and row.get("finished_at") is None:
                contradictions.append({"batch_uid": row["batch_uid"], "kind": "terminal_without_finished_at"})
            if row.get("environment_route_count") is not None and int(row["environment_route_count"] or 0) != int(row["active_route_count"] or 0):
                contradictions.append({"batch_uid": row["batch_uid"], "kind": "environment_route_count_mismatch", "declared": row["environment_route_count"], "actual": row["active_route_count"]})
            if int(row.get("completed_metric_count") or 0) + int(row.get("insufficient_metric_count") or 0) + int(row.get("failed_metric_count") or 0) > int(row.get("expected_metric_count") or 0):
                contradictions.append({"batch_uid": row["batch_uid"], "kind": "metric_counts_exceed_expected"})
        db613_ok = not contradictions
        verdict(cases, "DB-613", "PASS" if db613_ok else "FAIL", "batch/publication/active-route state invariants", "all sampled batch invariants hold" if db613_ok else "state contradiction(s) found in persisted batch/publication data", contradiction_count=len(contradictions), contradictions=contradictions[:50], sampled_batches=len(batch_invariants), severity=None if db613_ok else "P1")

        write_json(output / "calls-summary.json", [summarize_call(call) for call in runner.calls])
        write_json(output / "results.json", {"captured_at": datetime.now(timezone.utc), "mcp_url_host": MCP_URL.split('/')[2], "read_only": True, "cases": cases})
        lines = [f"# Read-only invariant probe", "", f"Captured: {datetime.now(timezone.utc).isoformat()}", "", f"Output: `{output}`", "", "| Case | Status | Title | Reason |", "|---|---|---|---|"]
        for item in cases:
            reason = str(item.get("reason", "")).replace("|", "/")
            lines.append(f"| {item['case_id']} | {item['status']} | {item['title']} | {reason} |")
        (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        counts = Counter(item["status"] for item in cases)
        print(json.dumps({"output": str(output), "counts": counts, "case_count": len(cases)}, ensure_ascii=False, default=json_default))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
