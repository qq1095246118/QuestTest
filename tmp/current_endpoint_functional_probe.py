#!/usr/bin/env python3
"""Run a bounded, MCP-only functional probe against the configured Factor Data endpoint.

The probe discovers all identifiers from the endpoint itself.  It deliberately
does not compare the endpoint with the local test database because the two
configured environments may have different catalog snapshots.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tmp import catalog_deep_readonly as transport  # noqa: E402


DEFAULT_URL = "https://factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "MCP_TOKEN"
KNOWN_BLOCKING_CODES = {
    "EXPORT_BUDGET_EXCEEDED",
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
    "AUTH_REQUIRED",
    "FORBIDDEN",
}


def error_code(call: dict[str, Any]) -> str | None:
    """Return a business or JSON-RPC error code from one normalized call."""

    business = call.get("business")
    if isinstance(business, dict) and isinstance(business.get("error"), dict):
        value = business["error"].get("code")
        if value is not None:
            return str(value)
    envelope = call.get("envelope")
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        value = envelope["error"].get("code")
        if value is not None:
            return str(value)
    return None


def data(call: dict[str, Any]) -> dict[str, Any]:
    """Extract the structured data object from a call."""

    if not isinstance(call, dict):
        return {}
    value = call.get("business")
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        return {}
    return value["data"]


def items(call: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """Extract object rows from a response using common endpoint containers."""

    source = data(call)
    for key in keys or ("items", "top_items", "bottom_items", "symbols", "tags", "results"):
        value = source.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def meta(call: dict[str, Any]) -> dict[str, Any]:
    """Extract response metadata."""

    value = call.get("business")
    return value.get("meta", {}) if isinstance(value, dict) and isinstance(value.get("meta"), dict) else {}


def successful(call: dict[str, Any]) -> bool:
    """Return whether a call has a successful MCP business result."""

    return call.get("http_status") == 200 and call.get("is_error") is False and error_code(call) is None


def blocked(call: dict[str, Any]) -> bool:
    """Return whether a call was stopped by environment or quota state."""

    return error_code(call) in KNOWN_BLOCKING_CODES


def parse_decimal(value: Any) -> Decimal | None:
    """Convert a scalar to Decimal without turning null or booleans into numbers."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO timestamp and normalize it to UTC."""

    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def record(
    cases: list[dict[str, Any]],
    case_id: str,
    title: str,
    passed: bool,
    expected: str,
    actual: Any,
    call: dict[str, Any] | None = None,
    *,
    blocked_reason: str | None = None,
) -> None:
    """Append a sanitized case verdict."""

    status = "BLOCKED" if blocked_reason else "PASS" if passed else "FAIL"
    cases.append(
        {
            "case_id": case_id,
            "title": title,
            "status": status,
            "expected": expected,
            "actual": actual,
            "error_code": error_code(call) if call else None,
            "http_status": call.get("http_status") if call else None,
            "blocking_reason": blocked_reason,
        }
    )


def scope_arguments(scope: dict[str, Any], as_of: str, *, validity: str | None = "valid") -> dict[str, Any]:
    """Build a complete dimension-specific metric scope from discovery output."""

    args = {
        "kind": scope.get("kind", "sub_factor"),
        "ic_scope": scope["ic_scope"],
        "validity_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": scope["forward_return_bars"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "scoring_version": scope["scoring_version"],
        "symbol": scope.get("symbol") or "",
        "as_of": as_of,
        "limit": 5,
    }
    if validity is not None:
        args["validity"] = validity
    return args


def metric_arguments(scope: dict[str, Any], factor_ref: str, as_of: str, *, run_id: str | None = None) -> dict[str, Any]:
    """Build an exact factor_get_metrics/validity scope request."""

    args = scope_arguments(scope, as_of, validity=None)
    args.pop("kind", None)
    args.pop("validity_scope", None)
    args.pop("limit", None)
    args["factor_ref"] = factor_ref
    if run_id is not None:
        args["run_id"] = run_id
    return args


def main() -> None:
    """Execute the read-only probe and write raw artifacts plus a verdict report."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("FACTOR4_MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} or FACTOR4_MCP_TOKEN is required")
    url = os.environ.get("MCP_URL", DEFAULT_URL)
    transport.MCP_URL = url
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-current-endpoint-functional"
    output.mkdir(parents=True, exist_ok=False)
    runner = transport.Runner(token, output, None)
    cases: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    as_of = now.isoformat()

    init = runner.request(
        "INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-current-endpoint-functional", "version": "1.0"},
        },
    )
    runner.protocol_version = ((init.get("envelope") or {}).get("result") or {}).get("protocolVersion")
    runner.notify_initialized("NOTIFY")
    tools_call = runner.request("TOOLS", "tools/list", {})
    tool_rows = (((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or [])
    tool_names = {row.get("name") for row in tool_rows if isinstance(row, dict)}
    required = {
        "factor_search",
        "factor_catalog_stats",
        "factor_list_metric_scopes",
        "factor_get_detail",
        "factor_get_formula",
        "factor_get_metrics",
        "factor_get_validity",
        "factor_get_metric_slices",
        "factor_rank",
        "kb_factor_candidate_search",
        "universe_list_symbols",
        "schema_get_factor_fields",
        "schema_get_raw_data",
    }
    ready = init.get("http_status") == 200 and runner.protocol_version == "2025-06-18" and required <= tool_names
    record(cases, "MCP-READY", "protocol negotiation and required tools", ready, "all required tools are listed", sorted(tool_names & required), tools_call)

    def call(case_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Invoke one tool through the shared sanitized Runner."""

        return runner.tool(case_id, tool, args)

    # Library pagination and filter self-consistency.  No external DB oracle is used.
    library_base = {"kind": "sub_factor", "library_status": "new", "library_coin_category": "custom"}
    stats = call("CAT-STATS", "factor_catalog_stats", library_base)
    first = call("CAT-PAGE-1", "factor_search", {**library_base, "limit": 10})
    all_rows = items(first)
    page_calls = [first]
    cursor = meta(first).get("next_cursor")
    second_args: dict[str, Any] | None = None
    second_refs: list[str] = []
    seen_cursors: set[str] = set()
    for page_number in range(2, 101):
        if not cursor or str(cursor) in seen_cursors:
            break
        seen_cursors.add(str(cursor))
        page_args = {**library_base, "limit": 10, "cursor": cursor}
        if page_number == 2:
            second_args = page_args
        page = call(f"CAT-PAGE-{page_number}", "factor_search", page_args)
        page_calls.append(page)
        page_rows = items(page)
        if page_number == 2:
            second_refs = [str(row.get("factor_ref")) for row in page_rows]
        if not successful(page):
            break
        all_rows.extend(page_rows)
        cursor = meta(page).get("next_cursor")
    refs = [str(row.get("factor_ref")) for row in all_rows]
    stats_total = data(stats).get("total")
    page_ok = (
        successful(stats)
        and all(successful(row) for row in page_calls)
        and not cursor
        and isinstance(stats_total, int)
        and stats_total == len(refs)
        and len(refs) == len(set(refs))
        and all(
            row.get("kind") == "sub_factor"
            and row.get("library_status") == "new"
            and "custom" in (row.get("library_coin_categories") or [])
            and row.get("factor_ref") == f"sub_factor:{row.get('id')}"
            for row in all_rows
        )
    )
    record(cases, "CAT-PAGINATION", "library search pages are complete and self-consistent", page_ok, "stats total equals unique filtered rows", {"stats_total": stats_total, "returned": len(refs), "pages": len(page_calls)}, first, blocked_reason=(f"{error_code(first)}" if blocked(first) else None))
    repeat = call("CAT-PAGE-REPEAT", "factor_search", {**library_base, "limit": 10})
    record(cases, "CAT-STABLE", "replaying the first catalog page is deterministic", successful(repeat) and [r.get("factor_ref") for r in items(repeat)] == [r.get("factor_ref") for r in items(first)], "same ordered refs", [r.get("factor_ref") for r in items(repeat)], repeat, blocked_reason=(error_code(repeat) if blocked(repeat) else None))
    if second_args is not None:
        replay = call("CAT-CURSOR-REPLAY", "factor_search", second_args)
        record(cases, "CAT-CURSOR-REPLAY", "replaying a cursor returns the same continuation", successful(replay) and [r.get("factor_ref") for r in items(replay)] == second_refs, "same continuation refs", [r.get("factor_ref") for r in items(replay)], replay, blocked_reason=(error_code(replay) if blocked(replay) else None))
    else:
        record(cases, "CAT-CURSOR-REPLAY", "cursor continuation is available", False, "at least two pages", "single page", first, blocked_reason="no second page in dynamic subset")

    seed = all_rows[0] if all_rows else None
    if seed is None:
        fallback = call("CAT-FALLBACK", "factor_search", {"kind": "sub_factor", "limit": 5})
        seed = items(fallback)[0] if items(fallback) else None
    if seed:
        seed_ref = seed.get("factor_ref")
        # Query, theme, tag, data source and Chinese display-name filters.
        filter_cases: list[tuple[str, dict[str, Any], str]] = [("CAT-QUERY", {"query": seed.get("name"), "limit": 10}, "query resolves the selected name")]
        themes = seed.get("themes") or []
        tags = seed.get("tags") or []
        if themes:
            filter_cases.append(("CAT-THEME", {"query": seed.get("name"), "theme": themes[0], "limit": 10}, "theme filter contains the selected factor"))
        if tags:
            filter_cases.append(("CAT-TAG", {"query": seed.get("name"), "tags": [tags[0]], "limit": 10}, "tag filter contains the selected factor"))
        if seed.get("data_source"):
            filter_cases.append(("CAT-SOURCE", {"query": seed.get("name"), "data_source": seed["data_source"], "limit": 10}, "data source filter contains the selected factor"))
        if seed.get("cn_name"):
            filter_cases.append(("CAT-CN", {"query": seed["cn_name"], "limit": 10}, "Chinese name filter contains the selected factor"))
        for case_id, args, title in filter_cases:
            c = call(case_id, "factor_search", args)
            found = [row for row in items(c) if row.get("factor_ref") == seed_ref]
            record(cases, case_id, title, successful(c) and len(found) == 1, "selected factor is returned", [row.get("factor_ref") for row in items(c)], c, blocked_reason=(error_code(c) if blocked(c) else None))
        impossible = call("CAT-EMPTY", "factor_search", {"query": f"__questtest_missing_{uuid4().hex}__", "limit": 5})
        record(cases, "CAT-EMPTY", "non-matching catalog query is a terminal empty page", successful(impossible) and not items(impossible) and not meta(impossible).get("next_cursor"), "success with no rows and no cursor", {"count": len(items(impossible)), "meta": meta(impossible)}, impossible, blocked_reason=(error_code(impossible) if blocked(impossible) else None))
        updated = parse_time(seed.get("updated_at"))
        if updated:
            threshold = (updated - timedelta(microseconds=1)).isoformat()
            after = call("CAT-UPDATED-AFTER", "factor_search", {"updated_after": threshold, "limit": 100})
            rows = items(after)
            parsed = [parse_time(row.get("updated_at")) for row in rows]
            updated_ok = successful(after) and all(value is not None and value > parse_time(threshold) for value in parsed) and all(parsed[i] >= parsed[i + 1] for i in range(len(parsed) - 1))
            record(cases, "CAT-UPDATED-AFTER", "updated_after is strict and results are newest first", updated_ok, "every timestamp is strictly after threshold and ordered", {"threshold": threshold, "count": len(rows), "first": rows[0].get("updated_at") if rows else None}, after, blocked_reason=(error_code(after) if blocked(after) else None))

    # Detail and batch identity checks.
    parent_search = call("PARENT-SEED", "factor_search", {"kind": "factor", "limit": 3})
    child_search = call("CHILD-SEED", "factor_search", {"kind": "sub_factor", "limit": 3})
    refs_to_check = [row.get("factor_ref") for row in (items(parent_search)[:1] + items(child_search)[:1]) if row.get("factor_ref")]
    for ref in refs_to_check:
        level_results: dict[str, Any] = {}
        for level in ("summary", "definition", "executable"):
            detail = call(f"DETAIL-{str(ref).replace(':', '-')}-{level}", "factor_get_detail", {"factor_ref": ref, "detail_level": level})
            d = data(detail)
            level_results[level] = {"success": successful(detail), "factor_ref": d.get("factor_ref"), "kind": d.get("kind"), "id": d.get("id"), "name": d.get("name")}
        identities = {(v.get("factor_ref"), v.get("kind"), v.get("id"), v.get("name")) for v in level_results.values() if v.get("success")}
        detail_ok = bool(identities) and len(identities) == 1
        record(cases, f"DETAIL-IDENTITY-{str(ref).replace(':', '-')}", "detail levels preserve one factor identity", detail_ok, "all successful levels have the same identity", level_results)
    if refs_to_check:
        missing = f"sub_factor:{9_000_000_000 + int(now.timestamp())}"
        batch = call("DETAIL-BATCH-PARTIAL", "factor_get_details_batch", {"factor_refs": [*refs_to_check, refs_to_check[0], missing], "detail_level": "summary"})
        batch_rows = items(batch)
        returned_refs = [row.get("factor_ref") for row in batch_rows]
        batch_ok = successful(batch) and all(ref in returned_refs for ref in refs_to_check) and missing in returned_refs
        record(cases, "DETAIL-BATCH-PARTIAL", "details batch retains valid rows and reports missing row", batch_ok, "valid refs and one per-item missing result", [{"factor_ref": row.get("factor_ref"), "success": row.get("success"), "error": row.get("error")} for row in batch_rows], batch, blocked_reason=(error_code(batch) if blocked(batch) else None))

    # Discover a dimension scope and exercise metric, validity, formula, slices and rank.
    scopes_call = call("SCOPE-DISCOVERY", "factor_list_metric_scopes", {"as_of": as_of, "kind": "sub_factor", "ic_scope": "cross_sectional", "limit": 20})
    scopes = [row for row in items(scopes_call) if (row.get("symbol") or "") == "" and int(row.get("available_factor_count") or 0) > 0]
    scopes.sort(key=lambda row: int(row.get("available_factor_count") or 0))
    selected_scope: dict[str, Any] | None = None
    metric_search: dict[str, Any] | None = None
    for index, scope in enumerate(scopes[:5]):
        candidate = call(f"METRIC-SEARCH-{index}", "factor_search", scope_arguments(scope, as_of))
        if successful(candidate) and items(candidate):
            selected_scope, metric_search = scope, candidate
            break
    if selected_scope is None or metric_search is None:
        record(cases, "METRIC-SURFACE", "a discovered metric scope supports read tools", False, "one successful metric search", {"scope_count": len(scopes)}, scopes_call, blocked_reason=(error_code(scopes_call) if blocked(scopes_call) else "no usable scope/search result"))
    else:
        record(cases, "METRIC-SURFACE", "a discovered metric scope supports read tools", True, "one successful metric search", {"scope": {k: selected_scope.get(k) for k in ("ic_scope", "factor_window_bars", "universe_key", "window_scope", "scoring_version")}, "count": len(items(metric_search))}, metric_search)
        metric_item = items(metric_search)[0]
        ref = str(metric_item["factor_ref"])
        run_id = metric_item.get("metric_run_id") or metric_item.get("validity_run_id")
        exact = metric_arguments(selected_scope, ref, as_of)
        metrics = call("METRICS-OMITTED-RUN", "factor_get_metrics", exact)
        md = data(metrics)
        metric_rows = [row for row in (md.get("ic_summaries") or []) if isinstance(row, dict)]
        metric_ok = successful(metrics) and md.get("factor_ref") == ref and all(row.get("factor_id") == int(ref.split(":", 1)[1]) for row in metric_rows)
        record(cases, "METRICS-OMITTED-RUN", "metrics read resolves the selected factor and scope", metric_ok, "successful response identifies selected factor", {"factor_ref": md.get("factor_ref"), "run_id": (md.get("run") or {}).get("run_id")}, metrics, blocked_reason=(error_code(metrics) if blocked(metrics) else None))
        if run_id:
            explicit = call("METRICS-EXPLICIT-RUN", "factor_get_metrics", {**exact, "run_id": run_id})
            ed = data(explicit)
            record(cases, "METRICS-EXPLICIT-RUN", "explicit run pin is honored", successful(explicit) and (ed.get("run") or {}).get("run_id") == run_id, "returned run_id equals requested run_id", (ed.get("run") or {}).get("run_id"), explicit, blocked_reason=(error_code(explicit) if blocked(explicit) else None))
        validity_args = dict(exact)
        validity_args.pop("ic_scope", None)
        validity_args["validity_scope"] = selected_scope["ic_scope"]
        validity = call("VALIDITY-SELECTED", "factor_get_validity", validity_args)
        vd = data(validity)
        vitem = vd.get("item") if isinstance(vd.get("item"), dict) else {}
        record(cases, "VALIDITY-SELECTED", "validity read preserves factor and scope identity", successful(validity) and vd.get("factor_ref") == ref and vitem.get("factor_id") == int(ref.split(":", 1)[1]), "successful validity item for selected factor", {"factor_ref": vd.get("factor_ref"), "item_id": vitem.get("id"), "status": vitem.get("validity_status")}, validity, blocked_reason=(error_code(validity) if blocked(validity) else None))
        summary_rows = [row for row in (md.get("ic_summaries") or []) if isinstance(row, dict)]
        start_time = next((row.get("period_start") for row in summary_rows if row.get("period_start")), None)
        end_time = next((row.get("period_end") for row in summary_rows if row.get("period_end")), None)
        if run_id and start_time and end_time:
            slice_request = {
                **exact,
                "ic_scope": selected_scope["ic_scope"],
                "run_id": run_id,
                "start_time": start_time,
                "end_time": end_time,
                "limit": 5,
            }
            slices = call("SLICES-SELECTED", "factor_get_metric_slices", slice_request)
        else:
            slices = None
        slice_rows = items(slices)
        if slices is None:
            record(cases, "SLICES-SELECTED", "slice read has a usable period identity", False, "metrics expose period_start and period_end", {"period_start": start_time, "period_end": end_time}, None, blocked_reason="selected summary did not expose a slice period")
        else:
            slice_identity_ok = successful(slices) and all(row.get("factor_ref", ref) == ref and row.get("run_id") == (run_id or row.get("run_id")) for row in slice_rows)
            record(cases, "SLICES-SELECTED", "slice read is bounded and stays on the selected factor/run", slice_identity_ok, "all returned slices have selected identity", {"count": len(slice_rows), "run_ids": sorted({row.get("run_id") for row in slice_rows})}, slices, blocked_reason=(error_code(slices) if blocked(slices) or error_code(slices) == "METRIC_SCOPE_NOT_FOUND" else None))
        if run_id:
            formula_args = {"factor_ref": ref, "run_id": run_id, "calculation_mode": selected_scope["calculation_mode"], "interval": selected_scope["factor_bar_interval"], "factor_window_bars": selected_scope["factor_window_bars"], "return_bar_interval": selected_scope["return_bar_interval"], "forward_return_bars": selected_scope["forward_return_bars"]}
            formula = call("FORMULA-SELECTED", "factor_get_formula", formula_args)
            fd = data(formula)
            record(cases, "FORMULA-SELECTED", "formula evidence is available for the selected run", successful(formula) and fd.get("factor_ref", ref) == ref and bool(fd.get("formula_hash") or fd.get("expression")), "factor identity plus formula evidence", {"factor_ref": fd.get("factor_ref"), "has_hash": bool(fd.get("formula_hash")), "has_expression": bool(fd.get("expression"))}, formula, blocked_reason=(error_code(formula) if blocked(formula) else None))
        rank_args = {**scope_arguments(selected_scope, as_of, validity=None), "metric": "mean_ic", "top_k": 3, "bottom_k": 3, "ranking_mode": "signed", "min_coverage_mean": 0, "min_valid_slice_count": 0, "require_oos": False}
        rank_args["validity_scope"] = selected_scope["ic_scope"]
        rank_args.pop("limit", None)
        rank = call("RANK-SELECTED", "factor_rank", rank_args)
        top = items(rank, "top_items")
        bottom = items(rank, "bottom_items")
        top_values = [parse_decimal(row.get("ranking_value")) for row in top]
        bottom_values = [parse_decimal(row.get("ranking_value")) for row in bottom]
        rank_ok = successful(rank) and bool(top or bottom) and all(v is not None for v in top_values + bottom_values) and top_values == sorted(top_values, reverse=True) and bottom_values == sorted(bottom_values) and len({row.get("metric_id") for row in top + bottom}) == len(top + bottom)
        rank_warnings = meta(rank).get("warnings") or []
        rank_has_no_direction = not (top or bottom) and "DIRECTION_CANDIDATES_UNRESOLVED" in rank_warnings
        record(cases, "RANK-SELECTED", "rank output is ordered, unique and bounded", rank_ok, "top desc, bottom asc, no duplicate metric ids", {"top": len(top), "bottom": len(bottom), "top_values": [str(v) for v in top_values], "bottom_values": [str(v) for v in bottom_values], "warnings": rank_warnings}, rank, blocked_reason=(error_code(rank) if blocked(rank) else ("direction unresolved for signed ranking; use diagnostic mode" if rank_has_no_direction else None)))
        batch_refs = [ref]
        if len(items(metric_search)) > 1:
            batch_refs.append(str(items(metric_search)[1].get("factor_ref")))
        batch_refs.append(f"sub_factor:{9_000_000_001 + int(now.timestamp())}")
        batch_base = dict(exact)
        batch_base.pop("limit", None)
        batch_base.pop("factor_ref", None)
        metric_batch = call("METRICS-BATCH-PARTIAL", "factor_get_metrics_batch", {**batch_base, "factor_refs": batch_refs, "ic_scope": selected_scope["ic_scope"]})
        record(cases, "METRICS-BATCH-PARTIAL", "metrics batch returns per-item results", successful(metric_batch) and bool(items(metric_batch)), "batch succeeds with item-level rows", [{"factor_ref": row.get("factor_ref"), "success": row.get("success")} for row in items(metric_batch)], metric_batch, blocked_reason=(error_code(metric_batch) if blocked(metric_batch) else None))
        validity_base = dict(exact)
        validity_base.pop("limit", None)
        validity_base.pop("ic_scope", None)
        validity_base.pop("factor_ref", None)
        validity_batch = call("VALIDITY-BATCH-PARTIAL", "factor_get_validity_batch", {**validity_base, "validity_scope": selected_scope["ic_scope"], "factor_refs": batch_refs})
        record(cases, "VALIDITY-BATCH-PARTIAL", "validity batch returns per-item results", successful(validity_batch) and bool(items(validity_batch)), "batch succeeds with item-level rows", [{"factor_ref": row.get("factor_ref"), "success": row.get("success")} for row in items(validity_batch)], validity_batch, blocked_reason=(error_code(validity_batch) if blocked(validity_batch) else None))

    # KB, Universe, schema and environment read surfaces.
    kb = call("KB-SEARCH", "kb_factor_candidate_search", {"query": "factor", "limit": 5})
    kb_rows = items(kb)
    record(cases, "KB-SEARCH", "KB candidate search returns a bounded result", successful(kb) and len(kb_rows) <= 5, "success with at most five rows", {"count": len(kb_rows)}, kb, blocked_reason=(error_code(kb) if blocked(kb) else None))
    if kb_rows and kb_rows[0].get("extraction_id") is not None:
        extraction = call("KB-EXACT", "kb_factor_candidate_search", {"extraction_id": int(kb_rows[0]["extraction_id"])})
        record(cases, "KB-EXACT", "KB exact extraction lookup is stable", successful(extraction) and any(row.get("extraction_id") == kb_rows[0]["extraction_id"] for row in items(extraction)), "selected extraction id is returned", [row.get("extraction_id") for row in items(extraction)], extraction, blocked_reason=(error_code(extraction) if blocked(extraction) else None))
    for universe in ("all", "main", "altcoin"):
        universe_call = call(f"UNIVERSE-{universe}", "universe_list_symbols", {"universe_key": universe, "as_of": as_of})
        universe_rows = items(universe_call)
        symbols = [row.get("symbol") for row in universe_rows]
        record(cases, f"UNIVERSE-{universe}", f"{universe} universe has unique symbols", successful(universe_call) and bool(symbols) and len(symbols) == len(set(symbols)), "nonempty unique symbol list", {"count": len(symbols), "first": symbols[:3]}, universe_call, blocked_reason=(error_code(universe_call) if blocked(universe_call) else None))
    unknown_universe = call("UNIVERSE-UNKNOWN", "universe_list_symbols", {"universe_key": f"__missing_{uuid4().hex}__"})
    record(cases, "UNIVERSE-UNKNOWN", "unknown universe is rejected explicitly", not successful(unknown_universe), "business error rather than fallback", error_code(unknown_universe), unknown_universe)
    fields = call("SCHEMA-FIELDS", "schema_get_factor_fields", {})
    raw_schema = call("SCHEMA-RAW", "schema_get_raw_data", {})
    record(cases, "SCHEMA-FIELDS", "factor field schema is readable", successful(fields) and bool(data(fields)), "nonempty schema response", list(data(fields).keys()), fields, blocked_reason=(error_code(fields) if blocked(fields) else None))
    record(cases, "SCHEMA-RAW", "raw-data schema is readable", successful(raw_schema) and bool(data(raw_schema)), "nonempty raw-data response", list(data(raw_schema).keys()), raw_schema, blocked_reason=(error_code(raw_schema) if blocked(raw_schema) else None))
    daily = call("ENV-DAILY-FACT", "environment_get_daily", {"label_kind": "fact", "limit": 5})
    daily_rows = items(daily)
    dates = [parse_time(row.get("available_at")) or row.get("environment_date") for row in daily_rows]
    record(cases, "ENV-DAILY-FACT", "environment fact read returns bounded rows", successful(daily) and len(daily_rows) <= 5 and all(value is not None for value in dates), "successful bounded dated response", {"count": len(daily_rows), "dates": [str(x) for x in dates]}, daily, blocked_reason=(error_code(daily) if blocked(daily) else None))
    recommendations = call("ENV-RECOMMENDATIONS", "environment_get_recommendations", {"market_scope": "all", "route_profile_key": "default", "as_of": as_of, "limit": 10})
    recommendation_ok = successful(recommendations) or error_code(recommendations) in {"NO_ELIGIBLE_FACTOR", "ACTIVE_PUBLICATION_NOT_FOUND", "NO_RECOMMENDATION"}
    record(cases, "ENV-RECOMMENDATIONS", "environment recommendation has an explicit terminal state", recommendation_ok, "success or documented no-recommendation error", {"error_code": error_code(recommendations), "data_keys": sorted(data(recommendations))}, recommendations, blocked_reason=(error_code(recommendations) if blocked(recommendations) else None))

    result = {
        "run_id": output.name,
        "environment": "endpoint-configured",
        "mcp_url": url,
        "mode": "MCP_ONLY_READ_ONLY",
        "case_counts": dict(sorted(Counter(row["status"] for row in cases).items())),
        "cases": cases,
        "confirmed_failures": [row for row in cases if row["status"] == "FAIL"],
        "blocked": [row for row in cases if row["status"] == "BLOCKED"],
        "tool_count": len(tool_names),
    }
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    lines = [
        "# Current endpoint MCP-only functional probe",
        "",
        f"- URL: `{url}`",
        "- Mode: MCP-only, read-only; local DB was not used as an oracle",
        f"- Counts: `{result['case_counts']}`",
        "",
        "| Case | Status | Expected | Actual |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(f"| {row['case_id']} | {row['status']} | {row['expected']} | {json.dumps(row['actual'], ensure_ascii=False, default=str)[:500]} |" for row in cases)
    lines.extend(["", "## Confirmed failures", ""])
    if result["confirmed_failures"]:
        lines.extend(f"- `{row['case_id']}`: {row['expected']}; actual={json.dumps(row['actual'], ensure_ascii=False, default=str)}" for row in result["confirmed_failures"])
    else:
        lines.append("No confirmed functional failure in the executable MCP-only coverage.")
    lines.extend(["", "Raw numbered request/response files are generated by the shared Runner and contain no authorization header."])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "case_counts": result["case_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
