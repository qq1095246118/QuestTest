#!/usr/bin/env python3
"""Run incremental read-only functional checks for Factor Data catalog search."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient, DatabaseTransaction  # noqa: E402
from tmp.catalog_deep_readonly import (  # noqa: E402
    MCP_URL,
    Runner,
    _data,
    _error_code,
    _items,
    _meta,
    _rejected,
    _success,
    _write_json,
)


TOKEN_ENV = "CATALOG_MCP_TOKEN"
LOCAL_TZ = timezone(timedelta(hours=8))
STATUS_NAMES = {0: "inactive", 1: "new", 2: "valid", 3: "invalid", 4: "deleted"}
METRIC_FIELDS = (
    "coverage_mean",
    "icir",
    "rank_icir",
    "oos_icir",
    "rank_oos_icir",
    "final_score",
    "valid_slice_count",
)


def _record(
    runner: Runner,
    case_id: str,
    title: str,
    ok: bool,
    pass_reason: str,
    fail_reason: str,
    evidence: dict[str, Any],
    *,
    severity: str = "P1",
    failure_class: str = "FAIL_BUSINESS",
) -> None:
    """Record one functional verdict with a consistent shape."""
    runner.record(
        case_id,
        title,
        "PASS" if ok else "FAIL",
        pass_reason if ok else fail_reason,
        evidence=evidence,
        severity=None if ok else severity,
        failure_class=None if ok else failure_class,
    )


def _block(runner: Runner, case_id: str, title: str, reason: str, **evidence: Any) -> None:
    """Record an unexecuted data- or quota-dependent case."""
    runner.record(
        case_id,
        title,
        "BLOCKED",
        reason,
        evidence=evidence,
        failure_class="BLOCKED_QUOTA" if "quota" in reason.lower() else "BLOCKED_DATA_PRECONDITION",
    )


def _error_signature(call: dict[str, Any]) -> dict[str, Any]:
    """Return non-sensitive transport and business error evidence."""
    return {
        "http_status": call.get("http_status"),
        "is_error": call.get("is_error"),
        "error_code": _error_code(call),
        "elapsed_seconds": call.get("elapsed_seconds"),
    }


def _remaining(call: dict[str, Any]) -> int | None:
    """Extract the reported remaining quota from one successful tool response."""
    quota = _meta(call).get("quota")
    value = quota.get("remaining") if isinstance(quota, dict) else None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _as_local(value: datetime) -> datetime:
    """Interpret naive database timestamps as Asia/Shanghai wall-clock timestamps."""
    if value.tzinfo is None:
        return value.replace(tzinfo=LOCAL_TZ)
    return value.astimezone(LOCAL_TZ)


def _db_time(value: datetime) -> datetime:
    """Convert an aware timestamp to the naive local form stored by the test DB."""
    return value.astimezone(LOCAL_TZ).replace(tzinfo=None)


def _same_number(left: Any, right: Any) -> bool:
    """Compare nullable numeric values without float conversion."""
    if left is None or right is None:
        return left is None and right is None
    return Decimal(str(left)) == Decimal(str(right))


def _catalog_fixture(tx: DatabaseTransaction) -> dict[str, Any] | None:
    """Select a small nonempty status/category subset whose full set can be paged."""
    row = tx.fetch_one(
        """
        SELECT kind, status_code, coin_category,
               COUNT(DISTINCT status_entity_id) AS raw_count,
               COUNT(DISTINCT entity_id) AS entity_count
        FROM (
            SELECT 'factor' AS kind, fs.status AS status_code,
                   fs.coin_category, fs.factor_id AS status_entity_id, f.id AS entity_id
            FROM factors_status fs
            LEFT JOIN factors f ON f.id=fs.factor_id
            WHERE fs.is_sub_factor_id=0
            UNION ALL
            SELECT 'sub_factor', fs.status, fs.coin_category, fs.factor_id, s.id
            FROM factors_status fs
            LEFT JOIN sub_factors s ON s.id=fs.factor_id
            WHERE fs.is_sub_factor_id=1
        ) scoped
        GROUP BY kind, status_code, coin_category
        HAVING entity_count BETWEEN 4 AND 20 AND raw_count=entity_count
        ORDER BY ABS(entity_count - 8), kind, status_code, coin_category
        LIMIT 1
        """
    )
    if row is None or int(row["status_code"]) not in STATUS_NAMES:
        return None
    kind = str(row["kind"])
    table = "factors" if kind == "factor" else "sub_factors"
    sub_flag = 0 if kind == "factor" else 1
    ids = tx.fetch_all(
        f"""
        SELECT DISTINCT e.id
        FROM {table} e
        JOIN factors_status fs ON fs.factor_id=e.id AND fs.is_sub_factor_id=%s
        WHERE fs.status=%s AND fs.coin_category=%s
        ORDER BY e.id
        """,
        (sub_flag, row["status_code"], row["coin_category"]),
    )
    return {
        "kind": kind,
        "library_status": STATUS_NAMES[int(row["status_code"])],
        "library_coin_category": str(row["coin_category"]),
        "expected_refs": {f"{kind}:{item['id']}" for item in ids},
    }


def _core_identity(tx: DatabaseTransaction, item: dict[str, Any]) -> dict[str, Any] | None:
    """Load one catalog entity's core identity from its authoritative table."""
    kind = item.get("kind")
    entity_id = item.get("id")
    if kind == "factor":
        return tx.fetch_one(
            "SELECT id, factor_name AS name, cn_name, serial_number, data_source, updated_at FROM factors WHERE id=%s",
            (entity_id,),
        )
    if kind == "sub_factor":
        return tx.fetch_one(
            "SELECT id, sub_factor_name AS name, cn_name, serial_number, data_source, updated_at FROM sub_factors WHERE id=%s",
            (entity_id,),
        )
    return None


def _catalog_item_mismatches(
    tx: DatabaseTransaction, items: list[dict[str, Any]], fixture: dict[str, Any]
) -> list[dict[str, Any]]:
    """Compare catalog page identities and status membership with DB rows."""
    mismatches: list[dict[str, Any]] = []
    for item in items:
        row = _core_identity(tx, item)
        fields = ("id", "name", "cn_name", "serial_number", "data_source")
        bad = [field for field in fields if row is None or item.get(field) != row.get(field)]
        if item.get("kind") != fixture["kind"]:
            bad.append("kind")
        if item.get("library_status") != fixture["library_status"]:
            bad.append("library_status")
        if fixture["library_coin_category"] not in (item.get("library_coin_categories") or []):
            bad.append("library_coin_categories")
        if bad:
            mismatches.append({"factor_ref": item.get("factor_ref"), "fields": sorted(set(bad))})
    return mismatches


def _run_catalog_pagination(runner: Runner, tx: DatabaseTransaction) -> dict[str, Any]:
    """Exhaust one dynamic DB-backed subset and verify stable cursor continuation."""
    fixture = _catalog_fixture(tx)
    if fixture is None:
        _block(runner, "CAT-PAGE-DB", "DB-backed catalog pagination", "no bounded catalog subset exists")
        return {}
    base = {key: value for key, value in fixture.items() if key != "expected_refs"}
    page_size = 3
    stats = runner.tool("CAT-PAGE-STATS", "factor_catalog_stats", base)
    calls: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    cursor: str | None = None
    page_two_args: dict[str, Any] | None = None
    page_two_refs: list[str] = []
    seen_cursors: set[str] = set()
    for page_number in range(1, 12):
        args = {**base, "limit": page_size}
        if cursor:
            args["cursor"] = cursor
        call = runner.tool(f"CAT-PAGE-{page_number:02d}", "factor_search", args)
        calls.append(call)
        if not _success(call):
            break
        page_items = _items(call)
        all_items.extend(page_items)
        if page_number == 2:
            page_two_args = args
            page_two_refs = [str(row.get("factor_ref")) for row in page_items]
        next_cursor = _meta(call).get("next_cursor")
        if not next_cursor:
            cursor = None
            break
        if str(next_cursor) in seen_cursors:
            cursor = str(next_cursor)
            break
        seen_cursors.add(str(next_cursor))
        cursor = str(next_cursor)

    refs = [str(row.get("factor_ref")) for row in all_items]
    expected_refs = fixture["expected_refs"]
    stats_total = _data(stats).get("total")
    shape_bad = [
        call["case_id"]
        for call in calls
        if _success(call)
        and (
            _meta(call).get("truncated") is not bool(_meta(call).get("next_cursor"))
            or len(_items(call)) > page_size
        )
    ]
    db_mismatches = _catalog_item_mismatches(tx, all_items, fixture)
    ok = (
        _success(stats)
        and all(_success(call) for call in calls)
        and cursor is None
        and stats_total == len(expected_refs)
        and len(refs) == len(set(refs))
        and set(refs) == expected_refs
        and not shape_bad
        and not db_mismatches
    )
    _record(
        runner,
        "CAT-PAGE-DB",
        "dynamic catalog subset paginates to the exact DB identity set",
        ok,
        "stats, every page, the terminal cursor, entity fields, and the DB identity set agree",
        "pagination omitted/repeated rows, violated page metadata, or disagreed with DB",
        {
            "filters": base,
            "expected_total": len(expected_refs),
            "stats_total": stats_total,
            "page_counts": [len(_items(call)) for call in calls],
            "returned_count": len(refs),
            "unique_count": len(set(refs)),
            "missing_refs": sorted(expected_refs - set(refs)),
            "extra_refs": sorted(set(refs) - expected_refs),
            "page_shape_mismatches": shape_bad,
            "db_field_mismatches": db_mismatches,
            "errors": [_error_signature(call) for call in calls if not _success(call)],
        },
        failure_class="FAIL_PAGINATION",
    )

    repeat = runner.tool("CAT-SORT-REPEAT", "factor_search", {**base, "limit": page_size})
    first_refs = [str(row.get("factor_ref")) for row in _items(calls[0])] if calls else []
    repeat_refs = [str(row.get("factor_ref")) for row in _items(repeat)]
    repeat_ok = bool(calls and _success(calls[0]) and _success(repeat) and first_refs == repeat_refs)
    _record(
        runner,
        "CAT-SORT-STABLE",
        "identical catalog searches return stable ordered pages",
        repeat_ok,
        "the ordered first page was identical on replay",
        "an unchanged query returned a different ordered page",
        {"filters": base, "first_refs": first_refs, "repeat_refs": repeat_refs},
        failure_class="FAIL_SORT_STABILITY",
    )

    if page_two_args is None:
        _block(runner, "CAT-CURSOR-REPLAY", "cursor replay is deterministic", "subset did not produce a second page")
    else:
        replay = runner.tool("CAT-CURSOR-REPLAY", "factor_search", page_two_args)
        replay_refs = [str(row.get("factor_ref")) for row in _items(replay)]
        replay_ok = _success(replay) and replay_refs == page_two_refs
        _record(
            runner,
            "CAT-CURSOR-REPLAY",
            "replaying the same signed cursor returns the same ordered continuation",
            replay_ok,
            "cursor replay returned the same continuation page",
            "cursor replay changed, failed, or crossed query state",
            {"expected_refs": page_two_refs, "replay_refs": replay_refs, "error": _error_signature(replay)},
            failure_class="FAIL_CURSOR_BINDING",
        )
    return {"fixture": fixture, "items": all_items, "remaining": _remaining(repeat)}


def _updated_after_fixture(tx: DatabaseTransaction) -> tuple[datetime, set[str]] | None:
    """Choose a timestamp with a small, exact strict-after result set."""
    rows = tx.fetch_all(
        """
        SELECT kind, entity_id, updated_at
        FROM (
            SELECT 'factor' AS kind, id AS entity_id, updated_at FROM factors WHERE updated_at IS NOT NULL
            UNION ALL
            SELECT 'sub_factor', id, updated_at FROM sub_factors WHERE updated_at IS NOT NULL
        ) entities
        ORDER BY updated_at DESC, kind, entity_id
        """
    )
    times = sorted({row["updated_at"] for row in rows}, reverse=True)
    for threshold in times:
        refs = {
            f"{row['kind']}:{row['entity_id']}"
            for row in rows
            if row["updated_at"] > threshold
        }
        if 5 <= len(refs) <= 15:
            return threshold, refs
    return None


def _run_catalog_boundaries(runner: Runner, tx: DatabaseTransaction, seed: dict[str, Any] | None) -> None:
    """Verify strict updated-after, empty-result, and Chinese-name query behavior."""
    fixture = _updated_after_fixture(tx)
    if fixture is None:
        _block(runner, "CAT-UPDATED-DB", "updated_after exact DB set", "no bounded timestamp fixture exists")
    else:
        threshold, expected_refs = fixture
        args = {"updated_after": _as_local(threshold).isoformat(), "limit": 50}
        call = runner.tool("CAT-UPDATED-DB", "factor_search", args)
        refs = [str(row.get("factor_ref")) for row in _items(call)]
        timestamps = [datetime.fromisoformat(str(row["updated_at"])) for row in _items(call)]
        ok = (
            _success(call)
            and set(refs) == expected_refs
            and len(refs) == len(set(refs))
            and _meta(call).get("next_cursor") is None
            and _meta(call).get("truncated") is False
            and all(value > _as_local(threshold) for value in timestamps)
            and timestamps == sorted(timestamps, reverse=True)
        )
        _record(
            runner,
            "CAT-UPDATED-DB",
            "updated_after returns the exact strict-after DB set in descending time order",
            ok,
            "all and only newer entities were returned once in stable time order",
            "updated_after omitted, leaked, duplicated, or misordered DB entities",
            {
                "threshold": _as_local(threshold).isoformat(),
                "expected_count": len(expected_refs),
                "returned_count": len(refs),
                "missing_refs": sorted(expected_refs - set(refs)),
                "extra_refs": sorted(set(refs) - expected_refs),
                "error": _error_signature(call),
            },
            failure_class="FAIL_DATA_CONSISTENCY",
        )

    missing_query = f"questtest-no-match-{uuid4().hex}"
    empty = runner.tool("CAT-EMPTY", "factor_search", {"query": missing_query, "limit": 5})
    empty_ok = (
        _success(empty)
        and not _items(empty)
        and _meta(empty).get("next_cursor") is None
        and _meta(empty).get("truncated") is False
    )
    _record(
        runner,
        "CAT-EMPTY",
        "a valid nonmatching search returns a successful terminal empty page",
        empty_ok,
        "the nonmatching selector returned an empty non-truncated result",
        "the nonmatching selector errored, returned rows, or advertised continuation",
        {"returned_count": len(_items(empty)), "meta": _meta(empty), "error": _error_signature(empty)},
        failure_class="FAIL_EMPTY_RESULT",
    )

    cn_seed = seed if seed and seed.get("cn_name") else None
    if cn_seed is None:
        row = tx.fetch_one(
            """
            SELECT id, cn_name FROM sub_factors
            WHERE cn_name IS NOT NULL AND TRIM(cn_name)<>''
            ORDER BY updated_at DESC, id DESC LIMIT 1
            """
        )
        cn_seed = {"factor_ref": f"sub_factor:{row['id']}", "cn_name": row["cn_name"]} if row else None
    if cn_seed is None:
        _block(runner, "CAT-QUERY-CN", "Chinese display-name search", "no Chinese display name exists")
    else:
        query = runner.tool("CAT-QUERY-CN", "factor_search", {"query": cn_seed["cn_name"], "limit": 10})
        refs = {str(row.get("factor_ref")) for row in _items(query)}
        query_ok = _success(query) and str(cn_seed["factor_ref"]) in refs
        _record(
            runner,
            "CAT-QUERY-CN",
            "query resolves an entity by its exact Chinese display name",
            query_ok,
            "the dynamically selected Chinese name resolved its catalog entity",
            "an exposed Chinese display name could not resolve its own entity",
            {"factor_ref": cn_seed["factor_ref"], "returned_refs": sorted(refs), "error": _error_signature(query)},
            failure_class="FAIL_SEARCH_RESOLUTION",
        )


def _scope_args(scope: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    """Build one complete point-in-time factor_search metric identity."""
    return {
        "kind": scope["kind"],
        "ic_scope": scope["ic_scope"],
        "validity_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": scope["forward_return_bars"],
        "scoring_version": scope["scoring_version"],
        "symbol": scope.get("symbol") or "",
        "as_of": as_of.isoformat(),
    }


def _metric_predicate(scope: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """Return a parameterized exact-scope predicate for summary metrics."""
    sql = """m.is_sub_factor_id=%s AND m.ic_scope=%s AND m.calculation_mode=%s
             AND m.factor_bar_interval=%s AND m.factor_window_bars=%s
             AND m.return_bar_interval=%s AND m.forward_return_bars=%s
             AND m.universe_key=%s AND m.symbol=%s AND m.window_scope=%s
             AND m.scoring_version=%s"""
    params = (
        1 if scope["kind"] == "sub_factor" else 0,
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
    return sql, params


def _latest_metric_rows(
    tx: DatabaseTransaction, scope: dict[str, Any], as_of: datetime
) -> list[dict[str, Any]]:
    """Load one latest completed metric row per factor for an exact scope."""
    predicate, params = _metric_predicate(scope)
    return tx.fetch_all(
        f"""
        SELECT ranked.*
        FROM (
            SELECT m.*, r.completed_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.factor_id
                       ORDER BY r.completed_at DESC, m.updated_at DESC, m.id DESC
                   ) AS row_num
            FROM factor_ic_summary_metrics m
            JOIN factor_ic_runs r ON r.run_id=m.run_id
            WHERE {predicate} AND r.status='completed' AND r.completed_at<=%s
        ) ranked
        WHERE ranked.row_num=1
        ORDER BY ranked.factor_id
        """,
        params + (_db_time(as_of),),
    )


def _metric_mismatches(
    tx: DatabaseTransaction,
    scope: dict[str, Any],
    as_of: datetime,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare returned metric identities and values to latest completed DB rows."""
    latest = {int(row["factor_id"]): row for row in _latest_metric_rows(tx, scope, as_of)}
    mismatches: list[dict[str, Any]] = []
    for item in items:
        row = latest.get(int(item.get("id", -1)))
        bad: list[str] = []
        if row is None:
            bad.append("missing_db_candidate")
        else:
            identities = {
                "metric_run_id": row["run_id"],
                "scoring_version": row["scoring_version"],
                "factor_bar_interval": row["factor_bar_interval"],
            }
            bad.extend(key for key, value in identities.items() if item.get(key) != value)
            bad.extend(field for field in METRIC_FIELDS if not _same_number(item.get(field), row.get(field)))
            completed = _as_local(row["completed_at"])
            if completed > as_of:
                bad.append("future_run")
        if bad:
            mismatches.append({"factor_ref": item.get("factor_ref"), "fields": sorted(set(bad))})
    return mismatches


def _run_metric_scope(runner: Runner, tx: DatabaseTransaction, now: datetime) -> dict[str, Any]:
    """Verify CS discovery, search, stats, thresholds, DB values, and PIT fallback."""
    scopes_call = runner.tool(
        "CS-SCOPE-DISCOVERY",
        "factor_list_metric_scopes",
        {"as_of": now.isoformat(), "kind": "sub_factor", "ic_scope": "cross_sectional", "limit": 20},
    )
    scopes = [
        row
        for row in _items(scopes_call)
        if row.get("ic_scope") == "cross_sectional" and (row.get("symbol") or "") == ""
    ]
    complete_keys = {
        "kind",
        "ic_scope",
        "calculation_mode",
        "factor_bar_interval",
        "factor_window_bars",
        "return_bar_interval",
        "forward_return_bars",
        "universe_key",
        "window_scope",
        "scoring_version",
    }
    complete = [row for row in scopes if complete_keys <= set(row) and all(row.get(key) is not None for key in complete_keys)]
    discovery_ok = (
        _success(scopes_call)
        and bool(complete)
        and all(datetime.fromisoformat(str(row["run_completed_at"])) <= now for row in complete)
    )
    _record(
        runner,
        "CS-SCOPE-DISCOVERY",
        "cross-sectional scope discovery returns only completed point-in-time identities",
        discovery_ok,
        "all discovered CS identities were complete and completed no later than as_of",
        "scope discovery failed, returned incomplete identities, or leaked a future run",
        {
            "as_of": now.isoformat(),
            "returned_count": len(scopes),
            "complete_count": len(complete),
            "future_run_ids": [
                row.get("run_id")
                for row in complete
                if datetime.fromisoformat(str(row["run_completed_at"])) > now
            ],
            "error": _error_signature(scopes_call),
        },
        severity="P0",
        failure_class="FAIL_POINT_IN_TIME",
    )
    if not discovery_ok:
        _block(runner, "CS-SEARCH-DB", "CS search and DB oracle", "no usable discovered CS scope exists")
        return {"remaining": _remaining(scopes_call)}

    scope = max(complete, key=lambda row: int(row.get("available_factor_count") or 0))
    args = {**_scope_args(scope, now), "limit": 5}
    search = runner.tool("CS-SEARCH-DB", "factor_search", args)
    stats = runner.tool(
        "CS-STATS-DB",
        "factor_catalog_stats",
        {key: value for key, value in args.items() if key != "limit"},
    )
    items = _items(search)
    db_rows = _latest_metric_rows(tx, scope, now)
    mismatches = _metric_mismatches(tx, scope, now, items)
    stats_total = _data(stats).get("total")
    search_ok = (
        _success(search)
        and bool(items)
        and len({row.get("factor_ref") for row in items}) == len(items)
        and not mismatches
    )
    _record(
        runner,
        "CS-SEARCH-DB",
        "CS metric search returns exact-scope latest completed DB values",
        search_ok,
        "every sampled row matched its latest completed summary metric and exact scope",
        "CS search failed, duplicated factors, mixed scope, or disagreed with DB metrics",
        {
            "scope": args,
            "returned_refs": [row.get("factor_ref") for row in items],
            "db_mismatches": mismatches,
            "error": _error_signature(search),
        },
        failure_class="FAIL_DATA_CONSISTENCY",
    )
    stats_ok = _success(stats) and stats_total == len(db_rows)
    _record(
        runner,
        "CS-STATS-DB",
        "CS scope statistics equal the DB latest-candidate count",
        stats_ok,
        "factor_catalog_stats total equals the independently selected DB candidate count",
        "scope statistics failed or counted a different candidate set than DB",
        {
            "stats_total": stats_total,
            "db_candidate_count": len(db_rows),
            "search_page_count": len(items),
            "error": _error_signature(stats),
        },
        failure_class="FAIL_DATA_CONSISTENCY",
    )

    score_values = [Decimal(str(row["final_score"])) for row in db_rows if row.get("final_score") is not None]
    if score_values:
        threshold = max(score_values) + Decimal("0.00000001")
        above = runner.tool(
            "CS-THRESHOLD-EMPTY",
            "factor_search",
            {**args, "min_score": float(threshold)},
        )
        threshold_ok = _success(above) and not _items(above) and _meta(above).get("next_cursor") is None
        _record(
            runner,
            "CS-THRESHOLD-EMPTY",
            "min_score above the DB maximum returns a terminal empty set",
            threshold_ok,
            "the impossible score threshold returned no metric candidates",
            "a candidate exceeded the DB maximum, the filter was ignored, or the query failed",
            {
                "db_max_score": str(max(score_values)),
                "threshold": str(threshold),
                "returned_refs": [row.get("factor_ref") for row in _items(above)],
                "error": _error_signature(above),
            },
            failure_class="FAIL_THRESHOLD_FILTER",
        )
    else:
        _block(runner, "CS-THRESHOLD-EMPTY", "CS score threshold boundary", "selected scope has no final_score")

    pit_result: dict[str, Any] = {}
    if items:
        target = items[0]
        current_row = next((row for row in db_rows if int(row["factor_id"]) == int(target["id"])), None)
        if current_row is not None:
            before = _as_local(current_row["completed_at"]) - timedelta(microseconds=1)
            pit_args = {**_scope_args(scope, before), "query": target["name"], "limit": 5}
            pit = runner.tool("CS-SEARCH-PIT", "factor_search", pit_args)
            pit_items = _items(pit)
            prior_rows = _latest_metric_rows(tx, scope, before)
            prior = next((row for row in prior_rows if int(row["factor_id"]) == int(target["id"])), None)
            target_items = [row for row in pit_items if int(row.get("id", -1)) == int(target["id"])]
            target_ok = (
                (prior is None and not target_items)
                or (
                    prior is not None
                    and len(target_items) == 1
                    and target_items[0].get("metric_run_id") == prior["run_id"]
                )
            )
            pit_mismatches = _metric_mismatches(tx, scope, before, pit_items)
            pit_ok = _success(pit) and target_ok and not pit_mismatches
            _record(
                runner,
                "CS-SEARCH-PIT",
                "historical CS search excludes the just-completed run and falls back exactly",
                pit_ok,
                "the pre-completion query returned only the independently selected historical state",
                "historical search leaked the future run, missed its prior state, or disagreed with DB",
                {
                    "factor_ref": target["factor_ref"],
                    "current_run_id": current_row["run_id"],
                    "current_completed_at": _as_local(current_row["completed_at"]).isoformat(),
                    "query_as_of": before.isoformat(),
                    "expected_prior_run_id": prior.get("run_id") if prior else None,
                    "returned_target_run_ids": [row.get("metric_run_id") for row in target_items],
                    "db_mismatches": pit_mismatches,
                    "error": _error_signature(pit),
                },
                severity="P0",
                failure_class="FAIL_POINT_IN_TIME",
            )
            pit_result = {"as_of": before, "scope": scope}
        else:
            _block(runner, "CS-SEARCH-PIT", "historical CS search fallback", "search target has no DB row")
    else:
        _block(runner, "CS-SEARCH-PIT", "historical CS search fallback", "CS search returned no target")
    return {
        "scope": scope,
        "items": items,
        "pit": pit_result,
        "remaining": _remaining(search),
    }


def _one_dim_candidates(tx: DatabaseTransaction, as_of: datetime) -> list[dict[str, Any]]:
    """Find latest CS metric rows whose overall validity comes from exactly one dimension."""
    found: list[dict[str, Any]] = []
    for mode in ("cs_only", "ts_only"):
        flag_sql = (
            "COALESCE(time_series_is_valid,0)=0 AND cross_sectional_is_valid=1"
            if mode == "cs_only"
            else "time_series_is_valid=1 AND COALESCE(cross_sectional_is_valid,0)=0"
        )
        validity_rows = tx.fetch_all(
            f"""
            SELECT * FROM factor_validity_status
            WHERE is_sub_factor_id=1 AND overall_is_valid=1 AND {flag_sql}
            ORDER BY id DESC LIMIT 30
            """
        )
        for validity in validity_rows:
            metric = tx.fetch_one(
                """
                SELECT m.run_id, m.factor_id, m.ic_scope, m.calculation_mode,
                       m.factor_bar_interval, m.factor_window_bars,
                       m.return_bar_interval, m.forward_return_bars,
                       m.universe_key, m.symbol, m.window_scope, m.scoring_version,
                       r.completed_at, s.sub_factor_name AS name
                FROM factor_ic_summary_metrics m
                JOIN factor_ic_runs r ON r.run_id=m.run_id
                JOIN sub_factors s ON s.id=m.factor_id
                WHERE m.run_id=%s AND m.factor_id=%s AND m.is_sub_factor_id=1
                  AND m.ic_scope='cross_sectional' AND r.status='completed'
                  AND r.completed_at<=%s
                ORDER BY m.id DESC LIMIT 1
                """,
                (validity["run_id"], validity["factor_id"], _db_time(as_of)),
            )
            if metric is None:
                continue
            predicate, params = _metric_predicate({**metric, "kind": "sub_factor"})
            latest = tx.fetch_one(
                f"""
                SELECT m.run_id
                FROM factor_ic_summary_metrics m
                JOIN factor_ic_runs r ON r.run_id=m.run_id
                WHERE {predicate} AND m.factor_id=%s
                  AND r.status='completed' AND r.completed_at<=%s
                ORDER BY r.completed_at DESC, m.updated_at DESC, m.id DESC
                LIMIT 1
                """,
                params + (validity["factor_id"], _db_time(as_of)),
            )
            if latest is None or latest["run_id"] != metric["run_id"]:
                continue
            candidate = {
                **metric,
                "time_series_status": validity["time_series_status"],
                "time_series_is_valid": validity["time_series_is_valid"],
                "cross_sectional_status": validity["cross_sectional_status"],
                "cross_sectional_is_valid": validity["cross_sectional_is_valid"],
                "overall_status": validity["overall_status"],
                "overall_is_valid": validity["overall_is_valid"],
                "valid_mode": mode,
                "kind": "sub_factor",
            }
            found.append(candidate)
            break
    return found


def _run_any_valid_scope(runner: Runner, tx: DatabaseTransaction, now: datetime) -> None:
    """Verify overall valid search accepts TS-only and CS-only validity rows."""
    candidates = _one_dim_candidates(tx, now)
    by_mode = {row["valid_mode"]: row for row in candidates}
    for mode, case_id in (("cs_only", "VALID-CS-ONLY"), ("ts_only", "VALID-TS-ONLY")):
        row = by_mode.get(mode)
        if row is None:
            _block(runner, case_id, f"overall validity accepts {mode}", f"no latest {mode} fixture exists")
            continue
        args = {
            **_scope_args(row, now),
            "validity_scope": "overall",
            "validity": "valid",
            "query": row["name"],
            "limit": 5,
        }
        call = runner.tool(case_id, "factor_search", args)
        targets = [item for item in _items(call) if int(item.get("id", -1)) == int(row["factor_id"])]
        target = targets[0] if len(targets) == 1 else {}
        expected_statuses = {
            "time_series_status": row["time_series_status"],
            "cross_sectional_status": row["cross_sectional_status"],
            "overall_status": row["overall_status"],
            "validity_status": row["overall_status"],
        }
        bad = [key for key, value in expected_statuses.items() if target.get(key) != value]
        ok = (
            _success(call)
            and len(targets) == 1
            and target.get("metric_run_id") == row["run_id"]
            and target.get("validity_run_id") == row["run_id"]
            and not bad
        )
        _record(
            runner,
            case_id,
            f"overall valid search admits a factor with only {mode.replace('_', ' ')} validity",
            ok,
            "the one-valid-dimension factor remained eligible and matched its DB validity snapshot",
            "overall filtering excluded a one-valid-dimension factor or projected the wrong validity row",
            {
                "factor_ref": f"sub_factor:{row['factor_id']}",
                "metric_run_id": row["run_id"],
                "db_flags": {
                    "time_series_is_valid": bool(row["time_series_is_valid"]),
                    "cross_sectional_is_valid": bool(row["cross_sectional_is_valid"]),
                    "overall_is_valid": bool(row["overall_is_valid"]),
                },
                "db_statuses": expected_statuses,
                "returned_refs": [item.get("factor_ref") for item in _items(call)],
                "returned_statuses": {key: target.get(key) for key in expected_statuses},
                "mismatched_fields": bad,
                "error": _error_signature(call),
            },
            failure_class="FAIL_VALIDITY_ELIGIBILITY",
        )


def _run_historical_scope_discovery(
    runner: Runner, tx: DatabaseTransaction, pit: dict[str, Any]
) -> None:
    """Verify metric-scope discovery itself observes its historical as_of cutoff."""
    before = pit.get("as_of")
    scope = pit.get("scope")
    if not isinstance(before, datetime) or not isinstance(scope, dict):
        _block(runner, "CS-SCOPE-PIT", "historical scope discovery", "no historical search fixture exists")
        return
    call = runner.tool(
        "CS-SCOPE-PIT",
        "factor_list_metric_scopes",
        {
            "as_of": before.isoformat(),
            "kind": "sub_factor",
            "ic_scope": "cross_sectional",
            "interval": scope["factor_bar_interval"],
            "universe_key": scope["universe_key"],
            "limit": 10,
        },
    )
    items = _items(call)
    future = [
        row
        for row in items
        if datetime.fromisoformat(str(row["run_completed_at"])) > before
    ]
    count_mismatches: list[dict[str, Any]] = []
    for item in items:
        exact_rows = _latest_metric_rows(tx, item, before)
        if int(item.get("available_factor_count") or 0) != len(exact_rows):
            count_mismatches.append(
                {
                    "identity": {
                        key: item.get(key)
                        for key in (
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
                    },
                    "mcp_count": item.get("available_factor_count"),
                    "db_count": len(exact_rows),
                }
            )
    ok = _success(call) and bool(items) and not future and not count_mismatches
    _record(
        runner,
        "CS-SCOPE-PIT",
        "historical scope discovery excludes future runs and reports exact DB counts",
        ok,
        "all historical identities were completed by as_of and their factor counts matched DB",
        "scope discovery leaked future state or reported a count inconsistent with DB",
        {
            "as_of": before.isoformat(),
            "returned_count": len(items),
            "future_run_ids": [row.get("run_id") for row in future],
            "count_mismatches": count_mismatches,
            "error": _error_signature(call),
        },
        severity="P0",
        failure_class="FAIL_POINT_IN_TIME",
    )


def _db_guard(tx: DatabaseTransaction) -> dict[str, Any]:
    """Capture row counts and latest writes for the tables in this test's scope."""
    rows = tx.fetch_all(
        """
        SELECT 'factors' entity, COUNT(*) row_count, MAX(updated_at) max_updated_at FROM factors
        UNION ALL SELECT 'sub_factors', COUNT(*), MAX(updated_at) FROM sub_factors
        UNION ALL SELECT 'factors_status', COUNT(*), MAX(updated_at) FROM factors_status
        UNION ALL SELECT 'factor_ic_runs', COUNT(*), MAX(created_at) FROM factor_ic_runs
        UNION ALL SELECT 'factor_ic_summary_metrics', COUNT(*), MAX(updated_at) FROM factor_ic_summary_metrics
        UNION ALL SELECT 'factor_validity_status', COUNT(*), MAX(updated_at) FROM factor_validity_status
        """
    )
    return {str(row["entity"]): {"row_count": row["row_count"], "max_updated_at": row["max_updated_at"]} for row in rows}


def _manifest() -> dict[str, Any]:
    """Return the human-auditable coverage manifest for this incremental run."""
    return {
        "environment": "test",
        "mode": "R0_READ_ONLY",
        "mcp_url": MCP_URL,
        "database": "test factor_db via config/test.yaml",
        "acceptance_rule": "TS or CS alone may establish overall validity",
        "excluded": [
            "known TS research-scope QUERY_TIMEOUT family",
            "orphan records",
            "end-time boundary behavior",
            "missing-document references",
            "VWAP historical recomputation",
            "UX, compatibility, and style-only behavior",
            "invalid-input classes already covered in the prior catalog regression",
        ],
        "planned_areas": [
            "dynamic status/category DB set and pagination",
            "stable ordering and deterministic cursor replay",
            "updated_after exact DB set",
            "empty result and Chinese display-name search",
            "CS scope discovery/search/stats/threshold DB consistency",
            "factor_search point-in-time fallback",
            "TS-only and CS-only overall validity eligibility",
            "historical scope discovery",
        ],
    }


def _write_summary(output_dir: Path, runner: Runner, db_guard: dict[str, Any]) -> dict[str, Any]:
    """Write machine-readable and concise Markdown results."""
    counts = Counter(row["status"] for row in runner.cases)
    result = {
        "run_id": output_dir.name,
        "environment": "test",
        "read_only": True,
        "case_counts": dict(sorted(counts.items())),
        "cases": runner.cases,
        "confirmed_failures": [row for row in runner.cases if row["status"] == "FAIL"],
        "blocked": [row for row in runner.cases if row["status"] == "BLOCKED"],
        "db_guard": db_guard,
    }
    _write_json(output_dir / "results.json", result)
    _write_json(output_dir / "call-ledger.json", runner.calls)
    lines = [
        "# Factor Data MCP catalog/search functional regression",
        "",
        f"- Environment: test",
        f"- Mode: R0 read-only",
        f"- Counts: {dict(sorted(counts.items()))}",
        "- Oracle: MCP plus a consistent read-only test factor_db snapshot",
        "- Acceptance: TS or CS alone may establish overall validity",
        "",
        "## Verdicts",
        "",
        "| Case | Status | Result |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {row['case_id']} | {row['status']} | {row['reason']} |" for row in runner.cases)
    failures = [row for row in runner.cases if row["status"] == "FAIL"]
    lines.extend(["", "## Confirmed failures", ""])
    if failures:
        for row in failures:
            lines.extend(
                [
                    f"### {row['severity']} {row['case_id']}: {row['title']}",
                    "",
                    row["reason"],
                    "",
                    "```json",
                    json.dumps(row["evidence"], ensure_ascii=False, indent=2, default=str),
                    "```",
                    "",
                ]
            )
    else:
        lines.append("No new functional defect was confirmed in the executed coverage.")
    lines.extend(
        [
            "",
            "## Scope notes",
            "",
            "The known TS research-scope timeout family and previously excluded orphan/end-boundary/missing-document/VWAP/UX/compatibility findings were not re-filed.",
            "",
            "Raw numbered request/response artifacts are sanitized and contain no authorization header, token, or DB credential.",
        ]
    )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> None:
    """Execute the incremental catalog/search regression and emit sanitized evidence."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%dT%H%M%S%z")
    output_dir = PROJECT_ROOT / "reports" / "factor4-deep" / f"{stamp}-catalog-search-functional"
    settings = SettingsLoader.load("test", PROJECT_ROOT)
    db = DatabaseClient.from_settings(settings.database)
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / "manifest.json", _manifest())

    with db.transaction() as tx:
        tx.execute("SET SESSION TRANSACTION READ ONLY")
        tx.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        runner = Runner(token, output_dir, tx)  # type: ignore[arg-type]
        init = runner.request(
            "INIT",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "QuestTest-catalog-search-functional", "version": "1.0"},
            },
        )
        runner.protocol_version = (((init.get("envelope") or {}).get("result") or {}).get("protocolVersion"))
        runner.notify_initialized("NOTIFY")
        tools_call = runner.request("TOOLS", "tools/list", {})
        tools = ((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or []
        names = {row.get("name") for row in tools if isinstance(row, dict)}
        init_ok = (
            init.get("http_status") == 200
            and runner.protocol_version == "2025-06-18"
            and {"factor_search", "factor_catalog_stats", "factor_list_metric_scopes"} <= names
        )
        _record(
            runner,
            "MCP-READY",
            "MCP protocol and required search tools are available",
            init_ok,
            "protocol negotiation and required tool discovery succeeded",
            "protocol negotiation or required catalog tool discovery failed",
            {
                "protocol_version": runner.protocol_version,
                "required_tools_present": sorted(
                    names & {"factor_search", "factor_catalog_stats", "factor_list_metric_scopes"}
                ),
                "session_present": bool(runner.session_id),
            },
            severity="P0",
            failure_class="FAIL_PROTOCOL",
        )

        start_guard = _db_guard(tx)
        catalog = _run_catalog_pagination(runner, tx)
        seed = next((row for row in catalog.get("items", []) if row.get("cn_name")), None)
        _run_catalog_boundaries(runner, tx, seed)
        now = datetime.now(LOCAL_TZ)
        metric = _run_metric_scope(runner, tx, now)
        remaining = metric.get("remaining")
        if remaining is not None and remaining < 8:
            _block(
                runner,
                "VALID-ANY-SCOPE",
                "one-dimension overall validity matrix",
                "catalog quota is too low for the two-call matrix",
                remaining=remaining,
            )
            _block(
                runner,
                "CS-SCOPE-PIT",
                "historical scope discovery",
                "catalog quota is too low for an additional scope call",
                remaining=remaining,
            )
        else:
            _run_any_valid_scope(runner, tx, now)
            _run_historical_scope_discovery(runner, tx, metric.get("pit") or {})
        end_guard = _db_guard(tx)
        guard_ok = start_guard == end_guard
        _record(
            runner,
            "DB-READ-ONLY",
            "test execution preserved all scoped DB tables in its read-only snapshot",
            guard_ok,
            "row counts and latest-write markers were unchanged",
            "a scoped table changed inside the consistent read-only snapshot",
            {"start": start_guard, "end": end_guard},
            severity="P0",
            failure_class="FAIL_UNAUTHORIZED_MUTATION",
        )
        result = _write_summary(output_dir, runner, {"start": start_guard, "end": end_guard})
    print(json.dumps({"output_dir": str(output_dir), "case_counts": result["case_counts"]}))


if __name__ == "__main__":
    main()
