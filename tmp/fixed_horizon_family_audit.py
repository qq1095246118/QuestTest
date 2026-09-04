#!/usr/bin/env python3
"""Audit all strongly suspected fixed-horizon factor formula families.

The runner discovers families from the test database, then asks the MCP formula
tool for the exact completed evidence identity of every candidate.  It is
strictly read-only and records only redacted request/response artifacts.
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
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql
import yaml

ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
TEMPORAL_RE = re.compile(
    r"\b(?P<op>diff|pct_change|shift|rolling|ewm)\s*\(\s*"
    r"(?:\w+\s*=\s*)?(?P<n>-?\d+(?:\.\d+)?)",
    re.I,
)
SENSITIVE_RE = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)


def _json(value: Any) -> Any:
    """Decode a JSON database value while preserving scalar types."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _safe(value: Any) -> Any:
    """Convert database values to JSON-safe values."""
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def _redact(value: Any) -> Any:
    """Remove credentials and credential-shaped fields from artifacts."""
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_RE.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and "naf_mcp_" in value:
        return "<redacted>"
    return value


def _write(path: Path, value: Any) -> None:
    """Write one sanitized JSON artifact."""
    path.write_text(
        json.dumps(_redact(value), ensure_ascii=False, indent=2, default=_safe) + "\n",
        encoding="utf-8",
    )


def _business(envelope: dict[str, Any] | None) -> dict[str, Any]:
    """Extract structured MCP business content."""
    result = envelope.get("result") if isinstance(envelope, dict) else None
    if not isinstance(result, dict):
        return {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        try:
            value = json.loads(content[0].get("text", ""))
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _error_code(business: dict[str, Any]) -> str | None:
    """Return a structured business error code, if present."""
    error = business.get("error")
    return str(error.get("code")) if isinstance(error, dict) and error.get("code") else None


def _client_call(
    token: str,
    output: Path,
    sequence: int,
    label: str,
    method: str,
    params: dict[str, Any] | None,
    protocol_version: str | None,
    session_id: str | None,
) -> tuple[dict[str, Any], str | None, str | None]:
    """Send one MCP request and return call data plus updated protocol/session."""
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": label, "method": method}
    if params is not None:
        payload["params"] = params
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": UA,
    }
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    if session_id:
        headers["MCP-Session-Id"] = session_id
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
    parse_error: str | None = None
    envelope: dict[str, Any] | None = None
    try:
        text = raw.decode("utf-8", "replace")
        if "text/event-stream" in response_headers.get("content-type", "").lower():
            events = []
            for block in re.split(r"\r?\n\r?\n", text):
                lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
                if lines:
                    events.append(json.loads("\n".join(lines)))
            envelope = events[0] if len(events) == 1 and isinstance(events[0], dict) else None
            if envelope is None:
                parse_error = f"SSE_EVENT_COUNT={len(events)}"
        else:
            value = json.loads(text) if text else {}
            envelope = value if isinstance(value, dict) else None
            if envelope is None:
                parse_error = "JSON_ROOT_NOT_OBJECT"
    except Exception as exc:  # noqa: BLE001
        parse_error = f"{type(exc).__name__}: {exc}"
    if response_headers.get("mcp-session-id"):
        session_id = response_headers["mcp-session-id"]
    if method == "initialize":
        protocol_version = str((envelope or {}).get("result", {}).get("protocolVersion") or "") or None
    result = {
        "label": label,
        "method": method,
        "http_status": status,
        "elapsed_seconds": elapsed,
        "parse_error": parse_error,
        "business": _business(envelope),
        "request_id": ((envelope or {}).get("result", {}).get("structuredContent") or {}).get("meta", {}).get("request_id"),
    }
    _write(output / f"{sequence:03d}-{label}.request.json", payload)
    _write(output / f"{sequence:03d}-{label}.response.json", envelope if envelope is not None else {"parse_error": parse_error, "status": status})
    return result, protocol_version, session_id


def _discover_candidates(connection: pymysql.Connection) -> list[dict[str, Any]]:
    """Discover same-family formulas with differing declared windows."""
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT d.factor_id,d.name,d.description,d.calc_logic,d.params,d.status,
                      s.sub_factor_name,s.window,s.factor_bar_interval,s.formula_summary,
                      (SELECT COUNT(*) FROM factor_ic_run_formula_evidence e
                       WHERE e.factor_id=d.factor_id AND e.is_sub_factor_id=1) AS evidence_count,
                      (SELECT COUNT(*) FROM market_environment_factor_route r
                       WHERE r.factor_id=d.factor_id AND r.is_active=1 AND r.is_eligible=1) AS active_route_count
               FROM factors_details d
               LEFT JOIN sub_factors s ON s.id=d.factor_id AND d.is_sub_factor_id=1
               WHERE d.is_sub_factor_id=1"""
        )
        rows = [dict(row) for row in cursor.fetchall()]
    groups: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        params = _json(row.get("params"))
        params = params if isinstance(params, dict) else {}
        parent = str(params.get("original_sub_factor") or params.get("primary_parent") or "")
        expression = str(row.get("calc_logic") or "").strip()
        match = re.search(r"(?<![A-Za-z])(\d+(?:\.\d+)?)", str(params.get("window", row.get("window") or "")))
        declared = float(match.group(1)) if match else None
        row["parent"] = parent
        row["declared_window"] = declared
        row["params_window"] = params.get("window", params.get("factor_window_bars"))
        row["temporal_constants"] = [
            {"op": m.group("op").lower(), "value": float(m.group("n"))}
            for m in TEMPORAL_RE.finditer(expression)
        ]
        if parent and expression and row["temporal_constants"] and not re.search(r"\bwindow\b", expression, re.I):
            groups[(parent, expression)].append(row)
    candidates: list[dict[str, Any]] = []
    for (parent, expression), rows_for_formula in groups.items():
        if len({row["declared_window"] for row in rows_for_formula}) < 2:
            continue
        candidates.extend(
            {
                **row,
                "parent": parent,
                "expression": expression,
                "family_key": hashlib.sha256(f"{parent}\n{expression}".encode()).hexdigest()[:16],
            }
            for row in rows_for_formula
        )
    candidates.sort(key=lambda row: (str(row["parent"]), int(row["factor_id"])))
    return candidates


def _latest_evidence(connection: pymysql.Connection, factor_id: int) -> dict[str, Any] | None:
    """Select the newest completed formula evidence for one factor."""
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT e.id,e.run_id,e.factor_id,e.calculation_mode,e.factor_bar_interval,
                      e.factor_window_bars,e.return_bar_interval,e.forward_return_bars,
                      e.formula_version,e.formula_hash,e.expression,e.recorded_at,r.status,
                      r.completed_at
               FROM factor_ic_run_formula_evidence e
               JOIN factor_ic_runs r ON r.run_id=e.run_id
               WHERE e.factor_id=%s AND e.is_sub_factor_id=1 AND r.status='completed'
               ORDER BY e.recorded_at DESC,e.id DESC LIMIT 1""",
            (factor_id,),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def main() -> None:
    """Run the read-only family audit and print its report directory."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    config = yaml.safe_load((ROOT / "config/test.yaml").read_text(encoding="utf-8"))["database"]
    connection = pymysql.connect(
        host=config["host"], port=config["port"], user=config["username"], password=config["password"],
        database=config["name"], cursorclass=pymysql.cursors.DictCursor, autocommit=True,
        connect_timeout=15, read_timeout=180,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-fixed-horizon-family-audit"
    output.mkdir(parents=True, exist_ok=False)
    try:
        candidates = _discover_candidates(connection)
        for row in candidates:
            row["evidence"] = _latest_evidence(connection, int(row["factor_id"]))
        _write(output / "db-candidates.json", candidates)
    finally:
        connection.close()

    calls: list[dict[str, Any]] = []
    protocol: str | None = None
    session: str | None = None
    sequence = 1
    call, protocol, session = _client_call(
        token, output, sequence, "MCP-INIT", "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest-fixed-family-audit", "version": "1.0"}},
        protocol, session,
    )
    calls.append(call)
    sequence += 1
    call, protocol, session = _client_call(token, output, sequence, "MCP-NOTIFY", "notifications/initialized", {}, protocol, session)
    calls.append(call)
    sequence += 1
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        evidence = candidate.get("evidence") or {}
        if not evidence:
            results.append({**candidate, "classification": "BLOCKED_NO_COMPLETED_EVIDENCE"})
            continue
        args = {
            "factor_ref": f"sub_factor:{int(candidate['factor_id'])}",
            "run_id": evidence["run_id"],
            "calculation_mode": evidence["calculation_mode"],
            "interval": evidence["factor_bar_interval"],
            "factor_window_bars": evidence["factor_window_bars"],
            "return_bar_interval": evidence["return_bar_interval"],
            "forward_return_bars": int(evidence["forward_return_bars"]),
        }
        call, protocol, session = _client_call(
            token, output, sequence, f"FORMULA-{int(candidate['factor_id'])}", "tools/call",
            {"name": "factor_get_formula", "arguments": args}, protocol, session,
        )
        sequence += 1
        calls.append(call)
        data = (call.get("business") or {}).get("data")
        data = data if isinstance(data, dict) else {}
        expression = str(data.get("expression") or evidence.get("expression") or "")
        mcp_matches_db = (
            data.get("expression") == evidence.get("expression")
            and data.get("formula_hash") == evidence.get("formula_hash")
            and data.get("formula_version") == evidence.get("formula_version")
        )
        results.append(
            {
                **candidate,
                "evidence": evidence,
                "mcp": {
                    "http_status": call.get("http_status"),
                    "error_code": _error_code(call.get("business") or {}),
                    "expression": data.get("expression"),
                    "formula_hash": data.get("formula_hash"),
                    "formula_version": data.get("formula_version"),
                    "run_id": data.get("run_id"),
                    "factor_ref": data.get("factor_ref"),
                },
                "mcp_matches_db": mcp_matches_db,
                "classification": "CONFIRMED_FIXED_HORIZON_MISMATCH" if mcp_matches_db else "MCP_OR_EVIDENCE_MISMATCH",
                "mcp_temporal_constants": [
                    {"op": m.group("op").lower(), "value": float(m.group("n"))}
                    for m in TEMPORAL_RE.finditer(expression)
                ],
            }
        )
    report = {
        "run_id": stamp,
        "environment": "test",
        "mode": "READ_ONLY",
        "mcp_url": MCP_URL,
        "candidate_count": len(candidates),
        "results": results,
        "calls": [{"label": row["label"], "http_status": row["http_status"], "elapsed_seconds": row["elapsed_seconds"], "error_code": _error_code(row.get("business") or {})} for row in calls],
        "confirmed_fixed_horizon_factors": [int(row["factor_id"]) for row in results if row.get("classification") == "CONFIRMED_FIXED_HORIZON_MISMATCH"],
    }
    _write(output / "results.json", report)
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        groups[str(row["parent"])].append(row)
    lines = [
        "# Fixed-horizon family audit",
        "",
        f"- Run: `{stamp}`; environment: `test`; mode: `READ_ONLY`",
        f"- Candidate rows: {len(candidates)}; confirmed rows: {len(report['confirmed_fixed_horizon_factors'])}",
        "",
    ]
    for parent, family in groups.items():
        lines.append(f"## {parent}")
        lines.append("")
        for row in family:
            lines.append(
                f"- `{row['factor_id']}` `{row['sub_factor_name']}`: declared `{row['params_window']}`; "
                f"constants `{row['temporal_constants']}`; status `{row['status']}`; "
                f"active eligible routes `{row['active_route_count']}`; classification `{row['classification']}`"
            )
        lines.append("")
    lines.extend([
        "MCP formula responses were compared to the immutable DB evidence row. "
        "A matching projection confirms a definition/evidence issue, not an MCP transport issue.",
        "",
    ])
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "candidate_count": len(candidates), "confirmed_count": len(report["confirmed_fixed_horizon_factors"]), "confirmed_ids": report["confirmed_fixed_horizon_factors"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
