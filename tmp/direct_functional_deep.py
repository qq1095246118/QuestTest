#!/usr/bin/env python3
"""Run a dynamic, read-only functional regression against Factor Data MCP.

The runner deliberately avoids write tools.  Identifiers and scopes come from
the test database, while every business assertion is made against the same
database snapshot or an explicit MCP contract.  Catalog-heavy operations are
attempted at most once and are reported as blocked when the service quota is
exhausted rather than being misreported as product failures.
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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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
LOCAL_TZ = timezone(timedelta(hours=8))
BLOCKING_CODES = {
    "AUTH_REQUIRED",
    "FORBIDDEN",
    "EXPORT_BUDGET_EXCEEDED",
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
}
ERROR_KEY_RE = re.compile(r"(authorization|token|password|secret|claim_token|signature)", re.I)
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


def json_default(value: Any) -> str:
    """Serialize common database scalar values for evidence files."""

    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def redact(value: Any) -> Any:
    """Remove credential-like fields recursively from an evidence object."""

    if isinstance(value, dict):
        return {
            key: "<redacted>" if ERROR_KEY_RE.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    """Write a UTF-8 JSON evidence artifact after credential redaction."""

    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def utc_instant(value: Any) -> datetime | None:
    """Normalize an ISO or database timestamp to a timezone-aware UTC instant."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        # Factor DB DATETIME values used by metric periods are UTC.  Run
        # lifecycle timestamps are local wall time and are handled separately
        # by the caller when a run boundary is tested.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def scalar_equal(left: Any, right: Any) -> bool:
    """Compare API and DB scalars without losing decimal or instant precision."""

    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    if isinstance(left, (int, float, Decimal)) or isinstance(right, (int, float, Decimal)):
        try:
            return Decimal(str(left)).normalize() == Decimal(str(right)).normalize()
        except (InvalidOperation, ValueError):
            return str(left) == str(right)
    if isinstance(right, datetime) or (
        isinstance(left, str) and "T" in left and isinstance(right, str) and " " in right
    ):
        left_time = utc_instant(left)
        right_time = utc_instant(right)
        if left_time is not None and right_time is not None:
            return left_time == right_time
    if isinstance(right, str) and right[:1] in "[{":
        try:
            right = json.loads(right)
        except json.JSONDecodeError:
            pass
    return left == right


def business(call: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized MCP business envelope from a Runner call."""

    value = call.get("business")
    return value if isinstance(value, dict) else {}


def data(call: dict[str, Any]) -> dict[str, Any]:
    """Return the response data object, or an empty object for errors."""

    value = business(call).get("data")
    return value if isinstance(value, dict) else {}


def error_code(call: dict[str, Any]) -> str | None:
    """Return a structured MCP or JSON-RPC error code."""

    value = business(call).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    envelope = call.get("envelope")
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        code = envelope["error"].get("code")
        return str(code) if code is not None else None
    return None


def successful(call: dict[str, Any]) -> bool:
    """Return whether a call returned a successful business data envelope."""

    return (
        call.get("http_status") == 200
        and call.get("is_error") is False
        and isinstance(data(call), dict)
        and error_code(call) is None
    )


def response_rows(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """Extract object rows from a tool data payload."""

    for key in keys or ("items", "results", "ic_summaries", "top_items", "bottom_items", "symbols", "tags"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def local_iso(value: Any) -> str:
    """Serialize a UTC database timestamp using the API's +08:00 display zone."""

    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(LOCAL_TZ).isoformat()
    text = str(value).replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(LOCAL_TZ).isoformat()


class DeepRunner:
    """Coordinate MCP calls, dynamic fixtures, assertions, and evidence."""

    def __init__(self, token: str, output: Path, db: DatabaseClient) -> None:
        """Initialize a runner for one test environment and output directory."""

        self.token = token
        self.output = output
        self.db = db
        self.output.mkdir(parents=True, exist_ok=True)
        self.runner = transport.Runner(token, output, db)
        self.cases: list[dict[str, Any]] = []
        self.protocol_version: str | None = None

    def call(self, case_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one MCP tool and persist sanitized request/response evidence."""

        return self.runner.tool(case_id, tool, arguments)

    def raw(self, case_id: str, payload: bytes, *, token: str | None = None) -> dict[str, Any]:
        """Send a low-level protocol request for authentication/error checks."""

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": CHROME_UA,
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(MCP_URL, data=payload, headers=headers, method="POST")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read()
                status = response.status
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status = exc.code
            response_headers = dict(exc.headers.items())
        elapsed = round(time.monotonic() - started, 3)
        try:
            envelope: Any = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            envelope = {"raw_body_sha256": hashlib.sha256(body).hexdigest()}
        try:
            request_envelope: Any = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Preserve malformed-protocol evidence without making the test
            # harness fail while trying to serialize the invalid request.
            request_envelope = {"raw_body_sha256": hashlib.sha256(payload).hexdigest()}
        write_json(self.output / f"raw-{case_id}.request.json", request_envelope)
        write_json(self.output / f"raw-{case_id}.response.json", envelope)
        return {
            "case_id": case_id,
            "http_status": status,
            "elapsed_seconds": elapsed,
            "headers": {key.lower(): value for key, value in response_headers.items() if key.lower() in {"content-type", "retry-after", "x-request-id", "x-trace-id"}},
            "envelope": envelope,
        }

    def record(
        self,
        case_id: str,
        title: str,
        passed: bool,
        expected: str,
        actual: Any,
        *,
        call: dict[str, Any] | None = None,
        blocked: str | None = None,
        severity: str = "P1",
        classification: str = "FAIL_FUNCTIONAL",
    ) -> None:
        """Append one normalized PASS/FAIL/BLOCKED verdict."""

        status = "BLOCKED" if blocked else "PASS" if passed else "FAIL"
        self.cases.append(
            {
                "case_id": case_id,
                "title": title,
                "status": status,
                "severity": None if status != "FAIL" else severity,
                "failure_class": None if status != "FAIL" else classification,
                "expected": expected,
                "actual": actual,
                "error_code": error_code(call) if call else None,
                "http_status": call.get("http_status") if call else None,
                "blocking_reason": blocked,
            }
        )


def table_state(db: DatabaseClient) -> dict[str, Any]:
    """Read stable counts and latest markers for business tables."""

    tables = {
        "market_environment_daily": "updated_at",
        "market_environment_eval_batch": "updated_at",
        "market_environment_factor_metric": "updated_at",
        "market_environment_factor_route": "updated_at",
        "factor_ic_runs": "created_at",
        "factor_ic_run_formula_evidence": "recorded_at",
        "factor_validity_status": "updated_at",
        "market_environment_strategy_feedback_submissions": "updated_at",
    }
    result: dict[str, Any] = {}
    with db.transaction() as tx:
        for table, marker in tables.items():
            result[table] = tx.fetch_one(
                f"SELECT COUNT(*) AS row_count, MAX(id) AS max_id, MAX(`{marker}`) AS max_marker FROM `{table}`"
            ) or {}
    return result


def discover(db: DatabaseClient) -> dict[str, Any]:
    """Discover representative completed scopes and environment fixtures."""

    result: dict[str, Any] = {}
    with db.transaction() as tx:
        # Prefer a recent direct aggregate with both dimensions and immutable
        # formula evidence.  This is the primary metrics/validity fixture.
        result["aggregate"] = tx.fetch_one(
            """
            SELECT s.*, r.completed_at
            FROM factor_ic_summary_metrics s
            JOIN factor_ic_runs r ON r.run_id=s.run_id AND r.status='completed'
            WHERE s.is_sub_factor_id=1 AND s.calculation_mode='direct' AND s.symbol=''
              AND s.ic_scope='time_series' AND s.period_end >= '2026-08-30'
              AND EXISTS (
                SELECT 1 FROM factor_ic_summary_metrics cs
                WHERE cs.factor_id=s.factor_id AND cs.is_sub_factor_id=s.is_sub_factor_id
                  AND cs.run_id=s.run_id AND cs.ic_scope='cross_sectional'
                  AND cs.calculation_mode=s.calculation_mode AND cs.symbol=''
                  AND cs.universe_key=s.universe_key AND cs.window_scope=s.window_scope
                  AND cs.factor_bar_interval=s.factor_bar_interval
                  AND cs.factor_window_bars=s.factor_window_bars
                  AND cs.return_bar_interval=s.return_bar_interval
                  AND cs.forward_return_bars=s.forward_return_bars
              )
            ORDER BY s.id DESC LIMIT 1
            """
        )
        result["symbol"] = tx.fetch_one(
            """
            SELECT s.*, r.completed_at
            FROM factor_ic_summary_metrics s
            JOIN factor_ic_runs r ON r.run_id=s.run_id AND r.status='completed'
            WHERE s.is_sub_factor_id=1 AND s.calculation_mode='direct' AND s.symbol<>''
              AND s.ic_scope='time_series'
              AND s.window_scope <> 'min_window'
              AND EXISTS (
                SELECT 1 FROM factor_ic_run_formula_evidence e
                WHERE e.factor_id=s.factor_id AND e.is_sub_factor_id=s.is_sub_factor_id
                  AND e.run_id=s.run_id
              )
            ORDER BY s.period_end DESC, s.id DESC LIMIT 1
            """
        )
        result["child"] = tx.fetch_one(
            """
            SELECT s.*, r.completed_at
            FROM factor_ic_summary_metrics s
            JOIN factor_ic_runs r ON r.run_id=s.run_id AND r.status='completed'
            WHERE s.calculation_mode='child_aggregate' AND s.symbol=''
              AND s.ic_scope='cross_sectional'
            ORDER BY s.id DESC LIMIT 1
            """
        )
        result["ts_only_validity"] = tx.fetch_one(
            """
            SELECT v.*, ts.scoring_version AS ts_scoring_version
            FROM factor_validity_status v
            JOIN factor_ic_summary_metrics ts ON ts.id=v.time_series_summary_id
            WHERE v.is_sub_factor_id=1 AND v.time_series_summary_id IS NOT NULL
              AND v.cross_sectional_summary_id IS NOT NULL
              AND v.time_series_is_valid=1 AND v.cross_sectional_is_valid=0
            ORDER BY v.updated_at DESC LIMIT 1
            """
        )
        result["cs_only_validity"] = tx.fetch_one(
            """
            SELECT v.*, cs.scoring_version AS cs_scoring_version
            FROM factor_validity_status v
            JOIN factor_ic_summary_metrics cs ON cs.id=v.cross_sectional_summary_id
            WHERE v.is_sub_factor_id=1 AND v.time_series_summary_id IS NOT NULL
              AND v.cross_sectional_summary_id IS NOT NULL
              AND v.time_series_is_valid=0 AND v.cross_sectional_is_valid=1
            ORDER BY v.updated_at DESC LIMIT 1
            """
        )
        result["route"] = tx.fetch_one(
            """
            SELECT r.*, m.evaluation_type, m.interval, m.return_bar_interval,
                   m.forward_return_bars, m.window_scope, m.scoring_version AS metric_scoring_version,
                   b.batch_uid, b.status AS batch_status, b.publish_status, b.published_at,
                   b.as_of_time AS batch_as_of_time, b.route_profile_key, b.environment_status,
                   b.environment_snapshot, b.factor_set_snapshot, b.score_rule_version
            FROM market_environment_factor_route r
            JOIN market_environment_factor_metric m ON m.id=r.metric_id
            JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
            WHERE r.is_active=1 AND r.is_eligible=1
            ORDER BY r.rank_no, r.id LIMIT 1
            """
        )
        result["daily_fact"] = tx.fetch_one(
            """
            SELECT * FROM market_environment_daily
            WHERE label_kind='fact' AND is_current=1
            ORDER BY environment_date DESC, id DESC LIMIT 1
            """
        )
        result["daily_forecast"] = tx.fetch_one(
            """
            SELECT * FROM market_environment_daily
            WHERE label_kind='forecast' AND is_current=1
            ORDER BY environment_date DESC, id DESC LIMIT 1
            """
        )
        result["insufficient_metric"] = tx.fetch_one(
            """
            SELECT m.*, b.batch_uid
            FROM market_environment_factor_metric m
            JOIN market_environment_eval_batch b ON b.id=m.eval_batch_id
            WHERE m.metric_status='insufficient_sample'
            ORDER BY m.id DESC LIMIT 1
            """
        )
        result["approved_schema"] = tx.fetch_one(
            "SELECT schema_version FROM raw_data_schema_version WHERE status='approved' ORDER BY id DESC LIMIT 1"
        )
        result["universe_keys"] = [
            row["universe_key"]
            for row in tx.fetch_all(
                "SELECT DISTINCT universe_key FROM coin_universe_symbols WHERE is_active=1 ORDER BY universe_key"
            )
        ]
        result["kb"] = tx.fetch_one(
            "SELECT id, mapping_status, validation_status FROM kb_factor_extractions ORDER BY id DESC LIMIT 1"
        )
        result["feedback"] = tx.fetch_one(
            "SELECT id FROM market_environment_strategy_feedback_submissions ORDER BY id DESC LIMIT 1"
        )
        # Find a revision pair for a point-in-time read test, if one exists.
        result["revision_pair"] = tx.fetch_one(
            """
            SELECT a.environment_date, a.label_kind, a.id AS old_id, a.revision AS old_revision,
                   a.available_at AS old_available_at, b.id AS new_id, b.revision AS new_revision,
                   b.available_at AS new_available_at
            FROM market_environment_daily a
            JOIN market_environment_daily b
              ON b.environment_date=a.environment_date AND b.label_kind=a.label_kind
             AND b.revision>a.revision
            WHERE a.available_at IS NOT NULL AND b.available_at IS NOT NULL
            ORDER BY b.available_at DESC LIMIT 1
            """
        )
    return result


def scope_args(row: dict[str, Any], *, ic_scope: str | None = None, as_of: datetime | None = None) -> dict[str, Any]:
    """Build exact metric arguments from one summary row."""

    return {
        "factor_ref": f"sub_factor:{row['factor_id']}" if int(row.get("is_sub_factor_id") or 0) else f"factor:{row['factor_id']}",
        "ic_scope": ic_scope or row["ic_scope"],
        "calculation_mode": row["calculation_mode"],
        "universe_key": row["universe_key"],
        "window_scope": row["window_scope"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
        "as_of": (as_of or datetime.now(timezone.utc)).isoformat(),
        "scoring_version": row["scoring_version"],
        "symbol": row.get("symbol") or "",
        "run_id": row["run_id"],
    }


def validity_args(row: dict[str, Any], scope: str, *, as_of: datetime | None = None) -> dict[str, Any]:
    """Build exact validity arguments from a validity row."""

    scoring = row.get(f"{scope}_scoring_version") or row.get("scoring_version")
    return {
        "factor_ref": f"sub_factor:{row['factor_id']}" if int(row.get("is_sub_factor_id") or 0) else f"factor:{row['factor_id']}",
        "validity_scope": scope,
        "calculation_mode": "direct",
        "universe_key": row["universe_key"],
        "window_scope": row["window_scope"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
        "as_of": (as_of or datetime.now(timezone.utc)).isoformat(),
        "scoring_version": scoring,
        "symbol": "",
        "run_id": row["run_id"],
    }


def compare_fields(api_row: dict[str, Any], db_row: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    """Return fields whose API and DB values differ under scalar semantics."""

    return [field for field in fields if field in api_row and not scalar_equal(api_row.get(field), db_row.get(field))]


def run() -> dict[str, Any]:
    """Execute the complete dynamic read-only matrix and return the report."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    if not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("test MCP host gate failed")
    settings = SettingsLoader.load("test", ROOT)
    if settings.environment != "test":
        raise SystemExit("test environment gate failed")
    db = DatabaseClient.from_settings(settings.database)
    fixtures = discover(db)
    stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%dT%H%M%S%z")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-direct-functional-deep"
    runner = DeepRunner(token, output, db)
    before = table_state(db)

    # Protocol and authentication boundary.
    init = runner.runner.request(
        "INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-direct-functional-deep", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    runner.protocol_version = init_result.get("protocolVersion")
    runner.runner.protocol_version = runner.protocol_version
    runner.record(
        "MCP-INIT",
        "MCP initialization and service identity",
        init.get("http_status") == 200 and runner.protocol_version == "2025-06-18" and bool(init_result.get("serverInfo")),
        "HTTP 200 with accepted protocolVersion and serverInfo",
        {"protocol_version": runner.protocol_version, "server_info": init_result.get("serverInfo")},
        call=init,
        severity="P0",
        classification="FAIL_PROTOCOL",
    )
    if runner.protocol_version:
        runner.runner.notify_initialized("NOTIFY")
    tools_call = runner.runner.request("TOOLS", "tools/list", {})
    tool_rows = (((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or [])
    tool_names = {row.get("name") for row in tool_rows if isinstance(row, dict)}
    required = {
        "factor_search", "factor_catalog_stats", "factor_rank", "factor_get_detail",
        "factor_get_details_batch", "factor_get_metrics", "factor_get_metrics_batch",
        "factor_get_validity", "factor_get_validity_batch", "factor_get_formula",
        "factor_get_metric_slices", "factor_get_environment_metrics", "factor_get_environment_tags",
        "environment_get_recommendations", "environment_get_daily", "universe_list_symbols",
        "schema_get_factor_fields", "schema_get_raw_data", "kb_factor_candidate_search",
    }
    runner.record(
        "MCP-TOOLS",
        "Required read-only tools are discoverable",
        required <= tool_names,
        "all required read-only tools are listed",
        {"tool_count": len(tool_names), "missing": sorted(required - tool_names)},
        call=tools_call,
    )
    list_payload = json.dumps({"jsonrpc": "2.0", "id": "auth-negative", "method": "tools/list", "params": {}}, separators=(",", ":")).encode()
    no_auth = runner.raw("NO-AUTH", list_payload, token=None)
    bad_auth = runner.raw("BAD-AUTH", list_payload, token="invalid-test-token")
    runner.record(
        "MCP-AUTH",
        "Unauthenticated and invalid-token requests are denied",
        no_auth["http_status"] in {401, 403} and bad_auth["http_status"] in {401, 403},
        "both requests return 401/403 without business data",
        {"no_auth_status": no_auth["http_status"], "bad_auth_status": bad_auth["http_status"]},
        severity="P0",
        classification="FAIL_AUTH",
    )
    unknown = runner.call("PROTO-UNKNOWN-TOOL", "tool_that_does_not_exist", {})
    extra = runner.call("PROTO-EXTRA-ARG", "universe_list_symbols", {"universe_key": "all", "unexpected": 1})
    runner.record(
        "MCP-INVALID-ARGS",
        "Unknown tool and additional argument are rejected",
        all(call.get("is_error") is True or bool(error_code(call)) for call in (unknown, extra)),
        "both invalid calls return structured errors",
        {"unknown_tool": error_code(unknown), "extra_argument": error_code(extra)},
        call=extra,
    )
    malformed = runner.raw("MALFORMED", b'{"jsonrpc":"2.0",')
    top_array = runner.raw("TOP-ARRAY", b'[]')
    runner.record(
        "MCP-MALFORMED",
        "Malformed JSON and non-object root do not enter business dispatch",
        malformed["http_status"] >= 400 or "error" in (malformed.get("envelope") or {})
        and top_array["http_status"] >= 400 or "error" in (top_array.get("envelope") or {}),
        "protocol errors or HTTP errors are returned",
        {"malformed_status": malformed["http_status"], "array_status": top_array["http_status"]},
        severity="P1",
        classification="FAIL_CONTRACT",
    )
    duplicate_payload = json.dumps({"jsonrpc": "2.0", "id": "dup", "method": "tools/list", "params": {}}, separators=(",", ":")).encode()
    duplicate_a = runner.raw("DUPLICATE-ID-A", duplicate_payload, token=runner.token)
    duplicate_b = runner.raw("DUPLICATE-ID-B", duplicate_payload, token=runner.token)
    runner.record(
        "MCP-DUPLICATE-ID",
        "Repeated JSON-RPC ids do not corrupt the response envelope",
        all((row.get("envelope") or {}).get("jsonrpc") == "2.0" for row in (duplicate_a, duplicate_b)),
        "each response remains a valid JSON-RPC envelope",
        {"first_status": duplicate_a["http_status"], "second_status": duplicate_b["http_status"]},
        classification="FAIL_CONTRACT",
    )

    # Schema contract and content/structuredContent parity.
    schema = runner.call("SCHEMA-FULL", "schema_get_factor_fields", {})
    schema_data = data(schema)
    schema_fields = schema_data.get("fields") if isinstance(schema_data.get("fields"), list) else []
    known_field = schema_fields[0].get("field_name") if schema_fields and isinstance(schema_fields[0], dict) else "close"
    subset = runner.call("SCHEMA-SUBSET", "schema_get_factor_fields", {"field_names": [known_field]})
    unknown_field = runner.call("SCHEMA-UNKNOWN", "schema_get_factor_fields", {"field_names": ["__questtest_unknown_field__"]})
    unknown_rows = data(unknown_field).get("fields") or []
    unknown_resolved = bool(unknown_rows) and unknown_rows[0].get("resolution_status") == "unresolved" and "FIELD_NOT_APPROVED" in (unknown_rows[0].get("unresolved_reasons") or [])
    runner.record(
        "SCHEMA-CONTRACT",
        "Approved schema, subset lookup, and unknown-field resolution are explicit",
        successful(schema) and successful(subset) and bool(schema_fields) and unknown_resolved,
        "known fields resolve and unknown fields are marked unresolved rather than silently mapped",
        {"field_count": len(schema_fields), "known_field": known_field, "unknown_resolution": unknown_rows[0] if unknown_rows else None},
        call=unknown_field,
    )
    approved_version = (fixtures.get("approved_schema") or {}).get("schema_version")
    raw_default = runner.call("RAW-SCHEMA", "schema_get_raw_data", {})
    raw_explicit = runner.call("RAW-SCHEMA-EXPLICIT", "schema_get_raw_data", {"schema_version": approved_version} if approved_version else {})
    runner.record(
        "SCHEMA-RAW-VERSION",
        "Default and explicit approved raw-data schema agree",
        successful(raw_default) and successful(raw_explicit) and data(raw_default).get("schema_version") == data(raw_explicit).get("schema_version"),
        "both requests return the same approved schema version",
        {"default": data(raw_default).get("schema_version"), "explicit": data(raw_explicit).get("schema_version")},
        call=raw_explicit,
    )
    bad_raw = runner.call("RAW-SCHEMA-UNKNOWN", "schema_get_raw_data", {"schema_version": "factor-canonical-does-not-exist"})
    runner.record(
        "SCHEMA-RAW-UNKNOWN",
        "Unknown raw-data schema does not silently fall back",
        bool(error_code(bad_raw)) or not data(bad_raw),
        "unknown version is rejected or returns no contract",
        {"error_code": error_code(bad_raw), "data_keys": sorted(data(bad_raw))},
        call=bad_raw,
    )
    parity = schema.get("representations_equal") is not False and raw_default.get("representations_equal") is not False
    runner.record(
        "MCP-CONTENT-PARITY",
        "content and structuredContent carry the same business representation",
        parity,
        "all comparable successful responses have equal representations",
        {"schema_equal": schema.get("representations_equal"), "raw_equal": raw_default.get("representations_equal")},
        call=raw_default,
        classification="FAIL_CONTRACT",
    )

    # Direct metrics and validity paths.
    aggregate = fixtures.get("aggregate")
    metric_calls: dict[str, dict[str, Any]] = {}
    if aggregate:
        for scope in ("time_series", "cross_sectional"):
            row = dict(aggregate)
            if scope == "cross_sectional":
                with db.transaction() as tx:
                    row = tx.fetch_one(
                        """SELECT * FROM factor_ic_summary_metrics WHERE factor_id=%s AND is_sub_factor_id=%s AND run_id=%s AND ic_scope='cross_sectional' AND calculation_mode=%s AND symbol='' LIMIT 1""",
                        (aggregate["factor_id"], aggregate["is_sub_factor_id"], aggregate["run_id"], aggregate["calculation_mode"]),
                    ) or row
            args = scope_args(row, ic_scope=scope)
            call = runner.call(f"METRIC-{scope.upper()}", "factor_get_metrics", args)
            metric_calls[scope] = call
            summaries = data(call).get("ic_summaries") or []
            matching = [item for item in summaries if isinstance(item, dict) and int(item.get("id") or 0) == int(row["id"])]
            fields = ("id", "run_id", "factor_id", "is_sub_factor_id", "ic_scope", "calculation_mode", "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars", "universe_key", "symbol", "window_scope", "period_start", "period_end", "valid_slice_count", "mean_ic", "mean_rank_ic", "icir", "rank_icir", "scoring_version", "final_score")
            mismatches = compare_fields(matching[0], row, fields) if matching else ["missing_summary"]
            runner.record(
                f"METRIC-{scope.upper()}",
                f"Exact {scope} metric is bound to one completed DB summary",
                successful(call) and bool(matching) and not mismatches,
                "returned summary identity and numeric fields equal the selected DB row",
                {"requested": {k: args[k] for k in ("factor_ref", "ic_scope", "run_id", "symbol", "window_scope")}, "returned_ids": [item.get("id") for item in summaries], "mismatches": mismatches},
                call=call,
                classification="FAIL_DATA",
            )
        # Explicit wrong scope values must not silently substitute another run.
        wrong_score = runner.call("METRIC-WRONG-SCORE", "factor_get_metrics", {**scope_args(aggregate, ic_scope="time_series"), "scoring_version": "v-no-such"})
        wrong_symbol = runner.call("METRIC-WRONG-SYMBOL", "factor_get_metrics", {**scope_args(aggregate, ic_scope="time_series"), "symbol": "__NO_SUCH_SYMBOL__"})
        runner.record(
            "METRIC-SCOPE-ISOLATION",
            "Wrong scoring version or symbol does not substitute another metric",
            (bool(error_code(wrong_score)) or not data(wrong_score).get("ic_summaries"))
            and all(str(item.get("symbol") or "") == "__NO_SUCH_SYMBOL__" for item in data(wrong_symbol).get("ic_summaries") or []),
            "mismatched scope returns an explicit empty/error result",
            {"wrong_score": error_code(wrong_score), "wrong_symbol_ids": [item.get("id") for item in data(wrong_symbol).get("ic_summaries") or []]},
            call=wrong_score,
        )
        # Batch partial result with two known factors and one syntactically valid missing ref.
        with db.transaction() as tx:
            second = tx.fetch_one(
                """SELECT s.* FROM factor_ic_summary_metrics s JOIN factor_ic_runs r ON r.run_id=s.run_id AND r.status='completed' WHERE s.is_sub_factor_id=1 AND s.ic_scope='time_series' AND s.calculation_mode=%s AND s.universe_key=%s AND s.symbol='' AND s.window_scope=%s AND s.factor_bar_interval=%s AND s.factor_window_bars=%s AND s.return_bar_interval=%s AND s.forward_return_bars=%s AND s.scoring_version=%s ORDER BY s.id DESC LIMIT 1""",
                (aggregate["calculation_mode"], aggregate["universe_key"], aggregate["window_scope"], aggregate["factor_bar_interval"], aggregate["factor_window_bars"], aggregate["return_bar_interval"], aggregate["forward_return_bars"], aggregate["scoring_version"]),
            )
        refs = [f"sub_factor:{aggregate['factor_id']}"]
        if second and int(second["factor_id"]) != int(aggregate["factor_id"]):
            refs.append(f"sub_factor:{second['factor_id']}")
        refs.append("sub_factor:999999999")
        batch_args = scope_args(aggregate, ic_scope="time_series")
        batch_args.pop("factor_ref", None)
        batch_args["factor_refs"] = refs
        batch = runner.call("METRIC-BATCH", "factor_get_metrics_batch", batch_args)
        batch_items = data(batch).get("items") or []
        batch_errors = [item for item in batch_items if item.get("success") is False]
        runner.record(
            "METRIC-BATCH",
            "Metric batch preserves per-item success and not-found outcomes",
            successful(batch) and len(batch_items) == len(refs) and len(batch_errors) == 1 and (batch_errors[0].get("error") or {}).get("code") == "FACTOR_NOT_FOUND",
            "known refs succeed and the missing ref remains item-scoped",
            {"requested_refs": refs, "items": [{"factor_ref": item.get("factor_ref"), "success": item.get("success"), "error": (item.get("error") or {}).get("code")} for item in batch_items]},
            call=batch,
        )
    else:
        for case_id in ("METRIC-TIME_SERIES", "METRIC-CROSS_SECTIONAL", "METRIC-SCOPE-ISOLATION", "METRIC-BATCH"):
            runner.record(case_id, "Metric fixture discovery", False, "completed aggregate scope", {}, blocked="no completed aggregate scope in test DB")

    child = fixtures.get("child")
    if child:
        child_call = runner.call("CHILD-METRIC", "factor_get_metrics", scope_args(child))
        child_rows = data(child_call).get("ic_summaries") or []
        child_ok = successful(child_call) and bool(child_rows) and all(row.get("calculation_mode") == "child_aggregate" and int(row.get("factor_id") or 0) == int(child["factor_id"]) for row in child_rows)
        runner.record(
            "CHILD-AGGREGATE-METRIC",
            "Parent child_aggregate metric does not cross factor or calculation mode",
            child_ok,
            "returned summaries remain on the requested parent and child_aggregate scope",
            {"factor_ref": f"factor:{child['factor_id']}", "returned": [{"id": row.get("id"), "factor_id": row.get("factor_id"), "mode": row.get("calculation_mode")} for row in child_rows]},
            call=child_call,
            classification="FAIL_DATA",
        )
    else:
        runner.record("CHILD-AGGREGATE-METRIC", "Parent child_aggregate fixture", False, "child aggregate summary", {}, blocked="no child_aggregate summary discovered")

    # Validity scope and any_valid_scope behavior.
    validity_rows: dict[str, dict[str, Any]] = {}
    for label in ("ts_only_validity", "cs_only_validity"):
        row = fixtures.get(label)
        if not row:
            runner.record(f"VALIDITY-{label}", "One-dimension validity fixture", False, "validity row", {}, blocked="no matching one-dimension validity row")
            continue
        scope = "time_series" if label.startswith("ts") else "cross_sectional"
        call = runner.call(f"VALIDITY-{scope.upper()}", "factor_get_validity", validity_args(row, scope))
        validity_rows[scope] = call
        item = data(call).get("item") or ((data(call).get("items") or [{}])[0])
        expected_id = int(row["id"])
        expected_status = row[f"{scope}_status"]
        expected_score = row[f"{scope}_score"]
        valid_item = bool(item) and int(item.get("id") or 0) == expected_id and item.get(f"{scope}_status") == expected_status and scalar_equal(item.get(f"{scope}_score"), expected_score)
        runner.record(
            f"VALIDITY-{scope.upper()}",
            f"{scope} validity returns the scope-specific row and status",
            successful(call) and valid_item,
            "validity id, status, and score match DB",
            {"expected_id": expected_id, "returned_id": item.get("id"), "expected_status": expected_status, "returned_status": item.get(f"{scope}_status"), "expected_score": expected_score, "returned_score": item.get(f"{scope}_score")},
            call=call,
            classification="FAIL_DATA",
        )
    # Batch validity uses one exact-scope known ref plus a missing ref.  The
    # TS-only and CS-only fixtures intentionally come from different runs, so
    # putting them in one batch would test an invalid mixed-scope request.
    batch_valid_row = fixtures.get("ts_only_validity") or fixtures.get("cs_only_validity")
    if batch_valid_row:
        refs = [f"sub_factor:{batch_valid_row['factor_id']}" ]
        refs.append("sub_factor:999999999")
        scope = "time_series" if fixtures.get("ts_only_validity") is batch_valid_row else "cross_sectional"
        args = validity_args(batch_valid_row, scope)
        args.pop("factor_ref", None)
        args["factor_refs"] = refs
        vb = runner.call("VALIDITY-BATCH", "factor_get_validity_batch", args)
        items = data(vb).get("items") or []
        failures = [item for item in items if item.get("success") is False]
        runner.record(
            "VALIDITY-BATCH",
            "Validity batch preserves per-item not-found isolation",
            successful(vb) and len(items) == len(refs) and len(failures) == 1 and (failures[0].get("error") or {}).get("code") == "FACTOR_NOT_FOUND",
            "known validity refs return rows and missing ref is item-scoped",
            {"requested_refs": refs, "items": [{"factor_ref": item.get("factor_ref"), "success": item.get("success"), "error": (item.get("error") or {}).get("code")} for item in items]},
            call=vb,
        )
    else:
        runner.record("VALIDITY-BATCH", "Validity batch fixture", False, "one-dimension validity row", {}, blocked="no validity fixture discovered")

    # Slices and immutable formula evidence.
    symbol = fixtures.get("symbol")
    if symbol:
        slice_args = scope_args(symbol)
        slice_args.update({"start_time": local_iso(symbol["period_start"]), "end_time": local_iso(symbol["period_end"]), "limit": 5})
        slices = runner.call("SLICES-PAGE-1", "factor_get_metric_slices", slice_args)
        slice_items = data(slices).get("items") or []
        cursor = (business(slices).get("meta") or {}).get("next_cursor")
        runner.record(
            "SLICES-PAGE-1",
            "Metric slices honor the bounded limit and requested identity",
            successful(slices) and len(slice_items) <= 5 and all(int(item.get("factor_id") or 0) == int(symbol["factor_id"]) and item.get("run_id") == symbol["run_id"] for item in slice_items),
            "slice rows are bounded and remain on the selected factor/run",
            {"count": len(slice_items), "ids": [item.get("id") for item in slice_items], "cursor_present": bool(cursor)},
            call=slices,
            classification="FAIL_DATA",
        )
        if cursor:
            page2 = runner.call("SLICES-PAGE-2", "factor_get_metric_slices", {**slice_args, "cursor": cursor})
            page2_items = data(page2).get("items") or []
            ids = [int(item.get("id")) for item in slice_items + page2_items if item.get("id") is not None]
            runner.record(
                "SLICES-PAGE-2",
                "Slice cursor continues without duplicates",
                successful(page2) and len(ids) == len(set(ids)) and ids == sorted(ids),
                "second page is monotonic and disjoint",
                {"count": len(page2_items), "ids": ids},
                call=page2,
            )
            tampered = runner.call("SLICES-CURSOR-TAMPER", "factor_get_metric_slices", {**slice_args, "cursor": str(cursor)[:-1] + ("A" if str(cursor)[-1:] != "A" else "B")})
            runner.record(
                "SLICES-CURSOR-BIND",
                "Tampered slice cursor is rejected or empty",
                bool(error_code(tampered)) or not data(tampered).get("items"),
                "cursor signature/query binding prevents another page",
                {"error_code": error_code(tampered), "count": len(data(tampered).get("items") or [])},
                call=tampered,
            )
        else:
            runner.record("SLICES-CURSOR-BIND", "Slice cursor precondition", False, "a cursor from a full first page", {}, blocked="selected symbol scope returned no continuation cursor")
        with db.transaction() as tx:
            evidence = tx.fetch_one(
                """SELECT e.* FROM factor_ic_run_formula_evidence e WHERE e.factor_id=%s AND e.is_sub_factor_id=%s AND e.run_id=%s ORDER BY e.id DESC LIMIT 1""",
                (symbol["factor_id"], symbol["is_sub_factor_id"], symbol["run_id"]),
            )
        if evidence:
            formula_args = {
                "factor_ref": f"sub_factor:{symbol['factor_id']}",
                "run_id": evidence["run_id"],
                "calculation_mode": "direct",
                "interval": evidence["factor_bar_interval"],
                "factor_window_bars": evidence["factor_window_bars"],
                "return_bar_interval": evidence["return_bar_interval"],
                "forward_return_bars": int(evidence["forward_return_bars"]),
                "as_of": datetime.now(timezone.utc).isoformat(),
            }
            formula = runner.call("FORMULA-EXACT", "factor_get_formula", formula_args)
            formula_data = data(formula)
            formula_ok = successful(formula) and formula_data.get("formula_hash") == evidence.get("formula_hash") and formula_data.get("expression") == evidence.get("expression")
            runner.record(
                "FORMULA-EXACT",
                "Immutable formula response matches DB evidence hash and expression",
                formula_ok,
                "formula hash and expression equal the selected completed evidence row",
                {"factor_ref": formula_args["factor_ref"], "run_id": formula_args["run_id"], "hash_match": formula_data.get("formula_hash") == evidence.get("formula_hash"), "expression_match": formula_data.get("expression") == evidence.get("expression")},
                call=formula,
                classification="FAIL_DATA",
            )
            wrong_formula = runner.call("FORMULA-WRONG-WINDOW", "factor_get_formula", {**formula_args, "factor_window_bars": "999999H"})
            runner.record(
                "FORMULA-SCOPE-ISOLATION",
                "Wrong formula window is not silently substituted",
                bool(error_code(wrong_formula)) or not data(wrong_formula),
                "mismatched window returns an explicit empty/error result",
                {"error_code": error_code(wrong_formula), "data_keys": sorted(data(wrong_formula))},
                call=wrong_formula,
            )
            run_completed = None
            with db.transaction() as tx:
                run_completed = (tx.fetch_one("SELECT completed_at FROM factor_ic_runs WHERE run_id=%s", (evidence["run_id"],)) or {}).get("completed_at")
            if run_completed:
                # factor_ic_runs lifecycle timestamps are persisted as local wall time.
                completed_local = run_completed.replace(tzinfo=LOCAL_TZ) if run_completed.tzinfo is None else run_completed
                before_formula = runner.call("FORMULA-BEFORE-COMPLETE", "factor_get_formula", {**formula_args, "as_of": (completed_local - timedelta(microseconds=1)).isoformat()})
                at_formula = runner.call("FORMULA-AT-COMPLETE", "factor_get_formula", {**formula_args, "as_of": completed_local.isoformat()})
                runner.record(
                    "FORMULA-PIT",
                    "Formula evidence is hidden before run completion and visible at completion",
                    (bool(error_code(before_formula)) or not data(before_formula)) and bool(data(at_formula)),
                    "completion boundary is inclusive and no earlier evidence leaks",
                    {"before_error": error_code(before_formula), "at_has_data": bool(data(at_formula)), "completed_at": completed_local.isoformat()},
                    call=at_formula,
                    classification="FAIL_DATA",
                )
    else:
        for case_id in ("SLICES-PAGE-1", "SLICES-CURSOR-BIND", "FORMULA-EXACT"):
            runner.record(case_id, "Symbol/evidence fixture", False, "symbol summary with formula evidence", {}, blocked="no symbol summary fixture discovered")

    # Environment metrics, tags, recommendations, and daily revisions.
    route = fixtures.get("route")
    if route:
        env_common = {"factor_ref": route["factor_ref"], "market_scope": route["market_scope"], "route_profile_key": route["route_profile_key"]}
        tags = runner.call("ENV-TAGS", "factor_get_environment_tags", env_common)
        tag_data = data(tags)
        tag_items = response_rows(tag_data, "items", "tags")
        tag_refs = {str(item.get("factor_ref")) for item in tag_items if item.get("factor_ref")}
        pub = tag_data.get("publication") if isinstance(tag_data.get("publication"), dict) else {}
        runner.record(
            "ENV-TAGS",
            "Environment tags stay on the active publication and factor",
            successful(tags) and (not tag_refs or tag_refs == {str(route["factor_ref"])}) and (not pub or str(pub.get("publication_uid")) == str(route["publication_uid"])),
            "tags reference only the requested active factor/publication",
            {"returned_refs": sorted(tag_refs), "returned_publication": pub.get("publication_uid"), "expected_publication": route["publication_uid"]},
            call=tags,
            severity="P0",
            classification="FAIL_DATA",
        )
        env_args = {**env_common, "batch_uid": route["batch_uid"], "label_code": route["label_code"], "evaluation_type": route["evaluation_type"], "limit": 100}
        env = runner.call("ENV-METRICS", "factor_get_environment_metrics", env_args)
        env_items = response_rows(data(env), "items", "metrics")
        env_ok = successful(env) and all(str(item.get("batch_uid") or route["batch_uid"]) == str(route["batch_uid"]) and str(item.get("label_code") or route["label_code"]) == str(route["label_code"]) for item in env_items)
        runner.record(
            "ENV-METRICS",
            "Environment metrics are bound to the requested batch and label",
            env_ok,
            "no metric from another batch or label is returned",
            {"count": len(env_items), "batch_uids": sorted({str(item.get("batch_uid")) for item in env_items if item.get("batch_uid")}), "labels": sorted({str(item.get("label_code")) for item in env_items if item.get("label_code")})},
            call=env,
            severity="P0",
            classification="FAIL_DATA",
        )
        wrong_batch = runner.call("ENV-WRONG-BATCH", "factor_get_environment_metrics", {**env_common, "batch_uid": str(uuid4()), "limit": 5})
        wrong_scope = runner.call("ENV-WRONG-SCOPE", "factor_get_environment_metrics", {**env_common, "market_scope": "__missing_scope__", "limit": 5})
        runner.record(
            "ENV-SCOPE-ISOLATION",
            "Unknown batch and market scope do not fall back to active data",
            (bool(error_code(wrong_batch)) or not response_rows(data(wrong_batch))) and (bool(error_code(wrong_scope)) or not response_rows(data(wrong_scope))),
            "both mismatched selectors return explicit empty/error results",
            {"wrong_batch": error_code(wrong_batch), "wrong_scope": error_code(wrong_scope)},
            call=wrong_batch,
            severity="P0",
            classification="FAIL_DATA",
        )
        publication_time = route.get("published_at")
        if publication_time:
            published = publication_time.replace(tzinfo=LOCAL_TZ) if isinstance(publication_time, datetime) and publication_time.tzinfo is None else utc_instant(publication_time)
            if published:
                historical = published - timedelta(hours=1)
                rec = runner.call("REC-PIT", "environment_get_recommendations", {"market_scope": route["market_scope"], "route_profile_key": route["route_profile_key"], "as_of": historical.isoformat(), "limit": 20})
                rec_pub = data(rec).get("publication") if isinstance(data(rec).get("publication"), dict) else {}
                returned_published = utc_instant(rec_pub.get("published_at")) if rec_pub else None
                leak = bool(returned_published and returned_published > historical)
                runner.record(
                    "REC-PIT",
                    "Historical recommendation does not expose a future publication",
                    not leak,
                    "publication.published_at is absent or not newer than as_of",
                    {"as_of": historical.isoformat(), "returned_publication": rec_pub.get("publication_uid"), "returned_published_at": rec_pub.get("published_at"), "future_leak": leak},
                    call=rec,
                    severity="P0",
                    classification="FAIL_DATA",
                )
        current_rec = runner.call("REC-CURRENT", "environment_get_recommendations", {"market_scope": route["market_scope"], "route_profile_key": route["route_profile_key"], "limit": 20})
        rec_status = data(current_rec).get("status")
        runner.record(
            "REC-CURRENT",
            "Current recommendation returns a documented status envelope",
            successful(current_rec) and rec_status in {"ready", "no_recommendation", "not_ready"},
            "current response is ready or an explicit no-recommendation state",
            {"status": rec_status, "reason_code": data(current_rec).get("reason_code"), "item_count": len(data(current_rec).get("items") or [])},
            call=current_rec,
        )
    else:
        for case_id in ("ENV-TAGS", "ENV-METRICS", "ENV-SCOPE-ISOLATION", "REC-PIT", "REC-CURRENT"):
            runner.record(case_id, "Active route fixture", False, "active eligible route", {}, blocked="no active route in test DB")

    for label, fixture in (("fact", fixtures.get("daily_fact")), ("forecast", fixtures.get("daily_forecast"))):
        if not fixture:
            runner.record(f"DAILY-{label.upper()}", "Daily fixture", False, f"current {label} row", {}, blocked=f"no current {label} row")
            continue
        daily = runner.call(f"DAILY-{label.upper()}", "environment_get_daily", {"label_kind": label, "limit": 10})
        rows = response_rows(data(daily), "items", "results")
        all_kind = all(item.get("label_kind") == label for item in rows)
        runner.record(
            f"DAILY-{label.upper()}",
            f"Current {label} daily rows preserve label kind",
            successful(daily) and all_kind,
            f"all returned rows have label_kind={label}",
            {"count": len(rows), "label_kinds": sorted({str(item.get("label_kind")) for item in rows})},
            call=daily,
        )
        exact = runner.call(f"DAILY-{label.upper()}-DATE", "environment_get_daily", {"label_kind": label, "environment_date": str(fixture["environment_date"]), "limit": 10})
        exact_rows = response_rows(data(exact), "items", "results")
        runner.record(
            f"DAILY-{label.upper()}-DATE",
            f"{label} exact date filter is not ignored",
            successful(exact) and all(str(item.get("environment_date", "")).split("T")[0] == str(fixture["environment_date"]) for item in exact_rows),
            "only the requested environment date is returned",
            {"requested": str(fixture["environment_date"]), "returned_dates": [item.get("environment_date") for item in exact_rows]},
            call=exact,
        )
    invalid_date = runner.call("DAILY-INVALID-DATE", "environment_get_daily", {"label_kind": "fact", "environment_date": "not-a-date", "limit": 5})
    runner.record(
        "DAILY-INVALID-DATE",
        "Invalid daily date is rejected",
        bool(error_code(invalid_date)) or invalid_date.get("is_error") is True,
        "invalid date returns a structured rejection",
        {"error_code": error_code(invalid_date), "http_status": invalid_date.get("http_status")},
        call=invalid_date,
    )
    revision = fixtures.get("revision_pair")
    if revision:
        old_available = utc_instant(revision["old_available_at"])
        new_available = utc_instant(revision["new_available_at"])
        if old_available and new_available and old_available < new_available:
            before_new = runner.call("DAILY-PIT-BEFORE-NEW", "environment_get_daily", {"label_kind": revision["label_kind"], "environment_date": str(revision["environment_date"]), "as_of": (new_available - timedelta(microseconds=1)).isoformat(), "limit": 10})
            after_new = runner.call("DAILY-PIT-AFTER-NEW", "environment_get_daily", {"label_kind": revision["label_kind"], "environment_date": str(revision["environment_date"]), "as_of": (new_available + timedelta(microseconds=1)).isoformat(), "limit": 10})
            before_ids = {int(item.get("id")) for item in response_rows(data(before_new), "items", "results") if item.get("id") is not None}
            after_ids = {int(item.get("id")) for item in response_rows(data(after_new), "items", "results") if item.get("id") is not None}
            runner.record(
                "DAILY-PIT-REVISION",
                "Daily as_of selects only revisions available at the requested instant",
                int(revision["new_id"]) not in before_ids and int(revision["new_id"]) in after_ids,
                "new revision is hidden before available_at and visible after it",
                {"old_id": revision["old_id"], "new_id": revision["new_id"], "before_ids": sorted(before_ids), "after_ids": sorted(after_ids)},
                call=after_new,
                severity="P0",
                classification="FAIL_DATA",
            )
    else:
        runner.record("DAILY-PIT-REVISION", "Daily revision fixture", False, "two revisions with ordered available_at", {}, blocked="no revision pair in test DB")

    # Universe and feedback read paths.
    universe_keys = [key for key in fixtures.get("universe_keys", []) if key in {"all", "main", "altcoin"}] or fixtures.get("universe_keys", [])[:1]
    for key in universe_keys:
        universe = runner.call(f"UNIVERSE-{key}", "universe_list_symbols", {"universe_key": key})
        symbols = response_rows(data(universe), "items", "symbols")
        values = [str(item.get("symbol") if isinstance(item, dict) else item) for item in symbols]
        runner.record(
            f"UNIVERSE-{key}",
            f"Universe {key} returns unique active symbols",
            successful(universe) and bool(values) and len(values) == len(set(values)),
            "symbol list is non-empty and has no duplicates",
            {"count": len(values), "sample": values[:5]},
            call=universe,
        )
    unknown_universe = runner.call("UNIVERSE-UNKNOWN", "universe_list_symbols", {"universe_key": f"missing-{uuid4()}"})
    runner.record(
        "UNIVERSE-UNKNOWN",
        "Unknown universe does not fall back to another universe",
        bool(error_code(unknown_universe)) or not response_rows(data(unknown_universe), "items", "symbols"),
        "unknown universe is rejected or empty",
        {"error_code": error_code(unknown_universe), "count": len(response_rows(data(unknown_universe), "items", "symbols"))},
        call=unknown_universe,
        severity="P0",
        classification="FAIL_DATA",
    )
    feedback = fixtures.get("feedback")
    if feedback:
        feedback_call = runner.call("FEEDBACK-STATUS", "get_feedback_submission_status", {"submission_id": str(feedback["id"])})
        runner.record(
            "FEEDBACK-STATUS",
            "Existing feedback status is readable without mutation",
            successful(feedback_call) or bool(error_code(feedback_call)),
            "status endpoint returns data or a documented business error",
            {"submission_id": str(feedback["id"]), "error_code": error_code(feedback_call), "data_keys": sorted(data(feedback_call))},
            call=feedback_call,
        )
    else:
        runner.record("FEEDBACK-STATUS", "Feedback fixture", False, "existing submission", {}, blocked="no feedback submission in test DB")

    # Invalid parameter matrix for non-catalog read tools.
    if aggregate:
        base_metric = scope_args(aggregate, ic_scope="time_series")
        missing_required = dict(base_metric)
        missing_required.pop("factor_ref")
        bad_enum = {**base_metric, "ic_scope": "invalid_scope"}
        unknown_arg = {**base_metric, "unexpected": 1}
        for case_id, args in (("MISSING-REQUIRED", missing_required), ("BAD-ENUM", bad_enum), ("UNKNOWN-ARG", unknown_arg)):
            call = runner.call(f"INPUT-{case_id}", "factor_get_metrics", args)
            runner.record(
                f"INPUT-{case_id}",
                f"Invalid metric input {case_id.lower()} is rejected",
                bool(error_code(call)) or call.get("is_error") is True,
                "invalid input produces a structured error",
                {"error_code": error_code(call), "http_status": call.get("http_status")},
                call=call,
                classification="FAIL_CONTRACT",
            )
    bad_limit = runner.call("INPUT-DAILY-LIMIT", "environment_get_daily", {"label_kind": "fact", "limit": 0})
    runner.record(
        "INPUT-DAILY-LIMIT",
        "Declared minimum daily limit is enforced",
        bool(error_code(bad_limit)) or bad_limit.get("is_error") is True,
        "limit=0 is rejected according to tools/list minimum",
        {"error_code": error_code(bad_limit), "http_status": bad_limit.get("http_status")},
        call=bad_limit,
        classification="FAIL_CONTRACT",
    )

    # Repeatability: run the same direct metric read concurrently.  Dynamic
    # request/trace metadata is excluded from the comparison.
    if aggregate:
        repeat_args = scope_args(aggregate, ic_scope="time_series")

        def repeat(_: int) -> dict[str, Any]:
            return runner.call(f"REPEAT-{_}", "factor_get_metrics", repeat_args)

        with ThreadPoolExecutor(max_workers=3) as pool:
            repeats = list(pool.map(repeat, range(3)))
        canonical = [
            [{key: row.get(key) for key in ("id", "run_id", "factor_id", "ic_scope", "symbol", "window_scope", "mean_ic", "final_score")} for row in data(call).get("ic_summaries") or []]
            for call in repeats
        ]
        runner.record(
            "METRIC-REPEATABILITY",
            "Concurrent identical metric reads return the same business rows",
            all(successful(call) for call in repeats) and len({json.dumps(value, sort_keys=True, default=str) for value in canonical}) == 1,
            "only request/trace metadata differs between concurrent reads",
            {"row_sets": canonical, "errors": [error_code(call) for call in repeats]},
            call=repeats[0] if repeats else None,
            classification="FAIL_DATA",
        )

    # Catalog/rank/KB calls are deliberately one-shot because the service may
    # advertise a daily result budget.  A quota response is environmental data,
    # not a product failure.
    catalog = runner.call("CATALOG-ONE-SHOT", "factor_catalog_stats", {"library_status": "valid", "kind": "sub_factor"})
    if error_code(catalog) in BLOCKING_CODES:
        runner.record("CATALOG-ONE-SHOT", "Catalog budget gate", False, "bounded catalog stats response", {"error_code": error_code(catalog)}, blocked=f"service returned {error_code(catalog)}")
    else:
        runner.record("CATALOG-ONE-SHOT", "Catalog stats returns a bounded count", successful(catalog) and isinstance(data(catalog).get("total"), int), "successful stats total", {"total": data(catalog).get("total"), "error_code": error_code(catalog)}, call=catalog)
    rank = runner.call("RANK-ONE-SHOT", "factor_rank", {"metric": "mean_ic", "top_k": 1, "bottom_k": 1, "ic_scope": "time_series", "validity_scope": "time_series", "interval": "1h", "factor_window_bars": "1", "return_bar_interval": "1h", "forward_return_bars": 1, "ranking_mode": "signed", "scoring_version": "v202606_default", "universe_key": "all", "window_scope": "min_window", "as_of": datetime.now(timezone.utc).isoformat(), "min_valid_slice_count": 0, "min_coverage_mean": 0, "require_oos": False, "kind": "sub_factor", "calculation_mode": "direct", "symbol": ""})
    if error_code(rank) in BLOCKING_CODES:
        runner.record("RANK-ONE-SHOT", "Rank budget gate", False, "bounded rank response", {"error_code": error_code(rank)}, blocked=f"service returned {error_code(rank)}")
    else:
        ranked = (data(rank).get("top_items") or []) + (data(rank).get("bottom_items") or [])
        runner.record("RANK-ONE-SHOT", "Rank returns bounded ordered items", successful(rank) and len(ranked) <= 2, "bounded rank response", {"count": len(ranked), "error_code": error_code(rank)}, call=rank)
    kb_args = {"extraction_id": int((fixtures.get("kb") or {}).get("id"))} if fixtures.get("kb") else {"query": "factor", "limit": 1}
    kb = runner.call("KB-ONE-SHOT", "kb_factor_candidate_search", kb_args)
    if error_code(kb) in BLOCKING_CODES:
        runner.record("KB-ONE-SHOT", "KB budget gate", False, "bounded KB response", {"error_code": error_code(kb)}, blocked=f"service returned {error_code(kb)}")
    else:
        runner.record("KB-ONE-SHOT", "KB lookup is bounded and structured", successful(kb) and len(response_rows(data(kb), "items", "results")) <= 50, "bounded candidate response", {"count": len(response_rows(data(kb), "items", "results")), "error_code": error_code(kb)}, call=kb)

    after = table_state(db)
    runner.record(
        "DB-READ-ONLY",
        "Read-only MCP matrix does not mutate business tables",
        before == after,
        "business-table counts and latest markers are unchanged",
        {"before": before, "after": after},
        severity="P0",
        classification="FAIL_UNAUTHORIZED_MUTATION",
    )
    counts: dict[str, int] = {}
    for case in runner.cases:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    report = {
        "environment": "test",
        "mcp_url": MCP_URL,
        "database": settings.database.name,
        "mode": "READ_ONLY",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "case_counts": counts,
        "failed_cases": [case["case_id"] for case in runner.cases if case["status"] == "FAIL"],
        "blocked_cases": [case["case_id"] for case in runner.cases if case["status"] == "BLOCKED"],
        "cases": runner.cases,
        "fixtures": {
            key: ({k: v for k, v in value.items() if k not in {"environment_snapshot", "factor_set_snapshot", "metrics_json", "features", "probabilities", "raw_payload", "status_reason_json"}} if isinstance(value, dict) else value)
            for key, value in fixtures.items()
        },
        "before_state": before,
        "after_state": after,
        "sensitive_values_written": False,
    }
    write_json(output / "results.json", report)
    lines = [
        "# Direct functional deep regression",
        "",
        f"- Environment: `test`; MCP: `{MCP_URL}`; mode: `READ_ONLY`",
        f"- Counts: `{counts}`",
        f"- Failed cases: `{report['failed_cases'] or 'none'}`",
        f"- Blocked cases: `{report['blocked_cases'] or 'none'}`",
        "",
        "## Functional failures",
        "",
    ]
    failures = [case for case in runner.cases if case["status"] == "FAIL"]
    if failures:
        for case in failures:
            lines.append(f"- **{case['case_id']}** ({case.get('severity')}): {case['title']} — {case['actual']}")
    else:
        lines.append("No new functional failure was confirmed by this matrix.")
    lines.extend(["", "Raw request/response artifacts are sanitized; no Authorization value or database password is stored."])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"output": str(output), "case_counts": counts, "failed_cases": report["failed_cases"], "blocked_cases": report["blocked_cases"]}


def main() -> None:
    """Run the matrix and print a compact machine-readable completion line."""

    print(json.dumps(run(), ensure_ascii=False))


if __name__ == "__main__":
    main()
