"""Run a bounded read-only Factor 4 metric/validity/rank/slice regression."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
AS_OF = datetime.now(ZoneInfo("Asia/Shanghai")).replace(microsecond=0)
RUN_STAMP = AS_OF.strftime("%Y%m%dT%H%M%S%z")
OUT = ROOT / "reports" / "factor4-deep" / f"{RUN_STAMP}-metrics"
RUN_ID = str(uuid.uuid4())
TOKEN = os.environ.get("FACTOR4_MCP_TOKEN")
if not TOKEN:
    raise SystemExit("FACTOR4_MCP_TOKEN is required")

CASES: list[dict[str, Any]] = []
CALLS: dict[str, dict[str, Any]] = {}


def json_default(value: Any) -> Any:
    """Convert database-native values to credential-free JSON values."""

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def dump(path: Path, value: Any) -> None:
    """Write one generated JSON evidence artifact."""

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def parse_business(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return the structured MCP business envelope."""

    result = envelope.get("result") or {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content") or []
    if content and isinstance(content[0], dict):
        try:
            parsed = json.loads(content[0].get("text") or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def mcp(case_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one read-only MCP tool and persist its request and response."""

    request_id = f"{case_id}-{RUN_ID}"
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    started = time.monotonic()
    response: requests.Response | None = None
    attempts = 0
    for attempts in range(1, 4):
        response = requests.post(
            MCP_URL,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=payload,
            timeout=90,
        )
        if response.status_code not in {429, 502, 503, 504}:
            break
        if attempts < 3:
            time.sleep(attempts)
    assert response is not None
    elapsed = round(time.monotonic() - started, 3)
    try:
        envelope = response.json()
    except ValueError:
        envelope = {"unparsed_body_sha256": hashlib.sha256(response.content).hexdigest()}
    business = parse_business(envelope)
    result = envelope.get("result") if isinstance(envelope, dict) else None
    call = {
        "case_id": case_id,
        "tool": tool,
        "arguments": arguments,
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "elapsed_seconds": elapsed,
        "attempt_count": attempts,
        "is_error": result.get("isError") if isinstance(result, dict) else None,
        "business": business,
        "envelope": envelope,
    }
    dump(OUT / f"{case_id}.request.json", payload)
    dump(OUT / f"{case_id}.response.json", envelope)
    CALLS[case_id] = call
    return call


def assertion(assertion_id: str, expected: str, actual: Any, passed: bool, source: str = "oracle") -> dict[str, Any]:
    """Build one structured test assertion."""

    return {
        "assertion_id": assertion_id,
        "source": source,
        "expected": expected,
        "actual": actual,
        "result": "PASS" if passed else "FAIL",
    }


def record(
    case_id: str,
    module: str,
    call: dict[str, Any] | None,
    assertions: list[dict[str, Any]],
    *,
    db_evidence: dict[str, Any] | None = None,
    notes: str = "",
    blocked: str | None = None,
) -> None:
    """Append one case result in the project execution-result format."""

    now = datetime.now(timezone.utc).isoformat()
    if blocked:
        status = "BLOCKED"
        failure_class = "BLOCKED_DATA_PRECONDITION"
        severity = None
        reproducible = None
    else:
        status = "PASS" if all(row["result"] == "PASS" for row in assertions) else "FAIL"
        failure_class = None if status == "PASS" else "FAIL_DATA"
        severity = None if status == "PASS" else "P1"
        reproducible = True
    observed = {}
    request = {"transport": "db"}
    attempt_count = 1
    artifacts: list[str] = []
    if call:
        request = {
            "transport": "mcp",
            "tool": call["tool"],
            "arguments_redacted": call["arguments"],
        }
        observed = {
            "http_status": call["http_status"],
            "is_error": call["is_error"],
            "request_id": (call["business"].get("meta") or {}).get("request_id")
            or (call["business"].get("error") or {}).get("request_id"),
        }
        attempt_count = call["attempt_count"]
        artifacts = [f"{case_id}.request.json", f"{case_id}.response.json"]
    item = {
        "case_id": case_id,
        "module": module,
        "mode": "READ_ONLY",
        "status": status,
        "failure_class": failure_class,
        "severity": severity,
        "preconditions": ["test environment", "read-only invocation", "dynamically discovered metric scope"],
        "request": request,
        "observed": observed,
        "database_evidence": db_evidence,
        "artifacts": artifacts,
        "assertions": assertions,
        "expected_vs_actual": "all assertions matched" if status == "PASS" else blocked or "see failing assertions",
        "reproducible": reproducible,
        "first_observed_at": now,
        "attempt_count": attempt_count,
        "notes": notes,
    }
    if blocked:
        item["blocking_reason"] = blocked
    CASES.append(item)


def data(call: dict[str, Any]) -> dict[str, Any]:
    """Return the data object from one successful tool response."""

    value = call["business"].get("data") or {}
    return value if isinstance(value, dict) else {}


def error_code(call: dict[str, Any]) -> str | None:
    """Return a structured business error code."""

    error = call["business"].get("error") or {}
    return error.get("code") if isinstance(error, dict) else None


def decimal_equal(left: Any, right: Any) -> bool:
    """Compare nullable database and JSON numeric values by decimal value."""

    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    try:
        return Decimal(str(left)).normalize() == Decimal(str(right)).normalize()
    except (InvalidOperation, ValueError):
        return left == right


def instant(value: Any) -> datetime | None:
    """Normalize MySQL and ISO datetimes to UTC."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # MySQL DATETIME values in factor_db are persisted in UTC. The API
        # renders the same instants with an explicit +08:00 offset.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def values_equal(left: Any, right: Any) -> bool:
    """Compare one API value with its database representation."""

    if isinstance(right, Decimal) or isinstance(left, (int, float, Decimal)):
        return decimal_equal(left, right)
    if isinstance(right, datetime) or (isinstance(left, str) and "T" in left and isinstance(right, str) and " " in right):
        try:
            return instant(left) == instant(right)
        except ValueError:
            pass
    if isinstance(right, str) and right[:1] in {"{", "["}:
        try:
            right = json.loads(right)
        except json.JSONDecodeError:
            pass
    return left == right


def db_connect(config: dict[str, Any]) -> pymysql.Connection:
    """Open a test-database connection for read-only transactions."""

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


def fetch_one(cursor: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    """Execute one parameterized read and return its first row."""

    cursor.execute(sql, params)
    row = cursor.fetchone()
    return dict(row) if row else None


def metric_db_assertions(prefix: str, api_row: dict[str, Any], db_row: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Compare the key and calculated fields of one summary metric."""

    checks = [assertion(f"{prefix}-DBROW", "metric id exists exactly once", bool(db_row), db_row is not None)]
    if not db_row:
        return checks
    fields = [
        "id", "run_id", "factor_id", "is_sub_factor_id", "ic_scope", "calculation始化_mode",
        "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars",
        "universe_key", "symbol", "window_scope", "period_start", "period_end", "slice_count",
        "valid_slice_count", "coverage_mean", "coverage_min", "mean_ic", "median_ic", "std_ic",
        "icir", "mean_rank_ic", "rank_icir", "ic_t_stat", "rank_ic_t_stat", "is_icir",
        "oos_icir", "rank_is_icir", "rank_oos_icir", "scoring_version", "final_score",
    ]
    fields[5] = "calculation_mode"
    mismatches = [field for field in fields if field in api_row and not values_equal(api_row.get(field), db_row.get(field))]
    checks.append(assertion(f"{prefix}-FIELDS", "all returned identity/numeric fields equal DB", mismatches, not mismatches))
    return checks


def db_row_by_id(cursor: Any, table: str, row_id: int) -> dict[str, Any] | None:
    """Read one row by numeric id from an allow-listed metric table."""

    if table not in {"factor_ic_summary_metrics", "factor_validity_status", "factor_ic_slice_metrics"}:
        raise ValueError("table is not allow-listed")
    return fetch_one(cursor, f"SELECT * FROM `{table}` WHERE id=%s", (row_id,))


def rank_checks(prefix: str, call: dict[str, Any], cursor: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate rank ordering, identity, metric values, and database foreign keys."""

    payload = data(call)
    top = payload.get("top_items") or []
    bottom = payload.get("bottom_items") or []
    top_values = [Decimal(str(row["ranking_value"])) for row in top]
    bottom_values = [Decimal(str(row["ranking_value"])) for row in bottom]
    ids = [row.get("metric_id") for row in top + bottom]
    refs = [row.get("factor_ref") for row in top + bottom]
    checks = [
        assertion(f"{prefix}-OK", "successful rank response", call["is_error"], call["http_status"] == 200 and call["is_error"] is False),
        assertion(f"{prefix}-COUNT", "non-empty Top and Bottom", [len(top), len(bottom)], bool(top) and bool(bottom)),
        assertion(f"{prefix}-TOP", "Top sorted descending", [str(x) for x in top_values], top_values == sorted(top_values, reverse=True)),
        assertion(f"{prefix}-BOTTOM", "Bottom sorted ascending", [str(x) for x in bottom_values], bottom_values == sorted(bottom_values)),
        assertion(f"{prefix}-DISJOINT", "Top and Bottom factor sets are disjoint", refs, len(refs) == len(set(refs))),
    ]
    db_mismatches: list[dict[str, Any]] = []
    for row in top + bottom:
        db_row = db_row_by_id(cursor, "factor_ic_summary_metrics", int(row["metric_id"]))
        if not db_row:
            db_mismatches.append({"metric_id": row.get("metric_id"), "reason": "missing"})
            continue
        field = str((payload.get("resolved_sort") or {}).get("field") or "")
        raw_field = row.get("metric") or "mean_ic"
        raw_value = db_row.get(raw_field)
        if not decimal_equal(row.get("raw_metric_value"), raw_value):
            db_mismatches.append({"metric_id": row["metric_id"], "field": raw_field, "reason": "raw metric mismatch"})
        if "direction_adjusted" in field:
            expected = Decimal(str(raw_value)) * Decimal(str(row.get("direction_sign")))
            if not decimal_equal(row.get("ranking_value"), expected):
                db_mismatches.append({"metric_id": row["metric_id"], "reason": "direction adjustment mismatch"})
        for identity in ("factor_id", "ic_scope", "calculation_mode", "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars", "universe_key", "symbol", "window_scope", "scoring_version", "run_id"):
            if identity in row and not values_equal(row.get(identity), db_row.get(identity)):
                db_mismatches.append({"metric_id": row["metric_id"], "field": identity, "reason": "identity mismatch"})
    checks.append(assertion(f"{prefix}-DB", "every ranked item matches its DB metric row", db_mismatches, not db_mismatches))
    return checks, {"metric_ids": ids, "mismatches": db_mismatches}


def exact_metric_args(scope: dict[str, Any], factor_ref: str, *, run_id: str | None = None) -> dict[str, Any]:
    """Build one exact point-in-time metric request from a discovered scope."""

    args = {
        "factor_ref": factor_ref,
        "ic_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": scope["forward_return_bars"],
        "as_of": AS_OF.isoformat(),
        "scoring_version": scope["scoring_version"],
        "symbol": scope.get("symbol") or "",
    }
    if run_id is not None:
        args["run_id"] = run_id
    return args


def rank_args(scope: dict[str, Any]) -> dict[str, Any]:
    """Build one bounded signed rank request from a discovered scope."""

    return {
        "metric": "mean_ic",
        "top_k": 5,
        "bottom_k": 5,
        "ic_scope": scope["ic_scope"],
        "validity_scope": scope["ic_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": scope["forward_return_bars"],
        "ranking_mode": "signed",
        "scoring_version": scope["scoring_version"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "as_of": AS_OF.isoformat(),
        "min_valid_slice_count": 0,
        "min_coverage_mean": 0,
        "require_oos": False,
        "kind": scope["kind"],
        "calculation_mode": scope["calculation_mode"],
        "symbol": scope.get("symbol") or "",
    }


def select_scope(items: list[dict[str, Any]], ic_scope: str, with_symbol: bool) -> dict[str, Any] | None:
    """Select a 1Y scope with direction evidence and the requested symbol shape."""

    candidates = [
        row for row in items
        if row.get("ic_scope") == ic_scope
        and row.get("window_scope") == "1y"
        and bool(row.get("symbol")) is with_symbol
    ]
    if not candidates:
        return None
    if with_symbol:
        preferred = next((row for row in candidates if row.get("symbol") == "ARBUSDT"), None)
        return dict(preferred or candidates[0])
    return dict(candidates[0])


def select_db_scope(cursor: Any, ic_scope: str, with_symbol: bool) -> dict[str, Any]:
    """Select a completed 1Y metric scope when bounded MCP discovery truncates it."""

    symbol_predicate = "symbol <> ''" if with_symbol else "symbol = ''"
    cursor.execute(
        """SELECT factor_id
           FROM factor_validity_status
           WHERE is_sub_factor_id=1
             AND time_series_summary_id IS NOT NULL
             AND cross_sectional_summary_id IS NOT NULL
           ORDER BY updated_at DESC LIMIT 1"""
    )
    candidate = cursor.fetchone()
    if not candidate:
        raise RuntimeError("No indexed validity candidate exists for scope discovery")
    cursor.execute(
        f"""SELECT 'sub_factor' kind, ic_scope, calculation_mode,
                   factor_bar_interval, factor_window_bars, return_bar_interval,
                   forward_return_bars, universe_key, symbol, window_scope,
                   scoring_version, period_end metric_period_end
            FROM factor_ic_summary_metrics
            WHERE factor_id=%s AND is_sub_factor_id=1
              AND ic_scope=%s AND window_scope='1y'
              AND {symbol_predicate} AND is_icir IS NOT NULL AND oos_icir IS NOT NULL
              AND period_end >= '2026-08-01'
            ORDER BY period_end DESC,
                     CASE WHEN symbol='ARBUSDT' THEN 0 ELSE 1 END, symbol,
                     updated_at DESC
            LIMIT 1""",
        (candidate["factor_id"], ic_scope),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError(f"No usable DB {ic_scope} scope was discovered")
    return dict(row)


def main() -> None:
    """Execute the fixed high-value read-only regression matrix."""

    OUT.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load((ROOT / "config" / "test.yaml").read_text(encoding="utf-8"))
    db_config = config["database"]
    connection = db_connect(db_config)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute("SELECT DATABASE() database_name, CURRENT_USER() db_user, @@hostname hostname, NOW(6) snapshot_at")
            identity = dict(cursor.fetchone())
            dump(OUT / "db-identity.json", {"database_name": identity["database_name"], "current_user": identity["db_user"], "host_sha256": hashlib.sha256(str(identity["hostname"]).encode()).hexdigest(), "snapshot_at": identity["snapshot_at"], "read_only_transaction": True})

            scopes_call = mcp("MET-SCOPE-DISCOVER", "factor_list_metric_scopes", {"as_of": AS_OF.isoformat(), "kind": "sub_factor", "limit": 100})
            scope_items = data(scopes_call).get("items") or []
            ts_scope_mcp = select_scope(scope_items, "time_series", True)
            cs_scope_mcp = select_scope(scope_items, "cross_sectional", False)
            ts_scope = ts_scope_mcp or select_db_scope(cursor, "time_series", True)
            cs_scope = cs_scope_mcp or select_db_scope(cursor, "cross_sectional", False)
            record("MET-SCOPE-DISCOVER", "metrics.scope", scopes_call, [
                assertion("MET-SCOPE-A01", "successful bounded scope discovery", scopes_call["is_error"], scopes_call["is_error"] is False),
                assertion("MET-SCOPE-A02", "TS real-symbol and CS aggregate 1Y scopes resolved", {"ts": ts_scope, "cs": cs_scope}, bool(ts_scope.get("symbol")) and cs_scope.get("symbol") == ""),
            ], db_evidence={"ts_fallback": ts_scope_mcp is None, "cs_fallback": cs_scope_mcp is None}, notes="The bounded MCP page did not expose every 1Y scope; exact scopes were dynamically selected from the same read-only DB snapshot when needed")

            ts_rank = mcp("MET-RANK-TS", "factor_rank", rank_args(ts_scope))
            checks, evidence = rank_checks("MET-RANK-TS", ts_rank, cursor)
            record("MET-RANK-TS", "metrics.rank", ts_rank, checks, db_evidence=evidence)
            ts_rank_repeat = mcp("MET-RANK-TS-REPEAT", "factor_rank", rank_args(ts_scope))
            first_order = [(row.get("metric_id"), row.get("ranking_value")) for row in (data(ts_rank).get("top_items") or []) + (data(ts_rank).get("bottom_items") or [])]
            second_order = [(row.get("metric_id"), row.get("ranking_value")) for row in (data(ts_rank_repeat).get("top_items") or []) + (data(ts_rank_repeat).get("bottom_items") or [])]
            record("MET-RANK-TS-REPEAT", "metrics.rank", ts_rank_repeat, [assertion("MET-RANK-STABLE-A01", "identical point-in-time rank is stable", {"first": first_order, "second": second_order}, first_order == second_order)])

            cs_rank = mcp("MET-RANK-CS", "factor_rank", rank_args(cs_scope))
            checks, evidence = rank_checks("MET-RANK-CS", cs_rank, cursor)
            record("MET-RANK-CS", "metrics.rank", cs_rank, checks, db_evidence=evidence)

            ts_refs = [row["factor_ref"] for row in data(ts_rank).get("top_items") or []]
            cs_refs = [row["factor_ref"] for row in data(cs_rank).get("top_items") or []]
            if len(ts_refs) < 2 or len(cs_refs) < 2:
                raise RuntimeError("Rank did not provide enough factors for exact metric tests")

            ts_metric = mcp("MET-METRIC-TS", "factor_get_metrics", exact_metric_args(ts_scope, ts_refs[0]))
            ts_summaries = data(ts_metric).get("ic_summaries") or []
            ts_row = ts_summaries[0] if len(ts_summaries) == 1 else None
            ts_db = db_row_by_id(cursor, "factor_ic_summary_metrics", int(ts_row["id"])) if ts_row else None
            ts_checks = [assertion("MET-TS-A01", "exactly one TS summary", len(ts_summaries), len(ts_summaries) == 1)]
            if ts_row:
                ts_checks += metric_db_assertions("MET-TS", ts_row, ts_db)
            record("MET-METRIC-TS", "metrics.single", ts_metric, ts_checks, db_evidence={"summary_id": ts_row.get("id") if ts_row else None})

            cs_metric = mcp("MET-METRIC-CS", "factor_get_metrics", exact_metric_args(cs_scope, cs_refs[0]))
            cs_summaries = data(cs_metric).get("ic_summaries") or []
            cs_row = cs_summaries[0] if len(cs_summaries) == 1 else None
            cs_db = db_row_by_id(cursor, "factor_ic_summary_metrics", int(cs_row["id"])) if cs_row else None
            cs_checks = [assertion("MET-CS-A01", "exactly one CS summary", len(cs_summaries), len(cs_summaries) == 1)]
            if cs_row:
                cs_checks += metric_db_assertions("MET-CS", cs_row, cs_db)
            record("MET-METRIC-CS", "metrics.single", cs_metric, cs_checks, db_evidence={"summary_id": cs_row.get("id") if cs_row else None})

            batch_args = exact_metric_args(cs_scope, cs_refs[0])
            batch_args.pop("factor_ref")
            batch_args["factor_refs"] = [cs_refs[0], cs_refs[1], "sub_factor:999999999"]
            metrics_batch = mcp("MET-METRIC-BATCH", "factor_get_metrics_batch", batch_args)
            batch_items = data(metrics_batch).get("items") or []
            successes = [row for row in batch_items if row.get("success") is True]
            failures = [row for row in batch_items if row.get("success") is False]
            batch_mismatches: list[Any] = []
            for item in successes:
                row = item.get("data") or {}
                db_row = db_row_by_id(cursor, "factor_ic_summary_metrics", int(row["id"]))
                if any(check["result"] == "FAIL" for check in metric_db_assertions("BATCH", row, db_row)):
                    batch_mismatches.append(item.get("factor_ref"))
            first_batch_metric = next(
                (item.get("data") or {} for item in successes if item.get("factor_ref") == cs_refs[0]),
                {},
            )
            metric_single_batch_fields = (
                "id", "run_id", "factor_id", "ic_scope", "calculation_mode",
                "universe_key", "window_scope", "scoring_version", "mean_ic",
                "mean_rank_ic", "final_score",
            )
            metric_single_batch_mismatches = [
                field for field in metric_single_batch_fields
                if not values_equal((cs_row or {}).get(field), first_batch_metric.get(field))
            ]
            record("MET-METRIC-BATCH", "metrics.batch", metrics_batch, [
                assertion("MET-BATCH-A01", "two successes and one isolated item error", {"success": len(successes), "failure": len(failures)}, len(successes) == 2 and len(failures) == 1),
                assertion("MET-BATCH-A02", "unknown factor error stays item-scoped", failures[0].get("error") if failures else None, bool(failures) and failures[0].get("error", {}).get("code") == "FACTOR_NOT_FOUND"),
                assertion("MET-BATCH-A03", "successful batch rows equal DB", batch_mismatches, not batch_mismatches),
                assertion("MET-BATCH-A04", "single and batch return identical core fields for the same factor/scope", metric_single_batch_mismatches, not metric_single_batch_mismatches),
            ], db_evidence={"mismatches": batch_mismatches})

            cursor.execute(
                """SELECT v.*, ts.scoring_version ts_scoring_version, cs.scoring_version cs_scoring_version
                   FROM factor_validity_status v
                   JOIN factor_ic_summary_metrics ts ON ts.id=v.time_series_summary_id
                   JOIN factor_ic_summary_metrics cs ON cs.id=v.cross_sectional_summary_id
                   WHERE v.is_sub_factor_id=1 AND v.overall_is_valid=1
                     AND ((v.time_series_is_valid=1 AND v.cross_sectional_is_valid=0)
                       OR (v.time_series_is_valid=0 AND v.cross_sectional_is_valid=1))
                   ORDER BY v.updated_at DESC LIMIT 2"""
            )
            validity_candidates = [dict(row) for row in cursor.fetchall()]
            if len(validity_candidates) < 2:
                raise RuntimeError("No two one-scope-valid rows exist")
            validity_calls: dict[str, dict[str, Any]] = {}
            for scope_name, scoring_key in (("time_series", "ts_scoring_version"), ("cross_sectional", "cs_scoring_version")):
                candidate = validity_candidates[0]
                args = {
                    "factor_ref": f"sub_factor:{candidate['factor_id']}",
                    "validity_scope": scope_name,
                    "calculation_mode": "direct",
                    "universe_key": candidate["universe_key"],
                    "window_scope": candidate["window_scope"],
                    "interval": candidate["factor_bar_interval"],
                    "factor_window_bars": candidate["factor_window_bars"],
                    "return_bar_interval": candidate["return_bar_interval"],
                    "forward_return_bars": candidate["forward_return_bars"],
                    "as_of": AS_OF.isoformat(),
                    "scoring_version": candidate[scoring_key],
                    "symbol": "",
                    "run_id": candidate["run_id"],
                }
                call = mcp(f"MET-VALIDITY-{scope_name.upper()}", "factor_get_validity", args)
                validity_calls[scope_name] = call
                item = data(call).get("item") or {}
                expected_metric_id = candidate["time_series_summary_id" if scope_name == "time_series" else "cross_sectional_summary_id"]
                checks = [
                    assertion(f"VALID-{scope_name}-A01", "selected validity row equals DB", item.get("id"), item.get("id") == candidate["id"]),
                    assertion(f"VALID-{scope_name}-A02", "scope metric foreign key selected", item.get("metric_id"), item.get("metric_id") == expected_metric_id),
                    assertion(f"VALID-{scope_name}-A03", "requested scope status/score equals DB", {"status": item.get("validity_status"), "score": item.get(f"{scope_name}_score")}, item.get("validity_status") == candidate[f"{scope_name}_status"] and decimal_equal(item.get(f"{scope_name}_score"), candidate.get(f"{scope_name}_score"))),
                ]
                record(f"MET-VALIDITY-{scope_name.upper()}", "metrics.validity", call, checks, db_evidence={"validity_id": candidate["id"], "summary_id": expected_metric_id})

            validity_batch_args = dict(validity_calls["cross_sectional"]["arguments"])
            validity_batch_args.pop("factor_ref")
            validity_batch_args.pop("run_id")
            validity_batch_args["factor_refs"] = [f"sub_factor:{validity_candidates[0]['factor_id']}", f"sub_factor:{validity_candidates[1]['factor_id']}", "sub_factor:999999999"]
            validity_batch = mcp("MET-VALIDITY-BATCH", "factor_get_validity_batch", validity_batch_args)
            vb_items = data(validity_batch).get("items") or []
            single_validity = data(validity_calls["cross_sectional"]).get("item") or {}
            first_batch_validity = next(
                (
                    item.get("data") or {}
                    for item in vb_items
                    if item.get("factor_ref") == f"sub_factor:{validity_candidates[0]['factor_id']}"
                ),
                {},
            )
            validity_single_batch_fields = (
                "id", "run_id", "factor_id", "metric_id", "validity_status",
                "time_series_is_valid", "cross_sectional_is_valid", "overall_is_valid",
                "scoring_version",
            )
            validity_single_batch_mismatches = [
                field for field in validity_single_batch_fields
                if not values_equal(single_validity.get(field), first_batch_validity.get(field))
            ]
            record("MET-VALIDITY-BATCH", "metrics.validity.batch", validity_batch, [
                assertion("VALID-BATCH-A01", "batch preserves all input item outcomes", len(vb_items), len(vb_items) == 3),
                assertion("VALID-BATCH-A02", "two data rows and one isolated not-found", [(x.get("factor_ref"), x.get("success"), (x.get("error") or {}).get("code")) for x in vb_items], sum(x.get("success") is True for x in vb_items) == 2 and sum((x.get("error") or {}).get("code") == "FACTOR_NOT_FOUND" for x in vb_items) == 1),
                assertion("VALID-BATCH-A03", "single and batch return identical core fields for the same factor/scope", validity_single_batch_mismatches, not validity_single_batch_mismatches),
            ])

            if not ts_row or not ts_db:
                raise RuntimeError("TS summary required for slice and as_of tests")
            slice_args = exact_metric_args(ts_scope, ts_refs[0], run_id=str(ts_row["run_id"]))
            slice_args.update({"start_time": ts_row["period_start"], "end_time": ts_row["period_end"], "limit": 5})
            slice_args.pop("factor_ref")
            slice_args["factor_ref"] = ts_refs[0]
            page1 = mcp("MET-SLICES-P1", "factor_get_metric_slices", slice_args)
            p1_items = data(page1).get("items") or []
            p1_cursor = (page1["business"].get("meta") or {}).get("next_cursor")
            p1_db_mismatch = []
            for row in p1_items:
                db_row = db_row_by_id(cursor, "factor_ic_slice_metrics", int(row["id"]))
                if not db_row or any(not values_equal(row.get(field), db_row.get(field)) for field in ("run_id", "factor_id", "ic_scope", "symbol", "window_scope", "slice_start", "slice_end", "as_of_time", "sample_count", "ic", "rank_ic", "icir", "rank_icir")):
                    p1_db_mismatch.append(row.get("id"))
            record("MET-SLICES-P1", "metrics.slices", page1, [
                assertion("SLICE-P1-A01", "first page is full and has cursor", {"count": len(p1_items), "cursor": bool(p1_cursor)}, len(p1_items) == 5 and bool(p1_cursor)),
                assertion("SLICE-P1-A02", "slice rows sorted by time/id", [x.get("id") for x in p1_items], [x.get("id") for x in p1_items] == sorted(x.get("id") for x in p1_items)),
                assertion("SLICE-P1-A03", "all first-page fields equal DB", p1_db_mismatch, not p1_db_mismatch),
            ], db_evidence={"ids": [x.get("id") for x in p1_items], "mismatches": p1_db_mismatch})

            page2_args = dict(slice_args)
            page2_args["cursor"] = p1_cursor
            page2 = mcp("MET-SLICES-P2", "factor_get_metric_slices", page2_args)
            p2_items = data(page2).get("items") or []
            all_ids = [x.get("id") for x in p1_items + p2_items]
            record("MET-SLICES-P2", "metrics.slices", page2, [
                assertion("SLICE-P2-A01", "second page is non-empty", len(p2_items), bool(p2_items)),
                assertion("SLICE-P2-A02", "pages are disjoint and continue monotonically", all_ids, len(all_ids) == len(set(all_ids)) and all_ids == sorted(all_ids)),
            ])

            tampered_args = dict(page2_args)
            tampered_args["symbol"] = "BTCUSDT" if ts_scope.get("symbol") != "BTCUSDT" else "ETHUSDT"
            tampered = mcp("MET-SLICES-CURSOR-BIND", "factor_get_metric_slices", tampered_args)
            record("MET-SLICES-CURSOR-BIND", "metrics.slices", tampered, [
                assertion("SLICE-CURSOR-A01", "cursor cannot be reused for a changed query", {"is_error": tampered["is_error"], "code": error_code(tampered)}, tampered["is_error"] is True and error_code(tampered) == "INVALID_ARGUMENT"),
            ])

            run_row = fetch_one(cursor, "SELECT completed_at, status FROM factor_ic_runs WHERE run_id=%s", (ts_row["run_id"],))
            completed = run_row["completed_at"] if run_row else None
            if completed:
                # Run lifecycle timestamps are local Asia/Shanghai DATETIME,
                # unlike metric period/slice timestamps, which are UTC.
                completed_local = completed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                before = completed_local - timedelta(microseconds=1)
                after = completed_local + timedelta(seconds=1)
                before_args = exact_metric_args(ts_scope, ts_refs[0])
                before_args["as_of"] = before.isoformat()
                before_call = mcp("MET-ASOF-BEFORE", "factor_get_metrics", before_args)
                before_ids = [x.get("id") for x in data(before_call).get("ic_summaries") or []]
                record("MET-ASOF-BEFORE", "metrics.as_of", before_call, [assertion("ASOF-BEFORE-A01", "run completed after as_of is not visible", {"selected_id": ts_row["id"], "returned_ids": before_ids, "error": error_code(before_call)}, int(ts_row["id"]) not in {int(x) for x in before_ids if x is not None})], db_evidence={"run_completed_at": completed, "selected_summary_id": ts_row["id"]})
                after_args = exact_metric_args(ts_scope, ts_refs[0])
                after_args["as_of"] = after.isoformat()
                after_call = mcp("MET-ASOF-AFTER", "factor_get_metrics", after_args)
                after_ids = [x.get("id") for x in data(after_call).get("ic_summaries") or []]
                record("MET-ASOF-AFTER", "metrics.as_of", after_call, [assertion("ASOF-AFTER-A01", "completed run becomes visible after completion", {"selected_id": ts_row["id"], "returned_ids": after_ids}, int(ts_row["id"]) in {int(x) for x in after_ids if x is not None})], db_evidence={"run_completed_at": completed, "selected_summary_id": ts_row["id"]})
            else:
                record("MET-ASOF-BEFORE", "metrics.as_of", None, [], blocked="completed_at missing for selected run")
                record("MET-ASOF-AFTER", "metrics.as_of", None, [], blocked="completed_at missing for selected run")

            connection.rollback()
    finally:
        connection.close()

    counts: dict[str, int] = {}
    for case in CASES:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    failed = [case["case_id"] for case in CASES if case["status"] == "FAIL"]
    summary = {
        "run_id": RUN_ID,
        "environment": "test",
        "mcp_host": "test-factor-frontend.questvector.ai",
        "database": db_config["name"],
        "mode": "READ_ONLY",
        "started_as_of": AS_OF.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(CASES),
        "status_counts": counts,
        "failed_cases": failed,
        "cases": CASES,
        "sensitive_values_written": False,
    }
    dump(OUT / "results.json", summary)
    dump(OUT / "manifest.json", {key: summary[key] for key in ("run_id", "environment", "mcp_host", "database", "mode", "started_as_of", "finished_at", "case_count", "status_counts", "failed_cases", "sensitive_values_written")})
    print(json.dumps({"artifact_dir": str(OUT), "case_count": len(CASES), "status_counts": counts, "failed_cases": failed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
