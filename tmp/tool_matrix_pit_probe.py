#!/usr/bin/env python3
"""Complete MCP-016, MCP-017 and MCP-019 with read-only dynamic fixtures.

This is a temporary Factor Library 4.0 probe.  It discovers every business
identifier from the test database, opens the database with an explicit
read-only consistent-snapshot transaction, never invokes write tools, and
writes credential-free evidence under ``reports/factor4-resume``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pymysql


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from tmp import catalog_deep_readonly as transport  # noqa: E402


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
SHANGHAI = ZoneInfo("Asia/Shanghai")
EARLY_AS_OF = datetime(1970, 1, 1, tzinfo=timezone.utc)
WRITE_TOOLS = {"submit_backtest_factor_feedback"}
EXPECTED_TOOLS = {
    "factor_search",
    "kb_factor_candidate_search",
    "factor_catalog_stats",
    "factor_get_detail",
    "factor_get_metrics",
    "factor_get_formula",
    "factor_list_metric_scopes",
    "factor_rank",
    "factor_get_details_batch",
    "factor_get_metrics_batch",
    "factor_get_validity_batch",
    "factor_get_metric_slices",
    "factor_get_validity",
    "environment_get_daily",
    "environment_get_recommendations",
    "factor_get_environment_metrics",
    "factor_get_environment_tags",
    "universe_list_symbols",
    "schema_get_factor_fields",
    "schema_get_raw_data",
    "submit_backtest_factor_feedback",
    "get_feedback_submission_status",
}
AS_OF_TOOLS = {
    "factor_search",
    "factor_catalog_stats",
    "factor_get_metrics",
    "factor_get_formula",
    "factor_list_metric_scopes",
    "factor_rank",
    "factor_get_metrics_batch",
    "factor_get_validity_batch",
    "factor_get_metric_slices",
    "factor_get_validity",
    "environment_get_daily",
    "environment_get_recommendations",
    "universe_list_symbols",
}
KNOWN_BLOCKING_CODES = {
    "AUTH_REQUIRED",
    "DEPENDENCY_UNAVAILABLE",
    "EXPORT_BUDGET_EXCEEDED",
    "FORBIDDEN",
    "INSUFFICIENT_SCOPE",
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
}
TRANSIENT_KEYS = {
    "request_id",
    "trace_id",
    "quota",
    "as_of",
    "data_as_of",
    "requested_as_of",
    "generated_at",
    "elapsed_ms",
    "execution_ms",
    "next_cursor",
    "selection_as_of",
    "snapshot_as_of",
}
SENSITIVE_RE = re.compile(
    r"authorization|password|secret|api[_-]?key|claim_token|signature|jwt|hmac|naf_mcp_",
    re.I,
)


def json_default(value: Any) -> str:
    """Serialize database and time values for sanitized evidence files."""

    if isinstance(value, (date, datetime, Decimal)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def redact(value: Any) -> Any:
    """Recursively redact credential-bearing keys and token-shaped values."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>"
            if SENSITIVE_RE.search(str(key))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and "naf_mcp_" in value:
        return re.sub(r"naf_mcp_[A-Za-z0-9_-]+", "<redacted-token>", value)
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one deterministic, UTF-8, credential-free JSON artifact."""

    path.write_text(
        json.dumps(
            redact(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=json_default,
        )
        + "\n",
        encoding="utf-8",
    )


def business(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return the structured business envelope for a normalized MCP call."""

    value = call.get("business") if isinstance(call, dict) else None
    return value if isinstance(value, dict) else {}


def response_data(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return a tool response data object, or an empty object."""

    value = business(call).get("data")
    return value if isinstance(value, dict) else {}


def response_meta(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return a tool response metadata object, or an empty object."""

    value = business(call).get("meta")
    return value if isinstance(value, dict) else {}


def response_items(call: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the first conventional object-array payload from a tool call."""

    payload = response_data(call)
    for key in ("items", "results", "top_items", "symbols", "ic_summaries"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def error_code(call: dict[str, Any] | None) -> str | None:
    """Return a JSON-RPC or structured business error code."""

    if not isinstance(call, dict):
        return None
    envelope = call.get("envelope")
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        value = envelope["error"].get("code")
        return str(value) if value is not None else None
    error = business(call).get("error")
    if isinstance(error, dict):
        for key in ("code", "error_code", "type"):
            if error.get(key) is not None:
                return str(error[key])
    return None


def success(call: dict[str, Any] | None) -> bool:
    """Return whether a call is an MCP business success."""

    if not isinstance(call, dict):
        return False
    envelope = call.get("envelope")
    return bool(
        call.get("http_status") == 200
        and call.get("parse_error") is None
        and isinstance(envelope, dict)
        and "result" in envelope
        and call.get("is_error") is not True
        and isinstance(call.get("business"), dict)
        and error_code(call) is None
    )


def call_summary(call: dict[str, Any] | None) -> dict[str, Any]:
    """Build a compact report-safe summary for one MCP call."""

    if not isinstance(call, dict):
        return {"executed": False}
    payload = response_data(call)
    counts: dict[str, int] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            counts[key] = len(value)
    return {
        "executed": True,
        "http_status": call.get("http_status"),
        "is_error": call.get("is_error"),
        "error_code": error_code(call),
        "elapsed_seconds": call.get("elapsed_seconds"),
        "representations_equal": call.get("representations_equal"),
        "array_counts": counts,
        "warnings": extract_warnings(call),
    }


def extract_warnings(call: dict[str, Any] | None) -> list[str]:
    """Return normalized warning codes from business or metadata envelopes."""

    values: list[Any] = []
    root = business(call)
    for container in (root, root.get("meta"), root.get("data")):
        if isinstance(container, dict) and isinstance(container.get("warnings"), list):
            values.extend(container["warnings"])
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, dict):
            code = value.get("code") or value.get("warning_code")
            if code is not None:
                result.append(str(code))
    return sorted(set(result))


def parse_time(value: Any, *, naive_zone: ZoneInfo | timezone = SHANGHAI) -> datetime | None:
    """Parse a timestamp and normalize it to UTC."""

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
        parsed = parsed.replace(tzinfo=naive_zone)
    return parsed.astimezone(timezone.utc)


def lifecycle_iso(value: Any) -> str:
    """Render a DB lifecycle DATETIME, whose wall clock is Asia/Shanghai."""

    parsed = parse_time(value, naive_zone=SHANGHAI)
    if parsed is None:
        raise ValueError(f"invalid lifecycle timestamp: {value!r}")
    return parsed.astimezone(SHANGHAI).isoformat()


def period_iso(value: Any) -> str:
    """Render a DB metric-period DATETIME, whose wall clock is UTC."""

    parsed = parse_time(value, naive_zone=timezone.utc)
    if parsed is None:
        raise ValueError(f"invalid period timestamp: {value!r}")
    return parsed.astimezone(SHANGHAI).isoformat()


def canonical(value: Any) -> Any:
    """Remove request-specific metadata before current/future comparison."""

    if isinstance(value, dict):
        return {
            key: canonical(item)
            for key, item in sorted(value.items())
            if key not in TRANSIENT_KEYS
        }
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def schema_hash(schema: dict[str, Any]) -> str:
    """Return a SHA-256 hash for one canonical JSON Schema object."""

    payload = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def schema_enums(schema: dict[str, Any]) -> dict[str, list[Any]]:
    """Extract direct and nullable-union enum values from input properties."""

    result: dict[str, list[Any]] = {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return result
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            continue
        values: list[Any] = []
        if isinstance(definition.get("enum"), list):
            values.extend(definition["enum"])
        for branch in definition.get("anyOf") or []:
            if isinstance(branch, dict) and isinstance(branch.get("enum"), list):
                values.extend(branch["enum"])
        if values:
            result[str(name)] = values
    return result


def schema_limits(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract numeric/array/string bounds declared by an input schema."""

    result: dict[str, dict[str, Any]] = {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return result
    keys = ("minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength")
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            continue
        bounds = {key: definition[key] for key in keys if key in definition}
        if bounds:
            result[str(name)] = bounds
    return result


def open_read_only_db(settings: Any) -> tuple[Any, Any]:
    """Open a repeatable-read, read-only MySQL transaction and cursor.

    Returns the live connection and dictionary cursor.  The caller must always
    roll back and close both objects.  Connection or transaction setup errors
    are propagated.
    """

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
        read_timeout=90,
        write_timeout=30,
    )
    cursor = connection.cursor()
    cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    cursor.execute("SET SESSION TRANSACTION READ ONLY")
    cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
    return connection, cursor


def fetch_one(cursor: Any, query: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Execute a parameterized read query and return its first row."""

    cursor.execute(query, parameters)
    row = cursor.fetchone()
    return dict(row) if isinstance(row, dict) else None


def fetch_all(cursor: Any, query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Execute a parameterized read query and return all rows as dictionaries."""

    cursor.execute(query, parameters)
    return [dict(row) for row in cursor.fetchall()]


def discover_formula_metric_slice(cursor: Any) -> dict[str, Any] | None:
    """Find a completed sub-factor run with formula, summary, and slice evidence."""

    evidence_rows = fetch_all(
        cursor,
        """
        SELECT e.run_id, e.factor_id, e.is_sub_factor_id, e.calculation_mode,
               e.factor_bar_interval, e.factor_window_bars,
               e.return_bar_interval, e.forward_return_bars,
               e.formula_hash, e.id AS formula_evidence_id, r.completed_at
        FROM factor_ic_run_formula_evidence e
        JOIN factor_ic_runs r ON r.run_id=e.run_id
        WHERE r.status='completed' AND e.is_sub_factor_id=1
        ORDER BY e.id DESC
        LIMIT 100
        """,
    )
    for evidence in evidence_rows:
        summary = fetch_one(
            cursor,
            """
            SELECT m.*
            FROM factor_ic_summary_metrics m
            WHERE m.run_id=%s AND m.factor_id=%s AND m.is_sub_factor_id=%s
              AND m.calculation_mode=%s AND m.factor_bar_interval=%s
              AND m.factor_window_bars=%s AND m.return_bar_interval=%s
              AND m.forward_return_bars=%s
            ORDER BY m.id DESC
            LIMIT 1
            """,
            (
                evidence["run_id"],
                evidence["factor_id"],
                evidence["is_sub_factor_id"],
                evidence["calculation_mode"],
                evidence["factor_bar_interval"],
                evidence["factor_window_bars"],
                evidence["return_bar_interval"],
                evidence["forward_return_bars"],
            ),
        )
        if summary is None:
            continue
        slice_row = fetch_one(
            cursor,
            """
            SELECT id, slice_start, slice_end
            FROM factor_ic_slice_metrics
            WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=%s
              AND ic_scope=%s AND calculation_mode=%s
              AND factor_bar_interval=%s AND factor_window_bars=%s
              AND return_bar_interval=%s AND forward_return_bars=%s
              AND universe_key=%s AND COALESCE(symbol,'')=%s
              AND window_scope=%s
            ORDER BY id
            LIMIT 1
            """,
            (
                summary["run_id"],
                summary["factor_id"],
                summary["is_sub_factor_id"],
                summary["ic_scope"],
                summary["calculation_mode"],
                summary["factor_bar_interval"],
                summary["factor_window_bars"],
                summary["return_bar_interval"],
                summary["forward_return_bars"],
                summary["universe_key"],
                summary.get("symbol") or "",
                summary["window_scope"],
            ),
        )
        if slice_row is not None:
            return {**summary, **evidence, "slice": slice_row}
    return None


def discover_validity(cursor: Any) -> dict[str, Any] | None:
    """Find one completed validity row and its exact dimension summary."""

    rows = fetch_all(
        cursor,
        """
        SELECT v.*, r.completed_at
        FROM factor_validity_status v
        JOIN factor_ic_runs r ON r.run_id=v.run_id
        WHERE r.status='completed' AND v.is_sub_factor_id=1
          AND (v.time_series_summary_id IS NOT NULL
               OR v.cross_sectional_summary_id IS NOT NULL)
        ORDER BY v.id DESC
        LIMIT 100
        """,
    )
    for row in rows:
        scope = "time_series" if row.get("time_series_summary_id") else "cross_sectional"
        summary_id = (
            row.get("time_series_summary_id")
            if scope == "time_series"
            else row.get("cross_sectional_summary_id")
        )
        summary = fetch_one(
            cursor,
            "SELECT * FROM factor_ic_summary_metrics WHERE id=%s",
            (summary_id,),
        )
        if summary is not None and summary.get("run_id") == row.get("run_id"):
            return {**row, "validity_scope": scope, "summary": summary}
    return None


def discover_fixtures(cursor: Any) -> dict[str, Any]:
    """Discover every database fixture needed by the tool and PIT matrix."""

    formula_metric = discover_formula_metric_slice(cursor)
    validity = discover_validity(cursor)
    route = fetch_one(
        cursor,
        """
        SELECT r.*, b.batch_uid, b.route_profile_key, b.published_at,
               b.publish_status, b.is_active AS batch_is_active
        FROM market_environment_factor_route r
        JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
        WHERE r.is_active=1 AND r.is_eligible=1
          AND b.publish_status='published' AND b.is_active=1
        ORDER BY b.published_at DESC, r.rank_no, r.id
        LIMIT 1
        """,
    )
    daily = fetch_one(
        cursor,
        """
        SELECT id, environment_date, label_kind, label_status, revision,
               is_current, available_at
        FROM market_environment_daily
        WHERE is_current=1
        ORDER BY available_at DESC, id DESC
        LIMIT 1
        """,
    )
    universe = fetch_one(
        cursor,
        """
        SELECT universe_key, COUNT(*) AS row_count
        FROM coin_universe_symbols
        GROUP BY universe_key
        ORDER BY row_count DESC, universe_key
        LIMIT 1
        """,
    )
    kb = fetch_one(
        cursor,
        """
        SELECT id, factor_name, validation_status, mapping_status,
               confidence_score
        FROM kb_factor_extractions
        ORDER BY id DESC
        LIMIT 1
        """,
    )
    approved_schema = fetch_one(
        cursor,
        """
        SELECT schema_version, schema_hash, approved_at, effective_from
        FROM raw_data_schema_version
        WHERE status='approved'
        ORDER BY id DESC
        LIMIT 1
        """,
    )
    maxima: dict[str, int] = {}
    for name, table in {
        "factor": "factors",
        "sub_factor": "sub_factors",
        "summary": "factor_ic_summary_metrics",
        "slice": "factor_ic_slice_metrics",
        "formula": "factor_ic_run_formula_evidence",
        "validity": "factor_validity_status",
        "daily": "market_environment_daily",
        "environment_metric": "market_environment_factor_metric",
        "route": "market_environment_factor_route",
        "universe": "coin_universe_symbols",
    }.items():
        row = fetch_one(cursor, f"SELECT COALESCE(MAX(id),0) AS max_id FROM `{table}`") or {}
        maxima[name] = int(row.get("max_id") or 0)
    publications = {
        str(row["publication_uid"])
        for row in fetch_all(
            cursor,
            """
            SELECT DISTINCT publication_uid
            FROM market_environment_eval_batch
            WHERE publication_uid IS NOT NULL
            """,
        )
    }
    runs = {
        str(row["run_id"]): lifecycle_iso(row["completed_at"])
        for row in fetch_all(
            cursor,
            """
            SELECT run_id, completed_at
            FROM factor_ic_runs
            WHERE status='completed' AND completed_at IS NOT NULL
            """,
        )
    }
    universe_membership = {
        str(key): [dict(row) for row in rows]
        for key in [universe.get("universe_key") if universe else None]
        if key is not None
        for rows in [
            fetch_all(
                cursor,
                """
                SELECT id, universe_key, symbol, is_active, valid_from, valid_to,
                       sort_order
                FROM coin_universe_symbols
                WHERE universe_key=%s
                ORDER BY sort_order, symbol
                """,
                (key,),
            )
        ]
    }
    transaction = fetch_one(
        cursor,
        """
        SELECT @@session.transaction_read_only AS transaction_read_only,
               @@session.transaction_isolation AS transaction_isolation
        """,
    )
    return {
        "formula_metric_slice": formula_metric,
        "validity": validity,
        "route": route,
        "daily": daily,
        "universe": universe,
        "kb": kb,
        "approved_schema": approved_schema,
        "snapshot_max_ids": maxima,
        "snapshot_publications": publications,
        "completed_runs": runs,
        "universe_membership": universe_membership,
        "transaction": transaction,
    }


def scope_args(scope: dict[str, Any], as_of: str, *, validity: str | None = None) -> dict[str, Any]:
    """Build a complete point-in-time metric scope from discovery output."""

    result = {
        "kind": scope.get("kind") or "sub_factor",
        "ic_scope": scope["ic_scope"],
        "validity_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "scoring_version": scope["scoring_version"],
        "symbol": scope.get("symbol") or "",
        "as_of": as_of,
    }
    if validity is not None:
        result["validity"] = validity
    return result


def exact_metric_args(row: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Build one exact metrics request from a summary row."""

    result = scope_args(row, as_of)
    result.pop("kind", None)
    result.pop("validity_scope", None)
    result["factor_ref"] = f"sub_factor:{row['factor_id']}"
    result["run_id"] = str(row["run_id"])
    return result


def exact_formula_args(row: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Build one exact immutable-formula request from evidence identity."""

    return {
        "factor_ref": f"sub_factor:{row['factor_id']}",
        "run_id": str(row["run_id"]),
        "calculation_mode": str(row["calculation_mode"]),
        "interval": str(row["factor_bar_interval"]),
        "factor_window_bars": str(row["factor_window_bars"]),
        "return_bar_interval": str(row["return_bar_interval"]),
        "forward_return_bars": int(row["forward_return_bars"]),
        "as_of": as_of,
    }


def exact_validity_args(fixture: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Build one scope-specific validity request from a validity fixture."""

    summary = fixture["summary"]
    result = exact_metric_args(summary, as_of)
    result.pop("ic_scope", None)
    result["validity_scope"] = fixture["validity_scope"]
    return result


def exact_slice_args(row: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Build a bounded slice request whose end lies after the final slice."""

    result = exact_metric_args(row, as_of)
    slice_row = row["slice"]
    end = parse_time(slice_row["slice_end"], naive_zone=timezone.utc)
    if end is None:
        raise ValueError("slice_end is not parseable")
    result.update(
        {
            "start_time": period_iso(slice_row["slice_start"]),
            "end_time": (end + timedelta(days=1)).astimezone(SHANGHAI).isoformat(),
            "limit": 5,
        }
    )
    return result


def rank_args(scope: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Build a bounded rank request for a discovered exact scope."""

    return {
        **scope_args(scope, as_of),
        "metric": "mean_ic",
        "top_k": 3,
        "bottom_k": 3,
        "ranking_mode": "raw_signed",
        "min_valid_slice_count": 0,
        "min_coverage_mean": 0,
        "require_oos": False,
    }


def choose_rank_scope(call: dict[str, Any]) -> dict[str, Any] | None:
    """Choose a populated aggregate cross-sectional scope from MCP discovery."""

    candidates = [
        row
        for row in response_items(call)
        if row.get("ic_scope") == "cross_sectional"
        and (row.get("symbol") or "") == ""
        and int(row.get("available_factor_count") or 0) > 0
    ]
    candidates.sort(
        key=lambda row: (
            parse_time(row.get("run_completed_at")) or EARLY_AS_OF,
            int(row.get("available_factor_count") or 0),
        ),
        reverse=True,
    )
    return dict(candidates[0]) if candidates else None


def tool_call(
    runner: transport.Runner,
    case_id: str,
    name: str,
    arguments: dict[str, Any],
    executed: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute and retain one normalized read-only MCP tool call."""

    if name in WRITE_TOOLS:
        raise AssertionError(f"write tool invocation refused: {name}")
    call = runner.tool(case_id, name, arguments)
    executed.append(call)
    return call


def replace_as_of(arguments: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Return a copy of tool arguments with one explicit point-in-time."""

    result = dict(arguments)
    result["as_of"] = as_of
    return result


def collect_ints(value: Any, key_names: set[str]) -> list[int]:
    """Collect integer values whose field names match a requested set."""

    result: list[int] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in key_names and isinstance(item, int) and not isinstance(item, bool):
                result.append(item)
            result.extend(collect_ints(item, key_names))
    elif isinstance(value, list):
        for item in value:
            result.extend(collect_ints(item, key_names))
    return result


def collect_strings(value: Any, key_names: set[str]) -> list[str]:
    """Collect non-empty string values whose field names match a set."""

    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in key_names and isinstance(item, str) and item:
                result.append(item)
            result.extend(collect_strings(item, key_names))
    elif isinstance(value, list):
        for item in value:
            result.extend(collect_strings(item, key_names))
    return result


def future_unseen(tool: str, call: dict[str, Any], fixtures: dict[str, Any]) -> list[str]:
    """Return evidence identifiers absent from the test-start DB snapshot."""

    payload = response_data(call)
    maxima = fixtures["snapshot_max_ids"]
    problems: list[str] = []
    mapping: dict[str, tuple[set[str], int]] = {
        "factor_search": ({"metric_id", "summary_id"}, maxima["summary"]),
        "factor_get_metrics": ({"id", "summary_id", "metric_id"}, maxima["summary"]),
        "factor_get_metrics_batch": ({"id", "summary_id", "metric_id"}, maxima["summary"]),
        "factor_rank": ({"metric_id", "summary_id"}, maxima["summary"]),
        "factor_get_formula": ({"formula_evidence_id", "evidence_id"}, maxima["formula"]),
        "factor_get_metric_slices": ({"id", "slice_id"}, maxima["slice"]),
        "factor_get_validity": ({"validity_id"}, maxima["validity"]),
        "factor_get_validity_batch": ({"validity_id"}, maxima["validity"]),
        "environment_get_daily": ({"id"}, maxima["daily"]),
        "environment_get_recommendations": ({"metric_id"}, maxima["environment_metric"]),
        "universe_list_symbols": ({"id"}, maxima["universe"]),
    }
    if tool in mapping:
        keys, maximum = mapping[tool]
        for identifier in collect_ints(payload, keys):
            if identifier > maximum:
                problems.append(f"{tool}:{sorted(keys)}={identifier}>snapshot_max={maximum}")
    if tool in {"factor_get_validity", "factor_get_validity_batch"}:
        candidates: list[dict[str, Any]] = []
        item = payload.get("item")
        if isinstance(item, dict):
            candidates.append(item)
        for batch_item in payload.get("items") or []:
            if not isinstance(batch_item, dict) or batch_item.get("success") is False:
                continue
            nested = batch_item.get("data")
            candidates.append(nested if isinstance(nested, dict) else batch_item)
        for candidate in candidates:
            identifier = candidate.get("id")
            if isinstance(identifier, int) and identifier > maxima["validity"]:
                problems.append(
                    f"{tool}:validity_id={identifier}>snapshot_max={maxima['validity']}"
                )
    if tool == "environment_get_recommendations":
        forecast = payload.get("forecast")
        if isinstance(forecast, dict):
            identifier = forecast.get("id")
            if isinstance(identifier, int) and identifier > maxima["daily"]:
                problems.append(
                    f"forecast_id={identifier}>snapshot_max={maxima['daily']}"
                )
        for route in payload.get("items") or []:
            if not isinstance(route, dict):
                continue
            route_id = route.get("route_id")
            if isinstance(route_id, int) and route_id > maxima["route"]:
                problems.append(f"route_id={route_id}>snapshot_max={maxima['route']}")
    for run_id in collect_strings(payload, {"run_id", "metric_run_id", "validity_run_id"}):
        if run_id not in fixtures["completed_runs"]:
            problems.append(f"{tool}:run_id_not_in_start_snapshot={run_id}")
    if tool == "environment_get_recommendations":
        for publication in collect_strings(payload, {"publication_uid"}):
            if publication not in fixtures["snapshot_publications"]:
                problems.append(f"publication_not_in_start_snapshot={publication}")
    return sorted(set(problems))


def history_leaks(tool: str, call: dict[str, Any], history_as_of: str, fixtures: dict[str, Any]) -> list[str]:
    """Return identifiers that were not visible at a historical query time."""

    if not success(call):
        return []
    payload = response_data(call)
    boundary = parse_time(history_as_of)
    if boundary is None:
        return ["history_as_of_unparseable"]
    problems: list[str] = []
    run_ids = collect_strings(payload, {"run_id", "metric_run_id", "validity_run_id"})
    for run_id in run_ids:
        completed = parse_time(fixtures["completed_runs"].get(run_id))
        if completed is None:
            problems.append(f"unknown_run={run_id}")
        elif completed > boundary:
            problems.append(f"future_run={run_id}")
    if tool in {"factor_search", "factor_list_metric_scopes", "factor_rank"}:
        if response_items(call) and not run_ids:
            problems.append("historical_items_missing_run_identity")
    if tool == "factor_catalog_stats":
        numeric_counts = [
            value
            for key, value in payload.items()
            if ("count" in key or key == "total")
            and isinstance(value, int)
            and not isinstance(value, bool)
        ]
        if history_as_of.startswith("1970-") and any(value > 0 for value in numeric_counts):
            problems.append(f"positive_1970_counts={numeric_counts}")
    if tool == "environment_get_daily" and history_as_of.startswith("1970-"):
        if response_items(call):
            problems.append("daily_rows_visible_in_1970")
    if tool == "environment_get_recommendations":
        published_values = collect_strings(payload, {"published_at"})
        for value in published_values:
            published = parse_time(value)
            if published is not None and published > boundary:
                problems.append(f"future_publication_time={value}")
    if tool == "universe_list_symbols":
        key = str(payload.get("universe_key") or "")
        rows = fixtures["universe_membership"].get(key, [])
        expected = []
        for row in rows:
            valid_from = parse_time(row.get("valid_from"), naive_zone=SHANGHAI)
            valid_to = parse_time(row.get("valid_to"), naive_zone=SHANGHAI)
            if int(row.get("is_active") or 0) == 1 and (
                valid_from is None or valid_from <= boundary
            ) and (valid_to is None or valid_to > boundary):
                expected.append(str(row["symbol"]))
        actual = [str(row.get("symbol")) for row in response_items(call)]
        if actual != expected:
            problems.append(
                f"universe_membership_mismatch:actual={len(actual)},expected={len(expected)}"
            )
    return sorted(set(problems))


def representation_result(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Adjudicate content/structuredContent equality for every tool response."""

    checked = [call for call in calls if call.get("representations_equal") is not None]
    mismatches = [
        {
            "case_id": call.get("case_id"),
            "tool": call.get("tool"),
            "is_error": call.get("is_error"),
            "error_code": error_code(call),
        }
        for call in checked
        if call.get("representations_equal") is not True
    ]
    error_checks = [
        call
        for call in checked
        if call.get("is_error") is True or error_code(call) is not None
    ]
    error_mapping_issues = [
        {
            "case_id": call.get("case_id"),
            "tool": call.get("tool"),
            "is_error": call.get("is_error"),
            "error_code": error_code(call),
        }
        for call in error_checks
        if call.get("is_error") is not True or error_code(call) is None
    ]
    status = "PASS" if checked and not mismatches and error_checks and not error_mapping_issues else "FAIL"
    return {
        "case_id": "MCP-017",
        "status": status,
        "severity": None if status == "PASS" else "P1",
        "checked_response_count": len(checked),
        "success_response_count": sum(success(call) for call in checked),
        "error_response_count": len(error_checks),
        "mismatches": mismatches,
        "error_mapping_issues": error_mapping_issues,
        "expected": "all JSON text representations deeply equal structuredContent; business errors set isError=true and retain one structured error code",
    }


def matrix_status(call: dict[str, Any] | None, tool: str) -> tuple[str, str | None, str]:
    """Classify one tool's minimum legal call for MCP-016."""

    if tool in WRITE_TOOLS:
        return "NOT_APPLICABLE", None, "write tool: schema and permission boundary inspected; invocation intentionally prohibited"
    if call is None:
        return "BLOCKED", "BLOCKED_DATA_PRECONDITION", "no dynamic legal arguments could be constructed"
    if success(call):
        return "COVERED", None, "minimum legal read returned an MCP business success"
    code = error_code(call)
    if tool == "get_feedback_submission_status" and code in {
        "SUBMISSION_NOT_FOUND",
        "FEEDBACK_SUBMISSION_NOT_FOUND",
        "NOT_FOUND",
    }:
        return "BLOCKED", "BLOCKED_DATA_PRECONDITION", "authenticated caller has no discoverable owned submission; not-found/error path was covered"
    if code in KNOWN_BLOCKING_CODES:
        return "BLOCKED", "BLOCKED_TECHNICAL_DEPENDENCY", f"service returned {code}"
    return "FAIL", "FAIL_CONTRACT", f"legal call failed with {code or 'unstructured response'}"


def report_fixture(value: Any) -> Any:
    """Return a compact non-payload fixture summary for the report."""

    if not isinstance(value, dict):
        return None
    allowed = {
        "id",
        "run_id",
        "factor_id",
        "factor_ref",
        "ic_scope",
        "calculation_mode",
        "factor_bar_interval",
        "factor_window_bars",
        "return_bar_interval",
        "forward_return_bars",
        "universe_key",
        "symbol",
        "window_scope",
        "scoring_version",
        "completed_at",
        "validity_scope",
        "publication_uid",
        "batch_uid",
        "market_scope",
        "route_profile_key",
        "published_at",
        "environment_date",
        "label_kind",
        "revision",
        "available_at",
        "schema_version",
        "row_count",
    }
    return {key: value[key] for key in allowed if key in value}


def audit_feedback_owner(settings: Any, call: dict[str, Any] | None) -> dict[str, Any]:
    """Verify whether the authenticated status caller owns a DB submission.

    The MCP access log row is created after the primary consistent snapshot,
    so this check deliberately uses a second explicit read-only transaction.
    It returns only presence/count facts and never writes caller identifiers to
    the report.
    """

    error = business(call).get("error")
    request_id = error.get("request_id") if isinstance(error, dict) else None
    if not request_id:
        return {
            "audit_row_found": False,
            "caller_user_id_present": False,
            "owned_submission_count": None,
        }
    connection, cursor = open_read_only_db(settings)
    try:
        caller = fetch_one(
            cursor,
            """
            SELECT caller_user_id
            FROM agent_data_access_logs
            WHERE request_id=%s AND tool_name='get_feedback_submission_status'
            ORDER BY id DESC
            LIMIT 1
            """,
            (request_id,),
        )
        caller_id = caller.get("caller_user_id") if caller else None
        if caller_id is None:
            return {
                "audit_row_found": bool(caller),
                "caller_user_id_present": False,
                "owned_submission_count": None,
            }
        owned = fetch_one(
            cursor,
            """
            SELECT COUNT(*) AS row_count
            FROM market_environment_strategy_feedback_submissions
            WHERE source_system=%s
            """,
            (f"mcp-user:{caller_id}",),
        )
        return {
            "audit_row_found": True,
            "caller_user_id_present": True,
            "owned_submission_count": int((owned or {}).get("row_count") or 0),
            "transaction_read_only": 1,
            "finalization": "ROLLBACK",
        }
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def main() -> int:
    """Execute the complete read-only tool matrix and PIT validation."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    settings = SettingsLoader.load("test", ROOT)
    if settings.environment != "test":
        raise SystemExit("test environment gate failed")
    if not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("test MCP host gate failed")
    transport.MCP_URL = MCP_URL

    started = datetime.now(SHANGHAI)
    current_as_of = started.isoformat()
    future_as_of = (started + timedelta(days=1)).isoformat()
    stamp = started.strftime("%Y%m%dT%H%M%S%z")
    output = ROOT / "reports" / "factor4-resume" / f"{stamp}-tool-matrix-pit"
    output.mkdir(parents=True, exist_ok=False)

    connection = None
    cursor = None
    executed: list[dict[str, Any]] = []
    calls_by_tool: dict[str, dict[str, Any]] = {}
    pit_calls: dict[str, dict[str, Any]] = {}
    fixtures: dict[str, Any] = {}
    runner = transport.Runner(token, output, None)
    try:
        connection, cursor = open_read_only_db(settings.database)
        fixtures = discover_fixtures(cursor)
        init = runner.request(
            "MCP-INIT",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "QuestTest-tool-matrix-pit", "version": "1.0"},
            },
        )
        init_result = (
            (init.get("envelope") or {}).get("result")
            if isinstance(init.get("envelope"), dict)
            else {}
        ) or {}
        runner.protocol_version = init_result.get("protocolVersion")
        runner.notify_initialized("MCP-NOTIFY")
        tools_call = runner.request("MCP-TOOLS", "tools/list", {})
        tools = (
            (((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or [])
            if isinstance(tools_call.get("envelope"), dict)
            else []
        )
        tool_by_name = {
            str(tool["name"]): tool
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        }

        # Scope discovery must run first because search/stats/rank arguments are
        # derived from the service's own current scope surface.
        scopes = tool_call(
            runner,
            "MATRIX-factor_list_metric_scopes-CURRENT",
            "factor_list_metric_scopes",
            {
                "as_of": current_as_of,
                "kind": "sub_factor",
                "ic_scope": "cross_sectional",
                "limit": 100,
            },
            executed,
        )
        calls_by_tool["factor_list_metric_scopes"] = scopes
        pit_calls["factor_list_metric_scopes"] = {"current": scopes}
        rank_scope = choose_rank_scope(scopes)

        formula_metric = fixtures.get("formula_metric_slice")
        validity = fixtures.get("validity")
        route = fixtures.get("route")
        daily = fixtures.get("daily")
        universe = fixtures.get("universe")
        kb = fixtures.get("kb")
        approved_schema = fixtures.get("approved_schema")

        current_arguments: dict[str, dict[str, Any] | None] = {
            "factor_list_metric_scopes": {
                "as_of": current_as_of,
                "kind": "sub_factor",
                "ic_scope": "cross_sectional",
                "limit": 100,
            },
            "factor_search": (
                {**scope_args(rank_scope, current_as_of), "limit": 5}
                if rank_scope
                else None
            ),
            "factor_catalog_stats": scope_args(rank_scope, current_as_of) if rank_scope else None,
            "factor_get_detail": (
                {"factor_ref": f"sub_factor:{formula_metric['factor_id']}", "detail_level": "summary"}
                if formula_metric
                else None
            ),
            "factor_get_metrics": exact_metric_args(formula_metric, current_as_of) if formula_metric else None,
            "factor_get_formula": exact_formula_args(formula_metric, current_as_of) if formula_metric else None,
            "factor_rank": rank_args(rank_scope, current_as_of) if rank_scope else None,
            "factor_get_details_batch": (
                {"factor_refs": [f"sub_factor:{formula_metric['factor_id']}"], "detail_level": "summary"}
                if formula_metric
                else None
            ),
            "factor_get_metrics_batch": (
                {
                    **{
                        key: value
                        for key, value in exact_metric_args(formula_metric, current_as_of).items()
                        if key != "factor_ref"
                    },
                    "factor_refs": [f"sub_factor:{formula_metric['factor_id']}"],
                }
                if formula_metric
                else None
            ),
            "factor_get_validity": exact_validity_args(validity, current_as_of) if validity else None,
            "factor_get_validity_batch": (
                {
                    **{
                        key: value
                        for key, value in exact_validity_args(validity, current_as_of).items()
                        if key != "factor_ref"
                    },
                    "factor_refs": [f"sub_factor:{validity['factor_id']}"],
                }
                if validity
                else None
            ),
            "factor_get_metric_slices": exact_slice_args(formula_metric, current_as_of) if formula_metric else None,
            "environment_get_daily": (
                {"label_kind": str(daily["label_kind"]), "as_of": current_as_of, "limit": 5}
                if daily
                else None
            ),
            "environment_get_recommendations": (
                {
                    "market_scope": str(route["market_scope"]),
                    "route_profile_key": str(route["route_profile_key"]),
                    "as_of": current_as_of,
                    "limit": 5,
                }
                if route
                else None
            ),
            "factor_get_environment_metrics": (
                {
                    "factor_ref": str(route["factor_ref"]),
                    "market_scope": str(route["market_scope"]),
                    "route_profile_key": str(route["route_profile_key"]),
                    "batch_uid": str(route["batch_uid"]),
                    "limit": 5,
                }
                if route
                else None
            ),
            "factor_get_environment_tags": (
                {
                    "factor_ref": str(route["factor_ref"]),
                    "market_scope": str(route["market_scope"]),
                    "route_profile_key": str(route["route_profile_key"]),
                }
                if route
                else None
            ),
            "universe_list_symbols": (
                {"universe_key": str(universe["universe_key"]), "as_of": current_as_of}
                if universe
                else None
            ),
            "kb_factor_candidate_search": (
                {"extraction_id": int(kb["id"]), "limit": 1} if kb else None
            ),
            "schema_get_factor_fields": (
                {"schema_version": str(approved_schema["schema_version"]), "field_names": ["close"]}
                if approved_schema
                else {}
            ),
            "schema_get_raw_data": (
                {"schema_version": str(approved_schema["schema_version"])}
                if approved_schema
                else {}
            ),
            "get_feedback_submission_status": {"submission_id": str(uuid4())},
        }

        # Invoke every known read tool exactly once for MCP-016, reusing the
        # current call as the current leg for each as_of-capable tool.
        for name in sorted(tool_by_name):
            if name in WRITE_TOOLS or name == "factor_list_metric_scopes":
                continue
            arguments = current_arguments.get(name)
            if arguments is None:
                continue
            call = tool_call(
                runner,
                f"MATRIX-{name}-CURRENT",
                name,
                arguments,
                executed,
            )
            calls_by_tool[name] = call
            if name in AS_OF_TOOLS:
                pit_calls[name] = {"current": call}

        # Each PIT tool receives a deliberately old or just-before-completion
        # historical instant plus a future instant.  Exact run tools use the
        # run's completion boundary; broad discovery tools use 1970.
        exact_run_tools = {
            "factor_get_metrics",
            "factor_get_formula",
            "factor_get_metrics_batch",
            "factor_get_validity_batch",
            "factor_get_metric_slices",
            "factor_get_validity",
        }
        run_completed = (
            parse_time(formula_metric.get("completed_at"), naive_zone=SHANGHAI)
            if formula_metric
            else None
        )
        validity_completed = (
            parse_time(validity.get("completed_at"), naive_zone=SHANGHAI)
            if validity
            else None
        )
        for name in sorted(AS_OF_TOOLS):
            arguments = current_arguments.get(name)
            current = pit_calls.get(name, {}).get("current")
            if arguments is None or current is None:
                continue
            if name in {"factor_get_validity", "factor_get_validity_batch"}:
                completed = validity_completed
            elif name in exact_run_tools:
                completed = run_completed
            elif name == "environment_get_recommendations" and route:
                completed = parse_time(route.get("published_at"), naive_zone=SHANGHAI)
            else:
                completed = None
            historical = (completed - timedelta(microseconds=1)) if completed else EARLY_AS_OF
            history_as_of = historical.astimezone(SHANGHAI).isoformat()
            history_args = replace_as_of(arguments, history_as_of)
            future_args = replace_as_of(arguments, future_as_of)
            history_call = tool_call(
                runner,
                f"PIT-{name}-HISTORY",
                name,
                history_args,
                executed,
            )
            future_call = tool_call(
                runner,
                f"PIT-{name}-FUTURE",
                name,
                future_args,
                executed,
            )
            pit_calls[name].update(
                {
                    "history": history_call,
                    "future": future_call,
                    "history_as_of": history_as_of,
                }
            )

        # Prove the transaction stayed active/read-only and leave it via an
        # explicit rollback in the finally block.
        tx_end = fetch_one(
            cursor,
            """
            SELECT @@session.transaction_read_only AS transaction_read_only,
                   @@session.transaction_isolation AS transaction_isolation
            """,
        )
        fixtures["transaction_end"] = tx_end
        fixtures["feedback_owner_audit"] = audit_feedback_owner(
            settings.database,
            calls_by_tool.get("get_feedback_submission_status"),
        )

        instructions = str(init_result.get("instructions") or "")
        write_boundary_ok = (
            "strategy.feedback.write/read" in instructions
            or ("strategy.feedback.write" in instructions and "strategy.feedback.read" in instructions)
        )
        matrix: list[dict[str, Any]] = []
        for name, definition in sorted(tool_by_name.items()):
            schema = definition.get("inputSchema") if isinstance(definition.get("inputSchema"), dict) else {}
            status, failure_class, reason = matrix_status(calls_by_tool.get(name), name)
            if name in WRITE_TOOLS and not write_boundary_ok:
                status = "FAIL"
                failure_class = "FAIL_PERMISSION_BOUNDARY"
                reason = "initialize instructions did not document strategy.feedback write/read boundaries"
            call = calls_by_tool.get(name)
            matrix.append(
                {
                    "name": name,
                    "access_mode": "write" if name in WRITE_TOOLS else "read",
                    "required_scope": (
                        "strategy.feedback.write"
                        if name == "submit_backtest_factor_feedback"
                        else "strategy.feedback.read"
                        if name == "get_feedback_submission_status"
                        else "factor.formula.read"
                        if name in {"factor_get_formula"}
                        else "read-only PAT scope"
                    ),
                    "schema_sha256": schema_hash(schema),
                    "required_arguments": schema.get("required") or [],
                    "enums": schema_enums(schema),
                    "limits": schema_limits(schema),
                    "pagination": "cursor" in (schema.get("properties") or {}),
                    "as_of": "as_of" in (schema.get("properties") or {}),
                    "request_media_type": "application/json",
                    "response_media_type": call.get("content_type") if call else None,
                    "content_block_types": sorted(
                        {
                            str(block.get("type"))
                            for block in (
                                (((call.get("envelope") or {}).get("result") or {}).get("content") or [])
                                if call and isinstance(call.get("envelope"), dict)
                                else []
                            )
                            if isinstance(block, dict) and block.get("type")
                        }
                    ),
                    "case_id": "MCP-016",
                    "status": status,
                    "failure_class": failure_class,
                    "reason": reason,
                    "call": call_summary(call),
                }
            )
        missing_tools = sorted(EXPECTED_TOOLS - set(tool_by_name))
        extra_tools = sorted(set(tool_by_name) - EXPECTED_TOOLS)
        for name in missing_tools:
            matrix.append(
                {
                    "name": name,
                    "access_mode": "unknown",
                    "case_id": "MCP-016",
                    "status": "FAIL",
                    "failure_class": "FAIL_REQUIRED_TOOL_MISSING",
                    "reason": "required Factor 4.0 tool absent from fresh tools/list",
                }
            )

        pit_results: list[dict[str, Any]] = []
        for name in sorted(AS_OF_TOOLS):
            group = pit_calls.get(name)
            if not group or not all(key in group for key in ("current", "history", "future")):
                pit_results.append(
                    {
                        "tool": name,
                        "status": "BLOCKED",
                        "failure_class": "BLOCKED_DATA_PRECONDITION",
                        "reason": "one or more current/history/future calls could not be constructed",
                    }
                )
                continue
            current = group["current"]
            history = group["history"]
            future = group["future"]
            history_as_of = str(group["history_as_of"])
            leaks = history_leaks(name, history, history_as_of, fixtures)
            unseen = future_unseen(name, future, fixtures) if success(future) else []
            missing_warnings: list[str] = []
            if name == "factor_search":
                for phase, call in (("current", current), ("future", future)):
                    items = response_items(call)
                    exposes_current_library = any("library_status" in item for item in items)
                    if (
                        success(call)
                        and exposes_current_library
                        and "CURRENT_LIBRARY_STATUS_NOT_POINT_IN_TIME" not in extract_warnings(call)
                    ):
                        missing_warnings.append(
                            f"{phase}:CURRENT_LIBRARY_STATUS_NOT_POINT_IN_TIME"
                        )
            technical = [
                f"{phase}:{error_code(call)}"
                for phase, call in (("current", current), ("history", history), ("future", future))
                if error_code(call) in KNOWN_BLOCKING_CODES
            ]
            current_future_equal = (
                canonical(business(current)) == canonical(business(future))
                if success(current) and success(future)
                else None
            )
            if leaks or unseen:
                status = "FAIL"
                failure_class = "FAIL_POINT_IN_TIME_FUTURE_LEAK"
                severity = "P0"
                reason = "historical/future response exposed data outside the visible test-start snapshot"
            elif missing_warnings:
                status = "FAIL"
                failure_class = "FAIL_WARNING_SEMANTICS"
                severity = "P1"
                reason = "current-only library status was returned without the required PIT warning"
            elif technical:
                status = "BLOCKED"
                failure_class = "BLOCKED_TECHNICAL_DEPENDENCY"
                severity = None
                reason = "; ".join(technical)
            elif not success(current) or not success(future):
                status = "FAIL"
                failure_class = "FAIL_CONTRACT"
                severity = "P1"
                reason = "legal current or future request failed outside a documented blocking class"
            else:
                status = "PASS"
                failure_class = None
                severity = None
                reason = "no historical leak, unseen future identifier, or required-warning loss"
            pit_results.append(
                {
                    "tool": name,
                    "status": status,
                    "failure_class": failure_class,
                    "severity": severity,
                    "reason": reason,
                    "history_as_of": history_as_of,
                    "future_as_of": future_as_of,
                    "history_leaks": leaks,
                    "future_unseen_identifiers": unseen,
                    "missing_expected_warnings": missing_warnings,
                    "current_future_normalized_equal": current_future_equal,
                    "calls": {
                        "current": call_summary(current),
                        "history": call_summary(history),
                        "future": call_summary(future),
                    },
                }
            )

        representation = representation_result(executed)
        matrix_counts = dict(Counter(row["status"] for row in matrix))
        pit_counts = dict(Counter(row["status"] for row in pit_results))
        failures = [
            {
                "case_id": "MCP-016",
                "tool": row["name"],
                "severity": "P1",
                "failure_class": row.get("failure_class"),
                "reason": row["reason"],
            }
            for row in matrix
            if row["status"] == "FAIL"
        ]
        if representation["status"] == "FAIL":
            failures.append(
                {
                    "case_id": "MCP-017",
                    "severity": "P1",
                    "failure_class": "FAIL_REPRESENTATION_MISMATCH",
                    "reason": "content and structuredContent, or business error mapping, diverged",
                }
            )
        failures.extend(
            {
                "case_id": "MCP-019",
                "tool": row["tool"],
                "severity": row["severity"],
                "failure_class": row["failure_class"],
                "reason": row["reason"],
            }
            for row in pit_results
            if row["status"] == "FAIL"
        )
        report = {
            "run_id": output.name,
            "environment": "test",
            "mcp_url": MCP_URL,
            "read_only": True,
            "started_at": started.isoformat(),
            "current_as_of": current_as_of,
            "future_as_of": future_as_of,
            "protocol_version": runner.protocol_version,
            "server_info": init_result.get("serverInfo"),
            "fresh_tool_count": len(tool_by_name),
            "missing_expected_tools": missing_tools,
            "extra_tools": extra_tools,
            "write_tools_invoked": [],
            "write_permission_boundary_documented": write_boundary_ok,
            "db_transaction": {
                "start": fixtures.get("transaction"),
                "end": fixtures.get("transaction_end"),
                "finalization": "ROLLBACK",
            },
            "fixture_summary": {
                key: report_fixture(fixtures.get(key))
                for key in (
                    "formula_metric_slice",
                    "validity",
                    "route",
                    "daily",
                    "universe",
                    "kb",
                    "approved_schema",
                )
            },
            "feedback_owner_audit": fixtures.get("feedback_owner_audit"),
            "rank_scope": report_fixture(rank_scope),
            "MCP-016": {"counts": matrix_counts, "tools": matrix},
            "MCP-017": representation,
            "MCP-019": {"counts": pit_counts, "tools": pit_results},
            "confirmed_failures": failures,
            "call_count": len(executed),
        }
        write_json(output / "results.json", report)
        write_json(
            output / "manifest.json",
            {
                "environment": "test",
                "read_only": True,
                "mcp_url": MCP_URL,
                "case_ids": ["MCP-016", "MCP-017", "MCP-019"],
                "report": "results.json",
                "raw_evidence": "numbered *.request.json/*.response.json files",
                "excluded": [
                    "write-tool invocation",
                    "slice end_time equality boundary",
                    "orphan rows",
                    "missing-document references",
                    "historical VWAP values",
                    "experience/style/compatibility findings",
                ],
            },
        )

        lines = [
            "# Factor 4.0 tool matrix and PIT validation",
            "",
            f"- Environment: `test`; mode: `READ_ONLY`",
            f"- Fresh tools/list: `{len(tool_by_name)}` tools",
            f"- MCP-016: `{matrix_counts}`",
            f"- MCP-017: `{representation['status']}` across `{representation['checked_response_count']}` dual representations",
            f"- MCP-019: `{pit_counts}`",
            f"- Confirmed failures: `{len(failures)}`",
            "",
            "## MCP-016 tool matrix",
            "",
            "| Tool | Mode | as_of | Cursor | Status | Reason |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in matrix:
            lines.append(
                f"| `{row['name']}` | {row.get('access_mode')} | {row.get('as_of', False)} | "
                f"{row.get('pagination', False)} | {row['status']} | {row['reason']} |"
            )
        lines.extend(
            [
                "",
                "## MCP-019 PIT matrix",
                "",
                "| Tool | Status | Historical leak | Future unseen | Warning loss |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in pit_results:
            lines.append(
                f"| `{row['tool']}` | {row['status']} | {len(row.get('history_leaks') or [])} | "
                f"{len(row.get('future_unseen_identifiers') or [])} | "
                f"{len(row.get('missing_expected_warnings') or [])} |"
            )
        lines.extend(["", "## Confirmed failures", ""])
        if failures:
            for index, failure in enumerate(failures, 1):
                lines.append(
                    f"{index}. `{failure['case_id']}` / `{failure.get('tool', '-')}` / "
                    f"`{failure.get('failure_class')}`: {failure['reason']}"
                )
        else:
            lines.append("No new P0/P1 product failure was confirmed in these three cases.")
        lines.extend(
            [
                "",
                "Blocked rows are environmental/data preconditions, not confirmed product defects.",
                "The feedback write tool was never invoked.",
                "",
            ]
        )
        (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    finally:
        if connection is not None:
            connection.rollback()
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    # Scan only after every artifact is closed.  Report counts, never values.
    forbidden_values = [token, settings.database.password or ""]
    findings: list[dict[str, Any]] = []
    for path in sorted(output.iterdir()):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        literal_hits = sum(bool(value and value in text) for value in forbidden_values)
        token_shape_hits = len(re.findall(r"naf_mcp_[A-Za-z0-9_-]+", text))
        if literal_hits or token_shape_hits:
            findings.append(
                {
                    "file": path.name,
                    "literal_secret_hits": literal_hits,
                    "token_shape_hits": token_shape_hits,
                }
            )
    write_json(
        output / "sensitive-scan.json",
        {
            "status": "PASS" if not findings else "FAIL",
            "scanned_file_count": len([path for path in output.iterdir() if path.is_file()]),
            "findings": findings,
        },
    )
    print(
        json.dumps(
            {
                "output_dir": str(output),
                "tool_count": len(tool_by_name),
                "matrix_counts": matrix_counts,
                "representation_status": representation["status"],
                "pit_counts": pit_counts,
                "confirmed_failure_count": len(failures),
                "sensitive_scan": "PASS" if not findings else "FAIL",
            },
            ensure_ascii=False,
        )
    )
    return 0 if not findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
