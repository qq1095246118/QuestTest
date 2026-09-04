#!/usr/bin/env python3
"""Probe MCP batch-input and cursor-binding boundaries without writes."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MCP_URL = os.environ.get("MCP_URL", "https://test-factor-frontend.questvector.ai/mcp/factor-data")
TOKEN = os.environ.get("MCP_TOKEN") or os.environ.get("FACTOR4_MCP_TOKEN")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
UNKNOWN_REF = "sub_factor:999999999"
SENSITIVE = re.compile(r"(authorization|token|password|secret|signature|hmac)", re.I)


def redact(value: Any) -> Any:
    """Recursively redact credential-shaped keys and token values."""
    if isinstance(value, dict):
        return {str(k): "<redacted>" if SENSITIVE.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and "naf_mcp_" in value:
        return "<redacted>"
    return value


def write_json(path: Path, value: Any) -> None:
    """Persist a sanitized JSON artifact."""
    path.write_text(json.dumps(redact(value), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def parse_response(raw: bytes, content_type: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse one JSON or single-event SSE response."""
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" not in content_type.lower():
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None, None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, f"{type(exc).__name__}: {exc}"
    events = []
    for block in re.split(r"\r?\n\r?\n", text):
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if data_lines:
            try:
                events.append(json.loads("\n".join(data_lines)))
            except json.JSONDecodeError as exc:
                return None, f"SSE_PARSE: {exc}"
    if len(events) != 1 or not isinstance(events[0], dict):
        return None, f"SSE_EVENT_COUNT={len(events)}"
    return events[0], None


def normalize(envelope: dict[str, Any] | None, parse_error: str | None, status: int | None, elapsed: float) -> dict[str, Any]:
    """Normalize transport and MCP business fields for assertions."""
    envelope = envelope if isinstance(envelope, dict) else {}
    result = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        structured = {}
        content = result.get("content") if isinstance(result.get("content"), list) else []
        if content and isinstance(content[0], dict):
            try:
                parsed = json.loads(content[0].get("text", ""))
                if isinstance(parsed, dict):
                    structured = parsed
            except (TypeError, json.JSONDecodeError):
                pass
    business_error = structured.get("error") if isinstance(structured, dict) else None
    top_error = envelope.get("error")
    code = None
    if isinstance(top_error, dict):
        code = top_error.get("code")
    elif isinstance(business_error, dict):
        code = business_error.get("code") or business_error.get("error_code")
    data = structured.get("data") if isinstance(structured.get("data"), dict) else {}
    items: list[dict[str, Any]] = []
    for key in ("items", "metrics", "results", "scopes"):
        if isinstance(data.get(key), list):
            items = [row for row in data[key] if isinstance(row, dict)]
            break
    return {
        "http_status": status,
        "elapsed_seconds": round(elapsed, 3),
        "parse_error": parse_error,
        "rpc_error_code": code,
        "is_error": result.get("isError"),
        "data_keys": sorted(data),
        "item_count": len(items),
        "item_refs": [row.get("factor_ref") for row in items],
        "item_success": [row.get("success") for row in items],
        "item_errors": [row.get("error") for row in items],
    }


def call(case_id: str, tool: str, arguments: Any, output: Path) -> dict[str, Any]:
    """Send one read-only MCP tool call and save sanitized request/response."""
    payload = {"jsonrpc": "2.0", "id": f"{case_id}-{datetime.now(timezone.utc).timestamp()}", "method": "tools/call", "params": {"name": tool}}
    if arguments is not None:
        payload["params"]["arguments"] = arguments
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    req = urllib.request.Request(
        MCP_URL,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream", "User-Agent": UA},
    )
    started = time.monotonic()
    response_headers: dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read()
            status = response.status
            response_headers = {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        response_headers = {k.lower(): v for k, v in exc.headers.items()}
    except Exception as exc:  # noqa: BLE001
        result = {"http_status": None, "elapsed_seconds": round(time.monotonic() - started, 3), "parse_error": f"{type(exc).__name__}: {exc}", "rpc_error_code": type(exc).__name__, "is_error": True, "data_keys": [], "item_count": 0, "item_refs": [], "item_success": [], "item_errors": []}
        write_json(output / f"{case_id}.request.json", payload)
        write_json(output / f"{case_id}.response.json", result)
        return result
    envelope, parse_error = parse_response(raw, response_headers.get("content-type", ""))
    result = normalize(envelope, parse_error, status, time.monotonic() - started)
    result["response_headers"] = {k: v for k, v in response_headers.items() if k in {"content-type", "x-request-id", "x-trace-id", "retry-after"}}
    write_json(output / f"{case_id}.request.json", payload)
    write_json(output / f"{case_id}.response.json", envelope if envelope is not None else {"parse_error": parse_error, "status": status})
    return result


def db_scope() -> tuple[str, dict[str, Any]]:
    """Discover one completed metric scope and a valid factor reference from test DB."""
    config = yaml.safe_load((ROOT / "config/test.yaml").read_text(encoding="utf-8"))["database"]
    conn = pymysql.connect(host=config["host"], port=config["port"], user=config["username"], password=config["password"], database=config["name"], cursorclass=pymysql.cursors.DictCursor, connect_timeout=10, read_timeout=60, autocommit=True)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""SELECT r.factor_ref FROM market_environment_factor_route r WHERE r.is_active=1 ORDER BY r.activated_at DESC,r.id DESC LIMIT 1""")
            route = cursor.fetchone()
            cursor.execute("""SELECT s.factor_id,s.is_sub_factor_id,s.ic_scope,s.calculation_mode,s.universe_key,s.symbol,s.window_scope,s.factor_bar_interval,s.factor_window_bars,s.return_bar_interval,s.forward_return_bars,s.scoring_version,s.run_id,s.period_start,s.period_end
                FROM factor_ic_summary_metrics s JOIN factor_ic_runs r ON r.run_id=s.run_id
                WHERE r.status='completed' AND s.is_sub_factor_id=1 ORDER BY s.id DESC LIMIT 1""")
            scope = cursor.fetchone()
    finally:
        conn.close()
    if not scope:
        raise RuntimeError("no completed metric scope in test DB")
    # Use the factor that owns the discovered completed metric scope.  An active
    # route can legitimately point at a different factor and would make a
    # metric-batch probe look like a data-precondition failure.
    factor_ref = f"sub_factor:{scope['factor_id']}"
    args = {
        "factor_refs": [],
        "ic_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scoring_version": scope["scoring_version"],
        "symbol": scope.get("symbol") or "",
    }
    return factor_ref, args


def is_rejected(result: dict[str, Any]) -> bool:
    """Return true when an MCP call is rejected at protocol/business level."""
    return bool(result.get("http_status", 0) >= 400 or result.get("parse_error") or result.get("is_error") is True or result.get("rpc_error_code") is not None)


def main() -> None:
    """Execute bounded batch and cursor boundary cases and write a report."""
    if not TOKEN:
        raise SystemExit("MCP_TOKEN or FACTOR4_MCP_TOKEN is required")
    if not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("test MCP host gate failed")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-batch-cursor-boundary"
    output.mkdir(parents=True, exist_ok=False)
    factor_ref, metric_args = db_scope()
    detail_valid = {"factor_refs": [factor_ref]}
    cases: list[dict[str, Any]] = []

    def record(case_id: str, title: str, result: dict[str, Any], expected: str, *, mode: str = "reject") -> None:
        """Record one result using strict or duplicate-determinism semantics."""
        if mode == "reject":
            passed = is_rejected(result)
            reason = "structured invalid-argument rejection" if passed else "invalid input returned success"
        elif mode == "duplicate":
            passed = is_rejected(result) or len(result.get("item_refs", [])) == len(set(result.get("item_refs", [])))
            reason = "rejected or returned at most one row per factor_ref" if passed else "duplicate input produced duplicate output rows"
        elif mode == "cursor":
            passed = is_rejected(result) or result.get("item_count", 0) == 0
            reason = "cursor rejected/empty" if passed else "cursor returned cross-query data"
        else:
            refs = result.get("item_refs", [])
            errors = result.get("item_errors", [])
            passed = is_rejected(result) or (factor_ref in refs and UNKNOWN_REF in refs) or (factor_ref in refs and any(error for error in errors))
            reason = "known and unknown refs remain item-scoped" if passed else "batch did not preserve per-item unknown-ref isolation"
        cases.append({"case_id": case_id, "title": title, "status": "PASS" if passed else "FAIL", "reason": reason, "expected": expected, "actual": result, "severity": None if passed else ("P0" if mode == "cursor" else "P1")})

    # Empty and duplicate detail batches.
    r = call("BATCH-DETAIL-EMPTY", "factor_get_details_batch", {"factor_refs": []}, output)
    record("BATCH-DETAIL-EMPTY", "details batch rejects empty factor_refs", r, "isError/INVALID_ARGUMENT", mode="reject")
    r = call("BATCH-DETAIL-DUPLICATE", "factor_get_details_batch", {"factor_refs": [factor_ref, factor_ref]}, output)
    record("BATCH-DETAIL-DUPLICATE", "details batch deduplicates or rejects duplicate refs", r, "one unique row or structured rejection", mode="duplicate")
    r = call("BATCH-DETAIL-MIXED-UNKNOWN", "factor_get_details_batch", {"factor_refs": [factor_ref, UNKNOWN_REF]}, output)
    record("BATCH-DETAIL-MIXED-UNKNOWN", "details batch isolates unknown ref", r, "known item plus item-scoped not-found", mode="mixed")

    # Empty and duplicate metric batches using a DB-discovered exact scope.
    r = call("BATCH-METRIC-EMPTY", "factor_get_metrics_batch", {**metric_args, "factor_refs": []}, output)
    record("BATCH-METRIC-EMPTY", "metrics batch rejects empty factor_refs", r, "isError/INVALID_ARGUMENT", mode="reject")
    r = call("BATCH-METRIC-DUPLICATE", "factor_get_metrics_batch", {**metric_args, "factor_refs": [factor_ref, factor_ref]}, output)
    record("BATCH-METRIC-DUPLICATE", "metrics batch deduplicates or rejects duplicate refs", r, "one unique row or structured rejection", mode="duplicate")
    r = call("BATCH-METRIC-MIXED-UNKNOWN", "factor_get_metrics_batch", {**metric_args, "factor_refs": [factor_ref, UNKNOWN_REF]}, output)
    record("BATCH-METRIC-MIXED-UNKNOWN", "metrics batch isolates unknown ref", r, "known item plus item-scoped not-found", mode="mixed")

    # Cursor from the previously captured factor_search page.  Reusing it on a
    # different tool or with changed filters must not return another dataset.
    cursor = None
    source = ROOT / "reports/factor4-deep/20260903T034917Z-readonly-invariant-probe/039-MCP-018-page-1.response.json"
    if source.exists():
        _, business, _ = load_response_for_cursor(source)
        cursor = ((business.get("meta") or {}).get("next_cursor") if isinstance(business, dict) else None)
    if cursor:
        r = call("CURSOR-CROSS-TOOL", "environment_get_daily", {"cursor": cursor, "limit": 1}, output)
        record("CURSOR-CROSS-TOOL", "factor_search cursor cannot be used on environment_get_daily", r, "structured rejection or empty result", mode="cursor")
        r = call("CURSOR-CHANGED-LIMIT", "factor_search", {"kind": "sub_factor", "limit": 2, "cursor": cursor}, output)
        record("CURSOR-CHANGED-LIMIT", "cursor bound to original limit/filter", r, "structured rejection or empty result", mode="cursor")
    else:
        cases.append({"case_id": "CURSOR-CROSS-TOOL", "title": "cursor binding", "status": "BLOCKED", "reason": "captured first page had no cursor", "blocking_reason": "BLOCKED_DATA_PRECONDITION"})
        cases.append({"case_id": "CURSOR-CHANGED-LIMIT", "title": "cursor binding", "status": "BLOCKED", "reason": "captured first page had no cursor", "blocking_reason": "BLOCKED_DATA_PRECONDITION"})

    write_json(output / "results.json", {"captured_at": datetime.now(timezone.utc), "mcp_url": MCP_URL, "read_only": True, "dynamic_factor_ref": factor_ref, "cases": cases, "counts": Counter(case["status"] for case in cases)})
    lines = ["# Batch and cursor boundary probe", "", f"Captured: {datetime.now(timezone.utc).isoformat()}", "", "| Case | Status | Title | Reason |", "|---|---|---|---|"]
    for item in cases:
        lines.append(f"| {item['case_id']} | {item['status']} | {item['title']} | {str(item.get('reason', '')).replace('|', '/') } |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "counts": dict(Counter(case["status"] for case in cases)), "case_count": len(cases)}, ensure_ascii=False, default=str))


def load_response_for_cursor(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load a captured response and return envelope/business/data objects."""
    envelope = json.loads(path.read_text(encoding="utf-8"))
    result = envelope.get("result") or {}
    business = result.get("structuredContent")
    if not isinstance(business, dict):
        business = {}
        content = result.get("content") or []
        if content and isinstance(content[0], dict):
            try:
                parsed = json.loads(content[0].get("text", ""))
                if isinstance(parsed, dict):
                    business = parsed
            except (TypeError, json.JSONDecodeError):
                pass
    data = business.get("data") if isinstance(business.get("data"), dict) else {}
    return envelope, business, data


if __name__ == "__main__":
    main()
