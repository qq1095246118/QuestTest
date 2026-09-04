#!/usr/bin/env python3
"""Correct false-positive oracles and recheck focused Factor 4.0 catalog cases."""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
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


def _load_json(pattern: str) -> dict[str, Any]:
    matches = glob.glob(pattern)
    if len(matches) != 1:
        raise RuntimeError(f"Expected one evidence file for {pattern}, got {len(matches)}")
    with open(matches[0], encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Evidence file is not an object: {matches[0]}")
    return value


def _business_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    result = envelope.get("result") or {}
    structured = result.get("structuredContent")
    return structured if isinstance(structured, dict) else {}


def _scope_args(row: dict[str, Any], as_of: str, *, validity: str | None = "valid") -> dict[str, Any]:
    result = {
        "kind": row["kind"],
        "ic_scope": row["ic_scope"],
        "validity_scope": row["ic_scope"],
        "calculation_mode": row["calculation_mode"],
        "universe_key": row["universe_key"],
        "window_scope": row["window_scope"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": row["forward_return_bars"],
        "as_of": as_of,
        "scoring_version": row["scoring_version"],
        "symbol": row["symbol"],
        "limit": 10,
    }
    if validity is not None:
        result["validity"] = validity
    return result


def _run_metric_rechecks(runner: Runner, original_dir: Path) -> None:
    scope_envelope = _load_json(str(original_dir / "*SEARCH-100-scopes.response.json"))
    rows = _business_from_envelope(scope_envelope).get("data", {}).get("items", [])
    aggregate = next(
        row for row in rows
        if row.get("ic_scope") == "time_series"
        and row.get("symbol") == ""
        and row.get("window_scope") == "min_window"
    )
    symbol_scope = next(
        row for row in rows
        if row.get("ic_scope") == "time_series"
        and isinstance(row.get("symbol"), str)
        and row.get("symbol")
        and row.get("window_scope") == aggregate.get("window_scope")
        and row.get("scoring_version") == aggregate.get("scoring_version")
    )
    cross_sectional = next(
        row for row in rows
        if row.get("ic_scope") == "cross_sectional" and row.get("symbol") == ""
    )
    as_of = datetime.now(timezone.utc).isoformat()
    aggregate_args = _scope_args(aggregate, as_of)
    retries = [runner.tool(f"SEARCH-101-AGG-{index}", "factor_search", aggregate_args) for index in range(1, 4)]
    retry_codes = [_error_code(call) for call in retries]
    stable_timeout = all(code == "QUERY_TIMEOUT" for code in retry_codes)
    aggregate_success = any(_success(call) for call in retries)
    runner.record(
        "SEARCH-101-AGGREGATE",
        "time-series aggregate metric search completes",
        "FAIL" if stable_timeout else ("PASS" if aggregate_success else "BLOCKED"),
        (
            "same advertised aggregate metric scope returned QUERY_TIMEOUT on all three attempts"
            if stable_timeout
            else "aggregate search completed on at least one attempt"
            if aggregate_success
            else "aggregate search failed inconsistently and needs a stable snapshot"
        ),
        evidence={"scope": aggregate_args, "attempt_error_codes": retry_codes, "attempt_elapsed_seconds": [x["elapsed_seconds"] for x in retries]},
        failure_class="FAIL_QUERY_TIMEOUT" if stable_timeout else (None if aggregate_success else "ASYNC_STATE_MOVING"),
        severity="P1" if stable_timeout else None,
    )
    omitted = dict(aggregate_args)
    omitted.pop("symbol")
    omitted_call = runner.tool("SEARCH-101-NO-SYMBOL", "factor_search", omitted)
    runner.record(
        "SEARCH-101-NO-SYMBOL",
        "metric search requires an explicit symbol identity",
        "PASS" if _rejected(omitted_call) else "FAIL",
        "omitted symbol was rejected" if _rejected(omitted_call) else "metric search silently inferred a symbol",
        evidence={"error_code": _error_code(omitted_call)},
        failure_class=None if _rejected(omitted_call) else "FAIL_SCOPE_ISOLATION",
        severity=None if _rejected(omitted_call) else "P1",
    )
    symbol_args = _scope_args(symbol_scope, as_of)
    symbol_call = runner.tool("SEARCH-101-SYMBOL", "factor_search", symbol_args)
    symbol_items = _items(symbol_call)
    symbol_ok = _success(symbol_call) and all(
        row.get("metric_run_id") is not None
        and row.get("scoring_version") == symbol_scope["scoring_version"]
        and row.get("validity_status") == "valid"
        for row in symbol_items
    )
    runner.record(
        "SEARCH-101-SYMBOL",
        "time-series metric search for a discovered symbol scope",
        "PASS" if symbol_ok else "FAIL",
        "discovered symbol scope returned only valid matching metric rows" if symbol_ok else "discovered symbol scope failed or mixed metric identity",
        evidence={"scope": symbol_args, "returned_count": len(symbol_items), "error_code": _error_code(symbol_call), "refs": [x.get("factor_ref") for x in symbol_items]},
        failure_class=None if symbol_ok else "FAIL_BUSINESS",
        severity=None if symbol_ok else "P1",
    )
    cs_args = _scope_args(cross_sectional, as_of)
    cs_call = runner.tool("SEARCH-101-CS", "factor_search", cs_args)
    cs_items = _items(cs_call)
    cs_ok = _success(cs_call) and all(
        row.get("scoring_version") == cross_sectional["scoring_version"]
        and row.get("validity_status") == "valid"
        for row in cs_items
    )
    runner.record(
        "SEARCH-101-CS",
        "cross-sectional aggregate metric search",
        "PASS" if cs_ok else "FAIL",
        "cross-sectional aggregate search completed with matching rows" if cs_ok else "cross-sectional aggregate search failed or mixed identity",
        evidence={"scope": cs_args, "returned_count": len(cs_items), "error_code": _error_code(cs_call), "refs": [x.get("factor_ref") for x in cs_items]},
        failure_class=None if cs_ok else "FAIL_BUSINESS",
        severity=None if cs_ok else "P1",
    )


def _run_detail_rechecks(runner: Runner, original_dir: Path) -> None:
    summary = json.loads((original_dir / "summary.json").read_text(encoding="utf-8"))
    requested = next(row for row in summary["cases"] if row["case_id"] == "DETAIL-020")["evidence"]["requested"]
    valid_refs = [ref for ref in requested if ref != "bad-ref" and int(ref.split(":", 1)[1]) < 9_000_000_000][:3]
    missing_ref = f"sub_factor:{9_000_000_000 + int(datetime.now().timestamp())}"
    mixed = runner.tool("DETAIL-020-PARTIAL", "factor_get_details_batch", {"factor_refs": valid_refs + [missing_ref], "detail_level": "summary"})
    items = _items(mixed)
    by_ref = {row.get("factor_ref"): row for row in items}
    partial_ok = (
        _success(mixed)
        and len(items) == len(valid_refs) + 1
        and all(by_ref.get(ref, {}).get("success") is True for ref in valid_refs)
        and by_ref.get(missing_ref, {}).get("success") is False
    )
    runner.record(
        "DETAIL-020-PARTIAL",
        "batch details return per-item not-found without losing valid rows",
        "PASS" if partial_ok else "FAIL",
        "valid references succeeded and the syntactically valid missing reference failed per item" if partial_ok else "valid and missing references were not separated per item",
        evidence={"requested": valid_refs + [missing_ref], "returned": [{"factor_ref": x.get("factor_ref"), "success": x.get("success"), "error": x.get("error")} for x in items], "error_code": _error_code(mixed)},
        failure_class=None if partial_ok else "FAIL_PARTIAL_RESULT",
        severity=None if partial_ok else "P1",
    )
    malformed = runner.tool("DETAIL-020-MALFORMED", "factor_get_details_batch", {"factor_refs": valid_refs + ["bad-ref"], "detail_level": "summary"})
    runner.record(
        "DETAIL-020-MALFORMED",
        "batch details reject malformed factor_ref at request level",
        "PASS" if _rejected(malformed) else "FAIL",
        "malformed reference was rejected as INVALID_ARGUMENT" if _rejected(malformed) else "malformed reference was accepted",
        evidence={"error_code": _error_code(malformed)},
        failure_class=None if _rejected(malformed) else "FAIL_CONTRACT",
        severity=None if _rejected(malformed) else "P1",
    )
    single = runner.tool("DETAIL-021-SINGLE", "factor_get_detail", {"factor_ref": valid_refs[0], "detail_level": "summary"})
    batch_data = by_ref.get(valid_refs[0], {}).get("data") or {}
    single_data = _data(single)
    keys = ("factor_ref", "id", "kind", "name", "serial_number", "cn_name")
    equal = partial_ok and _success(single) and all(batch_data.get(key) == single_data.get(key) for key in keys)
    runner.record(
        "DETAIL-021-CORRECTED",
        "batch and single detail core identity consistency",
        "PASS" if equal else "FAIL",
        "batch and single responses identify the same factor" if equal else "batch and single core identities differ",
        evidence={"factor_ref": valid_refs[0], "compared_keys": list(keys)},
        failure_class=None if equal else "FAIL_DATA_CONSISTENCY",
        severity=None if equal else "P1",
    )


def _run_formula_recheck(runner: Runner, original_dir: Path) -> None:
    envelope = _load_json(str(original_dir / "*FORMULA-001.response.json"))
    data = _business_from_envelope(envelope)["data"]
    oracle = runner.db.fetch_one(
        """
        SELECT e.run_id, e.factor_id, e.is_sub_factor_id, e.calculation_mode,
               e.factor_bar_interval, e.factor_window_bars, e.return_bar_interval,
               e.forward_return_bars, e.formula_version, e.formula_hash, e.expression
        FROM factor_ic_run_formula_evidence e
        WHERE e.run_id=%s AND e.factor_id=%s AND e.is_sub_factor_id=%s
          AND e.calculation_mode=%s AND e.factor_bar_interval=%s
          AND e.factor_window_bars=%s AND e.return_bar_interval=%s
          AND e.forward_return_bars=%s
        """,
        (
            data["run_id"], int(data["factor_ref"].split(":", 1)[1]),
            1 if data["factor_ref"].startswith("sub_factor:") else 0,
            data["metric_identity"]["calculation_mode"], data["metric_identity"]["factor_bar_interval"],
            data["metric_identity"]["factor_window_bars"], data["metric_identity"]["return_bar_interval"],
            data["metric_identity"]["forward_return_bars"],
        ),
    )
    ok = bool(
        oracle
        and data["expression"] == oracle["expression"]
        and data["formula_hash"] == oracle["formula_hash"]
        and data["formula_version"] == oracle["formula_version"]
    )
    runner.record(
        "FORMULA-001-CORRECTED",
        "exact-run formula evidence matches its DB row",
        "PASS" if ok else "FAIL",
        "nested metric identity, expression, version, and hash all match" if ok else "exact formula evidence differs from DB",
        evidence={"factor_ref": data["factor_ref"], "run_id": data["run_id"], "metric_identity": data["metric_identity"], "formula_hash": data["formula_hash"], "db_row_found": bool(oracle)},
        failure_class=None if ok else "FAIL_DATA_CONSISTENCY",
        severity=None if ok else "P1",
    )


def _run_kb_rechecks(runner: Runner, original_dir: Path) -> None:
    envelope = _load_json(str(original_dir / "*KB-001-id.response.json"))
    item = _business_from_envelope(envelope)["data"]["items"][0]
    extraction_id = int(item["extraction_id"])
    candidates = [
        ("validation_status", item["validation_status"]),
        ("mapping_status", item["mapping_status"]),
        ("min_confidence", item["confidence_score"]),
        ("target_asset_class", item["target_asset_class"][0]),
    ]
    for index, (name, value) in enumerate(candidates, 1):
        call = runner.tool(f"KB-003-CORRECTED-{index}", "kb_factor_candidate_search", {"extraction_id": extraction_id, name: value, "limit": 10})
        rows = _items(call)
        returned_ids = [int(row.get("extraction_id", -1)) for row in rows]
        ok = _success(call) and returned_ids == [extraction_id]
        runner.record(
            f"KB-003-CORRECTED-{index}",
            f"KB exact candidate plus {name} filter",
            "PASS" if ok else "FAIL",
            "matching exact candidate is retained by the filter" if ok else "schema-advertised filter rejected or removed an exact matching candidate",
            evidence={"extraction_id": extraction_id, "filter": {name: value}, "returned_ids": returned_ids, "error_code": _error_code(call)},
            failure_class=None if ok else "FAIL_BUSINESS",
            severity=None if ok else "P1",
        )


def _run_universe_rechecks(runner: Runner, original_dir: Path) -> None:
    envelope = _load_json(str(original_dir / "*UNIVERSE-001.response.json"))
    business = _business_from_envelope(envelope)
    data = business["data"]
    key = data["items"][0]["universe_key"]
    current_as_of = datetime.fromisoformat(data["as_of"])
    db_rows = runner.db.fetch_all(
        """
        SELECT symbol FROM coin_universe_symbols
        WHERE universe_key=%s AND is_active=1
          AND (valid_from IS NULL OR valid_from <= %s)
          AND (valid_to IS NULL OR valid_to > %s)
        ORDER BY sort_order, symbol
        """,
        (key, current_as_of.replace(tzinfo=None), current_as_of.replace(tzinfo=None)),
    )
    mcp_symbols = [row["symbol"] for row in data["items"]]
    db_symbols = [row["symbol"] for row in db_rows]
    current_ok = len(mcp_symbols) == len(set(mcp_symbols)) and set(mcp_symbols) == set(db_symbols)
    runner.record(
        "UNIVERSE-001-CORRECTED",
        "current universe membership with NULL unbounded validity",
        "PASS" if current_ok else "FAIL",
        "MCP set exactly matches active DB rows when NULL valid_from means no lower bound" if current_ok else "MCP and DB membership sets differ",
        evidence={"universe_key": key, "mcp_count": len(mcp_symbols), "db_count": len(db_symbols), "missing_in_mcp": sorted(set(db_symbols) - set(mcp_symbols)), "extra_in_mcp": sorted(set(mcp_symbols) - set(db_symbols))},
        failure_class=None if current_ok else "FAIL_DATA_CONSISTENCY",
        severity=None if current_ok else "P1",
    )
    historical_as_of = current_as_of - timedelta(days=365)
    historical = runner.tool("UNIVERSE-002-CORRECTED", "universe_list_symbols", {"universe_key": key, "as_of": historical_as_of.isoformat()})
    hitems = _data(historical).get("items") or []
    hmcp = [row["symbol"] for row in hitems]
    hdb_rows = runner.db.fetch_all(
        """
        SELECT symbol FROM coin_universe_symbols
        WHERE universe_key=%s AND is_active=1
          AND (valid_from IS NULL OR valid_from <= %s)
          AND (valid_to IS NULL OR valid_to > %s)
        ORDER BY sort_order, symbol
        """,
        (key, historical_as_of.replace(tzinfo=None), historical_as_of.replace(tzinfo=None)),
    )
    hdb = [row["symbol"] for row in hdb_rows]
    historical_ok = _success(historical) and len(hmcp) == len(set(hmcp)) and set(hmcp) == set(hdb)
    runner.record(
        "UNIVERSE-002-CORRECTED",
        "historical universe membership with unbounded rows",
        "PASS" if historical_ok else "FAIL",
        "historical MCP set equals the DB point-in-time set" if historical_ok else "historical membership differs from DB",
        evidence={"universe_key": key, "as_of": historical_as_of.isoformat(), "mcp_count": len(hmcp), "db_count": len(hdb), "error_code": _error_code(historical)},
        failure_class=None if historical_ok else "FAIL_POINT_IN_TIME",
        severity=None if historical_ok else "P0",
    )


def main() -> None:
    """Execute focused rechecks and emit a corrected verdict summary."""
    token = os.environ.get("CATALOG_MCP_TOKEN")
    if not token:
        raise SystemExit("CATALOG_MCP_TOKEN is required")
    original_dir = PROJECT_ROOT / "reports" / "factor4-deep" / "20260902T104949Z-catalog"
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = PROJECT_ROOT / "reports" / "factor4-deep" / f"{run_stamp}-catalog-recheck"
    settings = SettingsLoader.load("test", PROJECT_ROOT)
    runner = Runner(token, output_dir, DatabaseClient.from_settings(settings.database))
    init = runner.request(
        "MCP-INIT",
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest-catalog-recheck", "version": "1.0"}},
    )
    runner.protocol_version = (((init.get("envelope") or {}).get("result") or {}).get("protocolVersion"))
    runner.notify_initialized("MCP-NOTIFY")
    _run_metric_rechecks(runner, original_dir)
    _run_detail_rechecks(runner, original_dir)
    _run_formula_recheck(runner, original_dir)
    _run_kb_rechecks(runner, original_dir)
    _run_universe_rechecks(runner, original_dir)
    counts = Counter(row["status"] for row in runner.cases)
    result = {
        "run_id": run_stamp,
        "environment": "test",
        "mcp_url": MCP_URL,
        "read_only": True,
        "supersedes_initial_failed_cases": ["SEARCH-101", "DETAIL-020", "DETAIL-021", "FORMULA-001", "KB-003-1-validation_status", "KB-003-2-mapping_status", "KB-003-3-min_confidence", "KB-003-4-target_asset_class", "UNIVERSE-001"],
        "case_counts": dict(sorted(counts.items())),
        "cases": runner.cases,
        "confirmed_failures": [row for row in runner.cases if row["status"] == "FAIL"],
        "blocked": [row for row in runner.cases if row["status"] == "BLOCKED"],
    }
    _write_json(output_dir / "corrected-summary.json", result)
    _write_json(
        output_dir / "call-ledger.json",
        [{"case_id": call.get("case_id"), "tool": call.get("tool"), "arguments": call.get("arguments"), "http_status": call.get("http_status"), "elapsed_seconds": call.get("elapsed_seconds"), "is_error": call.get("is_error"), "error_code": _error_code(call), "request_id": _meta(call).get("request_id"), "trace_id": _meta(call).get("trace_id")} for call in runner.calls],
    )
    lines = [
        "# Corrected catalog deep-test verdicts",
        "",
        f"- Run: `{run_stamp}`",
        f"- PASS={counts.get('PASS', 0)}, FAIL={counts.get('FAIL', 0)}, BLOCKED={counts.get('BLOCKED', 0)}",
        "- These focused verdicts supersede the nine initial false-positive candidates listed in `corrected-summary.json`.",
        "",
        "| Case | Status | Result |",
        "| --- | --- | --- |",
    ]
    for row in runner.cases:
        lines.append(f"| {row['case_id']} | {row['status']} | {row['reason']} |")
    (output_dir / "corrected-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "case_counts": dict(counts), "call_count": len(runner.calls)}))


if __name__ == "__main__":
    main()
