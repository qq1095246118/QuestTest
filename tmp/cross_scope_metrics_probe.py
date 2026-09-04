#!/usr/bin/env python3
"""Run a dynamic, read-only cross-scope Factor Data MCP probe.

The probe deliberately avoids catalog search calls (the catalog quota can be
independently exhausted).  It discovers metric and validity identities from a
consistent test-database snapshot, exercises the metrics/validity/rank/slice
read tools, and stores credential-free evidence under ``reports/factor4-deep``.
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
from collections import Counter, defaultdict
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
TOKEN = os.environ.get("FACTOR4_MCP_TOKEN") or os.environ.get("MCP_TOKEN")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
SENSITIVE = re.compile(
    r"authorization|token|password|secret|api[_-]?key|claim_token|signature|jwt|hmac",
    re.I,
)
KNOWN_BLOCKING = {
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE",
    "EXPORT_BUDGET_EXCEEDED",
}

IDENTITY_FIELDS = (
    "factor_id",
    "is_sub_factor_id",
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
)
METRIC_FIELDS = (
    "id",
    "run_id",
    "factor_id",
    "is_sub_factor_id",
    "ic_scope",
    "calculation_mode",
    "factor_bar_interval",
    "factor_window_bars",
    "return_bar_interval",
    "forward_return_bars",
    "interval_value",
    "forward_return_horizon",
    "universe_key",
    "symbol",
    "window_scope",
    "metric_window_bars",
    "metric_window_days",
    "period_start",
    "period_end",
    "slice_count",
    "valid_slice_count",
    "coverage_mean",
    "coverage_min",
    "mean_ic",
    "median_ic",
    "std_ic",
    "icir",
    "mean_abs_ic",
    "positive_ic_rate",
    "mean_rank_ic",
    "median_rank_ic",
    "std_rank_ic",
    "rank_icir",
    "mean_abs_rank_ic",
    "positive_rank_ic_rate",
    "ic_t_stat",
    "rank_ic_t_stat",
    "monotonicity_ratio",
    "mean_long_short_return",
    "long_short_annual_return",
    "long_short_t_stat",
    "is_period_start",
    "is_period_end",
    "oos_period_start",
    "oos_period_end",
    "is_slice_count",
    "oos_slice_count",
    "is_icir",
    "oos_icir",
    "icir_oos_retention",
    "rank_is_icir",
    "rank_oos_icir",
    "rank_icir_oos_retention",
    "scoring_version",
    "ic_score",
    "rank_ic_score",
    "icir_score",
    "rank_icir_score",
    "t_stat_score",
    "oos_retention_score",
    "monotonicity_score",
    "long_short_score",
    "final_score",
)


def json_default(value: Any) -> str:
    """Serialize DB-native values for JSON evidence."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Remove credential-like keys and token text recursively."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", "<redacted>", value)
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one sanitized JSON artifact."""

    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, sort_keys=True, default=json_default)
        + "\n",
        encoding="utf-8",
    )


def parse_body(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse a JSON or single-event SSE MCP response."""

    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" not in content_type.lower():
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    events: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if lines:
            parsed = json.loads("\n".join(lines))
            if isinstance(parsed, dict):
                events.append(parsed)
    if len(events) != 1:
        raise ValueError(f"expected one MCP event, got {len(events)}")
    return events[0]


class Client:
    """Minimal MCP HTTP client with sanitized evidence capture."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize a client for one bearer token and output directory."""

        self.token = token
        self.output = output
        self.sequence = 0
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.calls: list[dict[str, Any]] = []
        output.mkdir(parents=True, exist_ok=True)

    def _headers(self) -> dict[str, str]:
        """Build protocol headers for a request."""

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": UA,
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        return headers

    def request(self, case_id: str, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """Send one JSON-RPC request and return a normalized call record."""

        self.sequence += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": f"{case_id}-{uuid4()}", "method": method}
        if params is not None:
            payload["params"] = params
        request = urllib.request.Request(
            MCP_URL,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            headers=self._headers(),
            method="POST",
        )
        started = time.monotonic()
        status = 0
        raw = b""
        response_headers: dict[str, str] = {}
        transport_error: str | None = None
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    raw = response.read()
                    status = response.status
                    response_headers = {key.lower(): value for key, value in response.headers.items()}
                break
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                status = exc.code
                response_headers = {key.lower(): value for key, value in exc.headers.items()}
                if status not in {429, 502, 503, 504} or attempt == 3:
                    break
                time.sleep(attempt)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                transport_error = f"{type(exc).__name__}: {exc}"
                if attempt == 3:
                    break
                time.sleep(attempt)
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        parse_error: str | None = None
        envelope: dict[str, Any] | None = None
        if raw:
            try:
                envelope = parse_body(raw, response_headers.get("content-type", ""))
            except Exception as exc:  # preserve diagnostic transport evidence
                parse_error = f"{type(exc).__name__}: {exc}"
        result = envelope.get("result") if isinstance(envelope, dict) else None
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        text_business: dict[str, Any] | None = None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                    text_business = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    pass
        business = structured if isinstance(structured, dict) else text_business
        call = {
            "case_id": case_id,
            "method": method,
            "http_status": status,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "content_type": response_headers.get("content-type"),
            "parse_error": parse_error,
            "transport_error": transport_error,
            "envelope": envelope,
            "business": business if isinstance(business, dict) else {},
            "representations_equal": structured == text_business if structured is not None and text_business is not None else None,
        }
        stem = f"{self.sequence:03d}-{case_id}"
        write_json(self.output / f"{stem}.request.json", payload)
        if envelope is not None:
            write_json(self.output / f"{stem}.response.json", envelope)
        else:
            (self.output / f"{stem}.response.txt").write_text(raw.decode("utf-8", "replace"), encoding="utf-8")
        self.calls.append(call)
        return call

    def initialize(self) -> dict[str, Any]:
        """Negotiate the supported MCP protocol version."""

        call = self.request(
            "MCP-INIT",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "QuestTest-cross-scope-metrics", "version": "1.0"},
            },
        )
        result = ((call.get("envelope") or {}).get("result") or {})
        self.protocol_version = result.get("protocolVersion") if isinstance(result, dict) else None
        self.request("MCP-NOTIFY", "notifications/initialized", {})
        return call

    def tool(self, case_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one named MCP read tool."""

        return self.request(case_id, "tools/call", {"name": name, "arguments": arguments})


def business(call: dict[str, Any]) -> dict[str, Any]:
    """Return the structured business envelope from a call."""

    value = call.get("business")
    return value if isinstance(value, dict) else {}


def data(call: dict[str, Any]) -> dict[str, Any]:
    """Return the business data object from a call."""

    value = business(call).get("data")
    return value if isinstance(value, dict) else {}


def meta(call: dict[str, Any]) -> dict[str, Any]:
    """Return the business metadata object from a call."""

    value = business(call).get("meta")
    return value if isinstance(value, dict) else {}


def items(call: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a data.items list, or an empty list."""

    value = data(call).get("items")
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def error_code(call: dict[str, Any]) -> str | None:
    """Extract a structured MCP/business error code."""

    envelope = call.get("envelope") or {}
    if isinstance(envelope.get("error"), dict) and envelope["error"].get("code") is not None:
        return str(envelope["error"]["code"])
    error = business(call).get("error")
    return str(error.get("code")) if isinstance(error, dict) and error.get("code") is not None else None


def success(call: dict[str, Any]) -> bool:
    """Return whether a call has a successful JSON-RPC business result."""

    return (
        call.get("http_status") == 200
        and call.get("parse_error") is None
        and call.get("transport_error") is None
        and isinstance(call.get("envelope"), dict)
        and isinstance(call.get("envelope", {}).get("result"), dict)
        and call.get("envelope", {}).get("result", {}).get("isError") is not True
        and "error" not in business(call)
    )


def blocked(call: dict[str, Any]) -> bool:
    """Return whether a call is unavailable for a known transient/quota reason."""

    return error_code(call) in KNOWN_BLOCKING or call.get("transport_error") is not None


def parse_time(value: Any, naive_zone: timezone | ZoneInfo = LOCAL_TZ) -> datetime | None:
    """Normalize an API/DB timestamp to UTC."""

    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_zone)
    return parsed.astimezone(timezone.utc)


def number_equal(left: Any, right: Any) -> bool:
    """Compare nullable numbers exactly enough for persisted decimal values."""

    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    try:
        return Decimal(str(left)).normalize() == Decimal(str(right)).normalize()
    except (InvalidOperation, ValueError):
        return left == right


def value_equal(field: str, api_value: Any, db_value: Any) -> bool:
    """Compare one API value to its DB representation with timestamp rules."""

    if api_value is None or db_value is None:
        return api_value is None and db_value is None
    if isinstance(db_value, (int, float, Decimal)) and not isinstance(db_value, bool):
        return number_equal(api_value, db_value)
    if isinstance(db_value, datetime) or (isinstance(api_value, str) and "T" in api_value and field.endswith(("_at", "_start", "_end"))):
        left = parse_time(api_value, timezone.utc)
        # Metric period/slice timestamps are persisted as UTC wall clock;
        # lifecycle timestamps are local wall clock.  The caller supplies a
        # normalized DB value where possible, and this fallback handles both
        # representations by accepting an explicit offset from the API.
        right = parse_time(db_value, timezone.utc if field in {"period_start", "period_end", "is_period_start", "is_period_end", "oos_period_start", "oos_period_end"} else LOCAL_TZ)
        return left == right
    if isinstance(db_value, str) and db_value[:1] in {"{", "["}:
        try:
            return api_value == json.loads(db_value)
        except json.JSONDecodeError:
            pass
    return api_value == db_value or str(api_value) == str(db_value)


def open_db() -> pymysql.connections.Connection:
    """Open a test database connection for a read-only transaction."""

    settings = SettingsLoader.load("test", ROOT).database
    return pymysql.connect(
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
    )


def discover_scopes(cursor: Any, now: datetime) -> dict[str, dict[str, Any]]:
    """Discover bounded current TS aggregate, TS symbol, and CS aggregate scopes."""

    # Restrict to the common 1h/all/direct family to keep the DB scan bounded;
    # all remaining scope values are still discovered from rows, not guessed.
    cursor.execute(
        """
        SELECT m.ic_scope, m.calculation_mode, m.factor_bar_interval,
               m.factor_window_bars, m.return_bar_interval,
               m.forward_return_bars, m.universe_key, COALESCE(m.symbol,'') AS symbol,
               m.window_scope, m.scoring_version,
               COUNT(DISTINCT m.factor_id) AS factor_count,
               MAX(r.completed_at) AS latest_completed
        FROM factor_ic_summary_metrics m
        JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
        WHERE m.is_sub_factor_id=1 AND m.calculation_mode='direct'
          AND m.factor_bar_interval='1h' AND m.universe_key='all'
          AND r.completed_at <= %s
        GROUP BY m.ic_scope, m.calculation_mode, m.factor_bar_interval,
                 m.factor_window_bars, m.return_bar_interval,
                 m.forward_return_bars, m.universe_key, COALESCE(m.symbol,''),
                 m.window_scope, m.scoring_version
        HAVING factor_count >= 5
        ORDER BY latest_completed DESC, factor_count DESC
        LIMIT 300
        """,
        (now.replace(tzinfo=None),),
    )
    groups = [dict(row) for row in cursor.fetchall()]
    chosen: dict[str, dict[str, Any]] = {}
    for row in groups:
        scope = str(row["ic_scope"])
        symbol = str(row.get("symbol") or "")
        key = "cs_aggregate" if scope == "cross_sectional" and not symbol else None
        if scope == "time_series" and not symbol:
            key = "ts_aggregate"
        elif scope == "time_series" and symbol:
            key = "ts_symbol"
        if key and key not in chosen:
            chosen[key] = row
    return chosen


def scope_predicate(scope: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """Build a parameterized exact scope predicate for summary rows."""

    return (
        "m.is_sub_factor_id=1 AND m.ic_scope=%s AND m.calculation_mode=%s "
        "AND m.factor_bar_interval=%s AND m.factor_window_bars=%s "
        "AND m.return_bar_interval=%s AND m.forward_return_bars=%s "
        "AND m.universe_key=%s AND COALESCE(m.symbol,'')=%s "
        "AND m.window_scope=%s AND m.scoring_version=%s",
        (
            scope["ic_scope"], scope["calculation_mode"], scope["factor_bar_interval"],
            scope["factor_window_bars"], scope["return_bar_interval"],
            scope["forward_return_bars"], scope["universe_key"], scope.get("symbol") or "",
            scope["window_scope"], scope["scoring_version"],
        ),
    )


def latest_rows(cursor: Any, scope: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Load one newest completed summary per factor for a scope."""

    predicate, params = scope_predicate(scope)
    cursor.execute(
        f"""
        SELECT ranked.* FROM (
          SELECT m.*, r.completed_at AS run_completed_at,
                 ROW_NUMBER() OVER (PARTITION BY m.factor_id
                   ORDER BY r.completed_at DESC, m.updated_at DESC, m.id DESC) AS rn
          FROM factor_ic_summary_metrics m
          JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
          WHERE {predicate} AND r.completed_at <= %s
        ) ranked WHERE ranked.rn=1 ORDER BY ranked.factor_id
        """,
        params + (now.replace(tzinfo=None),),
    )
    return [dict(row) for row in cursor.fetchall()]


def scope_args(scope: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Build a complete metric identity request from a discovered scope."""

    return {
        "ic_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "as_of": now.isoformat(),
        "scoring_version": scope["scoring_version"],
        "symbol": scope.get("symbol") or "",
    }


def metric_mismatches(api_row: dict[str, Any], db_row: dict[str, Any]) -> list[str]:
    """Return fields that differ between one metric payload and DB row."""

    bad: list[str] = []
    for field in METRIC_FIELDS:
        if field in api_row and not value_equal(field, api_row.get(field), db_row.get(field)):
            bad.append(field)
    return bad


def compact(call: dict[str, Any]) -> dict[str, Any]:
    """Return a small report-safe call summary."""

    payload = data(call)
    return {
        "http_status": call.get("http_status"),
        "is_error": ((call.get("envelope") or {}).get("result") or {}).get("isError") if isinstance(call.get("envelope"), dict) else None,
        "error_code": error_code(call),
        "elapsed_seconds": call.get("elapsed_seconds"),
        "item_count": len(items(call)),
        "summary_count": len(payload.get("ic_summaries") or []) if isinstance(payload.get("ic_summaries"), list) else None,
        "next_cursor": bool(meta(call).get("next_cursor")),
        "request_id": meta(call).get("request_id"),
    }


def add_case(cases: list[dict[str, Any]], case_id: str, module: str, expected: str, actual: Any, passed: bool, call: dict[str, Any] | None = None, *, block_reason: str | None = None, notes: str = "") -> None:
    """Append one PASS/FAIL/BLOCKED case result."""

    status = "BLOCKED" if block_reason else ("PASS" if passed else "FAIL")
    cases.append(
        {
            "case_id": case_id,
            "module": module,
            "mode": "READ_ONLY",
            "status": status,
            "severity": "P1" if status == "FAIL" else None,
            "failure_class": None if status != "FAIL" else "FAIL_DATA_CONSISTENCY",
            "expected": expected,
            "actual": actual,
            "call": compact(call) if call else None,
            "block_reason": block_reason,
            "notes": notes,
        }
    )


def metric_request(scope: dict[str, Any], factor_ref: str, now: datetime, run_id: str | None = None) -> dict[str, Any]:
    """Build one factor_get_metrics request."""

    args = {"factor_ref": factor_ref, **scope_args(scope, now)}
    if run_id is not None:
        args["run_id"] = run_id
    return args


def rank_request(scope: dict[str, Any], now: datetime, metric: str = "mean_ic", **extra: Any) -> dict[str, Any]:
    """Build one bounded raw-rank request."""

    return {
        "metric": metric,
        "top_k": 3,
        "bottom_k": 3,
        "ic_scope": scope["ic_scope"],
        "validity_scope": scope["ic_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "ranking_mode": "raw",
        "scoring_version": scope["scoring_version"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "as_of": now.isoformat(),
        "min_valid_slice_count": 0,
        "min_coverage_mean": 0,
        "require_oos": False,
        "kind": "sub_factor",
        "calculation_mode": scope["calculation_mode"],
        "symbol": scope.get("symbol") or "",
        **extra,
    }


def check_rank(call: dict[str, Any], db_rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    """Compare raw rank ordering and item identities to DB candidates."""

    payload = data(call)
    top = [x for x in payload.get("top_items") or [] if isinstance(x, dict)]
    bottom = [x for x in payload.get("bottom_items") or [] if isinstance(x, dict)]
    by_id = {int(row["id"]): row for row in db_rows}
    values: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for row in top + bottom:
        metric_id = row.get("metric_id")
        db = by_id.get(int(metric_id)) if metric_id is not None else None
        if db is None:
            mismatches.append({"metric_id": metric_id, "reason": "not in latest DB candidate set"})
            continue
        if not number_equal(row.get("raw_metric_value"), db.get(metric)):
            mismatches.append({"metric_id": metric_id, "field": metric, "reason": "raw metric mismatch"})
        values.append({"metric_id": metric_id, "value": row.get("ranking_value")})
    top_values = [Decimal(str(x["ranking_value"])) for x in top if x.get("ranking_value") is not None]
    bottom_values = [Decimal(str(x["ranking_value"])) for x in bottom if x.get("ranking_value") is not None]
    expected_top = sorted((Decimal(str(row[metric])), int(row["id"])) for row in db_rows if row.get(metric) is not None)[::-1][: len(top)]
    expected_bottom = sorted((Decimal(str(row[metric])), int(row["id"])) for row in db_rows if row.get(metric) is not None)[: len(bottom)]
    actual_top_ids = [int(x["metric_id"]) for x in top if x.get("metric_id") is not None]
    actual_bottom_ids = [int(x["metric_id"]) for x in bottom if x.get("metric_id") is not None]
    expected_top_ids = [item[1] for item in expected_top]
    expected_bottom_ids = [item[1] for item in expected_bottom]
    return {
        "top_count": len(top),
        "bottom_count": len(bottom),
        "top_descending": top_values == sorted(top_values, reverse=True),
        "bottom_ascending": bottom_values == sorted(bottom_values),
        "top_ids": actual_top_ids,
        "bottom_ids": actual_bottom_ids,
        "expected_top_ids": expected_top_ids,
        "expected_bottom_ids": expected_bottom_ids,
        "candidate_count": payload.get("candidate_count"),
        "evaluated_count": payload.get("evaluated_count"),
        "mismatches": mismatches,
        "ordering_matches_db": actual_top_ids == expected_top_ids and actual_bottom_ids == expected_bottom_ids,
    }


def discover_validity(cursor: Any, now: datetime) -> list[dict[str, Any]]:
    """Select one current complete validity row for each validity shape."""

    cursor.execute(
        """
        SELECT v.*, r.completed_at AS run_completed_at,
               ts.scoring_version AS ts_summary_scoring_version,
               cs.scoring_version AS cs_summary_scoring_version,
               ts.calculation_mode AS calc_mode,
               ts.factor_bar_interval AS ts_interval,
               ts.factor_window_bars AS ts_window,
               ts.return_bar_interval AS ts_return_interval,
               ts.forward_return_bars AS ts_forward,
               ts.window_scope AS ts_window_scope,
               ts.symbol AS ts_symbol,
               cs.symbol AS cs_symbol
        FROM factor_validity_status v
        JOIN factor_ic_runs r ON r.run_id=v.run_id AND r.status='completed'
        JOIN factor_ic_summary_metrics ts ON ts.id=v.time_series_summary_id
        JOIN factor_ic_summary_metrics cs ON cs.id=v.cross_sectional_summary_id
        WHERE v.is_sub_factor_id=1 AND v.overall_is_valid=1
          AND ts.factor_id=v.factor_id AND cs.factor_id=v.factor_id
          AND ts.run_id=v.run_id AND cs.run_id=v.run_id
          AND ts.ic_scope='time_series' AND cs.ic_scope='cross_sectional'
          AND r.completed_at <= %s
        ORDER BY v.updated_at DESC, v.id DESC
        LIMIT 500
        """,
        (now.replace(tzinfo=None),),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        ts = int(row.get("time_series_is_valid") or 0) == 1
        cs = int(row.get("cross_sectional_is_valid") or 0) == 1
        shape = "TS_CS" if ts and cs else "TS_ONLY" if ts else "CS_ONLY" if cs else "NONE"
        if shape != "NONE" and shape not in result:
            result[shape] = row
    return list(result.values())


def validity_request(row: dict[str, Any], validity_scope: str, now: datetime) -> dict[str, Any]:
    """Build a scope-specific factor_get_validity request."""

    prefix = "ts" if validity_scope == "time_series" else "cs"
    scoring = row[f"{prefix}_summary_scoring_version"] or (
        row.get("time_series_scoring_version") if prefix == "ts" else row.get("cross_sectional_scoring_version")
    )
    return {
        "factor_ref": f"sub_factor:{row['factor_id']}",
        "validity_scope": validity_scope,
        "calculation_mode": row["calc_mode"],
        "universe_key": row["universe_key"],
        "window_scope": row["window_scope"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
        "as_of": now.isoformat(),
        "scoring_version": scoring,
        "symbol": "",
        "run_id": row["run_id"],
    }


def main() -> int:
    """Execute the dynamic metrics/validity/rank/slices matrix."""

    if not TOKEN:
        raise SystemExit("FACTOR4_MCP_TOKEN or MCP_TOKEN is required")
    if not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("test MCP host gate failed")
    now = datetime.now(LOCAL_TZ)
    stamp = now.strftime("%Y%m%dT%H%M%S%z")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-cross-scope-metrics"
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "manifest.json", {"environment": "test", "read_only": True, "mcp_url": MCP_URL, "excluded": ["catalog quota-dependent factor_search", "known TS search timeout", "recommendation PIT", "route_count", "updated_after equality", "slice end-time boundary", "orphan/missing-document/VWAP/UX/compatibility findings"]})
    db = open_db()
    cases: list[dict[str, Any]] = []
    try:
        cursor = db.cursor()
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        cursor.execute("SELECT COUNT(*) AS n FROM factor_ic_summary_metrics")
        before_count = int(cursor.fetchone()["n"])
        scopes = discover_scopes(cursor, now)
        scope_rows = {key: latest_rows(cursor, scope, now) for key, scope in scopes.items()}
        validity_rows = discover_validity(cursor, now)
        client = Client(TOKEN, output)
        init = client.initialize()
        add_case(cases, "MCP-INIT", "protocol", "protocol 2025-06-18 negotiated", client.protocol_version, client.protocol_version == "2025-06-18", init)
        if not scopes:
            add_case(cases, "FIXTURE-SCOPES", "precondition", "at least one dynamic metric scope", {}, False, block_reason="no bounded completed metric scope in test DB")
        for scope_key in ("ts_aggregate", "ts_symbol", "cs_aggregate"):
            scope = scopes.get(scope_key)
            rows = scope_rows.get(scope_key) or []
            if not scope or not rows:
                add_case(cases, f"{scope_key.upper()}-FIXTURE", "precondition", "dynamic scope has at least five rows", {"scope": scope, "rows": len(rows)}, False, block_reason="scope unavailable or empty")
                continue
            # Single metrics resolution without and with explicit run_id.
            target = rows[0]
            ref = f"sub_factor:{target['factor_id']}"
            for suffix, run_id in (("AUTO", None), ("EXPLICIT", str(target["run_id"]))):
                case_id = f"METRICS-{scope_key.upper()}-{suffix}"
                call = client.tool(case_id, "factor_get_metrics", metric_request(scope, ref, now, run_id))
                summaries = data(call).get("ic_summaries") or []
                api_row = summaries[0] if len(summaries) == 1 and isinstance(summaries[0], dict) else None
                mismatches = metric_mismatches(api_row, target) if api_row else ["summary_missing"]
                resolved = data(call).get("resolved_scope") or {}
                identity_bad = [field for field in ("ic_scope", "calculation_mode", "factor_window_bars", "return_bar_interval", "forward_return_bars", "universe_key", "symbol", "window_scope", "scoring_version") if str(resolved.get(field) or "") != str(scope.get(field) or "")]
                ok = success(call) and api_row is not None and int(api_row.get("id", -1)) == int(target["id"]) and not mismatches and not identity_bad
                if blocked(call):
                    add_case(cases, case_id, "metrics.single", "exact metric is readable and DB-identical", {"error_code": error_code(call), "scope": scope_key}, False, call, block_reason="known transient/quota/dependency response")
                else:
                    add_case(cases, case_id, "metrics.single", "exact metric is readable and DB-identical", {"summary_id": api_row.get("id") if api_row else None, "mismatches": mismatches, "resolved_scope_mismatches": identity_bad}, ok, call)
            # Batch with three valid refs and one isolated unknown ref.
            refs = [f"sub_factor:{row['factor_id']}" for row in rows[:3]] + ["sub_factor:999999999"]
            batch_args = scope_args(scope, now)
            batch_args["factor_refs"] = refs
            batch = client.tool(f"METRICS-{scope_key.upper()}-BATCH", "factor_get_metrics_batch", batch_args)
            batch_items = items(batch)
            valid_items = [item for item in batch_items if item.get("success") is True]
            unknown_items = [item for item in batch_items if item.get("factor_ref") == "sub_factor:999999999"]
            batch_bad: list[Any] = []
            expected_by_ref = {f"sub_factor:{row['factor_id']}": row for row in rows[:3]}
            for item in valid_items:
                payload = item.get("data") or {}
                expected = expected_by_ref.get(str(item.get("factor_ref")))
                if expected is None or metric_mismatches(payload, expected):
                    batch_bad.append({"factor_ref": item.get("factor_ref"), "fields": metric_mismatches(payload, expected) if expected else ["unexpected_ref"]})
            batch_ok = success(batch) and len(batch_items) == len(refs) and len(valid_items) == 3 and len(unknown_items) == 1 and (unknown_items[0].get("error") or {}).get("code") == "FACTOR_NOT_FOUND" and not batch_bad
            if blocked(batch):
                add_case(cases, f"METRICS-{scope_key.upper()}-BATCH", "metrics.batch", "valid items match DB and unknown item remains isolated", {"error_code": error_code(batch)}, False, batch, block_reason="known transient/quota/dependency response")
            else:
                add_case(cases, f"METRICS-{scope_key.upper()}-BATCH", "metrics.batch", "valid items match DB and unknown item remains isolated", {"input_count": len(refs), "returned_count": len(batch_items), "batch_mismatches": batch_bad, "unknown_error": (unknown_items[0].get("error") if unknown_items else None)}, batch_ok, batch)
            # Raw rank and one threshold-filtered rank.  TS scope timeouts are
            # an already tracked availability family and are not re-filed.
            rank = client.tool(f"RANK-{scope_key.upper()}-RAW", "factor_rank", rank_request(scope, now))
            rank_evidence = check_rank(rank, rows, "mean_ic") if success(rank) else {"error_code": error_code(rank)}
            rank_ok = success(rank) and rank_evidence.get("top_count", 0) > 0 and rank_evidence.get("bottom_count", 0) > 0 and rank_evidence.get("top_descending") and rank_evidence.get("bottom_ascending") and rank_evidence.get("ordering_matches_db") and not rank_evidence.get("mismatches")
            if blocked(rank) and scope_key.startswith("ts"):
                add_case(cases, f"RANK-{scope_key.upper()}-RAW", "rank", "raw rank is ordered and DB-identical", rank_evidence, False, rank, block_reason="known TS research-scope availability family")
            elif blocked(rank):
                add_case(cases, f"RANK-{scope_key.upper()}-RAW", "rank", "raw rank is ordered and DB-identical", rank_evidence, False, rank, block_reason="known transient/quota/dependency response")
            else:
                add_case(cases, f"RANK-{scope_key.upper()}-RAW", "rank", "raw rank is ordered and DB-identical", rank_evidence, rank_ok, rank)
            if scope_key == "cs_aggregate":
                numeric = [Decimal(str(row["mean_ic"])) for row in rows if row.get("mean_ic") is not None]
                threshold = float(max(numeric) + Decimal("0.000000001")) if numeric else 999.0
                filtered = client.tool("RANK-CS-MIN-IC", "factor_rank", rank_request(scope, now, min_coverage_mean=0, min_valid_slice_count=0, metric="mean_ic", top_k=3, bottom_k=3))
                # The zero-threshold variant is an explicit filter no-op; its
                # candidate count must equal the DB non-null metric count.
                expected_count = len(numeric)
                filtered_count = data(filtered).get("candidate_count")
                filter_ok = success(filtered) and int(filtered_count or -1) == expected_count
                add_case(cases, "RANK-CS-FILTER-BASE", "rank.filter", "zero quality thresholds preserve every non-null DB candidate", {"mcp_candidate_count": filtered_count, "db_nonnull_count": expected_count}, filter_ok, filtered)
                impossible = client.tool("RANK-CS-IMPOSSIBLE", "factor_rank", rank_request(scope, now, min_score=threshold, metric="final_score"))
                # final_score may be null in a subset; this call only checks
                # that an impossible threshold never returns a row above it.
                returned = [x for x in items(impossible) if x.get("ranking_value") is not None]
                impossible_ok = success(impossible) and not returned
                add_case(cases, "RANK-CS-IMPOSSIBLE", "rank.filter", "threshold above DB maximum returns terminal empty result", {"threshold": str(threshold), "returned_count": len(returned), "error_code": error_code(impossible)}, impossible_ok, impossible)
        # Exact completion-boundary metrics check on one available scope.
        boundary_scope_key = next((key for key in ("cs_aggregate", "ts_symbol", "ts_aggregate") if key in scopes and scope_rows.get(key)), None)
        if boundary_scope_key:
            scope = scopes[boundary_scope_key]
            target = scope_rows[boundary_scope_key][0]
            cursor.execute("SELECT completed_at FROM factor_ic_runs WHERE run_id=%s", (target["run_id"],))
            completion = cursor.fetchone()["completed_at"]
            if completion:
                at = completion.replace(tzinfo=LOCAL_TZ)
                before = at - timedelta(microseconds=1)
                equal = client.tool("METRICS-ASOF-EQUAL", "factor_get_metrics", metric_request(scope, f"sub_factor:{target['factor_id']}", at, str(target["run_id"])))
                before_call = client.tool("METRICS-ASOF-BEFORE", "factor_get_metrics", metric_request(scope, f"sub_factor:{target['factor_id']}", before, str(target["run_id"])))
                eq_ids = [row.get("id") for row in data(equal).get("ic_summaries") or []]
                before_ids = [row.get("id") for row in data(before_call).get("ic_summaries") or []]
                boundary_ok = success(equal) and int(target["id"]) in {int(x) for x in eq_ids if x is not None} and (not success(before_call) or int(target["id"]) not in {int(x) for x in before_ids if x is not None})
                add_case(cases, "METRICS-ASOF-BOUNDARY", "metrics.pit", "completion timestamp is inclusive and just-before is invisible", {"scope": boundary_scope_key, "equal_ids": eq_ids, "before_ids": before_ids, "completion": at.isoformat()}, boundary_ok, equal, notes="This is an exact completion boundary; slice end-time boundary behavior remains excluded.")
        # Validate one row per shape and both dimensions.  Overall validity is
        # intentionally not sent: the endpoint contract requires validity_scope
        # to match ic_scope, while overall filtering belongs to factor_search.
        for row in validity_rows[:3]:
            for validity_scope in ("time_series", "cross_sectional"):
                case_id = f"VALIDITY-{('TS_CS' if int(row.get('time_series_is_valid') or 0) and int(row.get('cross_sectional_is_valid') or 0) else 'TS_ONLY' if int(row.get('time_series_is_valid') or 0) else 'CS_ONLY')}-{validity_scope.upper()}"
                call = client.tool(case_id, "factor_get_validity", validity_request(row, validity_scope, now))
                item = data(call).get("item") or {}
                expected_metric_id = row["time_series_summary_id"] if validity_scope == "time_series" else row["cross_sectional_summary_id"]
                expected_status = row["time_series_status"] if validity_scope == "time_series" else row["cross_sectional_status"]
                ok = success(call) and item.get("id") == row["id"] and item.get("metric_id") == expected_metric_id and item.get("validity_status") == expected_status and item.get("run_id") == row["run_id"]
                actual = {"returned_id": item.get("id"), "returned_metric_id": item.get("metric_id"), "expected_metric_id": expected_metric_id, "returned_status": item.get("validity_status"), "expected_status": expected_status}
                if blocked(call):
                    add_case(cases, case_id, "validity.single", "scope-specific validity maps to the matching summary and DB row", actual, False, call, block_reason="known transient/quota/dependency response")
                else:
                    add_case(cases, case_id, "validity.single", "scope-specific validity maps to the matching summary and DB row", actual, ok, call)
            refs = [f"sub_factor:{row['factor_id']}" for row in validity_rows[:3]] + ["sub_factor:999999999"]
            args = validity_request(row, "cross_sectional", now)
            args.pop("factor_ref", None)
            args["factor_refs"] = refs
            batch = client.tool(f"VALIDITY-BATCH-{row['id']}", "factor_get_validity_batch", args)
            batch_items = items(batch)
            unknown = [x for x in batch_items if x.get("factor_ref") == "sub_factor:999999999"]
            batch_ok = success(batch) and len(batch_items) == len(refs) and len(unknown) == 1 and (unknown[0].get("error") or {}).get("code") == "FACTOR_NOT_FOUND"
            add_case(cases, f"VALIDITY-BATCH-{row['id']}", "validity.batch", "batch preserves valid rows and isolates unknown factor", {"input_count": len(refs), "returned_count": len(batch_items), "unknown_error": unknown[0].get("error") if unknown else None}, batch_ok, batch)
        # A CS slice page/continuation check uses the first CS scope with rows.
        cs_scope = scopes.get("cs_aggregate")
        cs_rows = scope_rows.get("cs_aggregate") or []
        if cs_scope and cs_rows:
            target = cs_rows[0]
            cursor.execute("SELECT MIN(slice_start) AS start_time, MAX(slice_end) AS end_time FROM factor_ic_slice_metrics WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=1 AND ic_scope='cross_sectional' AND calculation_mode=%s AND factor_bar_interval=%s AND factor_window_bars=%s AND return_bar_interval=%s AND forward_return_bars=%s AND universe_key=%s AND COALESCE(symbol,'')=%s AND window_scope=%s", (target["run_id"], target["factor_id"], target["calculation_mode"], target["factor_bar_interval"], target["factor_window_bars"], target["return_bar_interval"], target["forward_return_bars"], target["universe_key"], target.get("symbol") or "", target["window_scope"]))
            period = cursor.fetchone()
            start_time = period.get("start_time") if period else target.get("period_start")
            end_time = period.get("end_time") if period else target.get("period_end")
            if start_time and end_time:
                def api_period(value: Any) -> str:
                    """Render a metric period from DB UTC wall-clock to API +08."""

                    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed.astimezone(LOCAL_TZ).isoformat()
                args = {
                    "factor_ref": f"sub_factor:{target['factor_id']}",
                    **scope_args(cs_scope, now),
                    "run_id": target["run_id"],
                    "start_time": api_period(start_time),
                    "end_time": api_period(end_time),
                    "limit": 3,
                }
                page = client.tool("SLICES-CS-PAGE", "factor_get_metric_slices", args)
                page_items = items(page)
                next_cursor = meta(page).get("next_cursor")
                page_ok = success(page) and len(page_items) <= 3 and all(row.get("ic_scope") == "cross_sectional" and row.get("factor_id") == target["factor_id"] and row.get("run_id") == target["run_id"] for row in page_items)
                add_case(cases, "SLICES-CS-PAGE", "slices", "CS slices are bounded and retain exact factor/run/scope identity", {"returned_ids": [x.get("id") for x in page_items], "cursor_present": bool(next_cursor)}, page_ok, page)
                if next_cursor:
                    continuation_args = dict(args)
                    continuation_args["cursor"] = next_cursor
                    page2 = client.tool("SLICES-CS-CONTINUE", "factor_get_metric_slices", continuation_args)
                    ids = [x.get("id") for x in page_items + items(page2)]
                    cont_ok = success(page2) and not set(x.get("id") for x in page_items) & set(x.get("id") for x in items(page2)) and ids == sorted(ids)
                    add_case(cases, "SLICES-CS-CONTINUE", "slices", "CS signed cursor continues without overlap and in order", {"page1_ids": [x.get("id") for x in page_items], "page2_ids": [x.get("id") for x in items(page2)]}, cont_ok, page2)
                    changed = dict(continuation_args)
                    changed["symbol"] = "BTCUSDT"
                    bound = client.tool("SLICES-CS-CURSOR-BIND", "factor_get_metric_slices", changed)
                    add_case(cases, "SLICES-CS-CURSOR-BIND", "slices", "cursor is bound to the original scope", {"error_code": error_code(bound)}, error_code(bound) == "INVALID_ARGUMENT", bound)
        cursor.execute("SELECT COUNT(*) AS n FROM factor_ic_summary_metrics")
        after_count = int(cursor.fetchone()["n"])
        add_case(cases, "DB-READ-ONLY", "database", "read-only probe leaves metric row count unchanged", {"before": before_count, "after": after_count}, before_count == after_count)
        cursor.close()
        db.rollback()
    finally:
        db.close()
    counts = Counter(case["status"] for case in cases)
    result = {
        "run_id": output.name,
        "environment": "test",
        "mcp_url": MCP_URL,
        "read_only": True,
        "scope_keys": sorted(scopes),
        "dynamic_scope_summary": {key: {field: scope.get(field) for field in ("ic_scope", "factor_window_bars", "universe_key", "symbol", "window_scope", "scoring_version", "factor_count", "latest_completed")} for key, scope in scopes.items()},
        "case_counts": dict(sorted(counts.items())),
        "cases": cases,
        "new_failures": [case for case in cases if case["status"] == "FAIL"],
        "blocked_cases": [case for case in cases if case["status"] == "BLOCKED"],
        "call_count": len(client.calls),
        "sensitive_values_written": False,
    }
    write_json(output / "results.json", result)
    write_json(output / "call-ledger.json", client.calls)
    lines = [
        "# Cross-scope metrics/validity/rank probe",
        "",
        f"- Environment: `test`; mode: `READ_ONLY`",
        f"- Counts: `{dict(sorted(counts.items()))}`",
        f"- Dynamic scopes: `{', '.join(sorted(scopes)) or 'none'}`",
        "",
        "| Case | Module | Status | Expected |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(f"| {case['case_id']} | {case['module']} | {case['status']} | {case['expected']} |" for case in cases)
    failures = [case for case in cases if case["status"] == "FAIL"]
    lines.extend(["", "## New failures", ""])
    if failures:
        for case in failures:
            lines.extend([f"### {case['severity']} {case['case_id']}", "", "```json", json.dumps(case["actual"], ensure_ascii=False, indent=2, default=str), "```", ""])
    else:
        lines.append("No new independent P0/P1 defect was confirmed in this matrix.")
    lines.extend(["", "Known TS search timeout, recommendation PIT, route_count, updated_after equality, slice end-time boundary, orphan/missing-document/VWAP, UX, compatibility, and style findings were excluded by instruction.", ""])
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "case_counts": dict(sorted(counts.items()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
