"""Run a bounded read-only Factor 4 rank and point-in-time regression."""

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
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime.now(LOCAL_TZ).replace(microsecond=0)
RUN_ID = str(uuid.uuid4())
RUN_STAMP = AS_OF.strftime("%Y%m%dT%H%M%S%z")
OUT = ROOT / "reports" / "factor4-deep" / f"{RUN_STAMP}-rank-functional"
TOKEN = os.environ.get("FACTOR4_MCP_TOKEN")
if not TOKEN:
    raise SystemExit("FACTOR4_MCP_TOKEN is required")

METRICS = (
    "mean_ic",
    "mean_rank_ic",
    "icir",
    "rank_icir",
    "ic_t_stat",
    "rank_ic_t_stat",
    "final_score",
    "icir_oos_retention",
    "rank_icir_oos_retention",
)

CASES: list[dict[str, Any]] = []
CALLS: dict[str, dict[str, Any]] = {}


def json_default(value: Any) -> Any:
    """Convert database-native values to JSON-safe evidence values."""

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def dump(path: Path, value: Any) -> None:
    """Persist one UTF-8 JSON evidence file without credentials."""

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def parse_business(envelope: dict[str, Any]) -> dict[str, Any]:
    """Extract the structured business envelope from an MCP response."""

    result = envelope.get("result") or {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content") or []
    if content and isinstance(content[0], dict):
        try:
            value = json.loads(content[0].get("text") or "{}")
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def mcp(case_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke one MCP tool with bounded transient retry and save evidence."""

    payload = {
        "jsonrpc": "2.0",
        "id": f"{case_id}-{RUN_ID}",
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
    try:
        envelope = response.json()
    except ValueError:
        envelope = {
            "unparsed_body_sha256": hashlib.sha256(response.content).hexdigest(),
        }
    result = envelope.get("result") if isinstance(envelope, dict) else None
    call = {
        "case_id": case_id,
        "tool": tool,
        "arguments": arguments,
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "attempt_count": attempts,
        "is_error": result.get("isError") if isinstance(result, dict) else None,
        "business": parse_business(envelope),
    }
    dump(OUT / f"{case_id}.request.json", payload)
    dump(OUT / f"{case_id}.response.json", envelope)
    CALLS[case_id] = call
    return call


def data(call: dict[str, Any]) -> dict[str, Any]:
    """Return the data object from a structured MCP call."""

    value = call["business"].get("data") or {}
    return value if isinstance(value, dict) else {}


def error_code(call: dict[str, Any]) -> str | None:
    """Return a structured business error code when present."""

    value = call["business"].get("error") or {}
    return value.get("code") if isinstance(value, dict) else None


def assertion(assertion_id: str, expected: str, actual: Any, passed: bool) -> dict[str, Any]:
    """Create one explicit executable assertion result."""

    return {
        "assertion_id": assertion_id,
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
    db_evidence: Any = None,
    notes: str = "",
    blocked: str | None = None,
) -> None:
    """Append one case result with request and database evidence links."""

    if blocked:
        status = "BLOCKED"
        failure_class = "BLOCKED_DATA_PRECONDITION"
    else:
        status = "PASS" if all(row["result"] == "PASS" for row in assertions) else "FAIL"
        failure_class = None if status == "PASS" else "FAIL_BUSINESS"
    item: dict[str, Any] = {
        "case_id": case_id,
        "module": module,
        "mode": "READ_ONLY",
        "status": status,
        "failure_class": failure_class,
        "severity": "P1" if status == "FAIL" else None,
        "request": {"transport": "db"},
        "observed": {},
        "database_evidence": db_evidence,
        "artifacts": [],
        "assertions": assertions,
        "notes": notes,
    }
    if call:
        item["request"] = {
            "transport": "mcp",
            "tool": call["tool"],
            "arguments_redacted": call["arguments"],
        }
        item["observed"] = {
            "http_status": call["http_status"],
            "is_error": call["is_error"],
            "error_code": error_code(call),
            "request_id": (call["business"].get("meta") or {}).get("request_id"),
        }
        item["artifacts"] = [f"{case_id}.request.json", f"{case_id}.response.json"]
    if blocked:
        item["blocking_reason"] = blocked
    CASES.append(item)


def decimal_value(value: Any) -> Decimal | None:
    """Convert a JSON or DB number to Decimal, preserving null."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def values_equal(left: Any, right: Any) -> bool:
    """Compare nullable scalar values using exact decimal semantics."""

    if left is None or right is None:
        return left is None and right is None
    left_decimal = decimal_value(left)
    right_decimal = decimal_value(right)
    if left_decimal is not None and right_decimal is not None:
        return left_decimal.normalize() == right_decimal.normalize()
    return left == right


def db_connect(config: dict[str, Any]) -> pymysql.Connection:
    """Open a database connection used only for a read-only transaction."""

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


def metric_counts_sql() -> str:
    """Return aggregate expressions for all documented rank metrics."""

    return ", ".join(f"SUM(m.`{metric}` IS NOT NULL) AS `{metric}_count`" for metric in METRICS)


def discover_scope_pair(cursor: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Discover matching TS and CS scopes with data for every rank metric."""

    cursor.execute(
        f"""SELECT m.ic_scope, m.calculation_mode, m.factor_bar_interval,
                   m.factor_window_bars, m.return_bar_interval,
                   m.forward_return_bars, m.universe_key, m.symbol,
                   m.window_scope, m.scoring_version,
                   COUNT(DISTINCT m.factor_id) candidate_count,
                   SUM(m.coverage_mean IS NOT NULL) coverage_count,
                   MIN(m.coverage_mean) min_coverage,
                   MAX(m.coverage_mean) max_coverage,
                   MIN(m.valid_slice_count) min_slices,
                   MAX(m.valid_slice_count) max_slices,
                   {metric_counts_sql()}
            FROM factor_ic_summary_metrics m
            JOIN factor_ic_runs r ON r.run_id=m.run_id
            WHERE m.is_sub_factor_id=1
              AND m.calculation_mode='direct'
              AND m.window_scope='1y'
              AND r.status='completed'
              AND r.completed_at <= %s
            GROUP BY m.ic_scope, m.calculation_mode, m.factor_bar_interval,
                     m.factor_window_bars, m.return_bar_interval,
                     m.forward_return_bars, m.universe_key, m.symbol,
                     m.window_scope, m.scoring_version
            HAVING candidate_count >= 10
            ORDER BY candidate_count DESC""",
        (AS_OF.replace(tzinfo=None),),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    for row in rows:
        row["kind"] = "sub_factor"
    shared_fields = (
        "calculation_mode",
        "factor_bar_interval",
        "factor_window_bars",
        "return_bar_interval",
        "forward_return_bars",
        "universe_key",
        "window_scope",
    )
    pairs: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any]]] = []
    for ts_scope in rows:
        if ts_scope["ic_scope"] != "time_series" or not ts_scope.get("symbol"):
            continue
        if any(int(ts_scope.get(f"{metric}_count") or 0) == 0 for metric in METRICS):
            continue
        for cs_scope in rows:
            if cs_scope["ic_scope"] != "cross_sectional" or (cs_scope.get("symbol") or "") != "":
                continue
            if any(int(cs_scope.get(f"{metric}_count") or 0) == 0 for metric in METRICS):
                continue
            if any(ts_scope[field] != cs_scope[field] for field in shared_fields):
                continue
            coverage_spread = Decimal(str(cs_scope.get("max_coverage") or 0)) - Decimal(
                str(cs_scope.get("min_coverage") or 0)
            )
            score = (
                min(int(ts_scope["candidate_count"]), int(cs_scope["candidate_count"])),
                int(cs_scope.get("coverage_count") or 0),
                coverage_spread,
            )
            pairs.append((score, ts_scope, cs_scope))
    if not pairs:
        raise RuntimeError("No matching TS/CS scope pair covers all rank metrics")
    _, ts_scope, cs_scope = max(pairs, key=lambda item: item[0])
    return ts_scope, cs_scope


def rank_args(
    scope: dict[str, Any],
    metric: str,
    mode: str,
    top_k: int,
    bottom_k: int,
    **overrides: Any,
) -> dict[str, Any]:
    """Build one exact factor_rank request for the selected scope."""

    args = {
        "metric": metric,
        "top_k": top_k,
        "bottom_k": bottom_k,
        "ic_scope": scope["ic_scope"],
        "validity_scope": scope["ic_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": scope["forward_return_bars"],
        "ranking_mode": mode,
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
    args.update(overrides)
    return args


def rank_rows(call: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return Top and Bottom item lists from a rank response."""

    payload = data(call)
    top = payload.get("top_items") or []
    bottom = payload.get("bottom_items") or []
    return list(top), list(bottom)


def rank_checks(
    case_id: str,
    call: dict[str, Any],
    args: dict[str, Any],
    cursor: Any,
    *,
    require_nonempty: bool = True,
) -> list[dict[str, Any]]:
    """Validate ordering, uniqueness, ranking semantics, scope, and DB values."""

    payload = data(call)
    top, bottom = rank_rows(call)
    combined = top + bottom
    top_values = [decimal_value(row.get("ranking_value")) for row in top]
    bottom_values = [decimal_value(row.get("ranking_value")) for row in bottom]
    ids = [row.get("metric_id") for row in combined]
    refs = [row.get("factor_ref") for row in combined]
    semantic_mismatches: list[dict[str, Any]] = []
    identity_mismatches: list[dict[str, Any]] = []
    db_mismatches: list[dict[str, Any]] = []
    for row in combined:
        raw = decimal_value(row.get("raw_metric_value"))
        actual = decimal_value(row.get("ranking_value"))
        expected: Decimal | None = None
        if args["ranking_mode"] == "raw_signed":
            expected = raw
        elif args["ranking_mode"] == "absolute_diagnostic" and raw is not None:
            expected = abs(raw)
        elif args["ranking_mode"] == "signed" and raw is not None:
            sign = decimal_value(row.get("direction_sign"))
            expected = raw * sign if sign is not None else None
        if expected is None or actual != expected:
            semantic_mismatches.append(
                {
                    "metric_id": row.get("metric_id"),
                    "raw": row.get("raw_metric_value"),
                    "direction_sign": row.get("direction_sign"),
                    "expected": expected,
                    "actual": actual,
                }
            )
        identities = {
            "metric": args["metric"],
            "ic_scope": args["ic_scope"],
            "calculation_mode": args["calculation_mode"],
            "factor_bar_interval": args["interval"],
            "factor_window_bars": args["factor_window_bars"],
            "return_bar_interval": args["return_bar_interval"],
            "forward_return_bars": args["forward_return_bars"],
            "universe_key": args["universe_key"],
            "window_scope": args["window_scope"],
            "scoring_version": args["scoring_version"],
            "symbol": args.get("symbol") or "",
        }
        bad = [field for field, expected_value in identities.items() if row.get(field) != expected_value]
        if bad:
            identity_mismatches.append({"metric_id": row.get("metric_id"), "fields": bad})
        metric_id = row.get("metric_id")
        if metric_id is None:
            db_mismatches.append({"metric_id": None, "reason": "missing metric id"})
            continue
        cursor.execute(
            f"SELECT id, `{args['metric']}` metric_value FROM factor_ic_summary_metrics WHERE id=%s",
            (int(metric_id),),
        )
        db_row = cursor.fetchone()
        if not db_row or not values_equal(row.get("raw_metric_value"), db_row.get("metric_value")):
            db_mismatches.append(
                {
                    "metric_id": metric_id,
                    "api": row.get("raw_metric_value"),
                    "db": db_row.get("metric_value") if db_row else None,
                }
            )
    top_sorted = all(value is not None for value in top_values) and top_values == sorted(
        top_values, reverse=True
    )
    bottom_sorted = all(value is not None for value in bottom_values) and bottom_values == sorted(
        bottom_values
    )
    expected_empty_sides = (not args["top_k"] or not top) and (not args["bottom_k"] or not bottom)
    if require_nonempty:
        expected_empty_sides = (args["top_k"] == 0 or bool(top)) and (
            args["bottom_k"] == 0 or bool(bottom)
        )
    checks = [
        assertion(
            f"{case_id}-A01",
            "successful metric-only rank response",
            {"http": call["http_status"], "is_error": call["is_error"], "error": error_code(call)},
            call["http_status"] == 200 and call["is_error"] is False,
        ),
        assertion(
            f"{case_id}-A02",
            "requested nonzero sides are populated and zero sides stay empty",
            {"top": len(top), "bottom": len(bottom)},
            expected_empty_sides,
        ),
        assertion(f"{case_id}-A03", "Top ranking_value is descending", top_values, top_sorted),
        assertion(f"{case_id}-A04", "Bottom ranking_value is ascending", bottom_values, bottom_sorted),
        assertion(
            f"{case_id}-A05",
            "no duplicate metric/factor inside or across Top and Bottom",
            {"metric_ids": ids, "factor_refs": refs},
            len(ids) == len(set(ids)) and len(refs) == len(set(refs)),
        ),
        assertion(
            f"{case_id}-A06",
            "ranking_value follows the selected ranking_mode semantics",
            semantic_mismatches,
            not semantic_mismatches,
        ),
        assertion(
            f"{case_id}-A07",
            "every item keeps the exact metric scope identity",
            identity_mismatches,
            not identity_mismatches,
        ),
        assertion(
            f"{case_id}-A08",
            "every raw metric value equals its DB row",
            db_mismatches,
            not db_mismatches,
        ),
        assertion(
            f"{case_id}-A09",
            "returned_count equals the materialized unique rows",
            {"reported": payload.get("returned_count"), "actual": len(combined)},
            payload.get("returned_count") == len(combined),
        ),
        assertion(
            f"{case_id}-A10",
            "validity is not evaluated by factor_rank",
            {
                "validity_evaluated": payload.get("validity_evaluated"),
                "warning": (call["business"].get("meta") or {}).get("warnings"),
            },
            payload.get("validity_evaluated") is False,
        ),
    ]
    return checks


def scope_sql_predicate(scope: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """Return a parameterized predicate and parameters for one exact scope."""

    predicate = """m.is_sub_factor_id=1 AND m.ic_scope=%s AND m.calculation_mode=%s
        AND m.factor_bar_interval=%s AND m.factor_window_bars=%s
        AND m.return_bar_interval=%s AND m.forward_return_bars=%s
        AND m.universe_key=%s AND m.symbol=%s AND m.window_scope=%s
        AND m.scoring_version=%s"""
    params = (
        scope["ic_scope"],
        scope["calculation_mode"],
        scope["factor_bar_interval"],
        scope["factor_window_bars"],
        scope["return_bar_interval"],
        scope["forward_return_bars"],
        scope["universe_key"],
        scope.get("symbol") or "",
        scope["window_scope"],
        scope["scoring_version"],
    )
    return predicate, params


def raw_db_oracle(cursor: Any, scope: dict[str, Any], metric: str, k: int) -> dict[str, Any]:
    """Calculate DB Top/Bottom raw values for a selected exact scope."""

    predicate, params = scope_sql_predicate(scope)
    cursor.execute(
        f"""SELECT m.id, m.factor_id, m.`{metric}` metric_value
            FROM factor_ic_summary_metrics m
            JOIN factor_ic_runs r ON r.run_id=m.run_id
            WHERE {predicate} AND m.`{metric}` IS NOT NULL
              AND r.status='completed' AND r.completed_at <= %s""",
        params + (AS_OF.replace(tzinfo=None),),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    values = sorted(Decimal(str(row["metric_value"])) for row in rows)
    return {
        "count": len(rows),
        "top_values": [str(value) for value in list(reversed(values))[:k]],
        "bottom_values": [str(value) for value in values[:k]],
    }


def find_theme(cursor: Any, scope: dict[str, Any]) -> dict[str, Any]:
    """Find a theme with enough candidates in the selected sub-factor scope."""

    predicate, params = scope_sql_predicate(scope)
    cursor.execute(
        f"""SELECT t.theme_key, COUNT(DISTINCT m.factor_id) candidate_count
            FROM factor_ic_summary_metrics m
            JOIN factor_ic_runs r ON r.run_id=m.run_id
            JOIN factor_sub_factor_relations fsr ON fsr.sub_factor_id=m.factor_id
            JOIN factor_theme_relations ftr ON ftr.factor_id=fsr.factor_id
            JOIN themes t ON t.id=ftr.theme_id
            WHERE {predicate} AND r.status='completed' AND r.completed_at <= %s
            GROUP BY t.id, t.theme_key
            HAVING candidate_count >= 10
            ORDER BY candidate_count DESC, t.theme_key
            LIMIT 1""",
        params + (AS_OF.replace(tzinfo=None),),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("No theme has enough candidates for a disjoint Top/Bottom test")
    return dict(row)


def factors_match_theme(cursor: Any, factor_ids: list[int], theme: str) -> tuple[bool, list[int]]:
    """Check that returned sub-factors inherit the requested parent theme."""

    if not factor_ids:
        return True, []
    placeholders = ",".join(["%s"] * len(factor_ids))
    cursor.execute(
        f"""SELECT DISTINCT fsr.sub_factor_id
            FROM factor_sub_factor_relations fsr
            JOIN factor_theme_relations ftr ON ftr.factor_id=fsr.factor_id
            JOIN themes t ON t.id=ftr.theme_id
            WHERE fsr.sub_factor_id IN ({placeholders}) AND t.theme_key=%s""",
        tuple(factor_ids) + (theme,),
    )
    matched = {int(row["sub_factor_id"]) for row in cursor.fetchall()}
    missing = [factor_id for factor_id in factor_ids if factor_id not in matched]
    return not missing, missing


def count_pair(call: dict[str, Any]) -> tuple[int, int]:
    """Return candidate and evaluated counts with null normalized to zero."""

    payload = data(call)
    return int(payload.get("candidate_count") or 0), int(payload.get("evaluated_count") or 0)


def exact_run_candidate(cursor: Any, ts_scope: dict[str, Any]) -> dict[str, Any]:
    """Find one exact run with metric, validity, formula, and slice evidence."""

    predicate, params = scope_sql_predicate(ts_scope)
    cursor.execute(
        f"""SELECT m.*, r.completed_at,
                   v.id validity_id, v.created_at validity_created_at,
                   v.time_series_scoring_version,
                   f.id formula_id, f.formula_hash, f.recorded_at formula_recorded_at
            FROM factor_ic_summary_metrics m
            JOIN factor_ic_runs r ON r.run_id=m.run_id
            JOIN factor_validity_status v
              ON v.run_id=m.run_id AND v.factor_id=m.factor_id AND v.is_sub_factor_id=1
            JOIN factor_ic_run_formula_evidence f
              ON f.run_id=m.run_id AND f.factor_id=m.factor_id AND f.is_sub_factor_id=1
            WHERE {predicate}
              AND r.status='completed' AND r.completed_at <= %s
              AND EXISTS (
                SELECT 1 FROM factor_ic_slice_metrics s
                WHERE s.run_id=m.run_id AND s.factor_id=m.factor_id
                  AND s.is_sub_factor_id=1 AND s.ic_scope=m.ic_scope
                  AND s.symbol=m.symbol
              )
            ORDER BY r.completed_at DESC
            LIMIT 1""",
        params + (AS_OF.replace(tzinfo=None),),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("No exact run has metric, validity, formula, and slice evidence")
    return dict(row)


def run_rank_matrix(cursor: Any, ts_scope: dict[str, Any], cs_scope: dict[str, Any]) -> None:
    """Execute ranking modes, metrics, shape variants, themes, and filters."""

    requests_to_run: list[tuple[str, dict[str, Any], bool]] = [
        ("RANK-TS-SIGNED", rank_args(ts_scope, "mean_ic", "signed", 5, 5), True),
        ("RANK-TS-RAW-TOP", rank_args(ts_scope, "mean_ic", "raw_signed", 5, 0), True),
        ("RANK-TS-ABS-BOTTOM", rank_args(ts_scope, "mean_ic", "absolute_diagnostic", 0, 5), True),
        ("RANK-CS-SIGNED", rank_args(cs_scope, "mean_ic", "signed", 5, 5), True),
        ("RANK-CS-RAW", rank_args(cs_scope, "mean_ic", "raw_signed", 5, 5), True),
        ("RANK-CS-ABS", rank_args(cs_scope, "mean_ic", "absolute_diagnostic", 3, 3), True),
        ("RANK-CS-ZERO", rank_args(cs_scope, "mean_ic", "signed", 0, 0), False),
    ]
    for scope_label, scope in (("TS", ts_scope), ("CS", cs_scope)):
        for metric in METRICS:
            if metric == "mean_ic":
                continue
            requests_to_run.append(
                (
                    f"RANK-{scope_label}-METRIC-{metric.upper()}",
                    rank_args(scope, metric, "raw_signed", 3, 3),
                    True,
                )
            )
    for case_id, args, require_nonempty in requests_to_run:
        call = mcp(case_id, "factor_rank", args)
        checks = rank_checks(case_id, call, args, cursor, require_nonempty=require_nonempty)
        if case_id == "RANK-CS-ZERO":
            top, bottom = rank_rows(call)
            checks.append(
                assertion(
                    "RANK-CS-ZERO-A11",
                    "top_k=bottom_k=0 is a successful legal empty result",
                    {"top": top, "bottom": bottom, "returned_count": data(call).get("returned_count")},
                    call["is_error"] is False
                    and not top
                    and not bottom
                    and data(call).get("returned_count") == 0,
                )
            )
        record(case_id, "rank.matrix", call, checks)

    repeat_args = CALLS["RANK-TS-SIGNED"]["arguments"]
    repeat_call = mcp("RANK-TS-SIGNED-REPEAT", "factor_rank", repeat_args)
    first_order = [
        (row.get("metric_id"), row.get("ranking_value"))
        for row in sum(rank_rows(CALLS["RANK-TS-SIGNED"]), [])
    ]
    repeat_order = [
        (row.get("metric_id"), row.get("ranking_value")) for row in sum(rank_rows(repeat_call), [])
    ]
    record(
        "RANK-TS-SIGNED-REPEAT",
        "rank.stability",
        repeat_call,
        [
            assertion(
                "RANK-REPEAT-A01",
                "an identical point-in-time request returns identical ordered ids and values",
                {"first": first_order, "repeat": repeat_order},
                first_order == repeat_order,
            )
        ],
    )

    raw_call = CALLS["RANK-CS-RAW"]
    oracle = raw_db_oracle(cursor, cs_scope, "mean_ic", 5)
    raw_top, raw_bottom = rank_rows(raw_call)
    api_top = [str(decimal_value(row.get("ranking_value"))) for row in raw_top]
    api_bottom = [str(decimal_value(row.get("ranking_value"))) for row in raw_bottom]
    record(
        "RANK-CS-RAW-DB-ORDER",
        "rank.db_oracle",
        None,
        [
            assertion(
                "RANK-DB-A01",
                "raw_signed candidate count equals the exact DB pool",
                {"api": count_pair(raw_call), "db": oracle["count"]},
                count_pair(raw_call)[0] == oracle["count"],
            ),
            assertion(
                "RANK-DB-A02",
                "raw_signed Top values equal DB descending extrema",
                {"api": api_top, "db": oracle["top_values"]},
                api_top == oracle["top_values"],
            ),
            assertion(
                "RANK-DB-A03",
                "raw_signed Bottom values equal DB ascending extrema",
                {"api": api_bottom, "db": oracle["bottom_values"]},
                api_bottom == oracle["bottom_values"],
            ),
        ],
        db_evidence=oracle,
    )

    theme = find_theme(cursor, cs_scope)
    theme_args = rank_args(cs_scope, "mean_ic", "raw_signed", 5, 5, theme=theme["theme_key"])
    theme_call = mcp("RANK-THEME-HIT", "factor_rank", theme_args)
    theme_checks = rank_checks("RANK-THEME-HIT", theme_call, theme_args, cursor)
    theme_ids = [int(row["factor_id"]) for row in sum(rank_rows(theme_call), [])]
    theme_ok, missing_theme = factors_match_theme(cursor, theme_ids, theme["theme_key"])
    theme_checks.append(
        assertion(
            "RANK-THEME-HIT-A11",
            "every returned sub-factor inherits the requested parent theme",
            {"theme": theme["theme_key"], "missing_factor_ids": missing_theme},
            theme_ok and bool(theme_ids),
        )
    )
    theme_checks.append(
        assertion(
            "RANK-THEME-HIT-A12",
            "theme filtering does not increase candidate/evaluated counts",
            {"baseline": count_pair(raw_call), "filtered": count_pair(theme_call)},
            all(left <= right for left, right in zip(count_pair(theme_call), count_pair(raw_call))),
        )
    )
    record("RANK-THEME-HIT", "rank.theme", theme_call, theme_checks, db_evidence=theme)

    missing_theme = f"qa_missing_{RUN_ID.replace('-', '')}"
    missing_args = rank_args(cs_scope, "mean_ic", "raw_signed", 5, 5, theme=missing_theme)
    missing_call = mcp("RANK-THEME-MISS", "factor_rank", missing_args)
    missing_top, missing_bottom = rank_rows(missing_call)
    record(
        "RANK-THEME-MISS",
        "rank.theme",
        missing_call,
        [
            assertion(
                "RANK-THEME-MISS-A01",
                "an unmatched well-formed theme returns a successful empty set",
                {
                    "is_error": missing_call["is_error"],
                    "top": len(missing_top),
                    "bottom": len(missing_bottom),
                    "counts": count_pair(missing_call),
                },
                missing_call["is_error"] is False and not missing_top and not missing_bottom,
            )
        ],
    )

    max_slices = int(cs_scope["max_slices"])
    slice_equal_args = rank_args(
        cs_scope,
        "mean_ic",
        "raw_signed",
        5,
        5,
        min_valid_slice_count=max_slices,
    )
    slice_equal = mcp("RANK-FILTER-SLICES-EQUAL", "factor_rank", slice_equal_args)
    slice_equal_rows = sum(rank_rows(slice_equal), [])
    record(
        "RANK-FILTER-SLICES-EQUAL",
        "rank.filters",
        slice_equal,
        rank_checks("RANK-FILTER-SLICES-EQUAL", slice_equal, slice_equal_args, cursor)
        + [
            assertion(
                "RANK-SLICES-EQUAL-A11",
                "minimum slice threshold is inclusive and strict for all returned rows",
                [row.get("valid_slice_count") for row in slice_equal_rows],
                bool(slice_equal_rows)
                and all(int(row.get("valid_slice_count") or -1) >= max_slices for row in slice_equal_rows),
            ),
            assertion(
                "RANK-SLICES-EQUAL-A12",
                "slice threshold does not increase candidate/evaluated counts",
                {"baseline": count_pair(raw_call), "filtered": count_pair(slice_equal)},
                all(left <= right for left, right in zip(count_pair(slice_equal), count_pair(raw_call))),
            ),
        ],
    )

    slice_above_args = rank_args(
        cs_scope,
        "mean_ic",
        "raw_signed",
        5,
        5,
        min_valid_slice_count=max_slices + 1,
    )
    slice_above = mcp("RANK-FILTER-SLICES-ABOVE", "factor_rank", slice_above_args)
    above_top, above_bottom = rank_rows(slice_above)
    record(
        "RANK-FILTER-SLICES-ABOVE",
        "rank.filters",
        slice_above,
        [
            assertion(
                "RANK-SLICES-ABOVE-A01",
                "threshold above observed maximum gives a successful empty result",
                {
                    "threshold": max_slices + 1,
                    "is_error": slice_above["is_error"],
                    "top": len(above_top),
                    "bottom": len(above_bottom),
                    "counts": count_pair(slice_above),
                },
                slice_above["is_error"] is False and not above_top and not above_bottom,
            )
        ],
    )

    cursor.execute(
        """SELECT coverage_mean FROM factor_ic_summary_metrics
           WHERE is_sub_factor_id=1 AND ic_scope=%s AND calculation_mode=%s
             AND factor_bar_interval=%s AND factor_window_bars=%s
             AND return_bar_interval=%s AND forward_return_bars=%s
             AND universe_key=%s AND symbol=%s AND window_scope=%s
             AND scoring_version=%s AND coverage_mean IS NOT NULL
           ORDER BY coverage_mean""",
        scope_sql_predicate(cs_scope)[1],
    )
    coverage_values = [Decimal(str(row["coverage_mean"])) for row in cursor.fetchall()]
    coverage_threshold = coverage_values[len(coverage_values) // 2]
    coverage_args = rank_args(
        cs_scope,
        "mean_ic",
        "raw_signed",
        5,
        5,
        min_coverage_mean=float(coverage_threshold),
    )
    coverage_call = mcp("RANK-FILTER-COVERAGE", "factor_rank", coverage_args)
    coverage_rows = sum(rank_rows(coverage_call), [])
    record(
        "RANK-FILTER-COVERAGE",
        "rank.filters",
        coverage_call,
        rank_checks("RANK-FILTER-COVERAGE", coverage_call, coverage_args, cursor)
        + [
            assertion(
                "RANK-COVERAGE-A11",
                "all returned rows meet the inclusive coverage threshold",
                [row.get("coverage_mean") for row in coverage_rows],
                bool(coverage_rows)
                and all(
                    decimal_value(row.get("coverage_mean")) is not None
                    and decimal_value(row.get("coverage_mean")) >= coverage_threshold
                    for row in coverage_rows
                ),
            ),
            assertion(
                "RANK-COVERAGE-A12",
                "coverage threshold does not increase candidate/evaluated counts",
                {"baseline": count_pair(raw_call), "filtered": count_pair(coverage_call)},
                all(left <= right for left, right in zip(count_pair(coverage_call), count_pair(raw_call))),
            ),
        ],
        db_evidence={"threshold": coverage_threshold},
    )

    coverage_one_args = rank_args(
        cs_scope,
        "mean_ic",
        "raw_signed",
        5,
        5,
        min_coverage_mean=1,
    )
    coverage_one = mcp("RANK-FILTER-COVERAGE-ONE", "factor_rank", coverage_one_args)
    coverage_one_rows = sum(rank_rows(coverage_one), [])
    record(
        "RANK-FILTER-COVERAGE-ONE",
        "rank.filters",
        coverage_one,
        [
            assertion(
                "RANK-COVERAGE-ONE-A01",
                "maximum legal threshold returns only full-coverage rows or a legal empty result",
                {
                    "is_error": coverage_one["is_error"],
                    "values": [row.get("coverage_mean") for row in coverage_one_rows],
                    "counts": count_pair(coverage_one),
                },
                coverage_one["is_error"] is False
                and all(decimal_value(row.get("coverage_mean")) == Decimal("1") for row in coverage_one_rows),
            )
        ],
    )

    oos_args = rank_args(cs_scope, "mean_ic", "raw_signed", 5, 5, require_oos=True)
    oos_call = mcp("RANK-FILTER-OOS", "factor_rank", oos_args)
    oos_rows = sum(rank_rows(oos_call), [])
    record(
        "RANK-FILTER-OOS",
        "rank.filters",
        oos_call,
        rank_checks("RANK-FILTER-OOS", oos_call, oos_args, cursor)
        + [
            assertion(
                "RANK-OOS-A11",
                "every returned row has an OOS period and OOS IC evidence",
                [
                    {
                        "metric_id": row.get("metric_id"),
                        "oos_period_start": row.get("oos_period_start"),
                        "oos_period_end": row.get("oos_period_end"),
                        "oos_icir": row.get("oos_icir"),
                        "rank_oos_icir": row.get("rank_oos_icir"),
                    }
                    for row in oos_rows
                ],
                bool(oos_rows)
                and all(
                    row.get("oos_period_start")
                    and row.get("oos_period_end")
                    and (row.get("oos_icir") is not None or row.get("rank_oos_icir") is not None)
                    for row in oos_rows
                ),
            ),
            assertion(
                "RANK-OOS-A12",
                "requiring OOS does not increase candidate/evaluated counts",
                {"baseline": count_pair(raw_call), "filtered": count_pair(oos_call)},
                all(left <= right for left, right in zip(count_pair(oos_call), count_pair(raw_call))),
            ),
        ],
    )


def target_visible(call: dict[str, Any], target_run_id: str, container: str) -> bool:
    """Return whether a target run appears in a known response item container."""

    payload = data(call)
    if container == "formula":
        return payload.get("run_id") == target_run_id
    if container == "validity":
        item = payload.get("item") or {}
        return item.get("run_id") == target_run_id
    rows = payload.get(container) or []
    return any(row.get("run_id") == target_run_id for row in rows if isinstance(row, dict))


def run_point_in_time(cursor: Any, ts_scope: dict[str, Any]) -> None:
    """Verify explicit-run as_of visibility for metrics, validity, slices, and formula."""

    target = exact_run_candidate(cursor, ts_scope)
    completed_at = target["completed_at"].replace(tzinfo=LOCAL_TZ)
    before_completed = completed_at - timedelta(microseconds=1)
    factor_ref = f"sub_factor:{target['factor_id']}"
    metric_args = {
        "factor_ref": factor_ref,
        "ic_scope": target["ic_scope"],
        "calculation_mode": target["calculation_mode"],
        "universe_key": target["universe_key"],
        "window_scope": target["window_scope"],
        "interval": target["factor_bar_interval"],
        "factor_window_bars": target["factor_window_bars"],
        "return_bar_interval": target["return_bar_interval"],
        "forward_return_bars": target["forward_return_bars"],
        "as_of": before_completed.isoformat(),
        "scoring_version": target["scoring_version"],
        "symbol": target.get("symbol") or "",
        "run_id": target["run_id"],
    }
    metric_before = mcp("PIT-METRIC-RUN-BEFORE", "factor_get_metrics", metric_args)
    record(
        "PIT-METRIC-RUN-BEFORE",
        "point_in_time.explicit_run",
        metric_before,
        [
            assertion(
                "PIT-METRIC-BEFORE-A01",
                "explicit run_id does not expose a run before completed_at",
                {"error": error_code(metric_before), "data": data(metric_before)},
                not target_visible(metric_before, target["run_id"], "ic_summaries"),
            )
        ],
        db_evidence={"run_id": target["run_id"], "completed_at": target["completed_at"]},
    )
    metric_after_args = dict(metric_args)
    metric_after_args["as_of"] = AS_OF.isoformat()
    metric_after = mcp("PIT-METRIC-RUN-AFTER", "factor_get_metrics", metric_after_args)
    record(
        "PIT-METRIC-RUN-AFTER",
        "point_in_time.explicit_run",
        metric_after,
        [
            assertion(
                "PIT-METRIC-AFTER-A01",
                "the same explicit run is visible after completion",
                {"error": error_code(metric_after), "run_id": target["run_id"]},
                target_visible(metric_after, target["run_id"], "ic_summaries"),
            )
        ],
    )

    validity_args = {
        "factor_ref": factor_ref,
        "validity_scope": "time_series",
        "calculation_mode": target["calculation_mode"],
        "universe_key": target["universe_key"],
        "window_scope": target["window_scope"],
        "interval": target["factor_bar_interval"],
        "factor_window_bars": target["factor_window_bars"],
        "return_bar_interval": target["return_bar_interval"],
        "forward_return_bars": target["forward_return_bars"],
        "as_of": before_completed.isoformat(),
        "scoring_version": target["time_series_scoring_version"],
        "symbol": "",
        "run_id": target["run_id"],
    }
    validity_before = mcp("PIT-VALIDITY-RUN-BEFORE", "factor_get_validity", validity_args)
    record(
        "PIT-VALIDITY-RUN-BEFORE",
        "point_in_time.explicit_run",
        validity_before,
        [
            assertion(
                "PIT-VALIDITY-BEFORE-A01",
                "explicit run_id does not expose validity before run completion",
                {"error": error_code(validity_before), "data": data(validity_before)},
                not target_visible(validity_before, target["run_id"], "validity"),
            )
        ],
        db_evidence={
            "validity_id": target["validity_id"],
            "validity_created_at": target["validity_created_at"],
            "run_completed_at": target["completed_at"],
        },
    )
    validity_after_args = dict(validity_args)
    validity_after_args["as_of"] = AS_OF.isoformat()
    validity_after = mcp("PIT-VALIDITY-RUN-AFTER", "factor_get_validity", validity_after_args)
    record(
        "PIT-VALIDITY-RUN-AFTER",
        "point_in_time.explicit_run",
        validity_after,
        [
            assertion(
                "PIT-VALIDITY-AFTER-A01",
                "the same explicit validity run is visible after completion",
                {"error": error_code(validity_after), "run_id": target["run_id"]},
                target_visible(validity_after, target["run_id"], "validity"),
            )
        ],
    )

    slice_args = {
        "factor_ref": factor_ref,
        "ic_scope": target["ic_scope"],
        "calculation_mode": target["calculation_mode"],
        "universe_key": target["universe_key"],
        "interval": target["factor_bar_interval"],
        "factor_window_bars": target["factor_window_bars"],
        "return_bar_interval": target["return_bar_interval"],
        "forward_return_bars": target["forward_return_bars"],
        "window_scope": target["window_scope"],
        "as_of": before_completed.isoformat(),
        "scoring_version": target["scoring_version"],
        "start_time": target["period_start"].replace(tzinfo=timezone.utc).isoformat(),
        "end_time": target["period_end"].replace(tzinfo=timezone.utc).isoformat(),
        "symbol": target.get("symbol") or "",
        "run_id": target["run_id"],
        "limit": 5,
    }
    slices_before = mcp("PIT-SLICES-RUN-BEFORE", "factor_get_metric_slices", slice_args)
    record(
        "PIT-SLICES-RUN-BEFORE",
        "point_in_time.explicit_run",
        slices_before,
        [
            assertion(
                "PIT-SLICES-BEFORE-A01",
                "explicit run_id does not expose slices before run completion",
                {"error": error_code(slices_before), "items": data(slices_before).get("items")},
                not target_visible(slices_before, target["run_id"], "items"),
            )
        ],
        db_evidence={"run_id": target["run_id"], "completed_at": target["completed_at"]},
    )
    slices_after_args = dict(slice_args)
    slices_after_args["as_of"] = AS_OF.isoformat()
    slices_after = mcp("PIT-SLICES-RUN-AFTER", "factor_get_metric_slices", slices_after_args)
    record(
        "PIT-SLICES-RUN-AFTER",
        "point_in_time.explicit_run",
        slices_after,
        [
            assertion(
                "PIT-SLICES-AFTER-A01",
                "the same explicit run has slices after completion",
                {"error": error_code(slices_after), "count": len(data(slices_after).get("items") or [])},
                target_visible(slices_after, target["run_id"], "items"),
            )
        ],
    )

    recorded_at = target["formula_recorded_at"].replace(tzinfo=LOCAL_TZ)
    formula_base = {
        "factor_ref": factor_ref,
        "run_id": target["run_id"],
        "calculation_mode": target["calculation_mode"],
        "interval": target["factor_bar_interval"],
        "factor_window_bars": target["factor_window_bars"],
        "return_bar_interval": target["return_bar_interval"],
        "forward_return_bars": target["forward_return_bars"],
    }
    formula_times = (
        ("BEFORE", recorded_at - timedelta(microseconds=1), False),
        ("EQUAL", recorded_at, True),
        ("AFTER", recorded_at + timedelta(microseconds=1), True),
    )
    for label, as_of, should_exist in formula_times:
        args = {**formula_base, "as_of": as_of.isoformat()}
        call = mcp(f"PIT-FORMULA-{label}", "factor_get_formula", args)
        visible = target_visible(call, target["run_id"], "formula")
        hash_matches = data(call).get("formula_hash") == target["formula_hash"] if visible else False
        record(
            f"PIT-FORMULA-{label}",
            "point_in_time.formula",
            call,
            [
                assertion(
                    f"PIT-FORMULA-{label}-A01",
                    "formula evidence is hidden before recorded_at and visible inclusively at/after it",
                    {
                        "as_of": as_of,
                        "recorded_at": recorded_at,
                        "visible": visible,
                        "hash_matches": hash_matches,
                        "error": error_code(call),
                    },
                    (not visible) if not should_exist else (visible and hash_matches),
                )
            ],
            db_evidence={
                "formula_id": target["formula_id"],
                "formula_hash": target["formula_hash"],
                "recorded_at": target["formula_recorded_at"],
            },
        )


def config_scope_observation(cursor: Any) -> dict[str, Any]:
    """Explain whether one CS summary belongs to a run producing both scopes."""

    cursor.execute(
        """SELECT m.id, m.run_id, m.ic_scope,
                  JSON_UNQUOTE(JSON_EXTRACT(r.config_json,'$.ic_scope')) run_config_ic_scope,
                  COUNT(DISTINCT siblings.ic_scope) sibling_scope_count,
                  GROUP_CONCAT(DISTINCT siblings.ic_scope ORDER BY siblings.ic_scope) produced_scopes
           FROM factor_ic_summary_metrics m
           JOIN factor_ic_runs r ON r.run_id=m.run_id
           JOIN factor_ic_summary_metrics siblings ON siblings.run_id=m.run_id
           WHERE m.ic_scope='cross_sectional'
             AND JSON_UNQUOTE(JSON_EXTRACT(r.config_json,'$.ic_scope'))='time_series'
           GROUP BY m.id, m.run_id, m.ic_scope, run_config_ic_scope
           HAVING sibling_scope_count >= 2
           ORDER BY m.updated_at DESC LIMIT 1"""
    )
    row = cursor.fetchone()
    return dict(row) if row else {}


def main() -> None:
    """Execute the bounded rank expansion and exact-run temporal checks."""

    OUT.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load((ROOT / "config" / "test.yaml").read_text(encoding="utf-8"))
    db_config = config["database"]
    connection = db_connect(db_config)
    scope_pair: dict[str, Any] = {}
    observation: dict[str, Any] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                "SELECT DATABASE() database_name, CURRENT_USER() db_user, @@hostname hostname, NOW(6) snapshot_at"
            )
            identity = dict(cursor.fetchone())
            dump(
                OUT / "db-identity.json",
                {
                    "database_name": identity["database_name"],
                    "current_user": identity["db_user"],
                    "host_sha256": hashlib.sha256(str(identity["hostname"]).encode()).hexdigest(),
                    "snapshot_at": identity["snapshot_at"],
                    "read_only_transaction": True,
                },
            )
            ts_scope, cs_scope = discover_scope_pair(cursor)
            scope_pair = {"time_series": ts_scope, "cross_sectional": cs_scope}
            dump(OUT / "selected-scopes.json", scope_pair)
            run_rank_matrix(cursor, ts_scope, cs_scope)
            run_point_in_time(cursor, ts_scope)
            observation = config_scope_observation(cursor)
            dump(OUT / "config-scope-observation.json", observation)
            connection.rollback()
    finally:
        connection.close()

    counts: dict[str, int] = {}
    for case in CASES:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    summary = {
        "run_id": RUN_ID,
        "environment": "test",
        "mcp_host": "test-factor-frontend.questvector.ai",
        "database": db_config["name"],
        "mode": "READ_ONLY",
        "started_as_of": AS_OF.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "request_count": len(CALLS),
        "case_count": len(CASES),
        "status_counts": counts,
        "failed_cases": [case["case_id"] for case in CASES if case["status"] == "FAIL"],
        "blocked_cases": [case["case_id"] for case in CASES if case["status"] == "BLOCKED"],
        "selected_scopes": scope_pair,
        "config_scope_observation": observation,
        "cases": CASES,
        "sensitive_values_written": False,
    }
    dump(OUT / "results.json", summary)
    dump(
        OUT / "manifest.json",
        {
            key: summary[key]
            for key in (
                "run_id",
                "environment",
                "mcp_host",
                "database",
                "mode",
                "started_as_of",
                "finished_at",
                "request_count",
                "case_count",
                "status_counts",
                "failed_cases",
                "blocked_cases",
                "sensitive_values_written",
            )
        },
    )
    print(
        json.dumps(
            {
                "artifact_dir": str(OUT),
                "request_count": len(CALLS),
                "case_count": len(CASES),
                "status_counts": counts,
                "failed_cases": summary["failed_cases"],
                "blocked_cases": summary["blocked_cases"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
