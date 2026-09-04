#!/usr/bin/env python3
"""Run focused expansion cases for the Factor 4.0 catalog deep regression."""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402
from tmp.catalog_deep_readonly import (  # noqa: E402
    Runner,
    _data,
    _error_code,
    _items,
    _meta,
    _rejected,
    _success,
    _write_json,
)


ORIGINAL_DIR = PROJECT_ROOT / "reports" / "factor4-deep" / "20260902T104949Z-catalog"
RECHECK_DIR = PROJECT_ROOT / "reports" / "factor4-deep" / "20260902T105923Z-catalog-recheck"


def _load_one(pattern: str) -> dict[str, Any]:
    matches = glob.glob(pattern)
    if len(matches) != 1:
        raise RuntimeError(f"Expected one match for {pattern}, got {len(matches)}")
    value = json.loads(Path(matches[0]).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {matches[0]}")
    return value


def _business(envelope: dict[str, Any]) -> dict[str, Any]:
    value = (envelope.get("result") or {}).get("structuredContent")
    return value if isinstance(value, dict) else {}


def _total(call: dict[str, Any]) -> int | None:
    value = _data(call).get("total")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _run_status_matrix(runner: Runner) -> None:
    for kind, is_sub in (("factor", 0), ("sub_factor", 1)):
        for status in ("inactive", "new", "valid", "invalid", "deleted"):
            case = f"CAT-STATUS-{kind}-{status}"
            stats = runner.tool(f"{case}-stats", "factor_catalog_stats", {"kind": kind, "library_status": status})
            search = runner.tool(f"{case}-search", "factor_search", {"kind": kind, "library_status": status, "limit": 2})
            items = _items(search)
            status_codes = {item.get("library_status_code") for item in items}
            one_code = next(iter(status_codes)) if len(status_codes) == 1 else None
            db_count = None
            row_identity_ok = False
            category_mismatches: list[dict[str, Any]] = []
            if one_code is not None:
                db_row = runner.db.fetch_one(
                    "SELECT COUNT(DISTINCT factor_id) AS cnt FROM factors_status WHERE is_sub_factor_id=%s AND status=%s",
                    (is_sub, one_code),
                )
                db_count = int(db_row["cnt"]) if db_row else None
                row_identity_ok = True
                for item in items:
                    rows = runner.db.fetch_all(
                        "SELECT coin_category FROM factors_status WHERE is_sub_factor_id=%s AND factor_id=%s AND status=%s ORDER BY coin_category",
                        (is_sub, int(item["id"]), one_code),
                    )
                    actual = sorted(str(row["coin_category"]) for row in rows)
                    returned = sorted(str(value) for value in (item.get("library_coin_categories") or []))
                    if actual != returned:
                        row_identity_ok = False
                        category_mismatches.append({"factor_ref": item.get("factor_ref"), "db": actual, "mcp": returned})
            expected_total = _total(stats)
            nonempty_expected = expected_total is not None and expected_total > 0
            ok = (
                _success(stats)
                and _success(search)
                and expected_total is not None
                and ((not nonempty_expected and not items) or (nonempty_expected and bool(items)))
                and all(item.get("kind") == kind and item.get("library_status") == status for item in items)
                and (not items or (db_count == expected_total and row_identity_ok))
            )
            runner.record(
                case,
                f"catalog/search status matrix: {kind} {status}",
                "PASS" if ok else "FAIL",
                "stats total, search rows, status code, and DB categories agree" if ok else "status filter count or returned identity differs across MCP and DB",
                evidence={"stats_total": expected_total, "search_count": len(items), "status_codes": sorted(x for x in status_codes if x is not None), "db_distinct_count": db_count, "category_mismatches": category_mismatches, "refs": [x.get("factor_ref") for x in items], "stats_error": _error_code(stats), "search_error": _error_code(search)},
                failure_class=None if ok else "FAIL_DATA_CONSISTENCY",
                severity=None if ok else "P1",
            )


def _run_category_matrix(runner: Runner) -> None:
    status_code = 2
    for category in ("all", "main", "altcoin", "custom"):
        case = f"CAT-CATEGORY-{category}"
        arguments = {"kind": "sub_factor", "library_status": "valid", "library_coin_category": category}
        stats = runner.tool(f"{case}-stats", "factor_catalog_stats", arguments)
        search = runner.tool(f"{case}-search", "factor_search", {**arguments, "limit": 2})
        items = _items(search)
        db_row = runner.db.fetch_one(
            "SELECT COUNT(DISTINCT factor_id) AS cnt FROM factors_status WHERE is_sub_factor_id=1 AND status=%s AND coin_category=%s",
            (status_code, category),
        )
        db_count = int(db_row["cnt"])
        expected_total = _total(stats)
        ok = (
            _success(stats)
            and _success(search)
            and expected_total == db_count
            and ((db_count == 0 and not items) or (db_count > 0 and bool(items)))
            and all(category in (item.get("library_coin_categories") or []) for item in items)
        )
        runner.record(
            case,
            f"valid sub-factor coin-category filter: {category}",
            "PASS" if ok else "FAIL",
            "stats/search/DB category membership agree" if ok else "coin-category filter differs across stats, search, and DB",
            evidence={"stats_total": expected_total, "db_count": db_count, "refs": [x.get("factor_ref") for x in items], "returned_categories": [x.get("library_coin_categories") for x in items]},
            failure_class=None if ok else "FAIL_DATA_CONSISTENCY",
            severity=None if ok else "P1",
        )
    mixed = runner.tool(
        "CAT-MODE-ASOF",
        "factor_search",
        {"library_status": "valid", "as_of": datetime.now(timezone.utc).isoformat(), "limit": 2},
    )
    runner.record(
        "CAT-MODE-ASOF",
        "library mode rejects point-in-time metric fields",
        "PASS" if _rejected(mixed) else "FAIL",
        "library_status plus as_of was rejected" if _rejected(mixed) else "library mode silently accepted a metric as_of",
        evidence={"error_code": _error_code(mixed)},
        failure_class=None if _rejected(mixed) else "FAIL_SCOPE_ISOLATION",
        severity=None if _rejected(mixed) else "P1",
    )


def _run_detail_levels_offline(runner: Runner) -> None:
    payloads: dict[str, dict[str, Any]] = {}
    for level in ("summary", "definition", "executable"):
        envelope = _load_one(str(ORIGINAL_DIR / f"*DETAIL-001-{level}.response.json"))
        payloads[level] = _business(envelope).get("data") or {}
    summary_forbidden = {"calc_logic", "formula_summary", "metadata", "params", "data_source_metadata", "calc_function"}
    definition_required = {"calc_logic", "formula_summary", "metadata", "params", "data_source_metadata"}
    executable_required = definition_required | {"calc_function"}
    ok = (
        not summary_forbidden.intersection(payloads["summary"])
        and definition_required <= set(payloads["definition"])
        and "calc_function" not in payloads["definition"]
        and executable_required <= set(payloads["executable"])
    )
    runner.record(
        "DETAIL-LEVEL-SHAPE",
        "detail levels expose progressively bounded definition data",
        "PASS" if ok else "FAIL",
        "summary is bounded, definition adds formula metadata, executable adds code" if ok else "detail level leaked or omitted level-specific data",
        evidence={level: sorted(value) for level, value in payloads.items()},
        failure_class=None if ok else "FAIL_ACCESS_BOUNDARY",
        severity=None if ok else "P1",
    )


def _run_full_parent_pagination(runner: Runner) -> None:
    parent = runner.db.fetch_one(
        """
        SELECT r.factor_id, COUNT(*) AS child_count
        FROM factor_sub_factor_relations r
        GROUP BY r.factor_id HAVING COUNT(*) > 200 AND COUNT(*) <= 400
        ORDER BY COUNT(*), r.factor_id LIMIT 1
        """
    )
    if parent is None:
        runner.record("DETAIL-PARENT-FULL", "full parent pagination at maximum page size", "BLOCKED", "no parent has 201..400 children", failure_class="BLOCKED_DATA_PRECONDITION")
        return
    parent_ref = f"factor:{parent['factor_id']}"
    page1 = runner.tool("DETAIL-PARENT-FULL-1", "factor_get_detail", {"factor_ref": parent_ref, "detail_level": "summary", "children_limit": 200})
    data1 = _data(page1)
    cursor = data1.get("children_next_cursor")
    page2 = runner.tool("DETAIL-PARENT-FULL-2", "factor_get_detail", {"factor_ref": parent_ref, "detail_level": "summary", "children_limit": 200, "children_cursor": cursor}) if cursor else None
    children1 = data1.get("children") or []
    children2 = (_data(page2).get("children") or []) if page2 else []
    refs = [row.get("factor_ref") for row in children1 + children2]
    db_rows = runner.db.fetch_all("SELECT sub_factor_id FROM factor_sub_factor_relations WHERE factor_id=%s", (parent["factor_id"],))
    db_refs = {f"sub_factor:{row['sub_factor_id']}" for row in db_rows}
    ok = (
        _success(page1)
        and bool(cursor)
        and bool(page2 and _success(page2))
        and len(children1) == 200
        and len(refs) == int(parent["child_count"])
        and len(refs) == len(set(refs))
        and set(refs) == db_refs
        and _data(page2).get("children_truncated") is False
        and _data(page2).get("children_next_cursor") is None
    )
    runner.record(
        "DETAIL-PARENT-FULL",
        "full parent children pagination at maximum page size",
        "PASS" if ok else "FAIL",
        "all parent-child relations were returned once across two pages" if ok else "parent pagination omitted, repeated, or mixed child relations",
        evidence={"parent_ref": parent_ref, "db_count": int(parent["child_count"]), "page1_count": len(children1), "page2_count": len(children2), "unique_count": len(set(refs)), "cursor_present": bool(cursor), "missing": sorted(db_refs - set(refs)), "extra": sorted(set(refs) - db_refs)},
        failure_class=None if ok else "FAIL_PAGINATION",
        severity=None if ok else "P1",
    )
    second_parent = runner.db.fetch_one("SELECT factor_id FROM factor_sub_factor_relations WHERE factor_id<>%s GROUP BY factor_id ORDER BY COUNT(*) DESC LIMIT 1", (parent["factor_id"],))
    if cursor and second_parent:
        cross = runner.tool("DETAIL-PARENT-CROSS-CURSOR", "factor_get_detail", {"factor_ref": f"factor:{second_parent['factor_id']}", "detail_level": "summary", "children_limit": 200, "children_cursor": cursor})
        runner.record(
            "DETAIL-PARENT-CROSS-CURSOR",
            "children cursor is bound to its parent",
            "PASS" if _rejected(cross) else "FAIL",
            "cursor replay against another parent was rejected" if _rejected(cross) else "cursor replay returned another parent's data",
            evidence={"source_parent": parent_ref, "target_parent": f"factor:{second_parent['factor_id']}", "error_code": _error_code(cross)},
            failure_class=None if _rejected(cross) else "FAIL_SCOPE_ISOLATION",
            severity=None if _rejected(cross) else "P0",
        )


def _run_overlapping_identity(runner: Runner) -> None:
    row = runner.db.fetch_one("SELECT f.id FROM factors f JOIN sub_factors s ON s.id=f.id ORDER BY f.id LIMIT 1")
    if row is None:
        runner.record("DETAIL-KIND-IDENTITY", "same numeric ID remains kind-scoped", "BLOCKED", "no overlapping factor/sub-factor numeric id", failure_class="BLOCKED_DATA_PRECONDITION")
        return
    factor_ref = f"factor:{row['id']}"
    child_ref = f"sub_factor:{row['id']}"
    parent = runner.tool("DETAIL-KIND-PARENT", "factor_get_detail", {"factor_ref": factor_ref, "detail_level": "summary"})
    child = runner.tool("DETAIL-KIND-CHILD", "factor_get_detail", {"factor_ref": child_ref, "detail_level": "summary"})
    parent_data, child_data = _data(parent), _data(child)
    ok = (
        _success(parent) and _success(child)
        and parent_data.get("factor_ref") == factor_ref and parent_data.get("kind") == "factor"
        and child_data.get("factor_ref") == child_ref and child_data.get("kind") == "sub_factor"
        and (parent_data.get("name"), parent_data.get("serial_number")) != (child_data.get("name"), child_data.get("serial_number"))
    )
    runner.record(
        "DETAIL-KIND-IDENTITY",
        "factor_ref kind disambiguates overlapping numeric IDs",
        "PASS" if ok else "FAIL",
        "factor and sub-factor prefixes resolved distinct authoritative objects" if ok else "numeric ID collision caused kind confusion",
        evidence={"numeric_id": row["id"], "factor": {key: parent_data.get(key) for key in ("factor_ref", "kind", "name", "serial_number")}, "sub_factor": {key: child_data.get(key) for key in ("factor_ref", "kind", "name", "serial_number")}},
        failure_class=None if ok else "FAIL_IDENTITY",
        severity=None if ok else "P1",
    )


def _run_formula_as_of(runner: Runner) -> None:
    envelope = _load_one(str(ORIGINAL_DIR / "*FORMULA-001.response.json"))
    data = _business(envelope)["data"]
    recorded = runner.db.fetch_one(
        "SELECT recorded_at FROM factor_ic_run_formula_evidence WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=%s AND formula_hash=%s",
        (data["run_id"], int(data["factor_ref"].split(":", 1)[1]), 1 if data["factor_ref"].startswith("sub_factor:") else 0, data["formula_hash"]),
    )
    if recorded is None:
        runner.record("FORMULA-ASOF", "formula evidence point-in-time visibility", "BLOCKED", "formula DB evidence disappeared", failure_class="ASYNC_STATE_MOVING")
        return
    base = {
        "factor_ref": data["factor_ref"], "run_id": data["run_id"], "calculation_mode": data["metric_identity"]["calculation_mode"],
        "interval": data["metric_identity"]["factor_bar_interval"], "factor_window_bars": data["metric_identity"]["factor_window_bars"],
        "return_bar_interval": data["metric_identity"]["return_bar_interval"], "forward_return_bars": data["metric_identity"]["forward_return_bars"],
    }
    before = (recorded["recorded_at"] - timedelta(seconds=1)).replace(tzinfo=timezone.utc).isoformat()
    call = runner.tool("FORMULA-ASOF", "factor_get_formula", {**base, "as_of": before})
    ok = _rejected(call) and _error_code(call) == "FORMULA_EVIDENCE_NOT_FOUND"
    runner.record(
        "FORMULA-ASOF",
        "formula evidence is invisible before recorded_at",
        "PASS" if ok else "FAIL",
        "historical query before evidence creation returned not-found" if ok else "formula evidence leaked before its recorded time",
        evidence={"recorded_at": recorded["recorded_at"], "query_as_of": before, "error_code": _error_code(call)},
        failure_class=None if ok else "FAIL_POINT_IN_TIME",
        severity=None if ok else "P0",
    )


def _run_kb_negative_filters(runner: Runner) -> None:
    envelope = _load_one(str(ORIGINAL_DIR / "*KB-001-id.response.json"))
    item = _business(envelope)["data"]["items"][0]
    extraction_id = int(item["extraction_id"])
    opposite_validation = next(value for value in ("pending", "verified", "invalid", "ignored") if value != item["validation_status"])
    opposite_mapping = next(value for value in ("unmapped", "mapped", "new_candidate", "duplicate") if value != item["mapping_status"])
    tests = [
        ("validation_status", opposite_validation),
        ("mapping_status", opposite_mapping),
        ("target_asset_class", "definitely-not-this-asset-class"),
    ]
    if Decimal(str(item["confidence_score"])) < Decimal("1"):
        tests.append(("min_confidence", float((Decimal(str(item["confidence_score"])) + Decimal("1")) / 2)))
    for index, (name, value) in enumerate(tests, 1):
        call = runner.tool(f"KB-NEGATIVE-{index}", "kb_factor_candidate_search", {"extraction_id": extraction_id, name: value, "limit": 10})
        rows = _items(call)
        ok = _success(call) and not rows
        runner.record(
            f"KB-NEGATIVE-{index}",
            f"KB exact lookup applies non-matching {name} filter",
            "PASS" if ok else "FAIL",
            "non-matching filter excluded the exact extraction" if ok else "non-matching filter was ignored or failed unexpectedly",
            evidence={"extraction_id": extraction_id, "filter": {name: value}, "returned_ids": [x.get("extraction_id") for x in rows], "error_code": _error_code(call)},
            failure_class=None if ok else "FAIL_BUSINESS",
            severity=None if ok else "P1",
        )
    db = runner.db.fetch_one(
        "SELECT factor_name, validation_status, mapping_status, confidence_score, target_asset_class FROM kb_factor_extractions WHERE id=%s",
        (extraction_id,),
    )
    db_asset = json.loads(db["target_asset_class"]) if isinstance(db.get("target_asset_class"), str) else db.get("target_asset_class")
    exact_ok = bool(
        db and item["factor_name"] == db["factor_name"]
        and item["validation_status"] == db["validation_status"]
        and item["mapping_status"] == db["mapping_status"]
        and Decimal(str(item["confidence_score"])) == Decimal(str(db["confidence_score"]))
        and item["target_asset_class"] == db_asset
    )
    runner.record(
        "KB-DB-IDENTITY",
        "KB candidate core extraction fields match DB",
        "PASS" if exact_ok else "FAIL",
        "name, statuses, confidence, and asset class match the extraction row" if exact_ok else "KB candidate core fields differ from DB",
        evidence={"extraction_id": extraction_id, "db": {**db, "target_asset_class": db_asset}, "mcp": {key: item.get(key) for key in ("factor_name", "validation_status", "mapping_status", "confidence_score", "target_asset_class")}},
        failure_class=None if exact_ok else "FAIL_DATA_CONSISTENCY",
        severity=None if exact_ok else "P1",
    )


def _run_universe_subsets(runner: Runner) -> None:
    sets: dict[str, set[str]] = {}
    for key in ("main", "altcoin"):
        call = runner.tool(f"UNIVERSE-{key}", "universe_list_symbols", {"universe_key": key})
        items = _data(call).get("items") or []
        mcp_set = {str(row["symbol"]) for row in items}
        db_rows = runner.db.fetch_all("SELECT symbol FROM coin_universe_symbols WHERE universe_key=%s AND is_active=1 ORDER BY sort_order", (key,))
        db_set = {str(row["symbol"]) for row in db_rows}
        order = [int(row["sort_order"]) for row in items]
        ok = _success(call) and len(items) == len(mcp_set) and mcp_set == db_set and order == sorted(order)
        sets[key] = mcp_set
        runner.record(
            f"UNIVERSE-{key}",
            f"authoritative {key} universe set and ordering",
            "PASS" if ok else "FAIL",
            "MCP set equals DB, contains no duplicates, and follows sort_order" if ok else "universe subset differs from DB or ordering is unstable",
            evidence={"mcp_count": len(mcp_set), "db_count": len(db_set), "missing": sorted(db_set - mcp_set), "extra": sorted(mcp_set - db_set)},
            failure_class=None if ok else "FAIL_DATA_CONSISTENCY",
            severity=None if ok else "P1",
        )
    all_envelope = _load_one(str(ORIGINAL_DIR / "*UNIVERSE-001.response.json"))
    all_set = {row["symbol"] for row in _business(all_envelope)["data"]["items"]}
    partition_ok = not sets["main"] & sets["altcoin"] and sets["main"] | sets["altcoin"] == all_set
    runner.record(
        "UNIVERSE-PARTITION",
        "main and altcoin universes partition all",
        "PASS" if partition_ok else "FAIL",
        "main/altcoin are disjoint and their union equals all" if partition_ok else "universe subsets overlap or do not reconstruct all",
        evidence={"all_count": len(all_set), "main_count": len(sets["main"]), "altcoin_count": len(sets["altcoin"]), "overlap": sorted(sets["main"] & sets["altcoin"]), "missing_from_union": sorted(all_set - (sets["main"] | sets["altcoin"]))},
        failure_class=None if partition_ok else "FAIL_DATA_CONSISTENCY",
        severity=None if partition_ok else "P1",
    )


def _scope_args(row: dict[str, Any], *, validity: str | None) -> dict[str, Any]:
    result = {
        "kind": row["kind"], "ic_scope": row["ic_scope"], "validity_scope": row["ic_scope"],
        "calculation_mode": row["calculation_mode"], "universe_key": row["universe_key"], "window_scope": row["window_scope"],
        "interval": row["factor_bar_interval"], "factor_window_bars": row["factor_window_bars"], "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": row["forward_return_bars"], "as_of": datetime.now(timezone.utc).isoformat(),
        "scoring_version": row["scoring_version"], "symbol": row["symbol"], "limit": 5,
    }
    if validity is not None:
        result["validity"] = validity
    return result


def _run_ts_timeout_diagnosis(runner: Runner) -> None:
    envelope = _load_one(str(ORIGINAL_DIR / "*SEARCH-100-scopes.response.json"))
    rows = _business(envelope)["data"]["items"]
    symbol_scope = next(row for row in rows if row["ic_scope"] == "time_series" and row.get("symbol"))
    calls: list[tuple[str, dict[str, Any]]] = [
        ("TS-NO-VALIDITY", _scope_args(symbol_scope, validity=None)),
        ("TS-INVALID", _scope_args(symbol_scope, validity="invalid")),
        ("TS-UNKNOWN", _scope_args(symbol_scope, validity="unknown")),
    ]
    for case, arguments in calls:
        call = runner.tool(case, "factor_search", arguments)
        ok = _success(call)
        runner.record(
            case,
            f"time-series metric search diagnostic: {case.lower()}",
            "PASS" if ok else "FAIL",
            "time-series metric search completed" if ok else f"time-series metric search returned {_error_code(call)}",
            evidence={"scope": arguments, "returned_count": len(_items(call)), "error_code": _error_code(call), "elapsed_seconds": call["elapsed_seconds"]},
            failure_class=None if ok else "FAIL_QUERY_TIMEOUT" if _error_code(call) == "QUERY_TIMEOUT" else "FAIL_BUSINESS",
            severity=None if ok else "P1",
        )


def main() -> None:
    """Execute expansion cases and write a credential-free evidence summary."""
    token = os.environ.get("CATALOG_MCP_TOKEN")
    if not token:
        raise SystemExit("CATALOG_MCP_TOKEN is required")
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = PROJECT_ROOT / "reports" / "factor4-deep" / f"{run_stamp}-catalog-expansion"
    settings = SettingsLoader.load("test", PROJECT_ROOT)
    runner = Runner(token, output_dir, DatabaseClient.from_settings(settings.database))
    init = runner.request("MCP-INIT", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest-catalog-expansion", "version": "1.0"}})
    runner.protocol_version = (((init.get("envelope") or {}).get("result") or {}).get("protocolVersion"))
    runner.notify_initialized("MCP-NOTIFY")
    _run_ts_timeout_diagnosis(runner)
    counts = Counter(row["status"] for row in runner.cases)
    summary = {
        "run_id": run_stamp, "environment": "test", "read_only": True,
        "case_counts": dict(sorted(counts.items())), "cases": runner.cases,
        "confirmed_failures": [row for row in runner.cases if row["status"] == "FAIL"],
        "blocked": [row for row in runner.cases if row["status"] == "BLOCKED"],
    }
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "call-ledger.json", [{"case_id": call.get("case_id"), "tool": call.get("tool"), "arguments": call.get("arguments"), "http_status": call.get("http_status"), "elapsed_seconds": call.get("elapsed_seconds"), "error_code": _error_code(call), "request_id": _meta(call).get("request_id"), "trace_id": _meta(call).get("trace_id")} for call in runner.calls])
    lines = ["# Catalog deep-test expansion", "", f"- PASS={counts.get('PASS', 0)}, FAIL={counts.get('FAIL', 0)}, BLOCKED={counts.get('BLOCKED', 0)}", "", "| Case | Status | Result |", "| --- | --- | --- |"]
    lines.extend(f"| {row['case_id']} | {row['status']} | {row['reason']} |" for row in runner.cases)
    (output_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "case_counts": dict(counts), "call_count": len(runner.calls)}))


if __name__ == "__main__":
    main()
