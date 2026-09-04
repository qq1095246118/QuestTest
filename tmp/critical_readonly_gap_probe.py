#!/usr/bin/env python3
"""Bounded read-only security and environment regression for Factor Data MCP.

The script is intentionally independent of the business write paths.  It
discovers the current test database state, calls only read tools, and writes
credential-free evidence under ``reports/factor4-deep``.  The database
connection is placed in a read-only transaction and rolled back.
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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pymysql

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402


MCP_URL = os.environ.get(
    "MCP_URL", "https://test-factor-frontend.questvector.ai/mcp/factor-data"
)
TOKEN_ENV = "MCP_TOKEN"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
SENSITIVE_KEY = re.compile(
    r"authorization|token|password|secret|api[_-]?key|claim_token|signature|jwt|hmac",
    re.I,
)
TOKEN_TEXT = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.I)
BLOCKING_CODES = {
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE",
    "EXPORT_BUDGET_EXCEEDED",
}
LABELS = {
    "UNILATERAL_UP",
    "UNILATERAL_DOWN",
    "WIDE_RANGE",
    "NARROW_RANGE",
    "CHOPPY_UP",
    "CHOPPY_DOWN",
}


def json_default(value: Any) -> str:
    """Serialize database-native values for generated JSON evidence."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Recursively remove credentials and token-like text from evidence."""

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
        json.dumps(redact(value), ensure_ascii=False, indent=2, sort_keys=True, default=json_default)
        + "\n",
        encoding="utf-8",
    )


def parse_response(raw: bytes, content_type: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse one JSON or single-event SSE MCP response."""

    if not raw:
        return None, "EMPTY_RESPONSE"
    text = raw.decode("utf-8", "replace")
    try:
        if "text/event-stream" not in content_type.lower():
            value = json.loads(text)
            return (value, None) if isinstance(value, dict) else (None, "ROOT_NOT_OBJECT")
        events: list[dict[str, Any]] = []
        for block in re.split(r"\r?\n\r?\n", text):
            lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
            if lines:
                value = json.loads("\n".join(lines))
                if isinstance(value, dict):
                    events.append(value)
        if len(events) != 1:
            return None, f"SSE_EVENT_COUNT={len(events)}"
        return events[0], None
    except Exception as exc:  # noqa: BLE001 - preserve malformed response diagnostics
        return None, f"{type(exc).__name__}: {exc}"


def business(envelope: dict[str, Any] | None) -> dict[str, Any]:
    """Extract structured business content from an MCP envelope."""

    if not isinstance(envelope, dict):
        return {}
    result = envelope.get("result")
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
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def error_code(call: dict[str, Any] | None) -> str | None:
    """Extract a JSON-RPC or business error code from a call."""

    if not isinstance(call, dict):
        return None
    value = (call.get("business") or {}).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    value = (call.get("envelope") or {}).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    return None


def successful(call: dict[str, Any] | None) -> bool:
    """Return whether an MCP call has a successful business result."""

    if not isinstance(call, dict):
        return False
    return (
        call.get("http_status") == 200
        and call.get("parse_error") is None
        and call.get("is_error") is not True
        and error_code(call) is None
        and isinstance(call.get("business"), dict)
    )


def data(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return a call's structured data object."""

    value = (call.get("business") or {}).get("data") if isinstance(call, dict) else None
    return value if isinstance(value, dict) else {}


def rows(call: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return object rows from the common ``items`` response container."""

    value = data(call).get("items")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def meta(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return response metadata when present."""

    value = (call.get("business") or {}).get("meta") if isinstance(call, dict) else None
    return value if isinstance(value, dict) else {}


def parse_time(value: Any, *, db_value: bool = False) -> datetime | None:
    """Normalize an API or MySQL timestamp to UTC."""

    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI if db_value else timezone.utc)
    return parsed.astimezone(timezone.utc)


def scalar_equal(left: Any, right: Any) -> bool:
    """Compare nullable API/DB scalars with decimal and timestamp normalization."""

    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    left_time = parse_time(left)
    right_time = parse_time(right, db_value=isinstance(right, datetime))
    if left_time is not None and right_time is not None:
        return left_time == right_time
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


class MCPClient:
    """Minimal MCP HTTP client with sanitized evidence capture."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize a stateless client for the configured endpoint."""

        self.token = token
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.sequence = 0
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        case_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        auth_mode: str = "valid",
        timeout: float = 90,
    ) -> dict[str, Any]:
        """Send one MCP request and persist a credential-free request/response pair."""

        self.sequence += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": f"{case_id}-{uuid4()}",
            "method": method,
        }
        if method == "notifications/initialized":
            payload.pop("id", None)
        if params is not None:
            payload["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": UA,
        }
        if auth_mode == "valid":
            headers["Authorization"] = f"Bearer {self.token}"
        elif auth_mode == "wrong":
            headers["Authorization"] = "Bearer naf_mcp_invalid_probe_token"
        elif auth_mode == "malformed":
            headers["Authorization"] = "Basic invalid-probe-credentials"
        if auth_mode == "valid" and self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if auth_mode == "valid" and self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        request = urllib.request.Request(
            MCP_URL,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        status: int | None = None
        raw = b""
        response_headers: dict[str, str] = {}
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                raw = response.read()
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
        except Exception as exc:  # noqa: BLE001 - keep transport diagnostics in ledger
            call = {
                "case_id": case_id,
                "method": method,
                "auth_mode": auth_mode,
                "http_status": None,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "parse_error": f"{type(exc).__name__}: {exc}",
                "envelope": None,
                "business": {},
                "is_error": True,
            }
            self._save(case_id, payload, call, raw)
            self.calls.append(call)
            return call
        envelope, parse_error = parse_response(raw, response_headers.get("content-type", ""))
        if auth_mode == "valid" and response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        result = envelope.get("result") if isinstance(envelope, dict) else None
        call = {
            "case_id": case_id,
            "method": method,
            "auth_mode": auth_mode,
            "http_status": status,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "content_type": response_headers.get("content-type"),
            "parse_error": parse_error,
            "envelope": envelope,
            "business": business(envelope),
            "is_error": result.get("isError") if isinstance(result, dict) else None,
            "response_headers": {
                key: value
                for key, value in response_headers.items()
                if key in {"content-type", "mcp-session-id", "mcp-protocol-version", "x-request-id", "x-trace-id"}
            },
            "credential_echo": bool(TOKEN_TEXT.search(raw.decode("utf-8", "replace"))),
        }
        self._save(case_id, payload, call, raw)
        self.calls.append(call)
        return call

    def tool(self, case_id: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke one named MCP tool with arguments."""

        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self.request(case_id, "tools/call", params)

    def _save(self, case_id: str, payload: dict[str, Any], call: dict[str, Any], raw: bytes) -> None:
        """Save one sanitized transport artifact without request headers."""

        stem = f"{self.sequence:03d}-{case_id}"
        request_artifact = {"auth_mode": call.get("auth_mode"), "payload": payload}
        write_json(self.output / f"{stem}.request.json", request_artifact)
        envelope = call.get("envelope")
        if envelope is not None:
            write_json(self.output / f"{stem}.response.json", envelope)
        else:
            text = redact(raw.decode("utf-8", "replace"))
            (self.output / f"{stem}.response.txt").write_text(str(text), encoding="utf-8")


def db_snapshot() -> dict[str, Any]:
    """Read current environment tables in one explicit read-only transaction."""

    settings = SettingsLoader.load("test", ROOT).database
    connection = pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.username,
        password=settings.password,
        database=settings.name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=120,
        write_timeout=30,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            stats: dict[str, Any] = {}
            for table in (
                "market_environment_daily",
                "market_environment_eval_batch",
                "market_environment_factor_metric",
                "market_environment_factor_route",
            ):
                cursor.execute(f"SELECT COUNT(*) AS n, MAX(updated_at) AS max_updated FROM `{table}`")
                stats[table] = cursor.fetchone()
            cursor.execute(
                """SELECT id,environment_date,label_kind,label_code,label_status,revision,is_current,
                          available_at,schema_version,created_at,updated_at
                   FROM market_environment_daily
                   WHERE label_kind IN ('fact','forecast')
                   ORDER BY environment_date DESC,label_kind,revision DESC,id DESC"""
            )
            daily_rows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT id,batch_uid,market_scope,route_profile_key,label_kind,status,publish_status,
                          is_active,publication_uid,as_of_time,published_at,environment_terminal_count,
                          environment_failed_count,expected_metric_count,completed_metric_count,
                          insufficient_metric_count,failed_metric_count,active_scope_key,updated_at
                   FROM market_environment_eval_batch ORDER BY id DESC"""
            )
            batches = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT id,factor_ref,factor_type,factor_id,factor_version,label_code,evaluation_type,
                          eval_batch_id,market_scope,metric_status,is_valid,time_series_score,
                          cross_sectional_score,routing_score,confidence,scoring_version,updated_at
                   FROM market_environment_factor_metric
                   WHERE eval_batch_id=(SELECT id FROM market_environment_eval_batch WHERE is_active=1 ORDER BY id DESC LIMIT 1)
                     AND factor_ref IN (
                         SELECT DISTINCT factor_ref FROM market_environment_factor_route WHERE is_active=1
                     )
                   ORDER BY factor_ref,label_code,evaluation_type"""
            )
            metrics = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT id,factor_ref,factor_type,factor_id,label_code,rank_no,routing_score,
                          time_series_score,cross_sectional_score,is_eligible,publication_uid,
                          eval_batch_id,market_scope,environment_date,as_of_time,publish_version
                   FROM market_environment_factor_route
                   WHERE is_active=1 ORDER BY label_code,rank_no,id"""
            )
            routes = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT label_code,COUNT(*) AS n
                   FROM market_environment_factor_route WHERE is_active=1 GROUP BY label_code"""
            )
            route_counts = {str(row["label_code"]): int(row["n"]) for row in cursor.fetchall()}
            cursor.execute(
                """SELECT COUNT(*) AS n FROM market_environment_daily
                   WHERE label_kind='fact' AND revision > 1"""
            )
            fact_revision_count = int(cursor.fetchone()["n"])
            cursor.execute(
                """SELECT COUNT(*) AS n FROM market_environment_daily
                   WHERE label_kind='forecast' AND revision > 1"""
            )
            forecast_revision_count = int(cursor.fetchone()["n"])
            published = [
                row
                for row in batches
                if row.get("publish_status") == "published" and row.get("published_at") is not None
            ]
            return {
                "db_name": settings.name,
                "table_stats": stats,
                "daily_rows": daily_rows,
                "batches": batches,
                "published_batches": published,
                "metrics": metrics,
                "routes": routes,
                "route_counts": route_counts,
                "revision_counts": {"fact_gt1": fact_revision_count, "forecast_gt1": forecast_revision_count},
            }
    finally:
        connection.rollback()
        connection.close()


def compact_db(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a small non-sensitive database summary for the report."""

    def compact_batch(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in (
                "id",
                "batch_uid",
                "market_scope",
                "route_profile_key",
                "label_kind",
                "status",
                "publish_status",
                "is_active",
                "publication_uid",
                "as_of_time",
                "published_at",
                "environment_terminal_count",
                "environment_failed_count",
                "expected_metric_count",
                "completed_metric_count",
                "insufficient_metric_count",
                "failed_metric_count",
                "active_scope_key",
            )
        }

    return {
        "db_name": snapshot.get("db_name"),
        "table_stats": snapshot.get("table_stats"),
        "batches": [compact_batch(row) for row in snapshot.get("batches", [])],
        "route_counts": snapshot.get("route_counts"),
        "revision_counts": snapshot.get("revision_counts"),
        "daily_bounds": {
            kind: {
                "count": sum(1 for row in snapshot.get("daily_rows", []) if row.get("label_kind") == kind),
                "min_date": min(
                    (str(row["environment_date"]) for row in snapshot.get("daily_rows", []) if row.get("label_kind") == kind),
                    default=None,
                ),
                "max_date": max(
                    (str(row["environment_date"]) for row in snapshot.get("daily_rows", []) if row.get("label_kind") == kind),
                    default=None,
                ),
            }
            for kind in ("fact", "forecast")
        },
    }


def record(
    cases: list[dict[str, Any]],
    case_id: str,
    module: str,
    status: str,
    expected: str,
    actual: Any,
    call: dict[str, Any] | None = None,
    *,
    note: str = "",
    duplicate_of: str | None = None,
) -> None:
    """Append one sanitized case verdict."""

    cases.append(
        {
            "case_id": case_id,
            "module": module,
            "status": status,
            "expected": expected,
            "actual": actual,
            "http_status": call.get("http_status") if call else None,
            "error_code": error_code(call),
            "note": note,
            "duplicate_of": duplicate_of,
            "artifact_prefix": case_id,
        }
    )


def reject(call: dict[str, Any] | None) -> bool:
    """Return whether a call was rejected at transport or business level."""

    if not isinstance(call, dict):
        return True
    return bool(
        (call.get("http_status") or 0) >= 400
        or call.get("parse_error")
        or call.get("is_error") is True
        or error_code(call) is not None
    )


def response_identity(call: dict[str, Any] | None) -> dict[str, Any]:
    """Extract compact environment identity fields from a response."""

    payload = data(call)
    publication = payload.get("publication") if isinstance(payload.get("publication"), dict) else None
    batch = payload.get("batch") if isinstance(payload.get("batch"), dict) else None
    forecast = payload.get("forecast") if isinstance(payload.get("forecast"), dict) else None
    return {
        "status": payload.get("status"),
        "reason_code": payload.get("reason_code"),
        "forecast_id": forecast.get("id") if forecast else None,
        "forecast_date": forecast.get("environment_date") if forecast else None,
        "publication_id": publication.get("id") if publication else None,
        "publication_uid": publication.get("publication_uid") if publication else None,
        "publication_batch_uid": publication.get("batch_uid") if publication else None,
        "publication_as_of_time": publication.get("as_of_time") if publication else None,
        "publication_published_at": publication.get("published_at") if publication else None,
        "batch_id": batch.get("id") if batch else None,
        "batch_uid": batch.get("batch_uid") if batch else None,
        "item_count": len(rows(call)),
        "returned_count": payload.get("returned_count"),
    }


def main() -> None:
    """Run security, environment, and database-consistency checks."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("FACTOR4_MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} or FACTOR4_MCP_TOKEN is required")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-critical-readonly-gap-probe"
    output.mkdir(parents=True, exist_ok=False)
    cases: list[dict[str, Any]] = []
    db_before = db_snapshot()
    write_json(output / "db-before.json", compact_db(db_before))
    client = MCPClient(token, output)

    # Authentication negative controls use a separate stateless request and
    # intentionally do not persist authorization headers.
    for mode, case_id in (
        ("none", "AUTH-NONE-INIT"),
        ("wrong", "AUTH-WRONG-INIT"),
        ("malformed", "AUTH-MALFORMED-INIT"),
    ):
        call = client.request(
            case_id,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "QuestTest-critical-readonly", "version": "1.0"},
            },
            auth_mode=mode,
        )
        record(
            cases,
            case_id,
            "authentication",
            "PASS" if reject(call) and not call.get("credential_echo") else "FAIL",
            "missing, invalid, and malformed credentials are rejected without echoing a token",
            {"rejected": reject(call), "credential_echo": bool(call.get("credential_echo")), "status": call.get("http_status")},
            call,
        )
    no_auth_call = client.request(
        "AUTH-NONE-CALL",
        "tools/call",
        {"name": "factor_catalog_stats", "arguments": {}},
        auth_mode="none",
    )
    record(
        cases,
        "AUTH-NONE-CALL",
        "authentication",
        "PASS" if reject(no_auth_call) else "FAIL",
        "an unauthenticated business tool call cannot bypass authentication",
        {"rejected": reject(no_auth_call), "status": no_auth_call.get("http_status")},
        no_auth_call,
    )

    init = client.request(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-critical-readonly", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    client.protocol_version = str(init_result.get("protocolVersion") or "") or None
    ready = successful(init) and client.protocol_version is not None
    record(
        cases,
        "MCP-INIT",
        "protocol",
        "PASS" if ready else "BLOCKED",
        "valid test token negotiates a protocol version",
        {"protocol_version": client.protocol_version, "server": init_result.get("serverInfo")},
        init,
        note="All authenticated checks are blocked if valid-token initialization fails.",
    )
    if not ready:
        db_after = db_snapshot()
        write_json(output / "db-after.json", compact_db(db_after))
        write_json(output / "summary.json", {"status": "BLOCKED", "cases": cases, "db_unchanged": db_before["table_stats"] == db_after["table_stats"]})
        print(json.dumps({"output_dir": str(output), "status": "BLOCKED"}, ensure_ascii=False))
        return
    notify = client.request("MCP-NOTIFY", "notifications/initialized", {})
    record(cases, "MCP-NOTIFY", "protocol", "PASS" if notify.get("http_status") in {200, 202, 204} else "FAIL", "initialized notification is accepted", {"status": notify.get("http_status")}, notify)
    tools_call = client.request("MCP-TOOLS", "tools/list", {})
    tool_rows = (((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or [])
    tool_names = {row.get("name") for row in tool_rows if isinstance(row, dict)}
    required = {"environment_get_daily", "environment_get_recommendations", "factor_get_environment_metrics", "factor_get_environment_tags"}
    record(cases, "MCP-TOOLS", "protocol", "PASS" if successful(tools_call) and required <= tool_names else "FAIL", "environment read tools are listed", {"listed": sorted(required & tool_names), "missing": sorted(required - tool_names)}, tools_call)
    write_json(output / "tool-schemas.json", [row for row in tool_rows if isinstance(row, dict) and row.get("name") in required])

    # Dynamic database facts used as the Oracle.
    daily_rows = db_before["daily_rows"]
    latest_by_kind: dict[str, dict[str, Any]] = {}
    for kind in ("fact", "forecast"):
        candidates = [row for row in daily_rows if row.get("label_kind") == kind]
        if candidates:
            latest_by_kind[kind] = candidates[0]
    active_batch = next((row for row in db_before["batches"] if row.get("is_active") == 1), None)
    route_seed = db_before["routes"][0] if db_before["routes"] else None
    metric_seed = None
    if route_seed:
        metric_seed = next((row for row in db_before["metrics"] if row.get("factor_ref") == route_seed.get("factor_ref") and row.get("label_code") == route_seed.get("label_code") and row.get("evaluation_type") == "time_series"), None)
    if metric_seed is None and db_before["metrics"]:
        metric_seed = db_before["metrics"][0]

    # Daily current/date-filter and schema/value checks.
    for kind in ("fact", "forecast"):
        target = latest_by_kind.get(kind)
        if not target:
            record(cases, f"DAILY-{kind.upper()}-LATEST", "environment.daily", "BLOCKED", "a current daily row exists", "no database row")
            continue
        call = client.tool(
            f"DAILY-{kind.upper()}-LATEST",
            "environment_get_daily",
            {"label_kind": kind, "environment_date": str(target["environment_date"]), "limit": 10},
        )
        api_rows = rows(call)
        hit = next((row for row in api_rows if str(row.get("id")) == str(target.get("id"))), None)
        keys = ("id", "environment_date", "label_kind", "label_code", "label_status", "revision", "is_current", "schema_version")
        identity_ok = successful(call) and hit is not None and all(scalar_equal(hit.get(key), target.get(key)) for key in keys)
        record(cases, f"DAILY-{kind.upper()}-LATEST", "environment.daily", "PASS" if identity_ok else "FAIL", "latest date filter returns the same current DB revision and identity", {"target_id": target.get("id"), "returned_ids": [row.get("id") for row in api_rows], "identity_match": identity_ok}, call)
        if kind == "forecast" and hit:
            probabilities = hit.get("probabilities")
            values = []
            if isinstance(probabilities, dict):
                values = [Decimal(str(probabilities.get(label))) for label in LABELS if probabilities.get(label) is not None]
            probability_ok = isinstance(probabilities, dict) and set(probabilities) == LABELS and len(values) == 6 and all(Decimal(0) <= value <= Decimal(1) for value in values) and abs(sum(values) - Decimal(1)) <= Decimal("0.000001")
            record(cases, "DAILY-FORECAST-PROBABILITIES", "environment.daily", "PASS" if probability_ok else "FAIL", "ready forecast exposes exactly six canonical probabilities summing to one", {"keys": sorted(probabilities) if isinstance(probabilities, dict) else None, "sum": str(sum(values)) if values else None}, call)

    fact_page = client.tool("DAILY-FACT-PAGE-1", "environment_get_daily", {"label_kind": "fact", "limit": 2})
    fact_page_rows = rows(fact_page)
    cursor = meta(fact_page).get("next_cursor")
    page2 = client.tool("DAILY-FACT-PAGE-2", "environment_get_daily", {"label_kind": "fact", "limit": 2, "cursor": cursor}) if cursor else None
    page2_rows = rows(page2)
    pagination_ok = successful(fact_page) and len(fact_page_rows) <= 2 and (not cursor or (page2 is not None and successful(page2) and not {row.get("id") for row in fact_page_rows} & {row.get("id") for row in page2_rows}))
    record(cases, "DAILY-PAGINATION", "environment.daily", "PASS" if pagination_ok else "FAIL", "daily limit is honored and signed cursor continuation is non-overlapping", {"page1_count": len(fact_page_rows), "page2_count": len(page2_rows), "cursor_present": bool(cursor)}, fact_page)

    latest_forecast = latest_by_kind.get("forecast")
    if latest_forecast:
        available = parse_time(latest_forecast.get("available_at"), db_value=True)
        if available:
            before = client.tool("DAILY-FORECAST-BEFORE-AVAILABLE", "environment_get_daily", {"label_kind": "forecast", "as_of": (available - timedelta(microseconds=1)).isoformat(), "limit": 100})
            equal = client.tool("DAILY-FORECAST-AT-AVAILABLE", "environment_get_daily", {"label_kind": "forecast", "as_of": available.isoformat(), "limit": 100})
            before_ids = {row.get("id") for row in rows(before)}
            equal_ids = {row.get("id") for row in rows(equal)}
            boundary_ok = successful(before) and successful(equal) and latest_forecast.get("id") not in before_ids and latest_forecast.get("id") in equal_ids
            record(cases, "DAILY-PIT-AVAILABILITY", "environment.daily", "PASS" if boundary_ok else "FAIL", "available_at boundary is point-in-time inclusive and does not expose future rows", {"target_id": latest_forecast.get("id"), "before_has_target": latest_forecast.get("id") in before_ids, "equal_has_target": latest_forecast.get("id") in equal_ids}, equal)
    for limit, case_id in ((0, "DAILY-LIMIT-ZERO"), (1001, "DAILY-LIMIT-OVER-MAX")):
        call = client.tool(case_id, "environment_get_daily", {"label_kind": "fact", "limit": limit})
        record(cases, case_id, "environment.daily.validation", "PASS" if reject(call) else "FAIL", "out-of-range limit is rejected", {"rejected": reject(call), "status": call.get("http_status")}, call)

    # Recommendation current, historical PIT, unknown scope, and route count.
    if active_batch:
        current_rec = client.tool("REC-CURRENT", "environment_get_recommendations", {"market_scope": active_batch["market_scope"], "route_profile_key": active_batch["route_profile_key"], "limit": 20})
        current_identity = response_identity(current_rec)
        record(cases, "REC-CURRENT", "environment.recommendations", "PASS" if successful(current_rec) and current_identity["item_count"] <= 20 else "FAIL", "current recommendation returns a bounded, typed result", current_identity, current_rec)
        pub_time = parse_time(active_batch.get("published_at"), db_value=True)
        if pub_time:
            before_time = pub_time - timedelta(seconds=1)
            historical = client.tool("REC-PIT-BEFORE-PUBLISHED", "environment_get_recommendations", {"market_scope": active_batch["market_scope"], "route_profile_key": active_batch["route_profile_key"], "as_of": before_time.isoformat(), "limit": 20})
            hist_identity = response_identity(historical)
            visible_pub = hist_identity["publication_uid"] or hist_identity["publication_id"]
            expected_prior = [row for row in db_before["published_batches"] if parse_time(row.get("published_at"), db_value=True) and parse_time(row.get("published_at"), db_value=True) <= before_time]
            expected_uid = expected_prior[-1].get("publication_uid") if expected_prior else None
            pit_ok = successful(historical) and visible_pub == expected_uid and (hist_identity["publication_published_at"] is None or parse_time(hist_identity["publication_published_at"]) <= before_time)
            record(cases, "REC-PIT-BEFORE-PUBLISHED", "environment.recommendations", "PASS" if pit_ok else "FAIL", "historical as_of cannot expose a publication published later than that instant", {**hist_identity, "expected_publication_uid": expected_uid, "requested_as_of": before_time.isoformat()}, historical, note="This is a recheck of the previously reported historical publication leak; do not count as a new defect.", duplicate_of="OPEN-REC-PIT")
        unknown = client.tool("REC-UNKNOWN-SCOPE", "environment_get_recommendations", {"market_scope": "__questtest_unknown_scope__", "route_profile_key": active_batch["route_profile_key"], "limit": 1})
        unknown_data = data(unknown)
        unknown_ok = successful(unknown) and unknown_data.get("publication") is None and not rows(unknown) and unknown_data.get("returned_count") == 0
        record(cases, "REC-UNKNOWN-SCOPE", "environment.recommendations", "PASS" if unknown_ok else "FAIL", "unknown market scope returns explicit no-publication/no-items state", response_identity(unknown), unknown)
        env_status = (data(current_rec).get("publication") or {}).get("environment_status") if isinstance(data(current_rec).get("publication"), dict) else None
        if isinstance(env_status, dict):
            mismatches = {label: {"api": (env_status.get(label) or {}).get("route_count"), "db": db_before["route_counts"].get(label, 0)} for label in set(env_status) | set(db_before["route_counts"]) if (env_status.get(label) or {}).get("route_count") != db_before["route_counts"].get(label, 0)}
            route_count_ok = not mismatches
            record(cases, "REC-ROUTE-COUNT-RECON", "environment.recommendations", "PASS" if route_count_ok else "FAIL", "publication environment_status.route_count equals active route rows by label", {"mismatches": mismatches, "api_labels": sorted(env_status), "db_counts": db_before["route_counts"]}, current_rec, note="Known publication route_count defect; this run only confirms whether it persists.", duplicate_of="OPEN-ROUTE-COUNT")

    # Metrics/tags use a dynamically selected active route and compare the
    # returned identity matrix to the database rows for that factor.
    if active_batch and metric_seed:
        factor_ref = str(metric_seed["factor_ref"])
        metric_args = {"factor_ref": factor_ref, "market_scope": active_batch["market_scope"], "route_profile_key": active_batch["route_profile_key"], "limit": 100}
        active_metrics = client.tool("ENV-METRICS-ACTIVE", "factor_get_environment_metrics", metric_args)
        explicit_metrics = client.tool("ENV-METRICS-EXPLICIT", "factor_get_environment_metrics", {**metric_args, "batch_uid": active_batch["batch_uid"]})
        expected_metrics = [row for row in db_before["metrics"] if row.get("factor_ref") == factor_ref]
        def metric_identity_ok(call: dict[str, Any]) -> bool:
            """Check the selected factor's metric rows against DB identity fields."""
            api = rows(call)
            if not successful(call) or not api or not all(str(item.get("factor_ref")) == factor_ref for item in api):
                return False
            expected_keys = {(str(row.get("label_code")), str(row.get("evaluation_type"))) for row in expected_metrics}
            actual_keys = {(str(item.get("label_code")), str(item.get("evaluation_type"))) for item in api}
            return actual_keys == expected_keys and all(str(item.get("eval_batch_id")) == str(active_batch.get("id")) for item in api)
        for case_id, call in (("ENV-METRICS-ACTIVE", active_metrics), ("ENV-METRICS-EXPLICIT", explicit_metrics)):
            identity = {"factor_ref": factor_ref, "batch_id": data(call).get("batch", {}).get("id") if isinstance(data(call).get("batch"), dict) else None, "batch_uid": data(call).get("batch", {}).get("batch_uid") if isinstance(data(call).get("batch"), dict) else None, "item_count": len(rows(call)), "expected_item_count": len(expected_metrics)}
            ok = metric_identity_ok(call) and identity["batch_uid"] == active_batch["batch_uid"]
            record(cases, case_id, "environment.metrics", "PASS" if ok else "FAIL", "metrics are complete for one factor and bound to the active batch", identity, call)
        label = str(metric_seed.get("label_code"))
        eval_type = str(metric_seed.get("evaluation_type"))
        filtered = client.tool("ENV-METRICS-FILTER", "factor_get_environment_metrics", {**metric_args, "batch_uid": active_batch["batch_uid"], "label_code": label, "evaluation_type": eval_type, "limit": 10})
        filter_ok = successful(filtered) and len(rows(filtered)) == 1 and all(item.get("label_code") == label and item.get("evaluation_type") == eval_type for item in rows(filtered))
        record(cases, "ENV-METRICS-FILTER", "environment.metrics", "PASS" if filter_ok else "FAIL", "label and evaluation filters return exactly the requested metric", {"label": label, "evaluation_type": eval_type, "returned": len(rows(filtered))}, filtered)
        tags = client.tool("ENV-TAGS", "factor_get_environment_tags", {"factor_ref": factor_ref, "market_scope": active_batch["market_scope"], "route_profile_key": active_batch["route_profile_key"]})
        tag_rows = rows(tags)
        route = next((row for row in db_before["routes"] if row.get("factor_ref") == factor_ref), None)
        tag_hit = next((row for row in tag_rows if row.get("factor_ref") == factor_ref), None)
        tag_ok = successful(tags) and route is not None and tag_hit is not None and all(scalar_equal(tag_hit.get(key), route.get(key)) for key in ("id", "publication_uid", "eval_batch_id", "label_code", "rank_no", "routing_score", "is_eligible", "publish_version"))
        record(cases, "ENV-TAGS", "environment.tags", "PASS" if tag_ok else "FAIL", "tags preserve active route identity, rank, score, and publication", {"factor_ref": factor_ref, "route_id": route.get("id") if route else None, "tag_id": tag_hit.get("id") if tag_hit else None, "returned_count": len(tag_rows)}, tags)
        invalid_batch = client.tool("ENV-METRICS-UNKNOWN-BATCH", "factor_get_environment_metrics", {**metric_args, "batch_uid": "00000000-0000-0000-0000-000000000000"})
        fallback_leak = bool(rows(invalid_batch)) and any(str(item.get("eval_batch_id")) == str(active_batch["id"]) for item in rows(invalid_batch))
        invalid_ok = (reject(invalid_batch) or not rows(invalid_batch)) and not fallback_leak
        record(cases, "ENV-METRICS-UNKNOWN-BATCH", "environment.metrics", "PASS" if invalid_ok else "FAIL", "unknown explicit batch cannot silently fall back to active metrics", {"rejected": reject(invalid_batch), "returned_count": len(rows(invalid_batch)), "fallback_leak": fallback_leak}, invalid_batch)
    else:
        record(cases, "ENV-METRICS", "environment.metrics", "BLOCKED", "an active metric and batch are available", "no dynamic metric seed")

    db_after = db_snapshot()
    write_json(output / "db-after.json", compact_db(db_after))
    db_unchanged = db_before["table_stats"] == db_after["table_stats"]
    record(cases, "DB-READONLY-SNAPSHOT", "database", "PASS" if db_unchanged else "FAIL", "read-only MCP run leaves environment table counts and update watermarks unchanged", {"unchanged": db_unchanged, "before": db_before["table_stats"], "after": db_after["table_stats"]})
    counts = {status: sum(1 for case in cases if case["status"] == status) for status in ("PASS", "FAIL", "BLOCKED")}
    summary = {
        "run_id": stamp,
        "environment": "test",
        "mode": "READ_ONLY",
        "mcp_url": MCP_URL,
        "counts": counts,
        "cases": cases,
        "db_unchanged": db_unchanged,
        "db_summary": compact_db(db_before),
        "known_defect_rechecks": [case["case_id"] for case in cases if case.get("duplicate_of")],
        "security_note": "Authorization headers and tokens are never persisted; response token leak is checked before redaction.",
    }
    write_json(output / "summary.json", summary)
    lines = [
        "# Critical read-only gap probe",
        "",
        f"- Environment: test MCP + test `factor_db`",
        f"- Counts: PASS={counts['PASS']} / FAIL={counts['FAIL']} / BLOCKED={counts['BLOCKED']}",
        f"- DB unchanged: `{db_unchanged}`",
        "",
        "| Case | Module | Status | Actual | Note |",
        "|---|---|---|---|---|",
    ]
    for case in cases:
        actual = json.dumps(
            redact(case.get("actual")),
            ensure_ascii=False,
            separators=(",", ":"),
            default=json_default,
        )
        note = case.get("note") or (f"duplicate_of={case['duplicate_of']}" if case.get("duplicate_of") else "")
        lines.append(f"| {case['case_id']} | {case['module']} | {case['status']} | `{actual}` | {note} |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "counts": counts, "db_unchanged": db_unchanged}, ensure_ascii=False))


if __name__ == "__main__":
    main()
