#!/usr/bin/env python3
"""Run one bounded TS and CS ``factor_rank`` check against the test MCP.

The scope is discovered from a read-only test-database snapshot and is kept
small (three factors where possible) so the MCP export budget is not consumed
by a broad catalog scan.  Request/response artifacts contain no credentials.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pymysql
import yaml


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
LOCAL_TZ = "Asia/Shanghai"
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|credential", re.I)


def redact(value: Any) -> Any:
    """Return a recursively redacted JSON-compatible value."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one sanitized JSON artifact."""

    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def parse_body(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse a JSON or single-event SSE MCP response."""

    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" not in content_type.lower():
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    events: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if data_lines:
            value = json.loads("\n".join(data_lines))
            if isinstance(value, dict):
                events.append(value)
    if len(events) != 1:
        raise ValueError(f"expected one MCP event, got {len(events)}")
    return events[0]


class MCPClient:
    """Minimal authenticated MCP client with credential-free evidence capture."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize the client for one MCP session."""

        self.token = token
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.sequence = 0
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.calls: dict[str, dict[str, Any]] = {}

    def _send(self, case_id: str, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """Send one JSON-RPC request and persist sanitized transport evidence."""

        self.sequence += 1
        request_id = f"{case_id}-{self.sequence}"
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": CHROME_UA,
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        request = urllib.request.Request(
            MCP_URL,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        status: int | None = None
        response_headers: dict[str, str] = {}
        raw = b""
        transport_error: str | None = None
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
            raw = exc.read()
        except Exception as exc:  # preserve network failures in the report
            transport_error = f"{type(exc).__name__}: {exc}"
        content_type = response_headers.get("content-type", "")
        envelope: dict[str, Any] | None = None
        parse_error: str | None = None
        if raw:
            try:
                envelope = parse_body(raw, content_type)
            except Exception as exc:
                parse_error = f"{type(exc).__name__}: {exc}"
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        result = envelope.get("result") if isinstance(envelope, dict) else None
        call: dict[str, Any] = {
            "case_id": case_id,
            "method": method,
            "http_status": status,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "content_type": content_type,
            "parse_error": parse_error,
            "transport_error": transport_error,
            "envelope": envelope,
            "is_error": result.get("isError") if isinstance(result, dict) else None,
        }
        write_json(self.output / f"{self.sequence:02d}-{case_id}.request.json", payload)
        if envelope is not None:
            write_json(self.output / f"{self.sequence:02d}-{case_id}.response.json", envelope)
        elif raw:
            (self.output / f"{self.sequence:02d}-{case_id}.response.txt").write_text(
                raw.decode("utf-8", errors="replace"), encoding="utf-8"
            )
        self.calls[case_id] = call
        return call

    def initialize(self) -> dict[str, Any]:
        """Negotiate the MCP protocol and send the initialized notification."""

        call = self._send(
            "MCP-INIT",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "QuestTest-rank-targeted", "version": "1.0"},
            },
        )
        result = call.get("envelope", {}).get("result", {}) if isinstance(call.get("envelope"), dict) else {}
        if isinstance(result, dict):
            self.protocol_version = result.get("protocolVersion") or "2025-06-18"
        self._send("MCP-READY", "notifications/initialized", {})
        return call

    def tool(self, case_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one named MCP tool and normalize its business envelope."""

        call = self._send(case_id, "tools/call", {"name": name, "arguments": arguments})
        envelope = call.get("envelope")
        result = envelope.get("result") if isinstance(envelope, dict) else None
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        parsed_text: dict[str, Any] | None = None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    value = json.loads(text)
                    parsed_text = value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    parsed_text = None
        call["business"] = structured if isinstance(structured, dict) else parsed_text or {}
        call["representations_equal"] = (
            structured == parsed_text if structured is not None and parsed_text is not None else None
        )
        call["tool"] = name
        call["arguments"] = arguments
        return call


def business(call: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized MCP business envelope."""

    value = call.get("business")
    return value if isinstance(value, dict) else {}


def data(call: dict[str, Any]) -> dict[str, Any]:
    """Return the response data object."""

    value = business(call).get("data")
    return value if isinstance(value, dict) else {}


def error_code(call: dict[str, Any]) -> str | None:
    """Return the structured business or JSON-RPC error code."""

    value = business(call).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    envelope = call.get("envelope")
    value = envelope.get("error") if isinstance(envelope, dict) else None
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    return None


def number(value: Any) -> Decimal | None:
    """Convert a JSON/DB scalar to Decimal without treating null as zero."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def equal_number(left: Any, right: Any) -> bool:
    """Compare numeric values exactly at Decimal precision."""

    left_value = number(left)
    right_value = number(right)
    return left_value == right_value if left_value is not None and right_value is not None else left == right


def db_connect() -> pymysql.Connection:
    """Open the configured test database for a read-only transaction."""

    config = yaml.safe_load((ROOT / "config" / "test.yaml").read_text(encoding="utf-8"))["database"]
    return pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["username"],
        password=config["password"],
        database=config["name"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=120,
    )


def discover_scope(cursor: Any, ic_scope: str, as_of_db: datetime) -> dict[str, Any] | None:
    """Select the smallest complete metric scope for TS or CS ranking."""

    symbol_clause = "m.symbol <> ''" if ic_scope == "time_series" else "m.symbol = ''"
    query = f"""
        SELECT m.ic_scope, m.symbol, m.calculation_mode,
               m.factor_bar_interval, m.factor_window_bars,
               m.return_bar_interval, m.forward_return_bars,
               m.universe_key, m.window_scope, m.scoring_version,
               COUNT(DISTINCT m.factor_id) AS factor_count,
               COUNT(DISTINCT CASE WHEN m.mean_ic IS NOT NULL THEN m.factor_id END) AS mean_count,
               COUNT(DISTINCT CASE WHEN f.factor_id IS NOT NULL THEN m.factor_id END) AS formula_count,
               MAX(r.completed_at) AS latest_completed
          FROM factor_ic_summary_metrics m
          JOIN factor_ic_runs r ON r.run_id=m.run_id
          LEFT JOIN (
              SELECT DISTINCT run_id, factor_id
                FROM factor_ic_run_formula_evidence
               WHERE is_sub_factor_id=1
          ) f ON f.run_id=m.run_id AND f.factor_id=m.factor_id
         WHERE m.is_sub_factor_id=1
           AND m.ic_scope=%s
           AND {symbol_clause}
           AND m.window_scope='1y'
           AND r.status='completed'
           AND r.completed_at <= %s
         GROUP BY m.ic_scope, m.symbol, m.calculation_mode,
                  m.factor_bar_interval, m.factor_window_bars,
                  m.return_bar_interval, m.forward_return_bars,
                  m.universe_key, m.window_scope, m.scoring_version
        HAVING factor_count BETWEEN 3 AND 5
           AND mean_count = factor_count
           AND formula_count = factor_count
         ORDER BY factor_count ASC, latest_completed DESC
         LIMIT 1
    """
    cursor.execute(query, (ic_scope, as_of_db))
    row = cursor.fetchone()
    return dict(row) if row else None


def scope_rows(cursor: Any, scope: dict[str, Any], as_of_db: datetime) -> list[dict[str, Any]]:
    """Read exact metric rows for one selected scope."""

    query = """
        SELECT m.id, m.factor_id, m.run_id, m.mean_ic, m.mean_rank_ic,
               m.icir, m.final_score, m.valid_slice_count, m.coverage_mean,
               r.completed_at
          FROM factor_ic_summary_metrics m
          JOIN factor_ic_runs r ON r.run_id=m.run_id
         WHERE m.is_sub_factor_id=1 AND m.ic_scope=%s AND m.symbol=%s
           AND m.calculation_mode=%s AND m.factor_bar_interval=%s
           AND m.factor_window_bars=%s AND m.return_bar_interval=%s
           AND m.forward_return_bars=%s AND m.universe_key=%s
           AND m.window_scope=%s AND m.scoring_version=%s
           AND r.status='completed' AND r.completed_at <= %s
           AND m.mean_ic IS NOT NULL
         ORDER BY m.factor_id, r.completed_at DESC
    """
    params = (
        scope["ic_scope"],
        scope["symbol"],
        scope["calculation_mode"],
        scope["factor_bar_interval"],
        scope["factor_window_bars"],
        scope["return_bar_interval"],
        scope["forward_return_bars"],
        scope["universe_key"],
        scope["window_scope"],
        scope["scoring_version"],
        as_of_db,
    )
    cursor.execute(query, params)
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in cursor.fetchall():
        factor_id = int(row["factor_id"])
        if factor_id not in seen:
            rows.append(dict(row))
            seen.add(factor_id)
    return rows


def rank_arguments(scope: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Build a bounded signed rank request from one exact scope."""

    return {
        "metric": "mean_ic",
        "top_k": 2,
        "bottom_k": 1,
        "ic_scope": scope["ic_scope"],
        "validity_scope": scope["ic_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "ranking_mode": "signed",
        "scoring_version": scope["scoring_version"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "as_of": as_of,
        "min_valid_slice_count": 0,
        "min_coverage_mean": 0,
        "require_oos": False,
        "kind": "sub_factor",
        "calculation_mode": scope["calculation_mode"],
        "symbol": scope["symbol"],
    }


def check_rank(call: dict[str, Any], args: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate one rank response against identity, ordering, and DB values."""

    payload = data(call)
    top = payload.get("top_items") if isinstance(payload.get("top_items"), list) else []
    bottom = payload.get("bottom_items") if isinstance(payload.get("bottom_items"), list) else []
    all_items = [item for item in top + bottom if isinstance(item, dict)]
    db_by_factor = {int(row["factor_id"]): row for row in rows}
    identity_fields = (
        ("ic_scope", args["ic_scope"]),
        ("calculation_mode", args["calculation_mode"]),
        ("factor_bar_interval", args["interval"]),
        ("factor_window_bars", args["factor_window_bars"]),
        ("return_bar_interval", args["return_bar_interval"]),
        ("forward_return_bars", args["forward_return_bars"]),
        ("universe_key", args["universe_key"]),
        ("symbol", args["symbol"]),
        ("window_scope", args["window_scope"]),
        ("scoring_version", args["scoring_version"]),
    )
    identity_bad: list[dict[str, Any]] = []
    value_bad: list[dict[str, Any]] = []
    semantics_bad: list[dict[str, Any]] = []
    missing_db: list[int] = []
    for item in all_items:
        mismatches = [field for field, expected in identity_fields if item.get(field) != expected]
        if mismatches:
            identity_bad.append({"factor_id": item.get("factor_id"), "fields": mismatches})
        factor_id = item.get("factor_id")
        db_row = db_by_factor.get(int(factor_id)) if factor_id is not None else None
        if db_row is None:
            missing_db.append(int(factor_id) if factor_id is not None else -1)
        else:
            if not equal_number(item.get("raw_metric_value"), db_row.get("mean_ic")):
                value_bad.append(
                    {
                        "factor_id": factor_id,
                        "api": item.get("raw_metric_value"),
                        "db": db_row.get("mean_ic"),
                    }
                )
        raw = number(item.get("raw_metric_value"))
        sign = number(item.get("direction_sign"))
        ranked = number(item.get("ranking_value"))
        expected_rank = raw * sign if raw is not None and sign is not None else None
        if expected_rank is None or ranked != expected_rank:
            semantics_bad.append(
                {
                    "factor_id": factor_id,
                    "raw": item.get("raw_metric_value"),
                    "direction_sign": item.get("direction_sign"),
                    "ranking_value": item.get("ranking_value"),
                }
            )
    top_values = [number(item.get("ranking_value")) for item in top if isinstance(item, dict)]
    bottom_values = [number(item.get("ranking_value")) for item in bottom if isinstance(item, dict)]
    counts_ok = bool(top) and bool(bottom) and len(top) <= args["top_k"] and len(bottom) <= args["bottom_k"]
    result = {
        "http_200": call.get("http_status") == 200,
        "business_success": call.get("is_error") is False and error_code(call) is None,
        "counts_ok": counts_ok,
        "top_descending": all(value is not None for value in top_values)
        and top_values == sorted(top_values, reverse=True),
        "bottom_ascending": all(value is not None for value in bottom_values)
        and bottom_values == sorted(bottom_values),
        "unique_items": len({item.get("metric_id") for item in all_items}) == len(all_items)
        and len({item.get("factor_ref") for item in all_items}) == len(all_items),
        "returned_count_ok": payload.get("returned_count") == len(all_items),
        "validity_not_evaluated": payload.get("validity_evaluated") is False,
        "identity_mismatches": identity_bad,
        "db_value_mismatches": value_bad,
        "missing_db_factor_ids": missing_db,
        "ranking_semantic_mismatches": semantics_bad,
        "candidate_count": payload.get("candidate_count"),
        "evaluated_count": payload.get("evaluated_count"),
        "returned_count": payload.get("returned_count"),
        "top_factor_ids": [item.get("factor_id") for item in top],
        "bottom_factor_ids": [item.get("factor_id") for item in bottom],
    }
    result["pass"] = all(
        [
            result["http_200"],
            result["business_success"],
            result["counts_ok"],
            result["top_descending"],
            result["bottom_ascending"],
            result["unique_items"],
            result["returned_count_ok"],
            result["validity_not_evaluated"],
            not identity_bad,
            not value_bad,
            not missing_db,
            not semantics_bad,
        ]
    )
    return result


def main() -> None:
    """Discover small scopes, call TS/CS rank once, and write a Markdown summary."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    now = datetime.now(timezone.utc)
    as_of = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    stamp = now.astimezone().strftime("%Y%m%dT%H%M%S%z")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-rank-targeted-current"
    client = MCPClient(token, output)
    db = db_connect()
    summary: dict[str, Any] = {
        "environment": "test",
        "mcp_url": MCP_URL,
        "mode": "READ_ONLY",
        "captured_at": now.isoformat(),
        "user_agent_family": "Chrome",
        "token_sha256_prefix": hashlib.sha256(token.encode()).hexdigest()[:12],
        "as_of": as_of,
        "cases": [],
    }
    try:
        init = client.initialize()
        init_result = init.get("envelope", {}).get("result", {}) if isinstance(init.get("envelope"), dict) else {}
        summary["initialize"] = {
            "http_status": init.get("http_status"),
            "protocol_version": init_result.get("protocolVersion") if isinstance(init_result, dict) else None,
            "server_name": (init_result.get("serverInfo") or {}).get("name") if isinstance(init_result, dict) else None,
            "server_version": (init_result.get("serverInfo") or {}).get("version") if isinstance(init_result, dict) else None,
        }
        with db.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            as_of_db = now.replace(tzinfo=None)
            for scope_name in ("time_series", "cross_sectional"):
                scope = discover_scope(cursor, scope_name, as_of_db)
                case_id = f"RANK-{'TS' if scope_name == 'time_series' else 'CS'}-ONE-SHOT"
                if not scope:
                    summary["cases"].append(
                        {
                            "case_id": case_id,
                            "status": "BLOCKED",
                            "reason": "No small complete scope with formula evidence was found in the read-only DB snapshot.",
                        }
                    )
                    continue
                rows = scope_rows(cursor, scope, as_of_db)
                args = rank_arguments(scope, as_of)
                call = client.tool(case_id, "factor_rank", args)
                code = error_code(call)
                if code in {"EXPORT_BUDGET_EXCEEDED", "QUERY_TIMEOUT", "RATE_LIMITED", "SERVICE_UNAVAILABLE"}:
                    status = "BLOCKED"
                    reason = f"MCP returned infrastructure/precondition code {code}."
                    checks = {"error_code": code, "http_status": call.get("http_status")}
                elif call.get("transport_error") or call.get("parse_error"):
                    status = "BLOCKED"
                    reason = call.get("transport_error") or call.get("parse_error")
                    checks = {"transport_error": call.get("transport_error"), "parse_error": call.get("parse_error")}
                else:
                    checks = check_rank(call, args, rows)
                    status = "PASS" if checks["pass"] else "FAIL"
                    reason = "All bounded rank and DB identity checks passed." if status == "PASS" else "One or more rank/DB checks failed."
                summary["cases"].append(
                    {
                        "case_id": case_id,
                        "status": status,
                        "reason": reason,
                        "scope": {key: scope.get(key) for key in scope if key not in {"latest_completed"}},
                        "db_row_count": len(rows),
                        "db_factor_ids": [row["factor_id"] for row in rows],
                        "request_arguments": args,
                        "error_code": code,
                        "checks": checks,
                        "artifacts": sorted(path.name for path in output.glob(f"*-{case_id}.*")),
                    }
                )
            db.rollback()
    finally:
        db.close()
    statuses = [item["status"] for item in summary["cases"]]
    summary["counts"] = {status: statuses.count(status) for status in ("PASS", "FAIL", "BLOCKED")}
    write_json(output / "summary.json", summary)
    lines = [
        "# Factor Data MCP rank targeted recheck",
        "",
        f"- Captured at: `{summary['captured_at']}`",
        f"- MCP: `{MCP_URL}`",
        "- Environment: `test`; mode: `READ_ONLY`",
        "- User-Agent: standard Chrome family (exact value omitted from report)",
        f"- Token fingerprint: `{summary['token_sha256_prefix']}...` (not the token)",
        "",
        f"## Result: {summary['counts']}",
        "",
    ]
    for case in summary["cases"]:
        lines.extend(
            [
                f"### {case['case_id']}: **{case['status']}**",
                f"- {case['reason']}",
                f"- Scope: `{json.dumps(case.get('scope', {}), ensure_ascii=False, sort_keys=True)}`",
                f"- DB factors: `{case.get('db_factor_ids', [])}`; artifacts: `{case.get('artifacts', [])}`",
                f"- Checks: `{json.dumps(case.get('checks', {}), ensure_ascii=False, sort_keys=True, default=str)}`",
                "",
            ]
        )
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"output": str(output), "counts": summary["counts"], "cases": summary["cases"]}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
