#!/usr/bin/env python3
"""Run complementary read-only filter, pagination, batch, and KB checks."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402
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


def _normalize_json(value: Any) -> Any:
    """Decode JSON database cells while leaving already structured values unchanged."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _error_signature(call: dict[str, Any]) -> dict[str, Any]:
    """Return stable transport and business error facts from one MCP call."""
    envelope = call.get("envelope") or {}
    result = envelope.get("result") if isinstance(envelope, dict) else None
    text = None
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = content[0].get("text")
    business = call.get("business")
    error = business.get("error") if isinstance(business, dict) else None
    return {
        "http_status": call.get("http_status"),
        "is_error": call.get("is_error"),
        "error_code": _error_code(call),
        "error_message": error.get("message") if isinstance(error, dict) else text,
        "retryable": error.get("retryable") if isinstance(error, dict) else None,
        "error_text": text,
    }


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
    """Record a binary verdict with one consistent report shape."""
    runner.record(
        case_id,
        title,
        "PASS" if ok else "FAIL",
        pass_reason if ok else fail_reason,
        evidence=evidence,
        failure_class=None if ok else failure_class,
        severity=None if ok else severity,
    )


def _library_seed(runner: Runner) -> dict[str, Any]:
    """Discover a current valid sub-factor with several independently checkable fields."""
    call = runner.tool(
        "FILTER-SEED",
        "factor_search",
        {"kind": "sub_factor", "library_status": "valid", "limit": 50},
    )
    if not _success(call):
        raise RuntimeError(f"Unable to discover catalog seed: {_error_signature(call)}")
    candidates = [
        row
        for row in _items(call)
        if row.get("name")
        and row.get("themes")
        and row.get("tags")
        and row.get("data_source")
        and row.get("factor_bar_interval")
        and row.get("library_coin_categories")
    ]
    if not candidates:
        raise RuntimeError("No catalog row exposes enough fields for combination-filter checks")
    return candidates[0]


def _run_combination_filters(runner: Runner) -> None:
    """Verify intersections, incompatible intersections, and current status/category counts."""
    seed = _library_seed(runner)
    theme = str(seed["themes"][0])
    tag = str(seed["tags"][0])
    category = str(seed["library_coin_categories"][0])
    arguments = {
        "kind": "sub_factor",
        "library_status": "valid",
        "library_coin_category": category,
        "query": str(seed["name"]),
        "theme": theme,
        "tags": [tag],
        "data_source": str(seed["data_source"]),
        "interval": str(seed["factor_bar_interval"]),
        "limit": 50,
    }
    call = runner.tool("FILTER-COMBO-AND", "factor_search", arguments)
    rows = _items(call)

    def matches(row: dict[str, Any]) -> bool:
        return (
            row.get("kind") == "sub_factor"
            and row.get("library_status") == "valid"
            and category in (row.get("library_coin_categories") or [])
            and str(seed["name"]).casefold()
            in json.dumps(row, ensure_ascii=False).casefold()
            and theme in (row.get("themes") or [])
            and tag in (row.get("tags") or [])
            and row.get("data_source") == seed["data_source"]
            and row.get("factor_bar_interval") == seed["factor_bar_interval"]
        )

    combo_ok = (
        _success(call)
        and bool(rows)
        and seed["factor_ref"] in {row.get("factor_ref") for row in rows}
        and all(matches(row) for row in rows)
    )
    _record(
        runner,
        "FILTER-COMBO-AND",
        "library filters use intersection semantics",
        combo_ok,
        "the seed remained visible and every returned row satisfied every filter",
        "one or more combined filters were ignored or combined with incorrect semantics",
        {
            "seed_ref": seed["factor_ref"],
            "arguments": arguments,
            "returned_refs": [row.get("factor_ref") for row in rows],
            "error": _error_signature(call),
        },
        failure_class="FAIL_FILTER_INTERSECTION",
    )

    incompatible = dict(arguments)
    incompatible["theme"] = "__questtest_nonmatching_theme__"
    empty_call = runner.tool("FILTER-COMBO-EMPTY", "factor_search", incompatible)
    empty_ok = _success(empty_call) and not _items(empty_call)
    _record(
        runner,
        "FILTER-COMBO-EMPTY",
        "incompatible filter intersection returns an empty page",
        empty_ok,
        "a mutually incompatible exact-name/theme intersection returned no rows",
        "the service returned rows that cannot satisfy both filters",
        {
            "seed_ref": seed["factor_ref"],
            "requested_theme": incompatible["theme"],
            "returned_refs": [row.get("factor_ref") for row in _items(empty_call)],
            "error": _error_signature(empty_call),
        },
        failure_class="FAIL_FILTER_INTERSECTION",
    )

    status_category_args = {
        "kind": "sub_factor",
        "library_status": "valid",
        "library_coin_category": category,
    }
    stats_call = runner.tool("FILTER-STATUS-CATEGORY-STATS", "factor_catalog_stats", status_category_args)
    search_call = runner.tool(
        "FILTER-STATUS-CATEGORY-SEARCH",
        "factor_search",
        {**status_category_args, "limit": 10},
    )
    db_count_row = runner.db.fetch_one(
        """
        SELECT COUNT(DISTINCT factor_id) AS cnt
        FROM factors_status
        WHERE is_sub_factor_id=1 AND status=2 AND coin_category=%s
        """,
        (category,),
    )
    stats_total = _data(stats_call).get("total")
    search_rows = _items(search_call)
    count_ok = (
        _success(stats_call)
        and _success(search_call)
        and isinstance(stats_total, int)
        and stats_total == int(db_count_row["cnt"])
        and all(
            row.get("kind") == "sub_factor"
            and row.get("library_status") == "valid"
            and category in (row.get("library_coin_categories") or [])
            for row in search_rows
        )
    )
    _record(
        runner,
        "FILTER-STATUS-CATEGORY-DB",
        "status/category combination agrees across stats, search, and DB",
        count_ok,
        "the aggregate count matched DB and every sample row matched the combined scope",
        "aggregate count, search membership, or DB membership disagreed",
        {
            "arguments": status_category_args,
            "stats_total": stats_total,
            "db_count": int(db_count_row["cnt"]),
            "search_refs": [row.get("factor_ref") for row in search_rows],
            "stats_error": _error_signature(stats_call),
            "search_error": _error_signature(search_call),
        },
        failure_class="FAIL_DATA_CONSISTENCY",
    )


def _run_pagination(runner: Runner) -> None:
    """Verify exhaustive bounded pagination and query binding for a small current subset."""
    page_size = 7
    filters = {
        "kind": "factor",
        "library_status": "valid",
        "library_coin_category": "all",
        "limit": page_size,
    }
    stats = runner.tool(
        "PAGE-STATS",
        "factor_catalog_stats",
        {key: value for key, value in filters.items() if key != "limit"},
    )
    expected_total = _data(stats).get("total")
    refs: list[str] = []
    cursor: str | None = None
    calls: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()
    for page_number in range(1, 20):
        arguments = dict(filters)
        if cursor:
            arguments["cursor"] = cursor
        call = runner.tool(f"PAGE-{page_number:02d}", "factor_search", arguments)
        calls.append(call)
        if not _success(call):
            break
        refs.extend(str(row["factor_ref"]) for row in _items(call))
        next_cursor = _meta(call).get("next_cursor")
        if not next_cursor:
            cursor = None
            break
        if next_cursor in seen_cursors:
            cursor = str(next_cursor)
            break
        seen_cursors.add(str(next_cursor))
        cursor = str(next_cursor)
    complete_ok = (
        _success(stats)
        and isinstance(expected_total, int)
        and all(_success(call) for call in calls)
        and cursor is None
        and len(refs) == expected_total
        and len(refs) == len(set(refs))
        and all(len(_items(call)) <= page_size for call in calls)
    )
    _record(
        runner,
        "PAGE-EXHAUSTIVE",
        "catalog pagination exhausts a bounded subset without loss or duplication",
        complete_ok,
        "all rows were returned exactly once and the terminal page cleared its cursor",
        "pagination lost, repeated, overfilled, failed, or never terminated",
        {
            "filters": filters,
            "expected_total": expected_total,
            "page_counts": [len(_items(call)) for call in calls],
            "returned_count": len(refs),
            "unique_count": len(set(refs)),
            "remaining_cursor": bool(cursor),
            "errors": [_error_signature(call) for call in calls if not _success(call)],
        },
        failure_class="FAIL_PAGINATION",
    )

    first = runner.tool("PAGE-LIMIT-SEED", "factor_search", filters)
    source_cursor = _meta(first).get("next_cursor")
    if not source_cursor:
        runner.record(
            "PAGE-LIMIT-BINDING",
            "cursor is bound to the original page size",
            "BLOCKED",
            "the selected subset fit in one page",
            failure_class="BLOCKED_DATA_PRECONDITION",
        )
    else:
        changed_limit = runner.tool(
            "PAGE-LIMIT-BINDING",
            "factor_search",
            {**filters, "limit": page_size + 1, "cursor": source_cursor},
        )
        limit_ok = _rejected(changed_limit)
        _record(
            runner,
            "PAGE-LIMIT-BINDING",
            "cursor is bound to the original page size",
            limit_ok,
            "reusing the cursor with a changed limit was rejected",
            "the cursor was accepted under a different page size",
            {"original_limit": page_size, "changed_limit": page_size + 1, "error": _error_signature(changed_limit)},
            failure_class="FAIL_CURSOR_BINDING",
        )


def _detail_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    """Return the stable identity fields of a successful batch-detail item."""
    data = item.get("data") or {}
    return tuple(data.get(key) for key in ("factor_ref", "id", "kind", "name", "serial_number", "cn_name"))


def _run_batch(runner: Runner) -> None:
    """Verify batch order, duplicate behavior, mixed kinds, and per-item not-found errors."""
    factor_search = runner.tool(
        "BATCH-SEED-FACTOR", "factor_search", {"kind": "factor", "library_status": "valid", "limit": 2}
    )
    child_search = runner.tool(
        "BATCH-SEED-CHILD", "factor_search", {"kind": "sub_factor", "library_status": "valid", "limit": 2}
    )
    factor_ref = _items(factor_search)[0]["factor_ref"]
    child_ref = _items(child_search)[0]["factor_ref"]
    missing_ref = f"sub_factor:{9_000_000_000 + int(datetime.now().timestamp())}"
    requested = [child_ref, factor_ref, child_ref, missing_ref]
    mixed = runner.tool(
        "BATCH-MIXED-DUPLICATE",
        "factor_get_details_batch",
        {"factor_refs": requested, "detail_level": "summary"},
    )
    rows = _items(mixed)
    actual_refs = [row.get("factor_ref") for row in rows]
    expected_success = [True, True, True, False]
    batch_ok = (
        _success(mixed)
        and actual_refs == requested
        and [row.get("success") for row in rows] == expected_success
        and len(rows) == len(requested)
        and _detail_identity(rows[0]) == _detail_identity(rows[2])
        and (rows[3].get("error") or {}).get("code") == "FACTOR_NOT_FOUND"
    )
    _record(
        runner,
        "BATCH-MIXED-DUPLICATE",
        "batch detail preserves order, duplicates, kinds, and per-item failure",
        batch_ok,
        "the response preserved all four input positions and isolated the missing reference",
        "the batch reordered, deduplicated, confused kinds, or lost partial-success semantics",
        {
            "requested_refs": requested,
            "returned_refs": actual_refs,
            "success_flags": [row.get("success") for row in rows],
            "item_error_codes": [(row.get("error") or {}).get("code") for row in rows],
            "error": _error_signature(mixed),
        },
        failure_class="FAIL_PARTIAL_RESULT",
    )

    valid_50 = [f"sub_factor:{row['id']}" for row in runner.db.fetch_all("SELECT id FROM sub_factors ORDER BY id LIMIT 50")]
    max_batch = runner.tool(
        "BATCH-MAX-50",
        "factor_get_details_batch",
        {"factor_refs": valid_50, "detail_level": "summary"},
    )
    max_rows = _items(max_batch)
    max_ok = (
        _success(max_batch)
        and len(max_rows) == 50
        and [row.get("factor_ref") for row in max_rows] == valid_50
        and all(row.get("success") is True for row in max_rows)
    )
    _record(
        runner,
        "BATCH-MAX-50",
        "batch detail accepts and preserves the declared maximum of 50 valid refs",
        max_ok,
        "all 50 requested references succeeded in input order",
        "the declared maximum failed, lost rows, or reordered rows",
        {
            "requested_count": 50,
            "returned_count": len(max_rows),
            "success_count": sum(row.get("success") is True for row in max_rows),
            "error": _error_signature(max_batch),
        },
        failure_class="FAIL_BATCH_BOUNDARY",
    )


def _run_invalid_inputs(runner: Runner) -> None:
    """Verify invalid input classes are rejected and errors are stable on exact replay."""
    cases: list[tuple[str, str, dict[str, Any], str]] = [
        ("ERR-SEARCH-LIMIT-NEG", "factor_search", {"limit": -1}, "schema"),
        ("ERR-SEARCH-LIMIT-FLOAT", "factor_search", {"limit": 1.5}, "schema"),
        ("ERR-SEARCH-TAGS-TYPE", "factor_search", {"tags": "momentum"}, "schema"),
        ("ERR-DETAIL-ZERO", "factor_get_detail", {"factor_ref": "factor:0"}, "business"),
        ("ERR-DETAIL-NEGATIVE", "factor_get_detail", {"factor_ref": "sub_factor:-1"}, "business"),
        ("ERR-BATCH-NONLIST", "factor_get_details_batch", {"factor_refs": "factor:1"}, "schema"),
        ("ERR-BATCH-NONSTRING", "factor_get_details_batch", {"factor_refs": [1]}, "schema"),
        ("ERR-BATCH-LEVEL", "factor_get_details_batch", {"factor_refs": ["factor:1"], "detail_level": "full"}, "schema"),
        ("ERR-KB-ID-ZERO", "kb_factor_candidate_search", {"extraction_id": 0}, "schema"),
        ("ERR-KB-BOTH-MISSING", "kb_factor_candidate_search", {"validation_status": "verified"}, "business"),
        ("ERR-KB-CONFIDENCE-NEG", "kb_factor_candidate_search", {"query": "btc", "min_confidence": -0.01}, "schema"),
        ("ERR-UNIVERSE-BLANK", "universe_list_symbols", {"universe_key": ""}, "business"),
        ("ERR-UNIVERSE-NULL", "universe_list_symbols", {"universe_key": None}, "schema"),
    ]
    for case_id, tool, arguments, error_layer in cases:
        first = runner.tool(f"{case_id}-1", tool, arguments)
        second = runner.tool(f"{case_id}-2", tool, arguments)
        first_signature = _error_signature(first)
        second_signature = _error_signature(second)
        rejected = _rejected(first) and _rejected(second)
        if error_layer == "schema":
            stable = (
                first_signature["http_status"] == second_signature["http_status"]
                and first_signature["is_error"] is True
                and second_signature["is_error"] is True
                and first_signature["error_text"] == second_signature["error_text"]
            )
        else:
            stable = (
                first_signature["http_status"] == second_signature["http_status"]
                and first_signature["is_error"] is True
                and second_signature["is_error"] is True
                and first_signature["error_code"] == second_signature["error_code"]
                and first_signature["error_message"] == second_signature["error_message"]
                and first_signature["retryable"] == second_signature["retryable"]
            )
        ok = rejected and stable
        _record(
            runner,
            case_id,
            f"{tool} rejects and stably classifies invalid input",
            ok,
            "two identical invalid calls produced the same rejection class",
            "the invalid input was accepted or its error classification changed on replay",
            {
                "tool": tool,
                "arguments": arguments,
                "expected_error_layer": error_layer,
                "attempts": [first_signature, second_signature],
            },
            failure_class="FAIL_ERROR_STABILITY",
        )


def _latest_task_for_extraction(runner: Runner, extraction_id: int) -> dict[str, Any] | None:
    """Read the same latest/active mining task selection used by the MCP view."""
    return runner.db.fetch_one(
        """
        SELECT id, status, lease_until, attempt_count, max_attempts, next_retry_at,
               pipeline_run_id, result_sub_factor_id, result_validity,
               last_error_stage, last_error_class, last_error_code,
               last_error_message, retryable
        FROM kb_factor_mining_tasks
        WHERE extraction_id=%s
        ORDER BY CASE WHEN status IN ('claimed','running') THEN 0 ELSE 1 END,
                 updated_at DESC, id DESC
        LIMIT 1
        """,
        (extraction_id,),
    )


def _task_matches(item: dict[str, Any], task: dict[str, Any] | None) -> bool:
    """Compare MCP mining-task projection with its authoritative DB row."""
    if task is None:
        return all(
            item.get(key) is None
            for key in (
                "task_id",
                "task_status",
                "active_task_id",
                "pipeline_run_id",
                "result_sub_factor_id",
                "result_validity",
            )
        )
    lease_until = task.get("lease_until")
    lease_active = (
        isinstance(lease_until, datetime)
        and lease_until > datetime.now(timezone.utc).replace(tzinfo=None)
    )
    active_id = task["id"] if task["status"] in {"claimed", "running"} and lease_active else None
    return all(
        (
            item.get("task_id") == task["id"],
            item.get("task_status") == task["status"],
            item.get("active_task_id") == active_id,
            item.get("attempt_count") == task["attempt_count"],
            item.get("task_max_attempts") == task["max_attempts"],
            item.get("pipeline_run_id") == task["pipeline_run_id"],
            item.get("result_sub_factor_id") == task["result_sub_factor_id"],
            item.get("result_validity") == task["result_validity"],
            item.get("last_error_stage") == task["last_error_stage"],
            item.get("last_error_class") == task["last_error_class"],
            item.get("last_error_code") == task["last_error_code"],
            item.get("last_error_message") == task["last_error_message"],
            bool(item.get("retryable")) == bool(task["retryable"]),
        )
    )


def _extraction_matches(item: dict[str, Any], row: dict[str, Any]) -> bool:
    """Compare MCP candidate and mapping fields with the extraction DB row."""
    return all(
        (
            item.get("factor_name") == row["factor_name"],
            item.get("validation_status") == row["validation_status"],
            item.get("mapping_status") == row["mapping_status"],
            Decimal(str(item["confidence_score"])) == Decimal(str(row["confidence_score"]))
            if item.get("confidence_score") is not None and row.get("confidence_score") is not None
            else item.get("confidence_score") is None and row.get("confidence_score") is None,
            item.get("target_asset_class") == _normalize_json(row["target_asset_class"]),
            item.get("mapped_factor_id") == row["mapped_factor_id"],
            item.get("is_sub_factor_id") == row["is_sub_factor_id"],
            item.get("result_sub_factor_id") == row["pipeline_sub_factor_id"]
            if row.get("pipeline_sub_factor_id") is not None
            else True,
        )
    )


def _run_kb(runner: Runner) -> None:
    """Verify selectors, combined filters, task projection, and mapped factor identity."""
    mapped_seed = runner.db.fetch_one(
        """
        SELECT id, factor_name, validation_status, mapping_status, confidence_score,
               target_asset_class, mapped_factor_id, is_sub_factor_id, pipeline_sub_factor_id
        FROM kb_factor_extractions
        WHERE mapping_status='mapped' AND mapped_factor_id IS NOT NULL
          AND factor_name IS NOT NULL AND factor_name<>''
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """
    )
    if mapped_seed is None:
        runner.record(
            "KB-MAPPED-SEED",
            "mapped KB candidate is available",
            "BLOCKED",
            "no mapped KB candidate exists",
            failure_class="BLOCKED_DATA_PRECONDITION",
        )
        return

    exact_args = {
        "extraction_id": int(mapped_seed["id"]),
        "query": str(mapped_seed["factor_name"]),
        "validation_status": mapped_seed["validation_status"],
        "mapping_status": mapped_seed["mapping_status"],
        "min_confidence": float(mapped_seed["confidence_score"]),
        "target_asset_class": str(_normalize_json(mapped_seed["target_asset_class"])[0]),
        "limit": 10,
    }
    exact_call = runner.tool("KB-COMBO-MATCH", "kb_factor_candidate_search", exact_args)
    exact_items = _items(exact_call)
    exact_ok = (
        _success(exact_call)
        and len(exact_items) == 1
        and exact_items[0].get("extraction_id") == mapped_seed["id"]
        and _extraction_matches(exact_items[0], mapped_seed)
    )
    _record(
        runner,
        "KB-COMBO-MATCH",
        "KB query, extraction ID, and all candidate filters intersect correctly",
        exact_ok,
        "the exact mapped candidate survived all matching selectors and matched DB",
        "matching selectors lost the candidate or returned a candidate inconsistent with DB",
        {
            "arguments": exact_args,
            "returned_ids": [item.get("extraction_id") for item in exact_items],
            "error": _error_signature(exact_call),
        },
        failure_class="FAIL_KB_FILTER_OR_MAPPING",
    )

    mismatch_call = runner.tool(
        "KB-COMBO-MISMATCH",
        "kb_factor_candidate_search",
        {"extraction_id": int(mapped_seed["id"]), "query": f"no-match-{uuid4()}", "limit": 10},
    )
    mismatch_ok = _success(mismatch_call) and not _items(mismatch_call)
    _record(
        runner,
        "KB-COMBO-MISMATCH",
        "KB extraction ID and query use intersection semantics",
        mismatch_ok,
        "a deliberately mismatched query excluded the exact extraction",
        "the extraction ID bypassed the conflicting query filter",
        {
            "extraction_id": mapped_seed["id"],
            "returned_ids": [item.get("extraction_id") for item in _items(mismatch_call)],
            "error": _error_signature(mismatch_call),
        },
        failure_class="FAIL_FILTER_INTERSECTION",
    )

    task_seeds = runner.db.fetch_all(
        """
        SELECT e.id, e.factor_name, t.status
        FROM kb_factor_extractions e
        JOIN kb_factor_mining_tasks t ON t.extraction_id=e.id
        WHERE t.id=(
          SELECT t2.id FROM kb_factor_mining_tasks t2
          WHERE t2.extraction_id=e.id
          ORDER BY CASE WHEN t2.status IN ('claimed','running') THEN 0 ELSE 1 END,
                   t2.updated_at DESC, t2.id DESC
          LIMIT 1
        )
        ORDER BY FIELD(t.status, 'running', 'claimed', 'completed', 'failed', 'cancelled'), e.id DESC
        """
    )
    selected: list[dict[str, Any]] = []
    seen_statuses: set[str] = set()
    for row in task_seeds:
        if row["status"] not in seen_statuses:
            selected.append(row)
            seen_statuses.add(str(row["status"]))
        if len(selected) >= 5:
            break
    no_task_seed = runner.db.fetch_one(
        """
        SELECT e.id, e.factor_name
        FROM kb_factor_extractions e
        LEFT JOIN kb_factor_mining_tasks t ON t.extraction_id=e.id
        WHERE t.id IS NULL AND e.factor_name IS NOT NULL AND e.factor_name<>''
        ORDER BY e.updated_at DESC, e.id DESC
        LIMIT 1
        """
    )
    if no_task_seed:
        selected.append({**no_task_seed, "status": None})

    task_mismatches: list[dict[str, Any]] = []
    for index, seed in enumerate(selected, 1):
        call = runner.tool(
            f"KB-TASK-{index}",
            "kb_factor_candidate_search",
            {"extraction_id": int(seed["id"]), "limit": 1},
        )
        items = _items(call)
        task = _latest_task_for_extraction(runner, int(seed["id"]))
        if not (
            _success(call)
            and len(items) == 1
            and _task_matches(items[0], task)
        ):
            task_mismatches.append(
                {
                    "extraction_id": seed["id"],
                    "expected_task_id": task.get("id") if task else None,
                    "expected_status": task.get("status") if task else None,
                    "mcp_task_id": items[0].get("task_id") if items else None,
                    "mcp_status": items[0].get("task_status") if items else None,
                    "error": _error_signature(call),
                }
            )
    tasks_ok = bool(selected) and not task_mismatches
    _record(
        runner,
        "KB-TASK-DB",
        "KB candidates expose their current mining-task state exactly as stored",
        tasks_ok,
        "all discovered running/completed/failed/cancelled/no-task projections matched DB",
        "at least one current mining-task projection differed from DB",
        {
            "tested": [
                {"extraction_id": row["id"], "discovered_status": row["status"]}
                for row in selected
            ],
            "mismatches": task_mismatches,
        },
        failure_class="FAIL_DATA_CONSISTENCY",
    )

    item = exact_items[0] if exact_items else {}
    mapped_id = mapped_seed["mapped_factor_id"]
    mapped_table = "sub_factors" if mapped_seed["is_sub_factor_id"] else "factors"
    name_column = "sub_factor_name" if mapped_seed["is_sub_factor_id"] else "factor_name"
    mapped_row = runner.db.fetch_one(
        f"SELECT id, {name_column} AS name, serial_number FROM {mapped_table} WHERE id=%s",
        (mapped_id,),
    )
    mapped_ref = f"sub_factor:{mapped_id}" if mapped_seed["is_sub_factor_id"] else f"factor:{mapped_id}"
    detail = runner.tool("KB-MAPPED-DETAIL", "factor_get_detail", {"factor_ref": mapped_ref, "detail_level": "summary"})
    detail_data = _data(detail)
    mapping_ok = (
        bool(mapped_row)
        and item.get("mapped_factor_id") == mapped_id
        and item.get("is_sub_factor_id") == mapped_seed["is_sub_factor_id"]
        and _success(detail)
        and detail_data.get("factor_ref") == mapped_ref
        and detail_data.get("id") == mapped_row["id"]
        and detail_data.get("name") == mapped_row["name"]
        and detail_data.get("serial_number") == mapped_row["serial_number"]
    )
    _record(
        runner,
        "KB-MAPPED-DETAIL",
        "KB mapped-factor result resolves to the same catalog entity and DB row",
        mapping_ok,
        "candidate mapping, DB entity, and MCP detail shared one typed identity",
        "the mapped factor ID was missing, wrong-kind, not resolvable, or inconsistent with DB",
        {
            "extraction_id": mapped_seed["id"],
            "mapping_status": item.get("mapping_status"),
            "mapped_factor_id": mapped_id,
            "mapped_ref": mapped_ref,
            "db_entity_found": bool(mapped_row),
            "detail_identity": {
                key: detail_data.get(key)
                for key in ("factor_ref", "id", "kind", "name", "serial_number")
            },
            "error": _error_signature(detail),
        },
        failure_class="FAIL_KB_MAPPING_IDENTITY",
    )


def _run_read_only_guard(runner: Runner, before: dict[str, Any]) -> None:
    """Confirm the complementary run did not mutate its authoritative data tables."""
    after_rows = runner.db.fetch_all(
        """
        SELECT 'factors' AS entity, COUNT(*) AS row_count, MAX(updated_at) AS max_updated_at FROM factors
        UNION ALL
        SELECT 'sub_factors', COUNT(*), MAX(updated_at) FROM sub_factors
        UNION ALL
        SELECT 'factors_status', COUNT(*), MAX(updated_at) FROM factors_status
        UNION ALL
        SELECT 'kb_factor_extractions', COUNT(*), MAX(updated_at) FROM kb_factor_extractions
        UNION ALL
        SELECT 'kb_factor_mining_tasks', COUNT(*), MAX(updated_at) FROM kb_factor_mining_tasks
        """
    )
    after = {
        str(row["entity"]): {
            "row_count": row["row_count"],
            "max_updated_at": row["max_updated_at"],
        }
        for row in after_rows
    }
    ok = before == after
    _record(
        runner,
        "READONLY-GUARD",
        "R0 catalog and KB calls do not mutate authoritative tables",
        ok,
        "row counts and maximum update timestamps were unchanged",
        "an authoritative table changed during the read-only run",
        {"before": before, "after": after},
        severity="P0",
        failure_class="FAIL_READ_ONLY_SIDE_EFFECT",
    )


def _db_state(runner: Runner) -> dict[str, Any]:
    """Read compact table state for the R0 mutation guard."""
    rows = runner.db.fetch_all(
        """
        SELECT 'factors' AS entity, COUNT(*) AS row_count, MAX(updated_at) AS max_updated_at FROM factors
        UNION ALL
        SELECT 'sub_factors', COUNT(*), MAX(updated_at) FROM sub_factors
        UNION ALL
        SELECT 'factors_status', COUNT(*), MAX(updated_at) FROM factors_status
        UNION ALL
        SELECT 'kb_factor_extractions', COUNT(*), MAX(updated_at) FROM kb_factor_extractions
        UNION ALL
        SELECT 'kb_factor_mining_tasks', COUNT(*), MAX(updated_at) FROM kb_factor_mining_tasks
        """
    )
    return {
        str(row["entity"]): {
            "row_count": row["row_count"],
            "max_updated_at": row["max_updated_at"],
        }
        for row in rows
    }


def _write_report(runner: Runner, run_stamp: str) -> None:
    """Write sanitized machine-readable and Markdown verdict reports."""
    counts = Counter(row["status"] for row in runner.cases)
    summary = {
        "run_id": run_stamp,
        "environment": "test",
        "mcp_url": MCP_URL,
        "read_only": True,
        "excluded": [
            "orphan data",
            "end-time boundary",
            "missing-document references",
            "UX, compatibility, and style-only observations",
            "previously confirmed factor_search time-series timeout",
        ],
        "case_counts": dict(sorted(counts.items())),
        "cases": runner.cases,
        "confirmed_failures": [row for row in runner.cases if row["status"] == "FAIL"],
        "blocked": [row for row in runner.cases if row["status"] == "BLOCKED"],
    }
    _write_json(runner.output_dir / "summary.json", summary)
    _write_json(
        runner.output_dir / "call-ledger.json",
        [
            {
                "case_id": call.get("case_id"),
                "tool": call.get("tool"),
                "arguments": call.get("arguments"),
                "http_status": call.get("http_status"),
                "elapsed_seconds": call.get("elapsed_seconds"),
                "is_error": call.get("is_error"),
                "error_code": _error_code(call),
                "request_id": _meta(call).get("request_id"),
                "trace_id": _meta(call).get("trace_id"),
            }
            for call in runner.calls
        ],
    )
    lines = [
        "# Complementary filter, batch, error, and KB functional regression",
        "",
        f"- Run: `{run_stamp}`",
        "- Environment: test",
        "- Mode: R0 read-only",
        f"- PASS={counts.get('PASS', 0)}, FAIL={counts.get('FAIL', 0)}, BLOCKED={counts.get('BLOCKED', 0)}",
        "- Excluded: orphan data, end-time boundary, missing-document references, UX/compatibility/style-only observations, and the already-confirmed TS search timeout.",
        "",
        "| Case | Status | Result |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| {row['case_id']} | {row['status']} | {row['reason']} |"
        for row in runner.cases
    )
    failures = [row for row in runner.cases if row["status"] == "FAIL"]
    if failures:
        lines.extend(["", "## Confirmed failures", ""])
        for row in failures:
            lines.extend(
                [
                    f"### {row['severity']}: {row['case_id']} - {row['title']}",
                    "",
                    row["reason"],
                    "",
                    "```json",
                    json.dumps(row["evidence"], ensure_ascii=False, indent=2, default=str),
                    "```",
                    "",
                ]
            )
    (runner.output_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Execute all complementary R0 checks and emit a new timestamped report."""
    token = os.environ.get("CATALOG_MCP_TOKEN")
    if not token:
        raise SystemExit("CATALOG_MCP_TOKEN is required")
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = PROJECT_ROOT / "reports" / "factor4-deep" / f"{run_stamp}-filter-error-kb"
    settings = SettingsLoader.load("test", PROJECT_ROOT)
    runner = Runner(token, output_dir, DatabaseClient.from_settings(settings.database))
    init = runner.request(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-filter-error-kb", "version": "1.0"},
        },
    )
    runner.protocol_version = (((init.get("envelope") or {}).get("result") or {}).get("protocolVersion"))
    runner.notify_initialized("MCP-NOTIFY")
    before = _db_state(runner)
    _run_combination_filters(runner)
    _run_pagination(runner)
    _run_batch(runner)
    _run_invalid_inputs(runner)
    _run_kb(runner)
    _run_read_only_guard(runner, before)
    _write_report(runner, run_stamp)
    counts = Counter(row["status"] for row in runner.cases)
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "case_counts": dict(counts),
                "call_count": len(runner.calls),
            }
        )
    )


if __name__ == "__main__":
    main()
