#!/usr/bin/env python3
"""Run a small, read-only Factor 4.0 MCP cross-invariant regression."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402
from tmp import catalog_deep_readonly as catalog  # noqa: E402


TOKEN_ENV = "CATALOG_MCP_TOKEN"
DEFAULT_MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _field_names(data: dict[str, Any]) -> set[str]:
    fields = data.get("fields")
    if not isinstance(fields, list):
        return set()
    return {
        str(item["field_name"])
        for item in fields
        if isinstance(item, dict) and item.get("field_name") is not None
    }


def _required_fields(value: Any) -> list[str]:
    parsed = _parse_json(value)
    if isinstance(parsed, list):
        return [str(item) for item in parsed if isinstance(item, str)]
    if isinstance(parsed, dict):
        for key in ("required_fields", "fields", "dependencies"):
            result = _required_fields(parsed.get(key))
            if result:
                return result
    return []


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "metrics", "recommendations", "tags"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)
    return hashlib.sha256(payload.encode()).hexdigest()


def _is_rejected(call: dict[str, Any]) -> bool:
    return catalog._rejected(call)


def _schema_db_snapshot(db: DatabaseClient) -> dict[str, Any]:
    approved = db.fetch_one(
        """
        SELECT schema_version, schema_hash, status, approved_at
        FROM raw_data_schema_version
        WHERE status='approved'
        ORDER BY approved_at DESC, id DESC
        LIMIT 1
        """
    )
    if approved is None:
        raise RuntimeError("No approved raw-data schema exists")
    version = approved["schema_version"]
    mappings = db.fetch_all(
        """
        SELECT field_name, source_dataset, source_field, data_type
        FROM raw_data_field_mapping
        WHERE schema_version=%s
        ORDER BY field_name
        """,
        (version,),
    )
    resolutions = db.fetch_all(
        """
        SELECT field_name, field_class, expression, dependency_fields_json, data_type
        FROM factor_field_resolution_mapping
        WHERE schema_version=%s
        ORDER BY field_name
        """,
        (version,),
    )
    replay_count = db.fetch_one(
        "SELECT COUNT(*) AS row_count FROM factor_replay_case WHERE schema_version=%s",
        (version,),
    )
    return {
        "approved": approved,
        "mapping_count": len(mappings),
        "mapping_fields": [row["field_name"] for row in mappings],
        "resolution_count": len(resolutions),
        "resolution_fields": [row["field_name"] for row in resolutions],
        "resolution_dependencies": {
            str(row["field_name"]): _parse_json(row.get("dependency_fields_json"))
            for row in resolutions
        },
        "replay_count": int((replay_count or {}).get("row_count") or 0),
    }


def _feedback_count(db: DatabaseClient) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS row_count FROM market_environment_strategy_feedback_submissions"
    )
    return int((row or {}).get("row_count") or 0)


def _formula_candidate(db: DatabaseClient, approved_fields: set[str]) -> dict[str, Any] | None:
    rows = db.fetch_all(
        """
        SELECT e.run_id, e.factor_id, e.is_sub_factor_id, e.calculation_mode,
               e.factor_bar_interval, e.factor_window_bars, e.return_bar_interval,
               e.forward_return_bars, e.formula_version, e.formula_hash,
               e.expression, e.required_fields, e.recorded_at
        FROM factor_ic_run_formula_evidence e
        JOIN factor_ic_runs r ON r.run_id=e.run_id
        WHERE r.status='completed' AND e.calculation_mode='direct'
        ORDER BY e.recorded_at DESC, e.id DESC
        LIMIT 5000
        """
    )
    for row in rows:
        required = _required_fields(row.get("required_fields"))
        if required and set(required) <= approved_fields:
            row["required_fields_parsed"] = required
            row["factor_ref"] = (
                f"sub_factor:{row['factor_id']}"
                if int(row["is_sub_factor_id"])
                else f"factor:{row['factor_id']}"
            )
            return row
    return None


def _active_route(db: DatabaseClient) -> dict[str, Any] | None:
    return db.fetch_one(
        """
        SELECT r.id AS route_id, r.metric_id, r.factor_ref, r.market_scope,
               r.label_code, r.publication_uid, r.eval_batch_id, r.publish_version,
               m.evaluation_type, m.factor_version, b.batch_uid,
               b.route_profile_key, b.publication_uid AS batch_publication_uid
        FROM market_environment_factor_route r
        JOIN market_environment_factor_metric m ON m.id=r.metric_id
        JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
        WHERE r.is_active=1 AND r.is_eligible=1
        ORDER BY r.activated_at DESC, r.id DESC
        LIMIT 1
        """
    )


def _identity_values(value: Any, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for current_key, current_value in value.items():
            if current_key == key and current_value is not None:
                found.append(str(current_value))
            found.extend(_identity_values(current_value, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_identity_values(item, key))
    return found


def _record_call(
    runner: catalog.Runner,
    case_id: str,
    title: str,
    call: dict[str, Any],
    ok: bool,
    reason_ok: str,
    reason_fail: str,
    evidence: dict[str, Any],
    severity: str = "P1",
) -> None:
    runner.record(
        case_id,
        title,
        "PASS" if ok else "FAIL",
        reason_ok if ok else reason_fail,
        evidence={
            **evidence,
            "http_status": call.get("http_status"),
            "error_code": catalog._error_code(call),
        },
        failure_class=None if ok else "FAIL_DATA_CONSISTENCY",
        severity=None if ok else severity,
    )


def main() -> None:
    """Execute the bounded read-only checks and save sanitized evidence."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    mcp_url = os.environ.get("MCP_URL", DEFAULT_MCP_URL)
    parsed_url = urlparse(mcp_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "test-factor-frontend.questvector.ai":
        raise SystemExit("This runner only permits the test MCP host")
    catalog.MCP_URL = mcp_url

    settings = SettingsLoader.load("test", PROJECT_ROOT)
    if settings.environment != "test":
        raise SystemExit("Test environment gate is not satisfied")
    db = DatabaseClient.from_settings(settings.database)
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = PROJECT_ROOT / "reports" / "factor4-deep" / f"{run_stamp}-cross-invariants"
    runner = catalog.Runner(token, output_dir, db)

    schema_db = _schema_db_snapshot(db)
    feedback_before = _feedback_count(db)
    route = _active_route(db)
    db_snapshots: dict[str, Any] = {
        "database": settings.database.name,
        "schema_before": schema_db,
        "feedback_before_count": feedback_before,
        "selected_route": route,
    }

    init = runner.request(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-cross-invariants", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    runner.protocol_version = init_result.get("protocolVersion")
    init_ok = init.get("http_status") == 200 and runner.protocol_version == "2025-06-18"
    _record_call(
        runner,
        "MCP-INIT",
        "MCP initialization",
        init,
        init_ok,
        "Protocol negotiation succeeded.",
        "Protocol negotiation failed.",
        {"protocol_version": runner.protocol_version, "server_info": init_result.get("serverInfo")},
        "P0",
    )
    if not init_ok:
        raise RuntimeError("MCP initialization failed")
    runner.notify_initialized("MCP-NOTIFY")
    tools_call = runner.request("MCP-TOOLS", "tools/list", {})
    tool_names = {
        item.get("name")
        for item in (((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or [])
        if isinstance(item, dict)
    }
    required_tools = {
        "schema_get_factor_fields",
        "schema_get_raw_data",
        "get_feedback_submission_status",
        "factor_get_formula",
        "environment_get_recommendations",
        "factor_get_environment_metrics",
        "factor_get_environment_tags",
    }
    tools_ok = required_tools <= tool_names
    _record_call(
        runner,
        "MCP-TOOLS",
        "Required read-only tools are discoverable",
        tools_call,
        tools_ok,
        "All required tools are present.",
        "One or more required tools are absent.",
        {"missing": sorted(required_tools - tool_names), "tool_count": len(tool_names)},
    )

    approved_version = str(schema_db["approved"]["schema_version"])
    expected_fields = set(schema_db["mapping_fields"]) | set(schema_db["resolution_fields"])
    fields_full = runner.tool("SCH-001", "schema_get_factor_fields", {})
    fields_data = catalog._data(fields_full)
    actual_fields = _field_names(fields_data)
    fields_ok = (
        catalog._success(fields_full)
        and fields_data.get("schema_version") == approved_version
        and actual_fields == expected_fields
    )
    _record_call(
        runner,
        "SCH-001",
        "Factor field Schema matches the approved DB Schema",
        fields_full,
        fields_ok,
        "Schema version and all approved field names match DB.",
        "MCP field Schema and approved DB Schema differ.",
        {
            "schema_version": fields_data.get("schema_version"),
            "expected_count": len(expected_fields),
            "actual_count": len(actual_fields),
            "missing": sorted(expected_fields - actual_fields),
            "unexpected": sorted(actual_fields - expected_fields),
        },
    )

    vwap_args = {"field_names": ["vwap"]}
    vwap_calls = [runner.tool("SCH-002-A", "schema_get_factor_fields", vwap_args)]
    vwap_data = catalog._data(vwap_calls[0])
    vwap_ok = catalog._success(vwap_calls[0]) and _field_names(vwap_data) == {"vwap"}
    vwap_field = next(
        (item for item in vwap_data.get("fields", []) if isinstance(item, dict)),
        {},
    )
    dependencies = set(_required_fields(vwap_field.get("dependency_fields")))
    if not dependencies:
        dependencies = set(_required_fields(vwap_field.get("dependency_fields_json")))
    dependency_ok = dependencies <= expected_fields and dependencies == {"high", "low", "close", "volume"}
    _record_call(
        runner,
        "SCH-002",
        "VWAP selector returns one resolvable approved derived field",
        vwap_calls[0],
        vwap_ok and dependency_ok,
        "VWAP resolves only to approved high/low/close/volume dependencies.",
        "VWAP selector or its dependency resolution is inconsistent.",
        {"returned_fields": sorted(_field_names(vwap_data)), "dependencies": sorted(dependencies)},
    )

    unknown_name = f"questtest_unknown_{uuid4().hex}"
    unknown_field = runner.tool(
        "SCH-003", "schema_get_factor_fields", {"field_names": [unknown_name]}
    )
    unknown_data = catalog._data(unknown_field)
    unknown_rows = [item for item in unknown_data.get("fields", []) if isinstance(item, dict)]
    unknown_ok = _is_rejected(unknown_field) or (
        len(unknown_rows) == 1
        and unknown_rows[0].get("field_name") == unknown_name
        and unknown_rows[0].get("resolution_status") == "unresolved"
        and "FIELD_NOT_APPROVED" in (unknown_rows[0].get("unresolved_reasons") or [])
        and not unknown_rows[0].get("final_raw_dependencies")
    )
    _record_call(
        runner,
        "SCH-003",
        "Unknown field never falls back to the full Schema",
        unknown_field,
        unknown_ok,
        "Unknown field was rejected or returned an explicit unresolved diagnostic.",
        "Unknown field fell back to unrelated approved fields or lacked an unresolved diagnostic.",
        {
            "unknown_field": unknown_name,
            "returned_fields": sorted(_field_names(unknown_data)),
            "resolution_status": unknown_rows[0].get("resolution_status") if unknown_rows else None,
            "unresolved_reasons": unknown_rows[0].get("unresolved_reasons") if unknown_rows else None,
        },
        "P0",
    )

    raw_default = runner.tool("SCH-004", "schema_get_raw_data", {})
    raw_data = catalog._data(raw_default)
    mapping_names = {
        str(item.get("field_name"))
        for item in raw_data.get("mappings", [])
        if isinstance(item, dict) and item.get("field_name") is not None
    }
    resolution_names = {
        str(item.get("field_name"))
        for item in raw_data.get("field_resolutions", [])
        if isinstance(item, dict) and item.get("field_name") is not None
    }
    replay_cases = raw_data.get("replay_cases")
    raw_ok = (
        catalog._success(raw_default)
        and raw_data.get("schema_version") == approved_version
        and mapping_names == set(schema_db["mapping_fields"])
        and resolution_names == set(schema_db["resolution_fields"])
        and isinstance(replay_cases, list)
        and len(replay_cases) == schema_db["replay_count"]
    )
    _record_call(
        runner,
        "SCH-004",
        "Raw-data Schema matches DB mappings, resolutions, and replay fixtures",
        raw_default,
        raw_ok,
        "Raw Schema content matches all DB row sets.",
        "Raw Schema content differs from DB.",
        {
            "mapping_count": len(mapping_names),
            "resolution_count": len(resolution_names),
            "replay_count": len(replay_cases) if isinstance(replay_cases, list) else None,
        },
    )

    unknown_version = f"questtest-missing-{uuid4()}"
    fields_unknown_version = runner.tool(
        "SCH-005", "schema_get_factor_fields", {"schema_version": unknown_version}
    )
    fields_unknown_data = catalog._data(fields_unknown_version)
    fields_unknown_ok = _is_rejected(fields_unknown_version) or not _field_names(fields_unknown_data)
    _record_call(
        runner,
        "SCH-005",
        "Unknown factor-field Schema version does not fall back to current",
        fields_unknown_version,
        fields_unknown_ok,
        "Unknown version was rejected or returned no fields.",
        "Unknown version silently returned current Schema fields.",
        {"requested_version": unknown_version, "returned_version": fields_unknown_data.get("schema_version")},
        "P0",
    )

    raw_unknown_version = runner.tool(
        "SCH-006", "schema_get_raw_data", {"schema_version": unknown_version}
    )
    raw_unknown_data = catalog._data(raw_unknown_version)
    raw_unknown_rows = sum(
        len(raw_unknown_data.get(key) or [])
        for key in ("mappings", "field_resolutions", "replay_cases")
        if isinstance(raw_unknown_data.get(key), list)
    )
    raw_unknown_ok = _is_rejected(raw_unknown_version) or raw_unknown_rows == 0
    _record_call(
        runner,
        "SCH-006",
        "Unknown raw Schema version does not fall back to current",
        raw_unknown_version,
        raw_unknown_ok,
        "Unknown version was rejected or returned no rows.",
        "Unknown version silently returned current raw Schema rows.",
        {"requested_version": unknown_version, "returned_version": raw_unknown_data.get("schema_version"), "returned_rows": raw_unknown_rows},
        "P0",
    )

    raw_invalid = runner.tool("SCH-007", "schema_get_raw_data", {"unexpected": True})
    invalid_ok = _is_rejected(raw_invalid)
    _record_call(
        runner,
        "SCH-007",
        "Raw Schema rejects undeclared arguments",
        raw_invalid,
        invalid_ok,
        "Undeclared argument was rejected.",
        "Undeclared argument was accepted.",
        {"arguments": {"unexpected": True}},
    )

    nonexistent_id = str(uuid4())
    feedback_missing = runner.tool(
        "FB-001", "get_feedback_submission_status", {"submission_id": nonexistent_id}
    )
    feedback_missing_ok = _is_rejected(feedback_missing)
    _record_call(
        runner,
        "FB-001",
        "Nonexistent well-formed feedback submission is not fabricated",
        feedback_missing,
        feedback_missing_ok,
        "Nonexistent submission returned a not-found/error result.",
        "Nonexistent submission returned business success.",
        {"submission_id": nonexistent_id},
        "P0",
    )
    feedback_invalid = runner.tool(
        "FB-002", "get_feedback_submission_status", {"submission_id": ""}
    )
    feedback_invalid_ok = _is_rejected(feedback_invalid)
    _record_call(
        runner,
        "FB-002",
        "Feedback status rejects an empty submission id",
        feedback_invalid,
        feedback_invalid_ok,
        "Empty submission id was rejected.",
        "Empty submission id was accepted.",
        {"submitted_length": 0},
    )

    candidate = _formula_candidate(db, expected_fields)
    db_snapshots["formula_candidate"] = candidate
    if candidate is None:
        runner.record(
            "FORM-001",
            "Exact-run formula required fields resolve through approved Schema",
            "BLOCKED",
            "No completed direct formula evidence with nonempty Schema-resolvable required_fields exists.",
            failure_class="BLOCKED_DATA_PRECONDITION",
        )
    else:
        formula_args = {
            "factor_ref": candidate["factor_ref"],
            "run_id": candidate["run_id"],
            "calculation_mode": "direct",
            "interval": candidate["factor_bar_interval"],
            "factor_window_bars": candidate["factor_window_bars"],
            "return_bar_interval": candidate["return_bar_interval"],
            "forward_return_bars": int(candidate["forward_return_bars"]),
        }
        formula = runner.tool("FORM-001", "factor_get_formula", formula_args)
        formula_data = catalog._data(formula)
        returned_required = _required_fields(formula_data.get("required_fields"))
        evidence_required = candidate["required_fields_parsed"]
        formula_ok = (
            catalog._success(formula)
            and formula_data.get("factor_ref") == candidate["factor_ref"]
            and formula_data.get("run_id") == candidate["run_id"]
            and returned_required == evidence_required
            and set(returned_required) <= expected_fields
        )
        _record_call(
            runner,
            "FORM-001",
            "Exact-run formula required fields match evidence and approved Schema",
            formula,
            formula_ok,
            "Formula identity and every required field match DB evidence and resolve in Schema.",
            "Formula identity or required fields differ from exact-run DB evidence/Schema.",
            {
                "factor_ref": candidate["factor_ref"],
                "run_id": candidate["run_id"],
                "evidence_required_fields": evidence_required,
                "returned_required_fields": returned_required,
                "unresolved_fields": sorted(set(returned_required) - expected_fields),
            },
            "P0",
        )

    recommendation = runner.tool(
        "ENV-001",
        "environment_get_recommendations",
        {"market_scope": (route or {}).get("market_scope", "all"), "route_profile_key": (route or {}).get("route_profile_key", "default"), "limit": 20},
    )
    recommendation_data = catalog._data(recommendation)
    recommendation_ok = catalog._success(recommendation)
    if route:
        publication_uids = _identity_values(recommendation_data.get("publication"), "publication_uid")
        batch_uids = _identity_values(recommendation_data.get("publication"), "batch_uid")
        recommendation_ok = recommendation_ok and (
            not publication_uids or str(route["publication_uid"]) in publication_uids
        ) and (not batch_uids or str(route["batch_uid"]) in batch_uids)
    _record_call(
        runner,
        "ENV-001",
        "Recommendation publication identity binds to the active publication",
        recommendation,
        recommendation_ok,
        "Recommendation result is internally bound to the current active publication.",
        "Recommendation result references a different active publication/batch.",
        {
            "status": recommendation_data.get("status"),
            "reason_code": recommendation_data.get("reason_code"),
            "selected_route_publication_uid": (route or {}).get("publication_uid"),
            "selected_route_batch_uid": (route or {}).get("batch_uid"),
        },
        "P0",
    )

    if route is None:
        for case_id, title in (
            ("ENV-002", "Environment tags bind to an active-route factor"),
            ("ENV-003", "Environment metrics bind to route metric identity"),
        ):
            runner.record(
                case_id,
                title,
                "BLOCKED",
                "No active eligible route exists in the test database.",
                failure_class="BLOCKED_DATA_PRECONDITION",
            )
    else:
        common_args = {
            "factor_ref": route["factor_ref"],
            "market_scope": route["market_scope"],
            "route_profile_key": route["route_profile_key"],
        }
        tags = runner.tool("ENV-002", "factor_get_environment_tags", common_args)
        tags_data = catalog._data(tags)
        tag_items = _items(tags_data)
        tag_refs = [str(item.get("factor_ref")) for item in tag_items if item.get("factor_ref")]
        tag_publication = tags_data.get("publication") or {}
        tags_ok = (
            catalog._success(tags)
            and tags_data.get("factor_ref") == route["factor_ref"]
            and bool(tag_items)
            and set(tag_refs) == {str(route["factor_ref"])}
            and str(tag_publication.get("publication_uid")) == str(route["publication_uid"])
            and str(tag_publication.get("batch_uid")) == str(route["batch_uid"])
            and all(str(item.get("publication_uid")) == str(route["publication_uid"]) for item in tag_items)
        )
        _record_call(
            runner,
            "ENV-002",
            "Environment tags bind to the requested active-route factor",
            tags,
            tags_ok,
            "All returned tag identities belong to the requested factor.",
            "Tag response contains a different factor identity.",
            {
                "factor_ref": route["factor_ref"],
                "returned_factor_refs": sorted(set(tag_refs)),
                "returned_publication_uid": tag_publication.get("publication_uid"),
                "returned_batch_uid": tag_publication.get("batch_uid"),
            },
            "P0",
        )

        metric_args = {
            **common_args,
            "batch_uid": route["batch_uid"],
            "label_code": route["label_code"],
            "evaluation_type": route["evaluation_type"],
            "limit": 100,
        }
        metrics = runner.tool("ENV-003", "factor_get_environment_metrics", metric_args)
        metrics_data = catalog._data(metrics)
        metric_items = _items(metrics_data)
        metric_batch = metrics_data.get("batch") or {}
        metric_refs = [str(item.get("factor_ref")) for item in metric_items if item.get("factor_ref")]
        metric_labels = [str(item.get("label_code")) for item in metric_items if item.get("label_code")]
        metric_types = [str(item.get("evaluation_type")) for item in metric_items if item.get("evaluation_type")]
        metrics_ok = (
            catalog._success(metrics)
            and metrics_data.get("factor_ref") == route["factor_ref"]
            and str(metric_batch.get("batch_uid")) == str(route["batch_uid"])
            and str(metric_batch.get("publication_uid")) == str(route["publication_uid"])
            and bool(metric_items)
            and set(metric_refs) == {str(route["factor_ref"])}
            and set(metric_labels) == {str(route["label_code"])}
            and set(metric_types) == {str(route["evaluation_type"])}
            and all(int(item.get("eval_batch_id")) == int(route["eval_batch_id"]) for item in metric_items)
        )
        _record_call(
            runner,
            "ENV-003",
            "Environment metrics bind to exact route factor, batch, label, and type",
            metrics,
            metrics_ok,
            "Returned metrics have the exact selected route identity.",
            "Metric response is empty or crosses route identity boundaries.",
            {
                "expected": metric_args,
                "returned_factor_refs": sorted(set(metric_refs)),
                "returned_batch_uid": metric_batch.get("batch_uid"),
                "returned_publication_uid": metric_batch.get("publication_uid"),
                "returned_labels": sorted(set(metric_labels)),
                "returned_evaluation_types": sorted(set(metric_types)),
                "returned_item_count": len(metric_items),
            },
            "P0",
        )

    vwap_calls.extend(
        [
            runner.tool("STABLE-001-B", "schema_get_factor_fields", vwap_args),
            runner.tool("STABLE-001-C", "schema_get_factor_fields", vwap_args),
        ]
    )
    stable_payloads = [catalog._data(call) for call in vwap_calls]
    stable_hashes = [_stable_hash(payload) for payload in stable_payloads]
    stable_ok = all(catalog._success(call) for call in vwap_calls) and len(set(stable_hashes)) == 1
    runner.record(
        "STABLE-001",
        "Three identical read-only Schema requests return stable business data",
        "PASS" if stable_ok else "FAIL",
        "All three business payloads are byte-normalized identical." if stable_ok else "Identical read-only requests returned different business data.",
        evidence={"data_hashes": stable_hashes, "request_count": 3},
        failure_class=None if stable_ok else "FAIL_NONDETERMINISTIC_READ",
        severity=None if stable_ok else "P1",
    )

    feedback_after = _feedback_count(db)
    schema_after = _schema_db_snapshot(db)
    db_snapshots.update(
        {
            "feedback_after_count": feedback_after,
            "schema_after": schema_after,
        }
    )
    readonly_ok = feedback_before == feedback_after and schema_db == schema_after
    runner.record(
        "READONLY-001",
        "MCP status and Schema reads have no observed DB side effects",
        "PASS" if readonly_ok else "FAIL",
        "Feedback count and Schema DB snapshot are unchanged." if readonly_ok else "A protected DB snapshot changed during read-only calls.",
        evidence={
            "feedback_before": feedback_before,
            "feedback_after": feedback_after,
            "schema_snapshot_equal": schema_db == schema_after,
        },
        failure_class=None if readonly_ok else "FAIL_UNEXPECTED_MUTATION",
        severity=None if readonly_ok else "P0",
    )

    # Recheck a suspected functional failure once, without expanding the matrix.
    failures = [row for row in runner.cases if row["status"] == "FAIL"]
    rechecks: list[dict[str, Any]] = []
    if failures:
        first = failures[0]
        matching = next(
            (
                call
                for call in runner.calls
                if call.get("case_id") == first["case_id"]
                and call.get("tool")
                and isinstance(call.get("arguments"), dict)
            ),
            None,
        )
        if matching:
            repeated = runner.tool(
                f"{first['case_id']}-RECHECK",
                str(matching["tool"]),
                dict(matching["arguments"]),
            )
            rechecks.append(
                {
                    "original_case_id": first["case_id"],
                    "tool": matching["tool"],
                    "recheck_success": catalog._success(repeated),
                    "recheck_rejected": _is_rejected(repeated),
                    "recheck_data_hash": _stable_hash(catalog._data(repeated)),
                }
            )

    counts = Counter(case["status"] for case in runner.cases)
    summary = {
        "run_id": run_stamp,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "environment": "test",
        "mcp_host": parsed_url.hostname,
        "database": settings.database.name,
        "read_only": True,
        "case_counts": dict(sorted(counts.items())),
        "mcp_request_count": len(runner.calls),
        "tool_call_count": sum(call.get("tool") is not None for call in runner.calls),
        "failed_case_ids": [case["case_id"] for case in runner.cases if case["status"] == "FAIL"],
        "blocked_case_ids": [case["case_id"] for case in runner.cases if case["status"] == "BLOCKED"],
        "rechecks": rechecks,
    }
    _write_json(output_dir / "cases.json", runner.cases)
    _write_json(output_dir / "db-snapshots.json", db_snapshots)
    _write_json(output_dir / "summary.json", summary)
    _write_json(
        output_dir / "call-ledger.json",
        [
            {
                "case_id": call.get("case_id"),
                "tool": call.get("tool"),
                "arguments": call.get("arguments"),
                "http_status": call.get("http_status"),
                "error_code": catalog._error_code(call),
                "request_id": catalog._meta(call).get("request_id"),
                "trace_id": catalog._meta(call).get("trace_id"),
                "elapsed_seconds": call.get("elapsed_seconds"),
            }
            for call in runner.calls
        ],
    )
    lines = [
        "# Factor 4.0 cross-invariant read-only check",
        "",
        f"- Run: `{run_stamp}`",
        f"- Cases: PASS={counts.get('PASS', 0)}, FAIL={counts.get('FAIL', 0)}, BLOCKED={counts.get('BLOCKED', 0)}",
        f"- Tool calls: {summary['tool_call_count']}",
        "- Writes: none",
        "",
        "## Results",
        "",
    ]
    for case in runner.cases:
        lines.append(f"- `{case['status']}` `{case['case_id']}` {case['title']}: {case['reason']}")
    (output_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output_dir)
    print(json.dumps(summary, ensure_ascii=False, default=_json_default))


if __name__ == "__main__":
    main()
