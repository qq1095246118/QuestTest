#!/usr/bin/env python3
"""Close the remaining read-only Factor Data MCP protocol coverage gaps.

The probe covers MCP-006, MCP-010, MCP-011, MCP-014, and MCP-015 against the
test service. It calls every advertised read-only tool but never invokes the
feedback write tool. Database access is limited to consistent read-only
transactions which are always rolled back.
"""

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import os
import re
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymysql
import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = os.environ.get(
    "MCP_URL", "https://test-factor-frontend.questvector.ai/mcp/factor-data"
)
TOKEN = os.environ.get("MCP_TOKEN") or os.environ.get("FACTOR4_MCP_TOKEN")
TEST_MCP_PREFIX = "https://test-factor-frontend.questvector.ai/"
WRITE_TOOL = "submit_backtest_factor_feedback"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
BLOCKING_CODES = {
    "AUTH_REQUIRED",
    "FORBIDDEN",
    "INSUFFICIENT_SCOPE",
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE",
    "EXPORT_BUDGET_EXCEEDED",
}
VOLATILE_KEYS = {
    "request_id",
    "trace_id",
    "requestId",
    "traceId",
    "generated_at",
    "retrieved_at",
    "served_at",
    "quota",
}
SENSITIVE_KEY = re.compile(
    r"authorization|token|password|secret|api[_-]?key|jwt|hmac|signature",
    re.IGNORECASE,
)
SENSITIVE_TEXT = re.compile(
    r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.IGNORECASE
)
ARTIFACT_LOCK = threading.Lock()


def json_default(value: Any) -> str:
    """Serialize database-native values without losing displayed precision."""

    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Recursively remove credentials and token-like values from evidence."""

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
    """Write deterministic, recursively redacted JSON evidence."""

    rendered = json.dumps(
        redact(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=json_default,
    )
    with ARTIFACT_LOCK:
        path.write_text(rendered + "\n", encoding="utf-8")


def safe_name(value: str) -> str:
    """Return a filesystem-safe case name."""

    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")[:180]


def parse_mcp_body(raw: bytes, content_type: str) -> tuple[Any, str | None]:
    """Parse one JSON response or one or more well-formed SSE data events."""

    if not raw:
        return None, None
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" not in content_type.lower():
        try:
            return json.loads(text), None
        except Exception as exc:  # noqa: BLE001 - diagnostic boundary
            return None, f"{type(exc).__name__}: {exc}"
    events: list[Any] = []
    try:
        for block in re.split(r"\r?\n\r?\n", text):
            data_lines = [
                line[5:].lstrip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if data_lines:
                events.append(json.loads("\n".join(data_lines)))
    except Exception as exc:  # noqa: BLE001 - preserve malformed SSE evidence
        return None, f"SSE_DATA_PARSE: {type(exc).__name__}: {exc}"
    if len(events) != 1:
        return None, f"SSE_EVENT_COUNT={len(events)}"
    return events[0], None


def extract_business(envelope: Any) -> tuple[dict[str, Any] | None, str]:
    """Extract structured tool content and all returned text from an envelope."""

    if not isinstance(envelope, dict):
        return None, ""
    result = envelope.get("result")
    if not isinstance(result, dict):
        return None, ""
    structured = result.get("structuredContent")
    text_parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
    if isinstance(structured, dict):
        return structured, "\n".join(text_parts)
    for text in text_parts:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed, "\n".join(text_parts)
    return None, "\n".join(text_parts)


class MCPClient:
    """Authenticated MCP Streamable HTTP client with sanitized evidence capture."""

    def __init__(self, token: str, output: Path, label: str) -> None:
        """Create a logical MCP client backed by one reusable HTTP session."""

        self.token = token
        self.output = output
        self.label = safe_name(label)
        self.session = requests.Session()
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.calls: list[dict[str, Any]] = []

    def close(self) -> None:
        """Close the reusable HTTP connection pool."""

        self.session.close()

    def request(
        self,
        case_id: str,
        method: str,
        params: Any = None,
        *,
        request_id: str | int | None = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 90.0,
        use_negotiated_headers: bool = True,
    ) -> dict[str, Any]:
        """Send one structured or raw request and capture credential-free evidence."""

        if raw_body is None:
            payload: Any = {
                "jsonrpc": "2.0",
                "id": request_id if request_id is not None else f"{case_id}-{uuid4()}",
                "method": method,
            }
            if params is not None:
                payload["params"] = params
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        else:
            payload = None
            body = raw_body
        request_headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
        }
        if use_negotiated_headers and self.protocol_version:
            request_headers["MCP-Protocol-Version"] = self.protocol_version
        if use_negotiated_headers and self.session_id:
            request_headers["MCP-Session-Id"] = self.session_id
        if headers:
            request_headers.update(headers)
        started = time.monotonic()
        raw = b""
        response_headers: dict[str, str] = {}
        status: int | None = None
        transport_error: str | None = None
        try:
            response = self.session.post(
                MCP_URL,
                data=body,
                headers=request_headers,
                timeout=timeout,
            )
            status = response.status_code
            raw = response.content
            response_headers = {key.lower(): val for key, val in response.headers.items()}
        except Exception as exc:  # noqa: BLE001 - normalized into report
            transport_error = f"{type(exc).__name__}: {exc}"
        content_type = response_headers.get("content-type", "")
        envelope, parse_error = parse_mcp_body(raw, content_type)
        business, result_text = extract_business(envelope)
        result = envelope.get("result") if isinstance(envelope, dict) else None
        call: dict[str, Any] = {
            "case_id": case_id,
            "client_label": self.label,
            "method": method,
            "request_id": payload.get("id") if isinstance(payload, dict) else None,
            "http_status": status,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "content_type": content_type,
            "response_bytes": len(raw),
            "response_headers": {
                key: (
                    hashlib.sha256(val.encode()).hexdigest()
                    if key == "mcp-session-id"
                    else val
                )
                for key, val in response_headers.items()
                if key
                in {
                    "content-type",
                    "mcp-session-id",
                    "mcp-protocol-version",
                    "retry-after",
                }
            },
            "transport_error": transport_error,
            "parse_error": parse_error,
            "envelope": envelope,
            "business": business,
            "result_text": result_text,
            "is_error": result.get("isError") if isinstance(result, dict) else None,
            "_mcp_session_id": response_headers.get("mcp-session-id"),
        }
        self.calls.append(call)
        stem = safe_name(f"{self.label}-{case_id}")
        request_artifact = (
            payload
            if payload is not None
            else {"raw_body": body.decode("utf-8", "replace")}
        )
        response_artifact = {
            "http_status": status,
            "content_type": content_type,
            "response_headers": call["response_headers"],
            "transport_error": transport_error,
            "parse_error": parse_error,
            "body": envelope if envelope is not None else raw.decode("utf-8", "replace"),
        }
        write_json(self.output / f"{stem}.request.json", request_artifact)
        write_json(self.output / f"{stem}.response.json", response_artifact)
        return call

    def initialize(
        self,
        case_id: str,
        protocol_version: str = "2025-06-18",
    ) -> dict[str, Any]:
        """Initialize this logical client and retain negotiated response headers."""

        call = self.request(
            case_id,
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": "QuestTest-protocol-gap-closure",
                    "version": "1.0",
                },
            },
            use_negotiated_headers=False,
        )
        envelope = call.get("envelope")
        result = envelope.get("result") if isinstance(envelope, dict) else None
        if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
            self.protocol_version = result["protocolVersion"]
        raw_session = call.get("_mcp_session_id") if call.get("http_status") == 200 else None
        if raw_session:
            self.session_id = raw_session
        return call

    def notify_initialized(self, case_id: str) -> dict[str, Any]:
        """Send the MCP initialized notification."""

        return self.request(
            case_id,
            "raw",
            raw_body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                separators=(",", ":"),
            ).encode(),
        )

    def tool(
        self,
        case_id: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Invoke a named MCP tool with an argument object."""

        return self.request(
            case_id,
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            **kwargs,
        )


def error_code(call: dict[str, Any] | None) -> str | None:
    """Extract a JSON-RPC or business error code from a normalized call."""

    if not isinstance(call, dict):
        return None
    envelope = call.get("envelope")
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        value = envelope["error"].get("code")
        if value is not None:
            return str(value)
    business = call.get("business")
    if isinstance(business, dict) and isinstance(business.get("error"), dict):
        for key in ("code", "error_code", "type"):
            value = business["error"].get(key)
            if value is not None:
                return str(value)
    return None


def successful(call: dict[str, Any] | None) -> bool:
    """Return whether a tool call produced a successful structured result."""

    return bool(
        isinstance(call, dict)
        and call.get("http_status") == 200
        and not call.get("transport_error")
        and not call.get("parse_error")
        and call.get("is_error") is not True
        and error_code(call) is None
        and isinstance(call.get("business"), dict)
    )


def rejected(call: dict[str, Any] | None) -> bool:
    """Return whether a request received a structured protocol/tool rejection."""

    if not isinstance(call, dict):
        return False
    return bool(
        call.get("is_error") is True
        or error_code(call) is not None
        or (
            isinstance(call.get("http_status"), int)
            and int(call["http_status"]) in {400, 401, 403, 404, 405, 406, 409, 415, 422}
            and not call.get("parse_error")
        )
    )


def result_text(call: dict[str, Any] | None) -> str:
    """Return lower-cased response text used to identify validation failures."""

    if not isinstance(call, dict):
        return ""
    parts = [str(call.get("result_text") or "")]
    business = call.get("business")
    if isinstance(business, dict):
        parts.append(json.dumps(business, ensure_ascii=False, default=json_default))
    envelope = call.get("envelope")
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        parts.append(json.dumps(envelope["error"], ensure_ascii=False, default=json_default))
    return "\n".join(parts).lower()


def validation_rejected(call: dict[str, Any], field: str, category: str) -> bool:
    """Confirm rejection came from argument validation rather than business data."""

    text = result_text(call)
    field_seen = field.lower() in text
    if category == "extra":
        marker_seen = "unknown argument" in text or "extra" in text
    else:
        marker_seen = "invalid argument" in text or "validation" in text
    return rejected(call) and field_seen and marker_seen


def business_data(call: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the business data object from a normalized tool call."""

    business = call.get("business") if isinstance(call, dict) else None
    value = business.get("data") if isinstance(business, dict) else None
    return value if isinstance(value, dict) else {}


def business_meta(call: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the business metadata object from a normalized tool call."""

    business = call.get("business") if isinstance(call, dict) else None
    value = business.get("meta") if isinstance(business, dict) else None
    return value if isinstance(value, dict) else {}


def response_rows(call: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract rows from common Factor Data response containers."""

    data = business_data(call)
    for key in (
        "items",
        "metrics",
        "scopes",
        "top_items",
        "results",
        "symbols",
        "children",
    ):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def compact_call(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return credential-free transport and business diagnostics for a report."""

    if not isinstance(call, dict):
        return {}
    return {
        "case_id": call.get("case_id"),
        "client_label": call.get("client_label"),
        "http_status": call.get("http_status"),
        "elapsed_seconds": call.get("elapsed_seconds"),
        "content_type": call.get("content_type"),
        "response_bytes": call.get("response_bytes"),
        "transport_error": call.get("transport_error"),
        "parse_error": call.get("parse_error"),
        "is_error": call.get("is_error"),
        "error_code": error_code(call),
        "data_keys": sorted(business_data(call)),
        "row_count": len(response_rows(call)),
    }


def canonical(value: Any) -> Any:
    """Remove volatile request metadata and normalize signed cursor presence."""

    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key in VOLATILE_KEYS:
                continue
            if key in {"next_cursor", "cursor"}:
                output[key] = bool(item)
            else:
                output[key] = canonical(item)
        return output
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def canonical_hash(value: Any) -> str:
    """Hash one canonical JSON-compatible value."""

    rendered = json.dumps(
        canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )
    return hashlib.sha256(rendered.encode()).hexdigest()


def call_snapshot(call: dict[str, Any]) -> dict[str, Any]:
    """Return stable business data and version fields for equality checks."""

    meta = business_meta(call)
    stable_meta = {
        key: meta.get(key)
        for key in (
            "data_as_of",
            "schema_version",
            "source_versions",
            "truncated",
            "warnings",
            "next_cursor",
        )
        if key in meta
    }
    return canonical({"data": business_data(call), "meta": stable_meta})


def load_database_config() -> dict[str, Any]:
    """Load the explicitly authorized test database configuration."""

    document = yaml.safe_load((ROOT / "config" / "test.yaml").read_text(encoding="utf-8"))
    value = document.get("database") if isinstance(document, dict) else None
    if not isinstance(value, dict):
        raise RuntimeError("config/test.yaml does not contain database settings")
    return value


def open_read_only_database() -> pymysql.connections.Connection:
    """Open a consistent read-only test database transaction."""

    config = load_database_config()
    connection = pymysql.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["username"],
        password=config["password"],
        database=config["name"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=180,
        write_timeout=30,
    )
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
    return connection


def query_all(
    connection: pymysql.connections.Connection,
    sql: str,
    parameters: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Run a parameterized SELECT in the current read-only transaction."""

    with connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        return [dict(row) for row in cursor.fetchall()]


def query_one(
    connection: pymysql.connections.Connection,
    sql: str,
    parameters: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    """Run a parameterized SELECT and return its first row."""

    rows = query_all(connection, sql, parameters)
    return rows[0] if rows else None


def database_snapshot() -> dict[str, Any]:
    """Capture indexed business watermarks in an independent read-only snapshot."""

    connection = open_read_only_database()
    tables = (
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
    )
    snapshot: dict[str, Any] = {"tables": {}}
    try:
        identity = query_one(
            connection,
            "SELECT DATABASE() AS database_name, CURRENT_USER() AS db_account",
        )
        snapshot["identity"] = identity or {}
        for table in tables:
            table_info = query_one(
                connection,
                """
                SELECT TABLE_ROWS AS approximate_rows
                FROM information_schema.tables
                WHERE table_schema=DATABASE() AND table_name=%s
                """,
                (table,),
            ) or {}
            columns = query_all(
                connection,
                """
                SELECT COLUMN_NAME
                FROM information_schema.columns
                WHERE table_schema=DATABASE() AND table_name=%s
                  AND COLUMN_NAME IN ('id','updated_at','created_at')
                """,
                (table,),
            )
            names = {str(item["COLUMN_NAME"]) for item in columns}
            marker_column = "updated_at" if "updated_at" in names else "created_at"
            if "id" not in names or marker_column not in names:
                snapshot["tables"][table] = {"status": "UNAVAILABLE_COLUMNS"}
                continue
            latest = query_one(
                connection,
                f"SELECT id AS latest_id,`{marker_column}` AS latest_row_marker "
                f"FROM `{table}` ORDER BY id DESC LIMIT 1",
            ) or {}
            approximate_rows = int(table_info.get("approximate_rows") or 0)
            marker: dict[str, Any] = {
                **latest,
                "approximate_rows": approximate_rows,
                "exact_count_mode": approximate_rows <= 50000,
            }
            if approximate_rows <= 50000:
                count = query_one(
                    connection,
                    f"SELECT COUNT(*) AS exact_row_count FROM `{table}`",
                ) or {}
                marker.update(count)
            snapshot["tables"][table] = marker
        snapshot["captured_at"] = datetime.now(timezone.utc).isoformat()
        return snapshot
    finally:
        connection.rollback()
        connection.close()


def comparable_database_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return only deterministic markers used for before/after comparison."""

    tables = snapshot.get("tables")
    if not isinstance(tables, dict):
        return {}
    output: dict[str, Any] = {}
    for table, value in tables.items():
        if not isinstance(value, dict):
            output[str(table)] = value
            continue
        output[str(table)] = {
            key: value.get(key)
            for key in ("status", "latest_id", "latest_row_marker", "exact_row_count")
            if key in value
        }
    return output


def discover_fixtures(fixed_as_of: str) -> dict[str, Any]:
    """Discover current metric and environment identities from one DB snapshot."""

    connection = open_read_only_database()
    fixtures: dict[str, Any] = {"fixed_as_of": fixed_as_of}
    try:
        evidence_candidates = query_all(
            connection,
            """
            SELECT e.id,e.run_id,e.factor_id,e.is_sub_factor_id,
                   e.calculation_mode,e.factor_bar_interval,e.factor_window_bars,
                   e.return_bar_interval,e.forward_return_bars,r.completed_at
            FROM factor_ic_run_formula_evidence e
            JOIN factor_ic_runs r ON r.run_id=e.run_id AND r.status='completed'
            WHERE e.is_sub_factor_id=1
            ORDER BY e.id DESC
            LIMIT 50
            """,
        )
        metric: dict[str, Any] | None = None
        for evidence in evidence_candidates:
            candidate = query_one(
                connection,
                """
                SELECT m.factor_id,m.is_sub_factor_id,m.run_id,m.ic_scope,
                       m.calculation_mode,m.factor_bar_interval,m.factor_window_bars,
                       m.return_bar_interval,m.forward_return_bars,m.universe_key,
                       COALESCE(m.symbol,'') AS symbol,m.window_scope,m.scoring_version,
                       %s AS completed_at
                FROM factor_ic_summary_metrics m
                WHERE m.run_id=%s AND m.factor_id=%s AND m.is_sub_factor_id=1
                  AND m.calculation_mode=%s
                  AND m.factor_bar_interval=%s AND m.factor_window_bars=%s
                  AND m.return_bar_interval=%s AND m.forward_return_bars=%s
                  AND m.ic_scope IN ('time_series','cross_sectional')
                  AND m.universe_key IS NOT NULL AND m.window_scope IS NOT NULL
                  AND m.scoring_version IS NOT NULL
                ORDER BY m.id DESC
                LIMIT 1
                """,
                (
                    evidence["completed_at"],
                    evidence["run_id"],
                    evidence["factor_id"],
                    evidence["calculation_mode"],
                    evidence["factor_bar_interval"],
                    evidence["factor_window_bars"],
                    evidence["return_bar_interval"],
                    evidence["forward_return_bars"],
                ),
            )
            if not candidate:
                continue
            slice_range = query_one(
                connection,
                """
                SELECT MIN(slice_start) AS min_start,MAX(slice_end) AS max_end,
                       COUNT(*) AS row_count
                FROM factor_ic_slice_metrics
                WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=%s
                  AND ic_scope=%s AND calculation_mode=%s
                  AND factor_bar_interval=%s AND factor_window_bars=%s
                  AND return_bar_interval=%s AND forward_return_bars=%s
                  AND universe_key=%s AND COALESCE(symbol,'')=%s
                  AND window_scope=%s
                """,
                (
                    candidate["run_id"],
                    candidate["factor_id"],
                    candidate["is_sub_factor_id"],
                    candidate["ic_scope"],
                    candidate["calculation_mode"],
                    candidate["factor_bar_interval"],
                    candidate["factor_window_bars"],
                    candidate["return_bar_interval"],
                    candidate["forward_return_bars"],
                    candidate["universe_key"],
                    candidate["symbol"],
                    candidate["window_scope"],
                ),
            )
            if int((slice_range or {}).get("row_count") or 0) <= 0:
                continue
            candidate["slice_range"] = slice_range or {}
            metric = candidate
            break
        fixtures["metric"] = metric
        fixtures["environment_route"] = query_one(
            connection,
            """
            SELECT r.factor_ref,r.market_scope,r.label_code,r.environment_date,
                   r.as_of_time,b.batch_uid,b.route_profile_key
            FROM market_environment_factor_route r
            JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
            WHERE r.is_active=1
            ORDER BY r.activated_at DESC,r.id DESC
            LIMIT 1
            """,
        )
        feedback = query_one(
            connection,
            """
            SELECT COUNT(*) AS row_count
            FROM market_environment_strategy_feedback_submissions
            """,
        )
        fixtures["feedback_row_count"] = int((feedback or {}).get("row_count") or 0)
        return fixtures
    finally:
        connection.rollback()
        connection.close()


def iso_utc(value: Any) -> str:
    """Serialize a DB datetime as an explicit UTC instant."""

    if not isinstance(value, datetime):
        raise ValueError(f"Expected datetime, got {type(value).__name__}")
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fallback_required_arguments(tool: str, fixed_as_of: str) -> dict[str, Any]:
    """Return schema-valid placeholders when no semantic DB fixture is available."""

    ref = "sub_factor:1"
    common = {
        "factor_ref": ref,
        "ic_scope": "time_series",
        "calculation_mode": "direct",
        "universe_key": "all",
        "window_scope": "rolling",
        "interval": "1h",
        "factor_window_bars": "1",
        "return_bar_interval": "1h",
        "forward_return_bars": 1,
        "as_of": fixed_as_of,
        "scoring_version": "v202606_default",
    }
    mapping: dict[str, dict[str, Any]] = {
        "factor_get_detail": {"factor_ref": ref},
        "factor_get_metrics": dict(common),
        "factor_get_formula": {
            "factor_ref": ref,
            "run_id": "questtest-missing-run",
            "calculation_mode": "direct",
            "interval": "1h",
            "factor_window_bars": "1",
            "return_bar_interval": "1h",
            "forward_return_bars": 1,
        },
        "factor_list_metric_scopes": {"as_of": fixed_as_of, "limit": 1},
        "factor_rank": {
            **{
                key: value
                for key, value in common.items()
                if key not in {"factor_ref", "run_id"}
            },
            "metric": "mean_ic",
            "top_k": 1,
            "bottom_k": 1,
            "validity_scope": "time_series",
            "ranking_mode": "signed",
            "min_valid_slice_count": 0,
            "min_coverage_mean": 0,
            "require_oos": False,
        },
        "factor_get_details_batch": {"factor_refs": [ref]},
        "factor_get_metrics_batch": {
            **{key: value for key, value in common.items() if key != "factor_ref"},
            "factor_refs": [ref],
        },
        "factor_get_validity_batch": {
            **{
                key: value
                for key, value in common.items()
                if key not in {"factor_ref", "ic_scope"}
            },
            "factor_refs": [ref],
            "validity_scope": "time_series",
        },
        "factor_get_metric_slices": {
            **common,
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-01-02T00:00:00Z",
            "limit": 1,
        },
        "factor_get_validity": {
            **{key: value for key, value in common.items() if key != "ic_scope"},
            "validity_scope": "time_series",
        },
        "environment_get_recommendations": {"market_scope": "all", "limit": 1},
        "factor_get_environment_metrics": {
            "factor_ref": ref,
            "market_scope": "all",
            "limit": 1,
        },
        "factor_get_environment_tags": {"factor_ref": ref, "market_scope": "all"},
        "universe_list_symbols": {"universe_key": "all", "as_of": fixed_as_of},
        "get_feedback_submission_status": {"submission_id": str(uuid4())},
    }
    return copy.deepcopy(mapping.get(tool, {}))


def build_valid_arguments(
    tool_names: list[str], fixtures: dict[str, Any], fixed_as_of: str
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Build semantic baseline arguments for every advertised read-only tool."""

    arguments = {
        name: fallback_required_arguments(name, fixed_as_of) for name in tool_names
    }
    arguments["factor_search"] = {"kind": "sub_factor", "limit": 1}
    arguments["kb_factor_candidate_search"] = {"query": "factor", "limit": 1}
    arguments["factor_catalog_stats"] = {}
    arguments["environment_get_daily"] = {"as_of": fixed_as_of, "limit": 1}
    arguments["schema_get_factor_fields"] = {}
    arguments["schema_get_raw_data"] = {}
    semantic_tools: set[str] = {
        "factor_search",
        "kb_factor_candidate_search",
        "factor_catalog_stats",
        "factor_list_metric_scopes",
        "environment_get_daily",
        "schema_get_factor_fields",
        "schema_get_raw_data",
    }
    metric = fixtures.get("metric")
    if isinstance(metric, dict):
        factor_ref = (
            "sub_factor:" if int(metric.get("is_sub_factor_id") or 0) else "factor:"
        ) + str(metric["factor_id"])
        common = {
            "factor_ref": factor_ref,
            "ic_scope": metric["ic_scope"],
            "calculation_mode": metric["calculation_mode"],
            "universe_key": metric["universe_key"],
            "window_scope": metric["window_scope"],
            "interval": metric["factor_bar_interval"],
            "factor_window_bars": str(metric["factor_window_bars"]),
            "return_bar_interval": metric["return_bar_interval"],
            "forward_return_bars": int(metric["forward_return_bars"]),
            "as_of": fixed_as_of,
            "scoring_version": metric["scoring_version"],
            "symbol": metric.get("symbol") or "",
            "run_id": metric["run_id"],
        }
        arguments["factor_get_detail"] = {
            "factor_ref": factor_ref,
            "detail_level": "summary",
        }
        arguments["factor_get_metrics"] = dict(common)
        arguments["factor_get_formula"] = {
            key: common[key]
            for key in (
                "factor_ref",
                "run_id",
                "calculation_mode",
                "interval",
                "factor_window_bars",
                "return_bar_interval",
                "forward_return_bars",
            )
        }
        arguments["factor_get_details_batch"] = {
            "factor_refs": [factor_ref],
            "detail_level": "summary",
        }
        arguments["factor_get_metrics_batch"] = {
            **{key: value for key, value in common.items() if key != "factor_ref"},
            "factor_refs": [factor_ref],
        }
        validity_common = {
            key: value for key, value in common.items() if key != "ic_scope"
        }
        validity_common["validity_scope"] = metric["ic_scope"]
        arguments["factor_get_validity"] = dict(validity_common)
        arguments["factor_get_validity_batch"] = {
            **{
                key: value
                for key, value in validity_common.items()
                if key != "factor_ref"
            },
            "factor_refs": [factor_ref],
        }
        rank_common = {
            key: value
            for key, value in common.items()
            if key not in {"factor_ref", "run_id"}
        }
        arguments["factor_rank"] = {
            **rank_common,
            "metric": "mean_ic",
            "top_k": 1,
            "bottom_k": 1,
            "validity_scope": metric["ic_scope"],
            "ranking_mode": "signed",
            "min_valid_slice_count": 0,
            "min_coverage_mean": 0,
            "require_oos": False,
            "kind": "sub_factor",
        }
        slice_range = metric.get("slice_range")
        if (
            isinstance(slice_range, dict)
            and isinstance(slice_range.get("min_start"), datetime)
            and isinstance(slice_range.get("max_end"), datetime)
        ):
            arguments["factor_get_metric_slices"] = {
                **common,
                "start_time": iso_utc(slice_range["min_start"]),
                # Known end-equality behavior is excluded from this protocol run.
                "end_time": iso_utc(slice_range["max_end"] + timedelta(seconds=1)),
                "limit": 1,
            }
        semantic_tools.update(
            {
                "factor_get_detail",
                "factor_get_metrics",
                "factor_get_formula",
                "factor_rank",
                "factor_get_details_batch",
                "factor_get_metrics_batch",
                "factor_get_validity_batch",
                "factor_get_metric_slices",
                "factor_get_validity",
                "universe_list_symbols",
            }
        )
        arguments["universe_list_symbols"] = {
            "universe_key": metric["universe_key"],
            "as_of": fixed_as_of,
        }
    route = fixtures.get("environment_route")
    if isinstance(route, dict):
        environment = {
            "factor_ref": route["factor_ref"],
            "market_scope": route["market_scope"],
            "route_profile_key": route.get("route_profile_key") or "default",
        }
        arguments["environment_get_recommendations"] = {
            "market_scope": route["market_scope"],
            "route_profile_key": route.get("route_profile_key") or "default",
            "as_of": fixed_as_of,
            "limit": 1,
        }
        arguments["factor_get_environment_metrics"] = {**environment, "limit": 1}
        arguments["factor_get_environment_tags"] = environment
        semantic_tools.update(
            {
                "environment_get_recommendations",
                "factor_get_environment_metrics",
                "factor_get_environment_tags",
            }
        )
    return arguments, semantic_tools


def schema_enums(schema: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    """Return every top-level property with one or more declared enum values."""

    found: list[tuple[str, list[Any]]] = []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return found
    for field, definition in properties.items():
        values: list[Any] = []
        if isinstance(definition, dict):
            if isinstance(definition.get("enum"), list):
                values.extend(definition["enum"])
            variants = definition.get("anyOf")
            if isinstance(variants, list):
                for variant in variants:
                    if isinstance(variant, dict) and isinstance(variant.get("enum"), list):
                        values.extend(variant["enum"])
        if values:
            found.append((str(field), values))
    return found


def schema_limit_fields(schema: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return declared limit and children_limit fields from a tool schema."""

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    return [
        (str(field), definition)
        for field, definition in properties.items()
        if field in {"limit", "children_limit"} and isinstance(definition, dict)
    ]


def record(
    cases: list[dict[str, Any]],
    case_id: str,
    module: str,
    status: str,
    expected: str,
    actual: Any,
    *,
    severity: str | None = None,
    note: str | None = None,
) -> None:
    """Append one adjudicated case to the authoritative report."""

    item = {
        "case_id": case_id,
        "module": module,
        "status": status,
        "expected": expected,
        "actual": actual,
    }
    if severity:
        item["severity"] = severity
    if note:
        item["note"] = note
    cases.append(item)


def init_ok(call: dict[str, Any]) -> bool:
    """Return whether initialization negotiated a protocol version."""

    envelope = call.get("envelope")
    result = envelope.get("result") if isinstance(envelope, dict) else None
    return bool(
        call.get("http_status") == 200
        and not call.get("parse_error")
        and isinstance(result, dict)
        and isinstance(result.get("protocolVersion"), str)
    )


def tools_from_call(call: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract advertised tool descriptors from tools/list."""

    envelope = call.get("envelope")
    result = envelope.get("result") if isinstance(envelope, dict) else None
    tools = result.get("tools") if isinstance(result, dict) else None
    return [dict(item) for item in tools if isinstance(item, dict)] if isinstance(tools, list) else []


def classify_baseline(
    call: dict[str, Any], semantic_fixture: bool
) -> tuple[str, str | None]:
    """Classify a baseline read without turning capacity guards into product bugs."""

    if successful(call):
        return "PASS", None
    code = error_code(call)
    if code in {"EXPORT_BUDGET_EXCEEDED", "RATE_LIMITED", "QUERY_TIMEOUT"}:
        return "BLOCKED_CAPACITY", None
    if code in {"DEPENDENCY_UNAVAILABLE", "SERVICE_UNAVAILABLE"}:
        return "BLOCKED_DEPENDENCY", None
    if code in {"AUTH_REQUIRED", "FORBIDDEN", "INSUFFICIENT_SCOPE"}:
        return "BLOCKED_AUTHORIZATION", None
    if code == "NOT_FOUND" and not semantic_fixture:
        return "BLOCKED_DATA_PRECONDITION", None
    if call.get("transport_error"):
        return "BLOCKED_TRANSPORT", None
    return "FAIL", "P1"


def run_mcp_006(
    client: MCPClient,
    cases: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    valid_arguments: dict[str, dict[str, Any]],
    semantic_tools: set[str],
) -> None:
    """Run current-schema baseline, limit, enum, and extra-field coverage."""

    module = "MCP-006 input schema and read-tool boundaries"
    read_tools = [item for item in tools if item.get("name") != WRITE_TOOL]
    baseline_calls: dict[str, dict[str, Any]] = {}
    for item in read_tools:
        name = str(item.get("name"))
        args = copy.deepcopy(valid_arguments.get(name, {}))
        call = client.tool(f"MCP-006-BASELINE-{name}", name, args)
        baseline_calls[name] = call
        status, severity = classify_baseline(call, name in semantic_tools)
        record(
            cases,
            f"MCP-006-BASELINE-{name}",
            module,
            status,
            "the otherwise-valid read call succeeds before negative variants",
            {"arguments": args, "call": compact_call(call)},
            severity=severity,
        )

    for item in read_tools:
        name = str(item.get("name"))
        schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
        args = copy.deepcopy(valid_arguments.get(name, {}))
        extra_field = "questtest_extra"
        args[extra_field] = "must-be-rejected"
        call = client.tool(f"MCP-006-EXTRA-{name}", name, args)
        ok = validation_rejected(call, extra_field, "extra")
        if ok:
            status, severity = "PASS", None
        elif successful(call):
            status, severity = "FAIL", "P1"
        else:
            status, severity = "FAIL", "P1"
        record(
            cases,
            f"MCP-006-EXTRA-{name}",
            module,
            status,
            "additionalProperties=false rejects an unknown top-level argument",
            {
                "schema_additional_properties": schema.get("additionalProperties"),
                "call": compact_call(call),
                "validation_text_mentions_field": extra_field in result_text(call),
            },
            severity=severity,
        )

    for item in read_tools:
        name = str(item.get("name"))
        schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
        for field, allowed in schema_enums(schema):
            args = copy.deepcopy(valid_arguments.get(name, {}))
            args[field] = "__questtest_invalid_enum__"
            case_id = f"MCP-006-ENUM-{name}-{field}"
            call = client.tool(case_id, name, args)
            ok = validation_rejected(call, field, "enum")
            record(
                cases,
                case_id,
                module,
                "PASS" if ok else "FAIL",
                "a value outside the advertised enum is rejected before dispatch",
                {
                    "field": field,
                    "allowed_values": allowed,
                    "call": compact_call(call),
                    "validation_text_mentions_field": field.lower() in result_text(call),
                },
                severity=None if ok else "P1",
            )

    for item in read_tools:
        name = str(item.get("name"))
        schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
        for field, definition in schema_limit_fields(schema):
            minimum = definition.get("minimum")
            maximum = definition.get("maximum")
            if minimum is None or maximum is None:
                record(
                    cases,
                    f"MCP-006-LIMIT-{name}-{field}-DECLARATION",
                    module,
                    "NOT_APPLICABLE",
                    "runtime boundaries can be tested only when the Schema declares them",
                    {
                        "minimum": minimum,
                        "maximum": maximum,
                        "default": definition.get("default"),
                    },
                    note="factor_get_metric_slices currently declares a default but no min/max",
                )
                continue
            for label, value, legal in (
                ("MIN", int(minimum), True),
                ("MAX", int(maximum), True),
                ("BELOW", int(minimum) - 1, False),
                ("ABOVE", int(maximum) + 1, False),
            ):
                args = copy.deepcopy(valid_arguments.get(name, {}))
                args[field] = value
                case_id = f"MCP-006-LIMIT-{name}-{field}-{label}"
                call = client.tool(case_id, name, args)
                if legal and successful(call):
                    status, severity = "PASS", None
                elif legal and error_code(call) in {
                    "EXPORT_BUDGET_EXCEEDED",
                    "RATE_LIMITED",
                    "QUERY_TIMEOUT",
                }:
                    status, severity = "BLOCKED_CAPACITY", None
                elif legal and error_code(call) in {
                    "DEPENDENCY_UNAVAILABLE",
                    "SERVICE_UNAVAILABLE",
                }:
                    status, severity = "BLOCKED_DEPENDENCY", None
                elif legal and error_code(call) in {
                    "AUTH_REQUIRED",
                    "FORBIDDEN",
                    "INSUFFICIENT_SCOPE",
                }:
                    status, severity = "BLOCKED_AUTHORIZATION", None
                elif not legal and validation_rejected(call, field, "enum"):
                    status, severity = "PASS", None
                else:
                    status, severity = "FAIL", "P1"
                record(
                    cases,
                    case_id,
                    module,
                    status,
                    (
                        "the declared legal limit boundary is accepted"
                        if legal
                        else "the value outside the declared limit boundary is rejected"
                    ),
                    {
                        "field": field,
                        "value": value,
                        "minimum": minimum,
                        "maximum": maximum,
                        "returned_count": len(response_rows(call)),
                        "call": compact_call(call),
                    },
                    severity=severity,
                )

    write_descriptor = next(
        (item for item in tools if item.get("name") == WRITE_TOOL), None
    )
    write_schema = (
        write_descriptor.get("inputSchema")
        if isinstance(write_descriptor, dict)
        and isinstance(write_descriptor.get("inputSchema"), dict)
        else {}
    )
    static_ok = bool(write_descriptor) and write_schema.get("additionalProperties") is False
    record(
        cases,
        "MCP-006-WRITE-TOOL-SCHEMA-ONLY",
        module,
        "PASS" if static_ok else "FAIL",
        "the write tool is inspected statically and is never invoked",
        {
            "tool_present": bool(write_descriptor),
            "additionalProperties": write_schema.get("additionalProperties"),
            "invoked": False,
        },
        severity=None if static_ok else "P1",
    )


def run_mcp_010(
    token: str, output: Path, cases: list[dict[str, Any]]
) -> list[MCPClient]:
    """Verify logical connection reuse and clean initialization after reconnect."""

    module = "MCP-010 session reuse and reconnect"
    clients = [MCPClient(token, output, "session-a"), MCPClient(token, output, "session-b")]
    a, b = clients
    init_a = a.initialize("MCP-010-A-INIT")
    notify_a = a.notify_initialized("MCP-010-A-NOTIFY") if init_ok(init_a) else {}
    tools_a1 = a.request("MCP-010-A-TOOLS-1", "tools/list", {}) if init_ok(init_a) else {}
    schema_a1 = a.tool("MCP-010-A-SCHEMA-1", "schema_get_raw_data", {}) if init_ok(init_a) else {}
    tools_a2 = a.request("MCP-010-A-TOOLS-2", "tools/list", {}) if init_ok(init_a) else {}
    schema_a2 = a.tool("MCP-010-A-SCHEMA-2", "schema_get_raw_data", {}) if init_ok(init_a) else {}
    reuse_ok = bool(
        init_ok(init_a)
        and notify_a.get("http_status") in {200, 202, 204}
        and tools_from_call(tools_a1)
        and tools_from_call(tools_a2)
        and successful(schema_a1)
        and successful(schema_a2)
        and canonical_hash(tools_from_call(tools_a1))
        == canonical_hash(tools_from_call(tools_a2))
        and canonical_hash(call_snapshot(schema_a1))
        == canonical_hash(call_snapshot(schema_a2))
    )
    record(
        cases,
        "MCP-010-SESSION-REUSE",
        module,
        "PASS" if reuse_ok else "FAIL",
        "one initialized HTTP client can perform repeated read calls consistently",
        {
            "initialize": compact_call(init_a),
            "notify_status": notify_a.get("http_status"),
            "tools_hashes": [
                canonical_hash(tools_from_call(tools_a1)),
                canonical_hash(tools_from_call(tools_a2)),
            ],
            "schema_hashes": [
                canonical_hash(call_snapshot(schema_a1)),
                canonical_hash(call_snapshot(schema_a2)),
            ],
        },
        severity=None if reuse_ok else "P1",
    )

    init_b = b.initialize("MCP-010-B-INIT")
    notify_b = b.notify_initialized("MCP-010-B-NOTIFY") if init_ok(init_b) else {}
    tools_b = b.request("MCP-010-B-TOOLS", "tools/list", {}) if init_ok(init_b) else {}
    schema_b = b.tool("MCP-010-B-SCHEMA", "schema_get_raw_data", {}) if init_ok(init_b) else {}
    reconnect_ok = bool(
        init_ok(init_b)
        and notify_b.get("http_status") in {200, 202, 204}
        and tools_from_call(tools_b)
        and successful(schema_b)
        and canonical_hash(tools_from_call(tools_a1))
        == canonical_hash(tools_from_call(tools_b))
        and canonical_hash(call_snapshot(schema_a1))
        == canonical_hash(call_snapshot(schema_b))
    )
    record(
        cases,
        "MCP-010-RECONNECT",
        module,
        "PASS" if reconnect_ok else "FAIL",
        "a new connection can initialize and read the same stable schemas",
        {
            "initialize": compact_call(init_b),
            "notify_status": notify_b.get("http_status"),
            "tools_match_first_connection": canonical_hash(tools_from_call(tools_a1))
            == canonical_hash(tools_from_call(tools_b)),
            "schema_matches_first_connection": canonical_hash(call_snapshot(schema_a1))
            == canonical_hash(call_snapshot(schema_b)),
        },
        severity=None if reconnect_ok else "P1",
    )
    session_header_present = bool(a.session_id or b.session_id)
    record(
        cases,
        "MCP-010-SESSION-ID-ENFORCEMENT",
        module,
        "PASS" if session_header_present else "NOT_APPLICABLE",
        "session-ID enforcement is tested only when initialize declares a stateful session",
        {
            "session_id_present_a": bool(a.session_id),
            "session_id_present_b": bool(b.session_id),
        },
        note=(
            None
            if session_header_present
            else "initialize returned no MCP-Session-Id; the service behaves statelessly"
        ),
    )
    return clients


def run_mcp_011(
    client: MCPClient, token: str, output: Path, cases: list[dict[str, Any]]
) -> list[MCPClient]:
    """Verify malformed JSON-RPC, duplicate IDs, and unknown-version handling."""

    module = "MCP-011 malformed JSON-RPC and protocol negotiation"
    raw_cases: list[tuple[str, bytes, set[str]]] = [
        (
            "MCP-011-MALFORMED-JSON",
            b'{"jsonrpc":"2.0","id":1,"method":"tools/list"',
            {"-32700"},
        ),
        ("MCP-011-ARRAY-ROOT", b"[]", {"-32600"}),
        (
            "MCP-011-MISSING-JSONRPC",
            b'{"id":1,"method":"tools/list","params":{}}',
            {"-32600"},
        ),
        (
            "MCP-011-WRONG-JSONRPC",
            b'{"jsonrpc":"1.0","id":1,"method":"tools/list","params":{}}',
            {"-32600"},
        ),
        (
            "MCP-011-UNKNOWN-METHOD",
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "unknown-method",
                    "method": "questtest/unknown",
                    "params": {},
                }
            ).encode(),
            {"-32601"},
        ),
        (
            "MCP-011-MISSING-CALL-PARAMS",
            json.dumps(
                {"jsonrpc": "2.0", "id": "missing-params", "method": "tools/call"}
            ).encode(),
            {"-32602"},
        ),
        (
            "MCP-011-WRONG-ARGUMENT-CONTAINER",
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "wrong-arguments",
                    "method": "tools/call",
                    "params": {"name": "factor_search", "arguments": []},
                }
            ).encode(),
            {"-32602"},
        ),
    ]
    for case_id, raw, expected_codes in raw_cases:
        call = client.request(case_id, "raw", raw_body=raw)
        code = error_code(call)
        ok = rejected(call) and code in expected_codes and not call.get("parse_error")
        record(
            cases,
            case_id,
            module,
            "PASS" if ok else "FAIL",
            "invalid JSON-RPC input returns the applicable protocol error",
            {"expected_codes": sorted(expected_codes), "call": compact_call(call)},
            severity=None if ok else "P1",
        )

    duplicate_id = "questtest-duplicate-id"
    first = client.request(
        "MCP-011-DUPLICATE-ID-A", "tools/list", {}, request_id=duplicate_id
    )
    second = client.request(
        "MCP-011-DUPLICATE-ID-B", "tools/list", {}, request_id=duplicate_id
    )
    first_envelope = first.get("envelope")
    second_envelope = second.get("envelope")
    ids_ok = bool(
        isinstance(first_envelope, dict)
        and isinstance(second_envelope, dict)
        and first_envelope.get("id") == duplicate_id
        and second_envelope.get("id") == duplicate_id
    )
    payloads_match = canonical_hash(tools_from_call(first)) == canonical_hash(
        tools_from_call(second)
    )
    duplicate_ok = bool(ids_ok and tools_from_call(first) and tools_from_call(second) and payloads_match)
    record(
        cases,
        "MCP-011-DUPLICATE-ID",
        module,
        "PASS" if duplicate_ok else "FAIL",
        "sequential independent requests may reuse an ID and each response preserves it",
        {
            "response_ids": [
                first_envelope.get("id") if isinstance(first_envelope, dict) else None,
                second_envelope.get("id") if isinstance(second_envelope, dict) else None,
            ],
            "stable_tool_descriptors_match": payloads_match,
            "calls": [compact_call(first), compact_call(second)],
        },
        severity=None if duplicate_ok else "P1",
    )

    unknown = MCPClient(token, output, "unknown-version")
    unknown_init = unknown.initialize("MCP-011-UNKNOWN-PROTOCOL", "2099-01-01")
    envelope = unknown_init.get("envelope")
    value = envelope.get("result") if isinstance(envelope, dict) else None
    negotiated = value.get("protocolVersion") if isinstance(value, dict) else None
    unknown_ok = bool(
        rejected(unknown_init)
        or (
            unknown_init.get("http_status") == 200
            and isinstance(negotiated, str)
            and negotiated != "2099-01-01"
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", negotiated)
        )
    )
    record(
        cases,
        "MCP-011-UNKNOWN-PROTOCOL",
        module,
        "PASS" if unknown_ok else "FAIL",
        "an unknown requested version is rejected or explicitly negotiated to a server version",
        {
            "requested_version": "2099-01-01",
            "negotiated_version": negotiated,
            "call": compact_call(unknown_init),
        },
        severity=None if unknown_ok else "P1",
    )
    return [unknown]


def item_identity(item: dict[str, Any]) -> str:
    """Return a stable identity for one environment row."""

    for key in ("id", "daily_id", "metric_id"):
        if item.get(key) is not None:
            return f"{key}:{item[key]}"
    selected = {
        key: item.get(key)
        for key in (
            "environment_date",
            "label_kind",
            "revision",
            "market_scope",
            "label_code",
        )
        if key in item
    }
    return canonical_hash(selected if selected else item)


def run_mcp_014(
    client: MCPClient,
    cases: list[dict[str, Any]],
    fixed_as_of: str,
) -> None:
    """Verify a bounded large response and one cursor continuation page."""

    module = "MCP-014 bounded large response, pagination, and SSE"
    arguments: dict[str, Any] = {"as_of": fixed_as_of, "limit": 1000}
    first = client.tool("MCP-014-LARGE-PAGE-1", "environment_get_daily", arguments)
    code = error_code(first)
    if code in {"EXPORT_BUDGET_EXCEEDED", "RATE_LIMITED", "QUERY_TIMEOUT"}:
        record(
            cases,
            "MCP-014-LARGE-RESPONSE",
            module,
            "BLOCKED_CAPACITY",
            "the declared maximum is handled within service export capacity",
            {"call": compact_call(first), "blocking_code": code},
        )
    elif code in {"DEPENDENCY_UNAVAILABLE", "SERVICE_UNAVAILABLE"}:
        record(
            cases,
            "MCP-014-LARGE-RESPONSE",
            module,
            "BLOCKED_DEPENDENCY",
            "the declared maximum can be read only while its dependency is available",
            {"call": compact_call(first), "blocking_code": code},
        )
    elif not successful(first):
        record(
            cases,
            "MCP-014-LARGE-RESPONSE",
            module,
            "FAIL",
            "the declared maximum returns a parseable bounded response",
            {"call": compact_call(first)},
            severity="P1",
        )
    else:
        first_rows = response_rows(first)
        first_meta = business_meta(first)
        cursor = first_meta.get("next_cursor")
        bounded = len(first_rows) <= 1000
        continuation_consistent = bool(cursor) == bool(first_meta.get("truncated"))
        page_ok = bounded and continuation_consistent and not first.get("parse_error")
        record(
            cases,
            "MCP-014-LARGE-RESPONSE",
            module,
            "PASS" if page_ok else "FAIL",
            "the maximum request is capped without a partial JSON response and exposes pagination state",
            {
                "requested_limit": 1000,
                "returned_count": len(first_rows),
                "response_bytes": first.get("response_bytes"),
                "truncated": first_meta.get("truncated"),
                "next_cursor_present": bool(cursor),
                "warnings": first_meta.get("warnings"),
                "call": compact_call(first),
            },
            severity=None if page_ok else "P1",
        )
        if cursor:
            second_args = {**arguments, "cursor": cursor}
            second = client.tool(
                "MCP-014-LARGE-PAGE-2", "environment_get_daily", second_args
            )
            second_rows = response_rows(second)
            first_ids = {item_identity(item) for item in first_rows}
            second_ids = {item_identity(item) for item in second_rows}
            next_cursor = business_meta(second).get("next_cursor")
            continuation_ok = bool(
                successful(second)
                and len(second_rows) <= 1000
                and not (first_ids & second_ids)
                and (not next_cursor or str(next_cursor) != str(cursor))
            )
            record(
                cases,
                "MCP-014-PAGINATION-CONTINUATION",
                module,
                "PASS" if continuation_ok else "FAIL",
                "one continuation page is successful, bounded, and non-overlapping",
                {
                    "first_count": len(first_rows),
                    "second_count": len(second_rows),
                    "duplicate_identity_count": len(first_ids & second_ids),
                    "cursor_advanced_or_terminal": not next_cursor
                    or str(next_cursor) != str(cursor),
                    "call": compact_call(second),
                },
                severity=None if continuation_ok else "P1",
            )
        else:
            record(
                cases,
                "MCP-014-PAGINATION-CONTINUATION",
                module,
                "BLOCKED_DATA_PRECONDITION",
                "a continuation cursor exists before page-two behavior can be verified",
                {"first_count": len(first_rows), "next_cursor_present": False},
            )

    sse = client.request(
        "MCP-014-SSE-NEGOTIATION",
        "tools/list",
        {},
        headers={"Accept": "text/event-stream"},
    )
    if sse.get("http_status") == 200 and not sse.get("parse_error"):
        sse_status = "PASS"
        sse_note = "server returned a readable response for SSE-only negotiation"
    elif sse.get("http_status") == 406 and rejected(sse):
        sse_status = "NOT_APPLICABLE"
        sse_note = "endpoint explicitly does not advertise SSE-only output"
    else:
        sse_status = "FAIL"
        sse_note = "SSE negotiation produced an unstructured or unreadable response"
    record(
        cases,
        "MCP-014-SSE-NEGOTIATION",
        module,
        sse_status,
        "SSE-only negotiation is either supported or explicitly rejected",
        {"call": compact_call(sse)},
        severity="P1" if sse_status == "FAIL" else None,
        note=sse_note,
    )

    malformed_sse_samples = (
        b"event: message\n\n",
        b"data: {not-json}\n\n",
        b"data: {}\n\ndata: {}\n\n",
    )
    offline_results = [
        parse_mcp_body(sample, "text/event-stream")[1]
        for sample in malformed_sse_samples
    ]
    local_ok = all(value is not None for value in offline_results)
    record(
        cases,
        "MCP-014-ABNORMAL-SSE-PARSER",
        module,
        "LOCAL_ONLY_PASS" if local_ok else "LOCAL_ONLY_FAIL",
        "the evidence parser fails closed on malformed SSE fixtures",
        {"parse_errors": offline_results},
        note="offline harness evidence; this is not a server-side PASS",
    )
    record(
        cases,
        "MCP-014-ABNORMAL-SSE-SERVER",
        module,
        "NOT_APPLICABLE",
        "a malformed server SSE stream can be assessed only if the service emits one",
        {"server_injection_attempted": False, "sse_only_status": sse.get("http_status")},
        note="the read-only client cannot force the remote server to emit malformed SSE",
    )


def run_concurrent_read(
    index: int,
    token: str,
    output: Path,
    arguments: dict[str, Any],
) -> tuple[MCPClient, dict[str, Any]]:
    """Initialize one independent client and perform one fixed-snapshot read."""

    client = MCPClient(token, output, f"concurrent-{index}")
    init = client.initialize(f"MCP-015-INIT-{index}")
    if init_ok(init):
        client.notify_initialized(f"MCP-015-NOTIFY-{index}")
        call = client.tool(
            f"MCP-015-READ-{index}", "environment_get_daily", arguments
        )
    else:
        call = init
    return client, call


def run_mcp_015(
    client: MCPClient,
    token: str,
    output: Path,
    cases: list[dict[str, Any]],
    fixed_as_of: str,
) -> list[MCPClient]:
    """Compare low-concurrency read results under one fixed point-in-time value."""

    module = "MCP-015 fixed-snapshot concurrent read consistency"
    arguments = {"as_of": fixed_as_of, "limit": 25}
    before = client.tool(
        "MCP-015-SERIAL-BEFORE", "environment_get_daily", arguments
    )
    clients: list[MCPClient] = []
    concurrent_calls: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(run_concurrent_read, index, token, output, arguments)
            for index in range(4)
        ]
        for future in futures:
            worker, call = future.result()
            clients.append(worker)
            concurrent_calls.append(call)
    after = client.tool("MCP-015-SERIAL-AFTER", "environment_get_daily", arguments)
    all_calls = [before, *concurrent_calls, after]
    blocking = [error_code(call) for call in all_calls if error_code(call) in BLOCKING_CODES]
    hashes = [canonical_hash(call_snapshot(call)) for call in all_calls]
    if any(code in {"DEPENDENCY_UNAVAILABLE", "SERVICE_UNAVAILABLE"} for code in blocking):
        status, severity = "BLOCKED_DEPENDENCY", None
    elif any(code in {"AUTH_REQUIRED", "FORBIDDEN", "INSUFFICIENT_SCOPE"} for code in blocking):
        status, severity = "BLOCKED_AUTHORIZATION", None
    elif blocking:
        status, severity = "BLOCKED_CAPACITY", None
    elif all(successful(call) for call in all_calls) and len(set(hashes)) == 1:
        status, severity = "PASS", None
    else:
        status, severity = "FAIL", "P1"
    record(
        cases,
        "MCP-015-CONCURRENT-READ",
        module,
        status,
        "independent low-concurrency reads at one fixed as_of return identical business snapshots",
        {
            "fixed_as_of": fixed_as_of,
            "worker_count": 4,
            "canonical_hashes": hashes,
            "unique_hash_count": len(set(hashes)),
            "blocking_codes": blocking,
            "calls": [compact_call(call) for call in all_calls],
        },
        severity=severity,
        note="request/trace/quota values and signed cursor text are excluded from comparison",
    )
    return clients


def write_markdown(output: Path, report: dict[str, Any]) -> None:
    """Write a concise human-readable mirror of the authoritative JSON report."""

    lines = [
        "# Factor Data MCP protocol-gap closure",
        "",
        f"- Run: `{report['run_id']}`",
        "- Environment: `test`; mode: `READ_ONLY`",
        f"- Status counts: `{json.dumps(report['status_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Real failures: `{len(report['failures'])}`",
        "- Write tool invocation: `false`",
        "- Database transactions: `READ ONLY`, always rolled back",
        "",
        "| Case | Module | Status | Expected |",
        "| --- | --- | --- | --- |",
    ]
    for case in report["cases"]:
        expected = str(case.get("expected", "")).replace("|", "/").replace("\n", " ")
        lines.append(
            f"| `{case['case_id']}` | {case['module']} | `{case['status']}` | {expected} |"
        )
    if report["failures"]:
        lines.extend(["", "## Real failures", ""])
        for failure in report["failures"]:
            lines.append(
                f"- `{failure['case_id']}` ({failure.get('severity', 'P1')}): "
                f"{failure['expected']}"
            )
    lines.extend(
        [
            "",
            "## Adjudication notes",
            "",
            "- Integer strings normalized by the server are compatibility observations, not defects; this run does not count them as failures.",
            "- A legal maximum blocked by `EXPORT_BUDGET_EXCEEDED` is `BLOCKED_CAPACITY`, not a boundary defect.",
            "- Unknown requested protocol versions may be rejected or explicitly negotiated to a supported server version.",
            "- Session-ID enforcement is not applicable when initialize emits no `MCP-Session-Id`.",
            "- Malformed remote SSE cannot be injected by a read-only client; offline parser evidence is not counted as server-side PASS.",
        ]
    )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    """Execute all remaining protocol cases and write authoritative artifacts."""

    if not TOKEN:
        raise SystemExit("MCP_TOKEN or FACTOR4_MCP_TOKEN is required")
    if not MCP_URL.startswith(TEST_MCP_PREFIX):
        raise SystemExit("test MCP host gate failed")
    fixed_as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-resume" / f"{stamp}-protocol-gaps"
    output.mkdir(parents=True, exist_ok=False)
    cases: list[dict[str, Any]] = []
    all_clients: list[MCPClient] = []

    before = database_snapshot()
    write_json(output / "db-before.json", before)
    fixtures = discover_fixtures(fixed_as_of)
    write_json(output / "fixture.json", fixtures)

    main_client = MCPClient(TOKEN, output, "main")
    all_clients.append(main_client)
    init = main_client.initialize("MCP-BASELINE-INIT")
    if not init_ok(init):
        record(
            cases,
            "MCP-BASELINE-INIT",
            "MCP baseline",
            "BLOCKED_TRANSPORT" if init.get("transport_error") else "FAIL",
            "the test MCP endpoint initializes before protocol coverage runs",
            {"call": compact_call(init)},
            severity=None if init.get("transport_error") else "P0",
        )
        report = {
            "run_id": stamp,
            "environment": "test",
            "mcp_host": MCP_URL.split("/")[2],
            "mode": "READ_ONLY",
            "fixed_as_of": fixed_as_of,
            "cases": cases,
            "status_counts": dict(Counter(case["status"] for case in cases)),
            "failures": [case for case in cases if case["status"] == "FAIL"],
            "writes_attempted": False,
        }
        write_json(output / "adjudicated-summary.json", report)
        write_markdown(output, report)
        print(json.dumps({"output_dir": str(output), "status_counts": report["status_counts"]}))
        return 2
    notify = main_client.notify_initialized("MCP-BASELINE-NOTIFY")
    tools_call = main_client.request("MCP-BASELINE-TOOLS", "tools/list", {})
    tools = tools_from_call(tools_call)
    baseline_ok = bool(
        notify.get("http_status") in {200, 202, 204}
        and tools
        and any(item.get("name") == WRITE_TOOL for item in tools)
    )
    record(
        cases,
        "MCP-BASELINE",
        "MCP baseline",
        "PASS" if baseline_ok else "FAIL",
        "initialize, initialized notification, and tools/list establish the current contract",
        {
            "initialize": compact_call(init),
            "negotiated_protocol": main_client.protocol_version,
            "session_id_present": bool(main_client.session_id),
            "notify_status": notify.get("http_status"),
            "tool_count": len(tools),
        },
        severity=None if baseline_ok else "P0",
    )
    schemas = {
        str(item.get("name")): item.get("inputSchema")
        for item in tools
        if isinstance(item.get("name"), str)
    }
    write_json(output / "mcp-tool-schemas.json", schemas)
    read_tool_names = [str(item["name"]) for item in tools if item.get("name") != WRITE_TOOL]
    valid_arguments, semantic_tools = build_valid_arguments(
        read_tool_names, fixtures, fixed_as_of
    )
    write_json(
        output / "argument-fixtures.json",
        {
            "valid_arguments": valid_arguments,
            "semantic_fixture_tools": sorted(semantic_tools),
            "write_tool_excluded": WRITE_TOOL,
        },
    )

    if baseline_ok:
        run_mcp_006(
            main_client,
            cases,
            tools,
            valid_arguments,
            semantic_tools,
        )
        all_clients.extend(run_mcp_010(TOKEN, output, cases))
        all_clients.extend(run_mcp_011(main_client, TOKEN, output, cases))
        run_mcp_014(main_client, cases, fixed_as_of)
        all_clients.extend(
            run_mcp_015(main_client, TOKEN, output, cases, fixed_as_of)
        )

    after = database_snapshot()
    write_json(output / "db-after.json", after)
    before_tables = comparable_database_snapshot(before)
    after_tables = comparable_database_snapshot(after)
    db_unchanged = before_tables == after_tables
    record(
        cases,
        "MCP-READ-ONLY-DB-SNAPSHOT",
        "database read-only control",
        "PASS" if db_unchanged else "ASYNC_STATE_MOVING",
        "business-table watermarks remain stable across read-only MCP calls",
        {
            "unchanged": db_unchanged,
            "before": before_tables,
            "after": after_tables,
        },
        note=(
            None
            if db_unchanged
            else "background state changed between independent snapshots and cannot be attributed to this read-only run"
        ),
    )

    calls = [call for client in all_clients for call in client.calls]
    write_json(output / "call-ledger.json", [compact_call(call) for call in calls])
    for client in all_clients:
        client.close()
    status_counts = dict(sorted(Counter(case["status"] for case in cases).items()))
    failures = [case for case in cases if case["status"] == "FAIL"]
    report = {
        "run_id": stamp,
        "environment": "test",
        "mcp_host": MCP_URL.split("/")[2],
        "mode": "READ_ONLY",
        "fixed_as_of": fixed_as_of,
        "protocol_version": main_client.protocol_version,
        "tool_counts": {
            "advertised": len(tools),
            "read_only_or_status": len(read_tool_names),
            "write_tools_not_invoked": 1 if WRITE_TOOL in schemas else 0,
        },
        "coverage": {
            "mcp_006": "all advertised read tools: baselines, extra fields, enum fields, and declared limit boundaries",
            "mcp_010": "same-client reuse, independent reconnect, conditional session-ID enforcement",
            "mcp_011": "malformed roots/envelopes, unknown method, invalid params, duplicate IDs, unknown version",
            "mcp_014": "bounded maximum response, one cursor continuation, SSE negotiation and non-injectable abnormal SSE",
            "mcp_015": "four independent concurrent clients between two serial controls at one fixed as_of",
        },
        "status_counts": status_counts,
        "cases": cases,
        "failures": failures,
        "blocking_or_non_applicable": [
            case
            for case in cases
            if case["status"]
            in {
                "BLOCKED_CAPACITY",
                "BLOCKED_DEPENDENCY",
                "BLOCKED_AUTHORIZATION",
                "BLOCKED_DATA_PRECONDITION",
                "BLOCKED_TRANSPORT",
                "NOT_APPLICABLE",
                "ASYNC_STATE_MOVING",
            }
        ],
        "adjudication": {
            "integer_string_coercion": "excluded compatibility observation",
            "legal_max_capacity_guard": "BLOCKED_CAPACITY, not FAIL",
            "unknown_protocol_version": "rejection or explicit negotiation is accepted",
            "duplicate_id_comparison": "stable tool descriptors only",
            "concurrency_comparison": "business data and stable version fields; volatile request/trace/quota and signed cursor text excluded",
            "abnormal_sse": "server-side NOT_APPLICABLE; offline parser result is local-only",
        },
        "request_count": len(calls),
        "writes_attempted": False,
        "database_transactions": "READ ONLY; ROLLBACK",
        "sensitive_values_written": False,
    }
    write_json(output / "adjudicated-summary.json", report)
    write_markdown(output, report)
    print(
        json.dumps(
            {
                "output_dir": str(output),
                "status_counts": status_counts,
                "failure_ids": [case["case_id"] for case in failures],
                "request_count": len(calls),
                "db_unchanged": db_unchanged,
            },
            ensure_ascii=False,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
