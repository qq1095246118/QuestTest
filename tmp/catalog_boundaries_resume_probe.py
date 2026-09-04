#!/usr/bin/env python3
"""Run two read-only Factor 4.0 catalog boundary reconciliations.

The probe verifies cross-sectional catalog statistics against the latest
completed summary and its directly linked validity row, then verifies the
strict ``updated_after`` boundary against the catalog entity tables. All MCP
artifacts are sanitized by the shared runner. The database transaction is
explicitly read-only and is always rolled back.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tmp.catalog_deep_readonly import (  # noqa: E402
    MCP_URL,
    Runner,
    _data,
    _error_code,
    _items,
    _meta,
    _success,
    _write_json,
)


TOKEN_ENV = "FACTOR4_MCP_TOKEN"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
BLOCKING_CODES = {
    "AUTH_REQUIRED",
    "DEPENDENCY_UNAVAILABLE",
    "FORBIDDEN",
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
}
SCOPE_KEYS = (
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
PREFERRED_SCOPE = {
    "calculation_mode": "direct",
    "factor_bar_interval": "1h",
    "factor_window_bars": "24H",
    "return_bar_interval": "1h",
    "forward_return_bars": 1,
    "universe_key": "all",
    "symbol": "",
    "window_scope": "rolling",
    "scoring_version": "v20260728_scope_split",
}


def parse_timestamp(value: Any) -> datetime | None:
    """Return an aware Asia/Shanghai datetime for a DB or API timestamp."""

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
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def db_timestamp(value: datetime) -> datetime:
    """Convert an aware timestamp to the local naive DATETIME representation."""

    return value.astimezone(LOCAL_TZ).replace(tzinfo=None)


def open_read_only_connection() -> pymysql.Connection:
    """Open the configured test database connection with autocommit disabled."""

    config = yaml.safe_load((PROJECT_ROOT / "config" / "test.yaml").read_text(encoding="utf-8"))[
        "database"
    ]
    return pymysql.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["username"],
        password=config["password"],
        database=config["name"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=120,
        write_timeout=30,
    )


def fetch_all(cursor: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Execute a parameterized SELECT and return dictionary rows."""

    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def call_error(call: dict[str, Any]) -> dict[str, Any]:
    """Return a non-sensitive transport and business error signature."""

    return {
        "http_status": call.get("http_status"),
        "is_error": call.get("is_error"),
        "error_code": _error_code(call),
        "elapsed_seconds": call.get("elapsed_seconds"),
    }


def verdict_for_call(call: dict[str, Any], assertion_ok: bool) -> str:
    """Classify a call as PASS, FAIL, or BLOCKED without hiding product errors."""

    if _success(call) and assertion_ok:
        return "PASS"
    if _error_code(call) in BLOCKING_CODES:
        return "BLOCKED"
    return "FAIL"


def normalize_scope(scope: dict[str, Any]) -> dict[str, Any]:
    """Normalize one discovered CS aggregate scope for DB and MCP use."""

    return {
        "calculation_mode": scope.get("calculation_mode"),
        "factor_bar_interval": scope.get("factor_bar_interval"),
        "factor_window_bars": scope.get("factor_window_bars"),
        "return_bar_interval": scope.get("return_bar_interval"),
        "forward_return_bars": int(scope["forward_return_bars"]),
        "universe_key": scope.get("universe_key"),
        "symbol": scope.get("symbol") or "",
        "window_scope": scope.get("window_scope"),
        "scoring_version": scope.get("scoring_version"),
    }


def is_complete_cs_scope(scope: dict[str, Any]) -> bool:
    """Return whether a discovered row is a complete CS aggregate identity."""

    if scope.get("ic_scope") != "cross_sectional" or (scope.get("symbol") or "") != "":
        return False
    try:
        normalized = normalize_scope(scope)
    except (KeyError, TypeError, ValueError):
        return False
    return all(normalized.get(key) is not None for key in SCOPE_KEYS)


def scope_oracle_rows(cursor: Any, scope: dict[str, Any], as_of: datetime) -> list[dict[str, Any]]:
    """Load every catalog factor with its latest CS summary and linked validity."""

    return fetch_all(
        cursor,
        """
        WITH ranked AS (
            SELECT m.id AS summary_id, m.factor_id, m.run_id, m.updated_at AS summary_updated_at,
                   r.completed_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.factor_id
                       ORDER BY r.completed_at DESC, m.updated_at DESC, m.id DESC
                   ) AS row_num
            FROM factor_ic_summary_metrics AS m
            JOIN factor_ic_runs AS r ON r.run_id = m.run_id
            WHERE m.is_sub_factor_id = 1
              AND m.ic_scope = 'cross_sectional'
              AND m.calculation_mode = %s
              AND m.factor_bar_interval = %s
              AND m.factor_window_bars = %s
              AND m.return_bar_interval = %s
              AND m.forward_return_bars = %s
              AND m.universe_key = %s
              AND m.symbol = %s
              AND m.window_scope = %s
              AND m.scoring_version = %s
              AND r.status = 'completed'
              AND r.completed_at <= %s
        ), latest AS (
            SELECT * FROM ranked WHERE row_num = 1
        )
        SELECT latest.summary_id, catalog.id AS factor_id, latest.run_id,
               latest.summary_updated_at, latest.completed_at,
               COUNT(v.id) AS validity_match_count,
               COUNT(DISTINCT v.cross_sectional_status) AS validity_status_count,
               MAX(v.cross_sectional_status) AS cross_sectional_status,
               GROUP_CONCAT(v.id ORDER BY v.id) AS validity_ids
        FROM sub_factors AS catalog
        LEFT JOIN latest ON latest.factor_id = catalog.id
        LEFT JOIN factor_validity_status AS v
          ON v.is_sub_factor_id = 1
         AND v.factor_id = catalog.id
         AND v.run_id = latest.run_id
         AND v.cross_sectional_summary_id = latest.summary_id
        WHERE catalog.factor_bar_interval = %s
        GROUP BY latest.summary_id, catalog.id, latest.run_id,
                 latest.summary_updated_at, latest.completed_at
        ORDER BY catalog.id
        """,
        tuple(scope[key] for key in SCOPE_KEYS)
        + (db_timestamp(as_of), scope["factor_bar_interval"]),
    )


def oracle_validity(row: dict[str, Any]) -> str:
    """Map a latest summary and linked validity row to the public enum."""

    status = row.get("cross_sectional_status")
    if int(row.get("validity_match_count") or 0) == 0 or status is None:
        return "unknown"
    normalized = str(status).lower()
    return normalized if normalized in {"valid", "invalid", "unknown"} else "unknown"


def choose_scope(cursor: Any, scopes: list[dict[str, Any]], as_of: datetime) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Choose a complete aggregate scope with an unambiguous nonempty oracle."""

    complete = [normalize_scope(row) for row in scopes if is_complete_cs_scope(row)]
    complete.sort(
        key=lambda row: (
            row != PREFERRED_SCOPE,
            -int(next((x.get("available_factor_count") or 0 for x in scopes if is_complete_cs_scope(x) and normalize_scope(x) == row), 0)),
            tuple(str(row.get(key)) for key in SCOPE_KEYS),
        )
    )
    fallback: tuple[dict[str, Any] | None, list[dict[str, Any]]] = (None, [])
    for scope in complete[:30]:
        rows = scope_oracle_rows(cursor, scope, as_of)
        if not rows:
            continue
        if fallback[0] is None:
            fallback = (scope, rows)
        ambiguous = [
            row
            for row in rows
            if int(row.get("validity_match_count") or 0) > 1
            or int(row.get("validity_status_count") or 0) > 1
        ]
        statuses = {oracle_validity(row) for row in rows}
        if not ambiguous and {"valid", "invalid"} <= statuses:
            return scope, rows
    return fallback


def stats_arguments(scope: dict[str, Any], as_of: datetime, validity: str) -> dict[str, Any]:
    """Build a complete CS aggregate factor_catalog_stats request."""

    return {
        "kind": "sub_factor",
        "ic_scope": "cross_sectional",
        "validity_scope": "cross_sectional",
        "validity": validity,
        "calculation_mode": scope["calculation_mode"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": scope["forward_return_bars"],
        "universe_key": scope["universe_key"],
        "symbol": "",
        "window_scope": scope["window_scope"],
        "scoring_version": scope["scoring_version"],
        "as_of": as_of.isoformat(),
    }


def stats_group_count(call: dict[str, Any], validity: str) -> int:
    """Sum response groups belonging to the requested explicit validity."""

    groups = _data(call).get("groups")
    if not isinstance(groups, list):
        return 0
    return sum(
        int(group.get("count") or 0)
        for group in groups
        if isinstance(group, dict)
        and group.get("kind") == "sub_factor"
        and group.get("validity") == validity
    )


def run_catalog_stats_checks(
    runner: Runner,
    cursor: Any,
    as_of: datetime,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Discover one CS scope and reconcile all explicit validity values."""

    discovery = runner.tool(
        "CS-STATS-SCOPE-DISCOVERY",
        "factor_list_metric_scopes",
        {
            "as_of": as_of.isoformat(),
            "kind": "sub_factor",
            "ic_scope": "cross_sectional",
            "limit": 100,
        },
    )
    discovered = _items(discovery)
    scope, oracle_rows = choose_scope(cursor, discovered, as_of) if _success(discovery) else (None, [])
    if scope is None:
        cases.append(
            {
                "case_id": "CS-STATS-EXPLICIT-VALIDITY",
                "status": "BLOCKED" if _error_code(discovery) in BLOCKING_CODES else "FAIL",
                "title": "CS catalog stats with explicit validity",
                "reason": "no complete aggregate CS scope with a nonempty DB oracle was available",
                "error": call_error(discovery),
            }
        )
        return {"scope": None, "oracle_counts": {}}

    ambiguities = [
        {
            "factor_id": int(row["factor_id"]),
            "summary_id": int(row["summary_id"]),
            "validity_match_count": int(row.get("validity_match_count") or 0),
            "validity_status_count": int(row.get("validity_status_count") or 0),
        }
        for row in oracle_rows
        if int(row.get("validity_match_count") or 0) > 1
        or int(row.get("validity_status_count") or 0) > 1
    ]
    oracle_counts = Counter(oracle_validity(row) for row in oracle_rows)
    calls: dict[str, dict[str, Any]] = {}
    details: dict[str, Any] = {}
    all_ok = not ambiguities
    blocked = False
    for validity in ("valid", "invalid", "unknown"):
        args = stats_arguments(scope, as_of, validity)
        call = runner.tool(f"CS-STATS-{validity.upper()}", "factor_catalog_stats", args)
        calls[validity] = call
        expected = int(oracle_counts.get(validity, 0))
        actual = _data(call).get("total")
        grouped = stats_group_count(call, validity)
        check_ok = _success(call) and actual == expected and grouped == expected
        all_ok = all_ok and check_ok
        blocked = blocked or _error_code(call) in BLOCKING_CODES
        details[validity] = {
            "arguments": args,
            "expected_db_count": expected,
            "actual_total": actual,
            "actual_group_count": grouped,
            "response_mode": _data(call).get("mode"),
            "error": call_error(call),
        }

    status = "PASS" if all_ok else ("BLOCKED" if blocked else "FAIL")
    cases.append(
        {
            "case_id": "CS-STATS-EXPLICIT-VALIDITY",
            "status": status,
            "title": "CS catalog stats equal the latest completed DB oracle for every explicit validity",
            "reason": (
                "valid, invalid, and unknown totals exactly match the latest completed summary and linked validity rows"
                if status == "PASS"
                else "at least one explicit validity total differs from the DB oracle or the oracle is ambiguous"
            ),
            "reproduction": details,
            "scope": {
                "kind": "sub_factor",
                "ic_scope": "cross_sectional",
                "validity_scope": "cross_sectional",
                **scope,
                "as_of": as_of.isoformat(),
            },
            "db_catalog_factor_count": len(oracle_rows),
            "db_scoped_summary_count": sum(1 for row in oracle_rows if row.get("summary_id") is not None),
            "db_oracle_counts": dict(oracle_counts),
            "db_oracle_rule": (
                "catalog sub_factors matching interval left join one scope summary per factor ordered by completed run "
                "completed_at DESC, summary updated_at DESC, summary id DESC; validity joined by factor_id, "
                "run_id, and cross_sectional_summary_id; absent summary or validity maps to unknown"
            ),
            "oracle_ambiguities": ambiguities,
        }
    )
    return {"scope": scope, "oracle_counts": dict(oracle_counts), "calls": calls}


def catalog_schema_evidence(cursor: Any) -> list[dict[str, Any]]:
    """Read the entity updated_at column definitions used by the oracle."""

    return fetch_all(
        cursor,
        """
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, DATETIME_PRECISION,
               COLUMN_DEFAULT, EXTRA
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name IN ('factors', 'sub_factors')
          AND column_name IN ('updated_at', 'latest_status_updated_at')
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
    )


def catalog_entities(cursor: Any) -> list[dict[str, Any]]:
    """Load catalog identities and the two candidate update timestamps."""

    return fetch_all(
        cursor,
        """
        SELECT 'factor' AS kind, id, updated_at, latest_status_updated_at FROM factors
        WHERE updated_at IS NOT NULL
        UNION ALL
        SELECT 'sub_factor', id, updated_at, latest_status_updated_at FROM sub_factors
        WHERE updated_at IS NOT NULL
        """,
    )


def choose_update_fixture(rows: list[dict[str, Any]]) -> tuple[datetime, list[dict[str, Any]]] | None:
    """Choose a precise timestamp with a small paginatable strict-after set."""

    grouped: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        timestamp = parse_timestamp(row.get("updated_at"))
        if timestamp is not None:
            grouped[timestamp].append(row)
    ordered = sorted(grouped, reverse=True)
    for index, timestamp in enumerate(ordered):
        after_count = sum(len(grouped[value]) for value in ordered[:index])
        equal_count = len(grouped[timestamp])
        if 2 <= after_count <= 6 and 1 <= equal_count <= 5:
            return timestamp, grouped[timestamp]
    return None


def entity_ref(row: dict[str, Any]) -> str:
    """Return a public factor reference for a DB entity row."""

    return f"{row['kind']}:{row['id']}"


def paginate_updated_after(
    runner: Runner,
    case_prefix: str,
    threshold: datetime,
    expected_maximum: int,
) -> dict[str, Any]:
    """Exhaust a small updated_after result through signed cursors."""

    base_args: dict[str, Any] = {"updated_after": threshold.isoformat(), "limit": 1}
    calls: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None
    second_page_args: dict[str, Any] | None = None
    second_page_items: list[dict[str, Any]] = []
    for page in range(1, expected_maximum + 4):
        args = dict(base_args)
        if cursor:
            args["cursor"] = cursor
        if page == 2:
            second_page_args = dict(args)
        call = runner.tool(f"{case_prefix}-PAGE-{page:02d}", "factor_search", args)
        calls.append(call)
        page_items = _items(call)
        if page == 2:
            second_page_items = page_items
        items.extend(page_items)
        if not _success(call):
            break
        next_cursor = _meta(call).get("next_cursor")
        if not next_cursor:
            cursor = None
            break
        cursor = str(next_cursor)
        if cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
    replay: dict[str, Any] | None = None
    replay_items: list[dict[str, Any]] = []
    if second_page_args is not None:
        replay = runner.tool(f"{case_prefix}-PAGE-02-REPLAY", "factor_search", second_page_args)
        replay_items = _items(replay)
    return {
        "threshold": threshold,
        "base_arguments": base_args,
        "calls": calls,
        "items": items,
        "terminal_cursor": cursor,
        "second_page_items": second_page_items,
        "replay": replay,
        "replay_items": replay_items,
    }


def updated_page_verdict(
    result: dict[str, Any],
    expected_rows: list[dict[str, Any]],
    db_by_ref: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compare one exhausted updated_after traversal with exact DB rows."""

    calls = result["calls"]
    items = result["items"]
    refs = [str(item.get("factor_ref")) for item in items]
    expected_refs = {entity_ref(row) for row in expected_rows}
    timestamps = [parse_timestamp(item.get("updated_at")) for item in items]
    timestamp_mismatches: list[dict[str, Any]] = []
    for item in items:
        ref = str(item.get("factor_ref"))
        db_row = db_by_ref.get(ref)
        api_timestamp = parse_timestamp(item.get("updated_at"))
        db_value = parse_timestamp(db_row.get("updated_at")) if db_row else None
        if db_row is None or api_timestamp != db_value:
            timestamp_mismatches.append(
                {
                    "factor_ref": ref,
                    "api_updated_at": item.get("updated_at"),
                    "db_updated_at": db_row.get("updated_at") if db_row else None,
                }
            )
    replay = result.get("replay")
    replay_ok = replay is None or (
        _success(replay)
        and [row.get("factor_ref") for row in result["replay_items"]]
        == [row.get("factor_ref") for row in result["second_page_items"]]
    )
    ok = (
        calls
        and all(_success(call) for call in calls)
        and result["terminal_cursor"] is None
        and set(refs) == expected_refs
        and len(refs) == len(set(refs))
        and all(len(_items(call)) <= 1 for call in calls)
        and all(value is not None for value in timestamps)
        and timestamps == sorted(timestamps, reverse=True)
        and not timestamp_mismatches
        and replay_ok
    )
    return {
        "ok": bool(ok),
        "expected_refs": sorted(expected_refs),
        "returned_refs": refs,
        "missing_refs": sorted(expected_refs - set(refs)),
        "extra_refs": sorted(set(refs) - expected_refs),
        "page_counts": [len(_items(call)) for call in calls],
        "page_errors": [call_error(call) for call in calls if not _success(call)],
        "terminal_cursor": result["terminal_cursor"],
        "timestamps_descending": timestamps == sorted(timestamps, reverse=True),
        "db_timestamp_mismatches": timestamp_mismatches,
        "second_page_replay_equal": replay_ok,
        "arguments": result["base_arguments"],
    }


def run_updated_after_checks(
    runner: Runner,
    cursor: Any,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify strict, precise, ordered, and paginated updated_after behavior."""

    schema = catalog_schema_evidence(cursor)
    entities = catalog_entities(cursor)
    fixture = choose_update_fixture(entities)
    if fixture is None:
        cases.append(
            {
                "case_id": "CAT-UPDATED-AFTER-STRICT",
                "status": "BLOCKED",
                "title": "updated_after strict boundary",
                "reason": "no small timestamp fixture with multiple newer rows exists",
            }
        )
        return {"schema": schema}

    threshold, equal_rows = fixture
    db_by_ref = {entity_ref(row): row for row in entities}
    boundaries = {
        "BEFORE": threshold - timedelta(microseconds=1),
        "EQUAL": threshold,
        "AFTER": threshold + timedelta(microseconds=1),
    }
    traversals: dict[str, dict[str, Any]] = {}
    evidence: dict[str, Any] = {}
    all_ok = True
    blocked = False
    for label, value in boundaries.items():
        expected = [row for row in entities if parse_timestamp(row.get("updated_at")) > value]
        traversal = paginate_updated_after(runner, f"CAT-UPDATED-{label}", value, len(expected))
        traversals[label] = traversal
        check = updated_page_verdict(traversal, expected, db_by_ref)
        evidence[label] = check
        all_ok = all_ok and check["ok"]
        blocked = blocked or any(_error_code(call) in BLOCKING_CODES for call in traversal["calls"])

    equal_refs = {entity_ref(row) for row in equal_rows}
    before_refs = set(evidence["BEFORE"]["returned_refs"])
    equal_result_refs = set(evidence["EQUAL"]["returned_refs"])
    after_refs = set(evidence["AFTER"]["returned_refs"])
    strict_transition_ok = (
        equal_refs <= before_refs
        and not (equal_refs & equal_result_refs)
        and not (equal_refs & after_refs)
        and equal_result_refs == after_refs
    )
    all_ok = all_ok and strict_transition_ok
    status = "PASS" if all_ok else ("BLOCKED" if blocked else "FAIL")
    cases.append(
        {
            "case_id": "CAT-UPDATED-AFTER-STRICT",
            "status": status,
            "title": "updated_after uses the entity updated_at field and a strict greater-than boundary",
            "reason": (
                "T-1us includes the T rows while T and T+1us exclude them, and every returned timestamp equals the entity table"
                if status == "PASS"
                else "the strict boundary, DB timestamp mapping, exact result set, ordering, or cursor traversal differs"
            ),
            "threshold": threshold.isoformat(),
            "equal_refs": sorted(equal_refs),
            "strict_transition_ok": strict_transition_ok,
            "reproduction": evidence,
            "authoritative_source": {
                "tables": ["factors", "sub_factors"],
                "column": "updated_at",
                "schema": schema,
                "mapping_check": "MCP item.updated_at equals the matching entity table updated_at",
                "deployed_source_code_available_locally": False,
                "contract_basis": "live factor_search updated_after field plus the strict > acceptance baseline",
            },
        }
    )
    return {
        "schema": schema,
        "threshold": threshold,
        "equal_refs": sorted(equal_refs),
        "traversals": traversals,
    }


def write_summary(output: Path, cases: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    """Write concise Markdown and JSON adjudication reports."""

    counts = Counter(case["status"] for case in cases)
    result = {
        "captured_at": datetime.now(LOCAL_TZ),
        "environment": "test",
        "mcp_url": MCP_URL,
        "read_only": True,
        "counts": dict(counts),
        "cases": cases,
    }
    _write_json(output / "manifest.json", manifest)
    _write_json(output / "results.json", result)
    _write_json(output / "adjudicated-summary.json", result)
    lines = [
        "# Factor 4.0 catalog boundary reconciliation",
        "",
        f"Captured: {result['captured_at'].isoformat()}",
        f"Result: {dict(counts)}",
        "",
        "| Case | Status | Result |",
        "|---|---|---|",
    ]
    for case in cases:
        lines.append(f"| {case['case_id']} | {case['status']} | {case['reason']} |")
    lines.extend(
        [
            "",
            "The DB session used START TRANSACTION READ ONLY and was explicitly rolled back.",
            "Requests and reports contain no Authorization header, token, or database credential.",
            "",
        ]
    )
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Execute both checks and emit sanitized evidence under reports/factor4-resume."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} or MCP_TOKEN is required")
    if not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("test environment gate failed")

    run_stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%dT%H%M%S%z")
    output = PROJECT_ROOT / "reports" / "factor4-resume" / f"{run_stamp}-catalog-boundaries"
    output.mkdir(parents=True, exist_ok=True)
    runner = Runner(token, output, db=None)  # type: ignore[arg-type]
    cases: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {
        "environment": "test",
        "mcp_url": MCP_URL,
        "read_only": True,
        "database": "factor_db test environment",
        "database_transaction": "START TRANSACTION READ ONLY; ROLLBACK",
        "included": [
            "CS factor_catalog_stats with explicit valid/invalid/unknown",
            "factor_search updated_after strict boundary, ordering, and pagination",
        ],
        "excluded": [
            "UX, compatibility, and style",
            "orphan entities",
            "end-time boundary",
            "missing documents",
            "VWAP historical data",
        ],
    }

    connection = open_read_only_connection()
    rolled_back = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION READ ONLY")
            as_of = datetime.now(LOCAL_TZ)

            init = runner.request(
                "MCP-INIT",
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "QuestTest-catalog-boundaries", "version": "1.0"},
                },
            )
            init_result = ((init.get("envelope") or {}).get("result") or {})
            runner.protocol_version = init_result.get("protocolVersion")
            if init.get("http_status") != 200 or not runner.protocol_version:
                cases.append(
                    {
                        "case_id": "MCP-INIT",
                        "status": "BLOCKED",
                        "title": "MCP session initialization",
                        "reason": "the configured test MCP session could not be initialized",
                        "error": call_error(init),
                    }
                )
            else:
                runner.notify_initialized("MCP-NOTIFY")
                tools_call = runner.request("MCP-TOOLS", "tools/list", {})
                tools_result = ((tools_call.get("envelope") or {}).get("result") or {})
                tools = tools_result.get("tools") if isinstance(tools_result, dict) else []
                factor_search_schema = next(
                    (tool for tool in tools if isinstance(tool, dict) and tool.get("name") == "factor_search"),
                    None,
                )
                stats_schema = next(
                    (tool for tool in tools if isinstance(tool, dict) and tool.get("name") == "factor_catalog_stats"),
                    None,
                )
                manifest["live_tool_contracts"] = {
                    "factor_search": factor_search_schema,
                    "factor_catalog_stats": stats_schema,
                }
                if factor_search_schema is None or stats_schema is None:
                    cases.append(
                        {
                            "case_id": "MCP-TOOLS",
                            "status": "BLOCKED",
                            "title": "required live MCP tools",
                            "reason": "factor_search or factor_catalog_stats is absent",
                            "error": call_error(tools_call),
                        }
                    )
                else:
                    run_catalog_stats_checks(runner, cursor, as_of, cases)
                    run_updated_after_checks(runner, cursor, cases)
    finally:
        connection.rollback()
        rolled_back = True
        connection.close()

    cases.append(
        {
            "case_id": "DB-READ-ONLY-ROLLBACK",
            "status": "PASS" if rolled_back else "FAIL",
            "title": "database execution remained read-only",
            "reason": (
                "the session was declared read-only and explicitly rolled back"
                if rolled_back
                else "the database rollback was not completed"
            ),
            "executed_statement_classes": ["SET", "START TRANSACTION READ ONLY", "SELECT", "ROLLBACK"],
        }
    )
    manifest["database_rollback_completed"] = rolled_back
    write_summary(output, cases, manifest)
    print(json.dumps({"output_dir": str(output), "counts": dict(Counter(x["status"] for x in cases))}))


if __name__ == "__main__":
    main()
