#!/usr/bin/env python3
"""Run an additional read-only functional regression against Factor Data MCP.

The runner discovers identifiers and metric scopes from the test database, then
checks the corresponding MCP read paths.  It deliberately does not call any
write tool and stores only redacted request/response evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402
from tmp import catalog_deep_readonly as catalog  # noqa: E402


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
CLIENT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _parse_json(value: Any) -> Any:
    """Decode a JSON column or response text when possible."""

    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _data(call: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized MCP data object."""

    return catalog._data(call)


def _business(call: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized MCP business envelope."""

    value = call.get("business")
    return value if isinstance(value, dict) else {}


def _items(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a list of object items from common MCP response shapes."""

    for key in ("items", "results", "metrics", "symbols", "tags"):
        rows = value.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _error_code(call: dict[str, Any]) -> str | None:
    """Return a structured MCP error code, if any."""

    return catalog._error_code(call)


def _success(call: dict[str, Any]) -> bool:
    """Return whether a call has a successful MCP business envelope."""

    return catalog._success(call)


def _rejected(call: dict[str, Any]) -> bool:
    """Return whether a call was rejected at transport or business level."""

    return catalog._rejected(call)


def _hash_business(call: dict[str, Any]) -> str:
    """Hash a business payload for stable repeatability comparison."""

    encoded = json.dumps(_business(call), ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _record(
    runner: catalog.Runner,
    case_id: str,
    title: str,
    passed: bool,
    reason_ok: str,
    reason_fail: str,
    evidence: dict[str, Any] | None = None,
    *,
    severity: str = "P1",
    blocked: str | None = None,
) -> None:
    """Append one normalized verdict to the runner ledger."""

    if blocked:
        runner.record(case_id, title, "BLOCKED", blocked, evidence=evidence)
        return
    runner.record(
        case_id,
        title,
        "PASS" if passed else "FAIL",
        reason_ok if passed else reason_fail,
        evidence=evidence,
        failure_class=None if passed else "FAIL_FUNCTIONAL",
        severity=None if passed else severity,
    )


def _db_state(db: DatabaseClient) -> dict[str, Any]:
    """Read compact business-table counters and update markers."""

    tables = (
        "market_environment_daily",
        "market_environment_eval_batch",
        "market_environment_factor_metric",
        "market_environment_factor_route",
        "factor_ic_runs",
        "factor_ic_run_formula_evidence",
        "factor_validity_status",
        "market_environment_strategy_feedback_submissions",
    )
    result: dict[str, Any] = {}
    with db.transaction() as tx:
        for table in tables:
            # Append-only run/evidence tables expose lifecycle-specific markers.
            if table == "factor_ic_runs":
                marker = "created_at"
            elif table == "factor_ic_run_formula_evidence":
                marker = "recorded_at"
            else:
                marker = "updated_at"
            row = tx.fetch_one(
                f"SELECT COUNT(*) AS row_count, MAX(id) AS max_id, MAX({marker}) AS max_marker FROM `{table}`"
            )
            result[table] = row or {}
    return result


def _discover(db: DatabaseClient) -> dict[str, Any]:
    """Discover a stable active route and exact metric/formula scopes."""

    with db.transaction() as tx:
        route = tx.fetch_one(
            """
            SELECT r.id AS route_id, r.factor_ref, r.factor_id, r.factor_type,
                   r.metric_id, r.market_scope, r.label_code, r.eval_batch_id,
                   r.publication_uid, r.publish_version, r.factor_version,
                   r.rank_no, r.routing_score, r.is_active, r.is_eligible,
                   m.evaluation_type, m.interval, m.return_bar_interval,
                   m.forward_return_bars, m.window_scope, m.scoring_version,
                   b.batch_uid, b.route_profile_key, b.status AS batch_status,
                   b.publish_status, b.published_at, b.as_of_time,
                   b.environment_snapshot_hash, b.factor_set_snapshot_hash
            FROM market_environment_factor_route r
            JOIN market_environment_factor_metric m ON m.id=r.metric_id
            JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
            WHERE r.is_active=1 AND r.is_eligible=1
            ORDER BY r.activated_at DESC, r.id DESC
            LIMIT 1
            """
        )
        summary = tx.fetch_one(
            """
            SELECT s.id, s.run_id, s.factor_id, s.is_sub_factor_id, s.ic_scope,
                   s.calculation_mode, s.universe_key, s.symbol, s.window_scope,
                   s.factor_bar_interval, s.factor_window_bars,
                   s.return_bar_interval, s.forward_return_bars,
                   s.period_start, s.period_end, s.scoring_version,
                   r.status AS run_status, r.completed_at
            FROM factor_ic_summary_metrics s
            JOIN factor_ic_runs r ON r.run_id=s.run_id
            WHERE r.status='completed' AND s.is_sub_factor_id=1
            ORDER BY s.id DESC
            LIMIT 1
            """
        )
        # Prefer a summary with a symbol for the slice endpoint.
        symbol_summary = tx.fetch_one(
            """
            SELECT s.id, s.run_id, s.factor_id, s.is_sub_factor_id, s.ic_scope,
                   s.calculation_mode, s.universe_key, s.symbol, s.window_scope,
                   s.factor_bar_interval, s.factor_window_bars,
                   s.return_bar_interval, s.forward_return_bars,
                   s.period_start, s.period_end, s.scoring_version,
                   r.status AS run_status, r.completed_at
            FROM factor_ic_summary_metrics s
            JOIN factor_ic_runs r ON r.run_id=s.run_id
            WHERE r.status='completed' AND s.is_sub_factor_id=1
              AND s.symbol IS NOT NULL AND s.symbol<>''
            ORDER BY s.id DESC
            LIMIT 1
            """
        )
        if symbol_summary:
            summary = symbol_summary
        evidence = None
        if summary:
            evidence = tx.fetch_one(
                """
                SELECT e.* , r.status AS run_status, r.completed_at
                FROM factor_ic_run_formula_evidence e
                JOIN factor_ic_runs r ON r.run_id=e.run_id
                WHERE e.factor_id=%s AND e.is_sub_factor_id=%s
                  AND e.run_id=%s AND r.status='completed'
                ORDER BY e.id DESC LIMIT 1
                """,
                (summary["factor_id"], summary["is_sub_factor_id"], summary["run_id"]),
            )
        detail = None
        if summary:
            detail = tx.fetch_one(
                """
                SELECT id, factor_id, serial_number, name, description, calc_function,
                       calc_logic, params, status, is_sub_factor_id, updated_at
                FROM factors_details
                WHERE factor_id=%s AND is_sub_factor_id=%s
                ORDER BY id DESC LIMIT 1
                """,
                (summary["factor_id"], summary["is_sub_factor_id"]),
            )
        validity = None
        if summary:
            validity = tx.fetch_one(
                """
                SELECT * FROM factor_validity_status
                WHERE factor_id=%s AND is_sub_factor_id=%s
                  AND run_id=%s AND universe_key=%s
                  AND factor_bar_interval=%s AND factor_window_bars=%s
                  AND window_scope=%s
                ORDER BY id DESC LIMIT 1
                """,
                (
                    summary["factor_id"],
                    summary["is_sub_factor_id"],
                    summary["run_id"],
                    summary["universe_key"],
                    summary["factor_bar_interval"],
                    summary["factor_window_bars"],
                    summary["window_scope"],
                ),
            )
        second = tx.fetch_one(
            """
            SELECT factor_ref FROM market_environment_factor_metric
            WHERE metric_status='success' AND is_valid=1
            ORDER BY id DESC LIMIT 1
            """
        )
        approved = tx.fetch_one(
            "SELECT schema_version FROM raw_data_schema_version WHERE status='approved' ORDER BY id DESC LIMIT 1"
        )
        daily = tx.fetch_one(
            """
            SELECT environment_date, label_kind, available_at
            FROM market_environment_daily
            WHERE label_kind='fact' AND is_current=1
            ORDER BY environment_date DESC, id DESC LIMIT 1
            """
        )
        universe = tx.fetch_one(
            "SELECT universe_key FROM coin_universe_symbols WHERE is_active=1 ORDER BY universe_key LIMIT 1"
        )
        kb = tx.fetch_one(
            "SELECT id, mapping_status, validation_status FROM kb_factor_extractions ORDER BY id DESC LIMIT 1"
        )
        failed_kb = tx.fetch_one(
            """
            SELECT extraction_id FROM kb_factor_mining_tasks
            WHERE status='failed' ORDER BY id DESC LIMIT 1
            """
        )
        parent = None
        if route and route["factor_type"] == "sub_factor":
            parent = tx.fetch_one(
                """
                SELECT factor_id AS parent_factor_id, sub_factor_id
                FROM factor_sub_factor_relations WHERE sub_factor_id=%s
                ORDER BY id LIMIT 1
                """,
                (route["factor_id"],),
            )
    return {
        "route": route,
        "summary": summary,
        "evidence": evidence,
        "detail": detail,
        "validity": validity,
        "second_factor_ref": (second or {}).get("factor_ref"),
        "schema_version": (approved or {}).get("schema_version"),
        "daily": daily,
        "universe_key": (universe or {}).get("universe_key"),
        "kb": kb,
        "failed_kb": failed_kb,
        "parent": parent,
    }


def _metric_args(summary: dict[str, Any], *, scope: str | None = None) -> dict[str, Any]:
    """Build the exact metric scope represented by a DB summary row."""

    return {
        "factor_ref": f"sub_factor:{summary['factor_id']}" if summary["is_sub_factor_id"] else f"factor:{summary['factor_id']}",
        "ic_scope": scope or summary["ic_scope"],
        "calculation_mode": summary["calculation_mode"],
        "universe_key": summary["universe_key"],
        "window_scope": summary["window_scope"],
        "interval": summary["factor_bar_interval"],
        "factor_window_bars": summary["factor_window_bars"],
        "return_bar_interval": summary["return_bar_interval"],
        "forward_return_bars": int(summary["forward_return_bars"]),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scoring_version": summary["scoring_version"],
        "symbol": summary.get("symbol") or "",
    }


def _validity_args(summary: dict[str, Any], scope: str) -> dict[str, Any]:
    """Build a validity request from an exact metric scope."""

    args = _metric_args(summary)
    args["validity_scope"] = scope
    args.pop("ic_scope", None)
    return args


def _formula_args(evidence: dict[str, Any], factor_ref: str) -> dict[str, Any]:
    """Build an exact immutable formula evidence lookup."""

    return {
        "factor_ref": factor_ref,
        "run_id": evidence["run_id"],
        "calculation_mode": "direct",
        "interval": evidence["factor_bar_interval"],
        "factor_window_bars": evidence["factor_window_bars"],
        "return_bar_interval": evidence["return_bar_interval"],
        "forward_return_bars": int(evidence["forward_return_bars"]),
    }


def _raw_request(token: str | None, payload: bytes, *, label: str) -> tuple[int, dict[str, Any] | None, str]:
    """Send one low-level protocol request for auth/error-boundary checks."""

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": CLIENT_USER_AGENT,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(MCP_URL, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        envelope = None
    return status, envelope, label


def main() -> None:
    """Execute additional read-only checks and write a machine-readable report."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} or MCP_TOKEN is required")
    if not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("Only the configured test MCP host is permitted")
    settings = SettingsLoader.load("test", ROOT)
    if settings.environment != "test":
        raise SystemExit("test environment gate failed")
    db = DatabaseClient.from_settings(settings.database)
    discovered = _discover(db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-functional-extra-r0"
    output.mkdir(parents=True, exist_ok=True)
    runner = catalog.Runner(token, output, db)
    before_state = _db_state(db)

    init = runner.request(
        "EXTRA-MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-functional-extra", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    runner.protocol_version = init_result.get("protocolVersion")
    init_ok = init.get("http_status") == 200 and bool(init_result.get("serverInfo")) and runner.protocol_version == "2025-06-18"
    _record(runner, "EXTRA-MCP-INIT", "MCP handshake and service identity", init_ok,
            "protocol and service identity accepted", "handshake or service identity failed",
            {"protocol_version": runner.protocol_version, "server_info": init_result.get("serverInfo")}, severity="P0")
    if not init_ok:
        raise SystemExit("MCP initialization failed")
    runner.notify_initialized("EXTRA-MCP-NOTIFY")
    tools_call = runner.request("EXTRA-MCP-TOOLS", "tools/list", {})
    tools = (((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or [])
    names = {row.get("name") for row in tools if isinstance(row, dict)}
    required = {
        "factor_get_detail", "factor_get_details_batch", "factor_get_metrics",
        "factor_get_metrics_batch", "factor_get_validity", "factor_get_validity_batch",
        "factor_get_formula", "factor_get_metric_slices", "factor_get_environment_metrics",
        "factor_get_environment_tags", "environment_get_recommendations",
        "environment_get_daily", "universe_list_symbols", "schema_get_factor_fields",
        "schema_get_raw_data", "kb_factor_candidate_search",
    }
    _record(runner, "EXTRA-MCP-TOOLS", "Required read-only tools are discoverable", required <= names,
            "all selected read-only tools are present", "one or more required read-only tools are absent",
            {"tool_count": len(names), "missing": sorted(required - names)})

    # Auth and protocol errors are sent without a Runner so they do not alter
    # the session used by the positive checks.
    list_payload = json.dumps({"jsonrpc": "2.0", "id": "auth-negative", "method": "tools/list", "params": {}}, separators=(",", ":")).encode()
    no_auth_status, no_auth_env, _ = _raw_request(None, list_payload, label="no-auth")
    bad_status, bad_env, _ = _raw_request("invalid-test-token", list_payload, label="bad-auth")
    no_auth_data = ((no_auth_env or {}).get("result") or {}).get("structuredContent") if isinstance(no_auth_env, dict) else None
    bad_data = ((bad_env or {}).get("result") or {}).get("structuredContent") if isinstance(bad_env, dict) else None
    auth_ok = no_auth_status in (401, 403) and bad_status in (401, 403) and not no_auth_data and not bad_data
    _record(runner, "EXTRA-AUTH-BOUNDARY", "Unauthenticated and invalid-token reads are denied", auth_ok,
            "both negative auth requests were denied without business data",
            "an unauthenticated request was accepted or returned business data",
            {"no_auth_status": no_auth_status, "bad_token_status": bad_status,
             "no_auth_has_data": bool(no_auth_data), "bad_token_has_data": bool(bad_data)}, severity="P0")

    # Schema and raw-data contract paths.
    fields = runner.tool("EXTRA-SCHEMA-FULL", "schema_get_factor_fields", {})
    fields_data = _data(fields)
    field_rows = fields_data.get("fields") if isinstance(fields_data.get("fields"), list) else []
    unknown_field = runner.tool("EXTRA-SCHEMA-UNKNOWN", "schema_get_factor_fields", {"field_names": ["__questtest_unknown_field__"]})
    unknown_field_ok = _rejected(unknown_field) or _error_code(unknown_field) in {"FIELD_NOT_APPROVED", "UNKNOWN_FIELD"}
    _record(runner, "EXTRA-SCHEMA", "Approved field schema and unknown-field rejection", _success(fields) and bool(field_rows) and unknown_field_ok,
            "approved field metadata is readable and unknown field is rejected",
            "schema response is empty/malformed or unknown field is silently accepted",
            {"field_count": len(field_rows), "unknown_error_code": _error_code(unknown_field),
             "unknown_http_status": unknown_field.get("http_status")})
    raw_schema = runner.tool("EXTRA-RAW-SCHEMA", "schema_get_raw_data", {})
    explicit_raw = runner.tool("EXTRA-RAW-SCHEMA-VERSION", "schema_get_raw_data", {"schema_version": discovered.get("schema_version")})
    raw_data = _data(raw_schema)
    explicit_data = _data(explicit_raw)
    raw_version = raw_data.get("schema_version") or _business(raw_schema).get("schema_version")
    explicit_version = explicit_data.get("schema_version") or _business(explicit_raw).get("schema_version")
    _record(runner, "EXTRA-RAW-SCHEMA", "Raw-data contract default and explicit version agree",
            _success(raw_schema) and _success(explicit_raw) and raw_version == explicit_version,
            "default and explicit approved schema return the same version",
            "raw-data schema default/explicit lookup differs or fails",
            {"db_schema_version": discovered.get("schema_version"), "default_version": raw_version,
             "explicit_version": explicit_version})
    unknown_raw = runner.tool("EXTRA-RAW-SCHEMA-UNKNOWN", "schema_get_raw_data", {"schema_version": "factor-canonical-does-not-exist"})
    _record(runner, "EXTRA-RAW-SCHEMA-UNKNOWN", "Unknown raw-data schema does not fall back silently",
            _rejected(unknown_raw) or not _data(unknown_raw),
            "unknown version was rejected or returned no contract", "unknown version returned an approved contract",
            {"http_status": unknown_raw.get("http_status"), "error_code": _error_code(unknown_raw)})

    route = discovered.get("route") or {}
    summary = discovered.get("summary") or {}
    factor_ref = route.get("factor_ref") or (f"sub_factor:{summary.get('factor_id')}" if summary else None)

    # Detail identity, batch ordering and parent/child shape.
    if factor_ref:
        detail_summary = runner.tool("EXTRA-DETAIL-SUMMARY", "factor_get_detail", {"factor_ref": factor_ref, "detail_level": "summary"})
        detail_definition = runner.tool("EXTRA-DETAIL-DEFINITION", "factor_get_detail", {"factor_ref": factor_ref, "detail_level": "definition"})
        detail_executable = runner.tool("EXTRA-DETAIL-EXECUTABLE", "factor_get_detail", {"factor_ref": factor_ref, "detail_level": "executable"})
        detail_ids = [_data(row).get("factor_ref") for row in (detail_summary, detail_definition, detail_executable)]
        detail_ok = all(_success(row) for row in (detail_summary, detail_definition, detail_executable)) and len(set(detail_ids)) == 1 and detail_ids[0] == factor_ref
        _record(runner, "EXTRA-DETAIL-LAYERS", "Detail levels preserve one factor identity", detail_ok,
                "summary/definition/executable resolve to the same factor", "detail levels cross or lose factor identity",
                {"requested": factor_ref, "returned_refs": detail_ids})
        refs = [factor_ref]
        second_ref = discovered.get("second_factor_ref")
        if second_ref and second_ref not in refs:
            refs.append(str(second_ref))
        batch_detail = runner.tool("EXTRA-DETAIL-BATCH", "factor_get_details_batch", {"factor_refs": refs})
        batch_rows = _items(_data(batch_detail))
        returned_refs = [str(row.get("factor_ref")) for row in batch_rows if row.get("factor_ref")]
        batch_ok = _success(batch_detail) and set(returned_refs) >= set(refs) and len(returned_refs) == len(set(returned_refs))
        _record(runner, "EXTRA-DETAIL-BATCH", "Batch detail preserves input identities without duplicates", batch_ok,
                "batch detail returned each requested factor once", "batch detail omitted, duplicated, or mixed factor identities",
                {"requested_refs": refs, "returned_refs": returned_refs})
        missing_detail = runner.tool("EXTRA-DETAIL-MISSING", "factor_get_detail", {"factor_ref": "sub_factor:999999999"})
        _record(runner, "EXTRA-DETAIL-MISSING", "Missing factor detail is not substituted", _rejected(missing_detail) or not _data(missing_detail),
                "missing factor was rejected or returned empty", "missing factor returned another factor", {"error_code": _error_code(missing_detail)})
    else:
        for case_id in ("EXTRA-DETAIL-LAYERS", "EXTRA-DETAIL-BATCH", "EXTRA-DETAIL-MISSING"):
            _record(runner, case_id, "Factor detail precondition", False, "", "", blocked="no factor identity discovered")

    # Exact metric, validity and slices paths use a completed DB scope.
    if summary and summary.get("factor_id"):
        metric_args = _metric_args(summary)
        metric = runner.tool("EXTRA-METRIC", "factor_get_metrics", metric_args)
        metric_data = _data(metric)
        metric_rows = _items(metric_data)
        metric_refs = {str(row.get("factor_ref")) for row in metric_rows if row.get("factor_ref")}
        metric_ok = _success(metric) and (not metric_rows or metric_refs <= {metric_args["factor_ref"]})
        _record(runner, "EXTRA-METRIC", "Exact metric scope does not cross factor identity", metric_ok,
                "metric response is successful and identity-bound", "metric response crosses or corrupts factor identity",
                {"requested": metric_args, "returned_count": len(metric_rows), "returned_refs": sorted(metric_refs)})
        refs = [metric_args["factor_ref"]]
        if discovered.get("second_factor_ref") and discovered["second_factor_ref"] not in refs:
            refs.append(str(discovered["second_factor_ref"]))
        metric_batch = runner.tool("EXTRA-METRIC-BATCH", "factor_get_metrics_batch", {**{k: metric_args[k] for k in metric_args if k != "factor_ref"}, "factor_refs": refs})
        batch_rows = _items(_data(metric_batch))
        batch_refs = {str(row.get("factor_ref")) for row in batch_rows if row.get("factor_ref")}
        batch_ok = _success(metric_batch) and batch_refs <= set(refs)
        _record(runner, "EXTRA-METRIC-BATCH", "Metric batch remains within requested scope", batch_ok,
                "batch metrics contain only requested factors", "batch metrics contain an unexpected factor",
                {"requested_refs": refs, "returned_refs": sorted(batch_refs), "error_code": _error_code(metric_batch)})
        validity_checks: list[dict[str, Any]] = []
        for scope in ("time_series", "cross_sectional"):
            validity = runner.tool(f"EXTRA-VALIDITY-{scope}", "factor_get_validity", _validity_args(summary, scope))
            validity_checks.append(validity)
            vdata = _data(validity)
            vref = vdata.get("factor_ref")
            ok = (_success(validity) and (vref is None or vref == metric_args["factor_ref"])) or _rejected(validity)
            _record(runner, f"EXTRA-VALIDITY-{scope}", "Scope-specific validity is identity-bound", ok,
                    "validity response is scoped or has a documented empty/error result", "validity response crosses factor identity",
                    {"requested_scope": scope, "returned_ref": vref, "error_code": _error_code(validity)})
        validity_batch_args = {k: metric_args[k] for k in metric_args if k != "ic_scope"}
        validity_batch = runner.tool("EXTRA-VALIDITY-BATCH", "factor_get_validity_batch", {**validity_batch_args, "validity_scope": "time_series", "factor_refs": refs})
        validity_rows = _items(_data(validity_batch))
        validity_refs = {str(row.get("factor_ref")) for row in validity_rows if row.get("factor_ref")}
        _record(runner, "EXTRA-VALIDITY-BATCH", "Validity batch stays within requested factors", _success(validity_batch) and validity_refs <= set(refs),
                "batch validity contains only requested factors", "batch validity contains an unexpected factor",
                {"requested_refs": refs, "returned_refs": sorted(validity_refs), "error_code": _error_code(validity_batch)})
        if summary.get("period_start") and summary.get("period_end"):
            slice_args = {
                "factor_ref": metric_args["factor_ref"], "ic_scope": summary["ic_scope"],
                "calculation_mode": summary["calculation_mode"], "universe_key": summary["universe_key"],
                "interval": summary["factor_bar_interval"], "factor_window_bars": summary["factor_window_bars"],
                "return_bar_interval": summary["return_bar_interval"], "forward_return_bars": int(summary["forward_return_bars"]),
                "window_scope": summary["window_scope"], "as_of": datetime.now(timezone.utc).isoformat(),
                "scoring_version": summary["scoring_version"], "start_time": str(summary["period_start"]).replace(" ", "T") + "+00:00",
                "end_time": str(summary["period_end"]).replace(" ", "T") + "+00:00", "symbol": summary.get("symbol") or "", "limit": 5,
            }
            slices = runner.tool("EXTRA-SLICES", "factor_get_metric_slices", slice_args)
            slice_rows = _items(_data(slices))
            slice_ok = _success(slices) and len(slice_rows) <= 5
            _record(runner, "EXTRA-SLICES", "Metric slices honor the requested bounded limit", slice_ok,
                    "slice response is bounded by the request", "slice response exceeds the requested limit or is malformed",
                    {"requested_limit": 5, "returned_count": len(slice_rows), "error_code": _error_code(slices)})
        else:
            _record(runner, "EXTRA-SLICES", "Metric slice precondition", False, "", "", blocked="selected summary has no period")
    else:
        for case_id in ("EXTRA-METRIC", "EXTRA-METRIC-BATCH", "EXTRA-VALIDITY-time_series", "EXTRA-VALIDITY-cross_sectional", "EXTRA-VALIDITY-BATCH", "EXTRA-SLICES"):
            _record(runner, case_id, "Metric scope precondition", False, "", "", blocked="no completed summary scope discovered")

    # Active environment evidence and isolation checks.
    if route:
        env_common = {"factor_ref": route["factor_ref"], "market_scope": route["market_scope"], "route_profile_key": route["route_profile_key"]}
        tags = runner.tool("EXTRA-ENV-TAGS", "factor_get_environment_tags", env_common)
        tags_data = _data(tags)
        tag_rows = _items(tags_data)
        tag_refs = {str(row.get("factor_ref")) for row in tag_rows if row.get("factor_ref")}
        publication = tags_data.get("publication") if isinstance(tags_data.get("publication"), dict) else {}
        tags_ok = _success(tags) and (not tag_refs or tag_refs == {str(route["factor_ref"])}) and (not publication or str(publication.get("publication_uid")) == str(route["publication_uid"]))
        _record(runner, "EXTRA-ENV-TAGS", "Environment tags stay on the requested active publication", tags_ok,
                "tag identities and publication are bound", "tags cross factor or publication boundaries",
                {"returned_factor_refs": sorted(tag_refs), "returned_publication_uid": publication.get("publication_uid"), "expected_publication_uid": route.get("publication_uid")}, severity="P0")
        env_args = {**env_common, "batch_uid": route["batch_uid"], "label_code": route["label_code"], "evaluation_type": route["evaluation_type"], "limit": 100}
        env_metrics = runner.tool("EXTRA-ENV-METRICS", "factor_get_environment_metrics", env_args)
        env_data = _data(env_metrics)
        env_rows = _items(env_data)
        env_batches = {str(row.get("batch_uid")) for row in env_rows if row.get("batch_uid")}
        env_labels = {str(row.get("label_code")) for row in env_rows if row.get("label_code")}
        env_ok = _success(env_metrics) and env_batches <= {str(route["batch_uid"])} and env_labels <= {str(route["label_code"])}
        _record(runner, "EXTRA-ENV-METRICS", "Environment metrics stay on exact batch and label", env_ok,
                "environment metrics are batch/label bound", "environment metrics mix another batch or label",
                {"returned_count": len(env_rows), "returned_batches": sorted(env_batches), "returned_labels": sorted(env_labels)}, severity="P0")
        wrong_scope = runner.tool("EXTRA-ENV-WRONG-SCOPE", "factor_get_environment_metrics", {**env_common, "market_scope": "scope-does-not-exist", "limit": 5})
        wrong_scope_rows = _items(_data(wrong_scope))
        _record(runner, "EXTRA-ENV-WRONG-SCOPE", "Unknown market scope does not fall back", (_rejected(wrong_scope) or not wrong_scope_rows),
                "unknown scope is rejected or empty", "unknown scope returned active data", {"error_code": _error_code(wrong_scope), "returned_count": len(wrong_scope_rows)}, severity="P0")
    else:
        for case_id in ("EXTRA-ENV-TAGS", "EXTRA-ENV-METRICS", "EXTRA-ENV-WRONG-SCOPE"):
            _record(runner, case_id, "Environment route precondition", False, "", "", blocked="no active route discovered")

    # Historical recommendation point-in-time check.  This is intentionally
    # dynamic: it derives a time between forecast availability and publication.
    if route and route.get("published_at") and route.get("as_of_time"):
        published = route["published_at"]
        if isinstance(published, datetime) and published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        historical_as_of = published - timedelta(hours=1) if isinstance(published, datetime) else None
        if historical_as_of:
            rec = runner.tool("EXTRA-REC-PIT", "environment_get_recommendations", {"market_scope": route["market_scope"], "route_profile_key": route["route_profile_key"], "as_of": historical_as_of.isoformat(), "limit": 20})
            rec_data = _data(rec)
            rec_pub = rec_data.get("publication") if isinstance(rec_data.get("publication"), dict) else {}
            rec_published = rec_pub.get("published_at")
            future_leak = bool(rec_pub) and rec_published and str(rec_published) > historical_as_of.isoformat()
            _record(runner, "EXTRA-REC-PIT", "Historical recommendation does not expose a future publication", not future_leak,
                    "historical response contains no publication newer than as_of", "historical response exposes a publication newer than as_of",
                    {"as_of": historical_as_of.isoformat(), "publication_uid": rec_pub.get("publication_uid"), "publication_published_at": rec_published, "future_leak": future_leak}, severity="P0")
    else:
        _record(runner, "EXTRA-REC-PIT", "Recommendation PIT precondition", False, "", "", blocked="no active publication timestamp discovered")

    # Universe, daily date filter and KB read paths.
    if discovered.get("universe_key"):
        universe = runner.tool("EXTRA-UNIVERSE", "universe_list_symbols", {"universe_key": discovered["universe_key"]})
        rows = _data(universe).get("items") or _data(universe).get("symbols") or []
        symbols = [row.get("symbol") if isinstance(row, dict) else row for row in rows]
        symbols = [str(row) for row in symbols if row]
        _record(runner, "EXTRA-UNIVERSE", "Universe symbols are nonempty and unique", _success(universe) and len(symbols) == len(set(symbols)) and bool(symbols),
                "universe returned a unique symbol set", "universe returned duplicates, no symbols, or an error", {"universe_key": discovered["universe_key"], "count": len(symbols)})
        unknown_universe = runner.tool("EXTRA-UNIVERSE-UNKNOWN", "universe_list_symbols", {"universe_key": f"missing-{uuid4()}"})
        unknown_rows = _data(unknown_universe).get("items") or _data(unknown_universe).get("symbols") or []
        _record(runner, "EXTRA-UNIVERSE-UNKNOWN", "Unknown universe does not fall back", _rejected(unknown_universe) or not unknown_rows,
                "unknown universe is rejected or empty", "unknown universe returned another universe's symbols", {"error_code": _error_code(unknown_universe), "count": len(unknown_rows)}, severity="P0")
    else:
        for case_id in ("EXTRA-UNIVERSE", "EXTRA-UNIVERSE-UNKNOWN"):
            _record(runner, case_id, "Universe precondition", False, "", "", blocked="no universe discovered")
    if discovered.get("daily"):
        daily = discovered["daily"]
        date_call = runner.tool("EXTRA-DAILY-DATE", "environment_get_daily", {"label_kind": daily["label_kind"], "environment_date": str(daily["environment_date"]), "limit": 10})
        date_rows = _items(_data(date_call))
        date_ok = _success(date_call) and all(str(row.get("environment_date")) == str(daily["environment_date"]) for row in date_rows)
        _record(runner, "EXTRA-DAILY-DATE", "Daily exact-date filter returns only the requested date", date_ok,
                "all returned rows match the requested date", "date filter was ignored or mixed dates", {"requested_date": str(daily["environment_date"]), "returned_count": len(date_rows)})
        invalid_date = runner.tool("EXTRA-DAILY-INVALID-DATE", "environment_get_daily", {"label_kind": daily["label_kind"], "environment_date": "not-a-date", "limit": 10})
        _record(runner, "EXTRA-DAILY-INVALID-DATE", "Invalid daily date is rejected", _rejected(invalid_date),
                "invalid date returned a structured rejection", "invalid date was silently accepted", {"error_code": _error_code(invalid_date)})
    if discovered.get("kb"):
        kb = runner.tool("EXTRA-KB-ID", "kb_factor_candidate_search", {"extraction_id": int(discovered["kb"]["id"])})
        kb_rows = _items(_data(kb))
        kb_ids = {str(row.get("extraction_id")) for row in kb_rows if row.get("extraction_id") is not None}
        _record(runner, "EXTRA-KB-ID", "KB extraction-id lookup is exact", _success(kb) and (not kb_rows or str(discovered["kb"]["id"]) in kb_ids),
                "KB lookup returned the requested extraction or a documented empty result", "KB lookup returned a different extraction", {"requested_id": discovered["kb"]["id"], "returned_ids": sorted(kb_ids)})
    else:
        _record(runner, "EXTRA-KB-ID", "KB precondition", False, "", "", blocked="no extraction discovered")

    # Formula identity and approved dependency check.
    if discovered.get("evidence") and factor_ref:
        evidence = discovered["evidence"]
        formula = runner.tool("EXTRA-FORMULA", "factor_get_formula", _formula_args(evidence, factor_ref))
        formula_data = _data(formula)
        expression_ok = formula_data.get("expression") == evidence.get("expression")
        hash_ok = formula_data.get("formula_hash") == evidence.get("formula_hash")
        _record(runner, "EXTRA-FORMULA", "Formula lookup matches immutable DB evidence", _success(formula) and expression_ok and hash_ok,
                "formula expression and hash match the selected completed run", "formula expression or hash differs from DB evidence",
                {"factor_ref": factor_ref, "run_id": evidence.get("run_id"), "expression_match": expression_ok, "hash_match": hash_ok})
        wrong_formula = runner.tool("EXTRA-FORMULA-WRONG-WINDOW", "factor_get_formula", {**_formula_args(evidence, factor_ref), "factor_window_bars": "999999H"})
        _record(runner, "EXTRA-FORMULA-WRONG-WINDOW", "Formula scope mismatch is not silently substituted", _rejected(wrong_formula) or not _data(wrong_formula),
                "wrong window was rejected or empty", "wrong window returned a different formula", {"error_code": _error_code(wrong_formula)})
    else:
        for case_id in ("EXTRA-FORMULA", "EXTRA-FORMULA-WRONG-WINDOW"):
            _record(runner, case_id, "Formula precondition", False, "", "", blocked="no completed formula evidence discovered")

    # Unknown method/tool/extra argument protocol boundaries.
    unknown_tool = runner.tool("EXTRA-UNKNOWN-TOOL", "tool_that_does_not_exist", {})
    extra_arg = runner.tool("EXTRA-UNKNOWN-ARG", "universe_list_symbols", {"universe_key": discovered.get("universe_key") or "all", "unexpected": 1})
    _record(runner, "EXTRA-PROTOCOL-ERRORS", "Unknown tool and invalid argument produce structured errors", _rejected(unknown_tool) and _rejected(extra_arg),
            "both invalid calls were rejected", "an invalid MCP call returned successful business data",
            {"unknown_tool_error": _error_code(unknown_tool), "extra_argument_error": _error_code(extra_arg)})
    malformed_status, malformed_env, _ = _raw_request(token, b'{"jsonrpc":"2.0",', label="malformed-json")
    malformed_ok = malformed_status >= 400 or (isinstance(malformed_env, dict) and ("error" in malformed_env or "result" not in malformed_env))
    _record(runner, "EXTRA-MALFORMED-JSON", "Malformed JSON is rejected before business dispatch", malformed_ok,
            "malformed request returned a protocol/HTTP error", "malformed request was accepted as business data",
            {"http_status": malformed_status, "has_top_level_error": isinstance(malformed_env, dict) and "error" in malformed_env})

    # New session/reconnect should have the same read behavior.
    reconnect_dir = output / "reconnect"
    reconnect = catalog.Runner(token, reconnect_dir, db)
    reconnect_init = reconnect.request("RECONNECT-INIT", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest-reconnect", "version": "1.0"}})
    reconnect_result = ((reconnect_init.get("envelope") or {}).get("result") or {})
    reconnect.protocol_version = reconnect_result.get("protocolVersion")
    reconnect.notify_initialized("RECONNECT-NOTIFY")
    reconnect_schema = reconnect.tool("RECONNECT-SCHEMA", "schema_get_factor_fields", {})
    reconnect_ok = _success(reconnect_schema) and _success(fields) and _hash_business(reconnect_schema) == _hash_business(fields)
    _record(runner, "EXTRA-RECONNECT", "A new MCP session can perform the same read", reconnect_ok,
            "reconnected session returned the same schema business payload", "reconnected session failed or returned a different schema",
            {"initial_protocol": runner.protocol_version, "reconnect_protocol": reconnect.protocol_version, "initial_hash": _hash_business(fields), "reconnect_hash": _hash_business(reconnect_schema)})

    after_state = _db_state(db)
    unchanged = before_state == after_state
    _record(runner, "EXTRA-READ-ONLY", "Additional read-only calls do not mutate business tables", unchanged,
            "business-table counts and update markers are unchanged", "business-table state changed during read-only checks",
            {"unchanged": unchanged, "before": before_state, "after": after_state}, severity="P0")

    comparable = [call for call in runner.calls if call.get("representations_equal") is not None]
    unequal = [call.get("case_id") for call in comparable if call.get("representations_equal") is False]
    _record(runner, "EXTRA-REPRESENTATIONS", "content and structuredContent agree", not unequal and bool(comparable),
            "all comparable responses have identical business representations", "content and structuredContent differ",
            {"compared_count": len(comparable), "unequal_cases": unequal})

    counts: dict[str, int] = {}
    for case in runner.cases:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    report = {
        "environment": "test",
        "mcp_url": MCP_URL,
        "mode": "READ_ONLY",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "database": settings.database.name,
        "discovered": {
            "route": {k: v for k, v in (discovered.get("route") or {}).items() if k not in {"evidence"}},
            "summary": {k: v for k, v in (discovered.get("summary") or {}).items() if k not in {"metrics_json"}},
            "schema_version": discovered.get("schema_version"),
        },
        "case_counts": counts,
        "cases": runner.cases,
        "before_state": before_state,
        "after_state": after_state,
    }
    catalog._write_json(output / "summary.json", report)
    print(json.dumps({"output": str(output), "case_counts": counts, "failed": [c["case_id"] for c in runner.cases if c["status"] == "FAIL"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
