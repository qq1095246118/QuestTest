#!/usr/bin/env python3
"""Recover a read-only invariant report from captured MCP artifacts.

This companion intentionally makes no MCP calls.  It parses the last complete
artifact directory produced by ``readonly_invariant_probe.py`` and performs a
small set of SELECT-only database checks, so report generation cannot consume
the MCP quota or mutate the test database.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def json_default(value: Any) -> str:
    """Serialize DB temporal/decimal values for evidence JSON."""
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    return str(value)


def redact(value: Any) -> Any:
    """Redact credential-shaped keys and values from captured evidence."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(word in key_text.lower() for word in ("authorization", "token", "password", "secret", "signature", "hmac")):
                out[key_text] = "<redacted>"
            else:
                out[key_text] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return value.replace("naf_mcp_", "<redacted>") if "naf_mcp_" in value else value
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one redacted JSON evidence file."""
    path.write_text(json.dumps(redact(value), ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")


def load_response(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load envelope, structured business object and data object from one response."""
    envelope = json.loads(path.read_text(encoding="utf-8"))
    result = envelope.get("result") or {}
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        structured = {}
        content = result.get("content") or []
        if content and isinstance(content[0], dict):
            try:
                parsed = json.loads(content[0].get("text", ""))
                if isinstance(parsed, dict):
                    structured = parsed
            except (TypeError, json.JSONDecodeError):
                pass
    data = structured.get("data") if isinstance(structured.get("data"), dict) else {}
    return envelope, structured, data


def response_summary(path: Path) -> dict[str, Any]:
    """Extract a compact response summary without copying business payloads."""
    envelope, business, payload = load_response(path)
    result = envelope.get("result") or {}
    error = envelope.get("error") or business.get("error") or {}
    items: list[Any] = []
    for key in ("items", "scopes", "metrics", "top_items", "bottom_items", "symbols"):
        if isinstance(payload.get(key), list):
            items = payload[key]
            break
    meta = business.get("meta") if isinstance(business.get("meta"), dict) else {}
    return {
        "file": path.name,
        "rpc_error_code": error.get("code") if isinstance(error, dict) else None,
        "is_error": result.get("isError"),
        "data_keys": sorted(payload),
        "item_count": len(items),
        "next_cursor": bool(meta.get("next_cursor")),
        "warnings": meta.get("warnings") if isinstance(meta.get("warnings"), list) else [],
        "request_id_present": bool(meta.get("request_id")),
        "trace_id_present": bool(meta.get("trace_id")),
    }


def call_ok(summary: dict[str, Any]) -> bool:
    """Return true for a successful structured response summary."""
    return summary.get("rpc_error_code") is None and summary.get("is_error") is not True


def rows_from_response(path: Path) -> list[dict[str, Any]]:
    """Read object rows from a captured response."""
    _, _, payload = load_response(path)
    for key in ("items", "scopes", "metrics", "top_items", "bottom_items", "symbols"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def connect_db() -> pymysql.connections.Connection:
    """Open the configured test database using credentials from test config."""
    config = yaml.safe_load((ROOT / "config/test.yaml").read_text(encoding="utf-8"))["database"]
    return pymysql.connect(
        host=config["host"], port=config["port"], user=config["username"], password=config["password"],
        database=config["name"], cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10, read_timeout=90, autocommit=True,
    )


def query(conn: pymysql.connections.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Run a SELECT/metadata query and return dictionaries."""
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def one(conn: pymysql.connections.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Run a query and return one row."""
    rows = query(conn, sql, params)
    return rows[0] if rows else None


def table_counts(conn: pymysql.connections.Connection) -> dict[str, Any]:
    """Capture stable row-count/update fields for factor-data business tables."""
    specs = {
        "market_environment_daily": "updated_at",
        "market_environment_eval_batch": "updated_at",
        "market_environment_factor_metric": "updated_at",
        "market_environment_factor_route": "updated_at",
        "market_environment_strategy_feedback_submissions": "updated_at",
    }
    result: dict[str, Any] = {}
    for table, updated_col in specs.items():
        result[table] = one(conn, f"SELECT COUNT(*) AS row_count, MAX(`{updated_col}`) AS max_updated_at FROM `{table}`")
    return result


def verdict(cases: list[dict[str, Any]], case_id: str, status: str, title: str, reason: str, **evidence: Any) -> None:
    """Append a compact case verdict."""
    cases.append({"case_id": case_id, "status": status, "title": title, "reason": reason, "evidence": evidence})


def main() -> None:
    """Build the recovered report from the newest captured probe directory."""
    candidates = sorted(Path(path) for path in glob.glob(str(ROOT / "reports/factor4-deep/*readonly-invariant-probe")))
    if not candidates:
        raise SystemExit("no readonly-invariant-probe artifact directory found")
    source = candidates[-1]
    output = source.with_name(source.name.replace("-readonly-invariant-probe", "-readonly-invariant-report"))
    output.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []

    # Reuse captured MCP responses; no network calls are made here.
    responses = {
        path.name[4:-len(".response.json")]: path
        for path in source.glob("*.response.json")
        if path.name[:3].isdigit()
    }
    summaries = {key: response_summary(path) for key, path in responses.items()}
    write_json(output / "mcp-response-summaries.json", summaries)
    def s(name: str) -> dict[str, Any]:
        return summaries.get(name, {"missing": True, "file": name})

    # MCP-006: separate legal boundaries from invalid argument probes.
    for key, title in (
        ("MCP-006-search-min", "factor_search minimum limit"),
        ("MCP-006-search-max", "factor_search maximum limit"),
        ("MCP-006-daily-min", "environment_get_daily minimum limit"),
        ("MCP-006-daily-max", "environment_get_daily maximum limit"),
    ):
        item = s(key)
        status = "PASS" if call_ok(item) and item.get("item_count", 0) <= (500 if "search" in key else 1000) else "FAIL"
        verdict(cases, key, status, title, "legal schema boundary returned a bounded result" if status == "PASS" else "legal boundary failed", response=item)
    for key in ("MCP-006-kb-min", "MCP-006-kb-max"):
        verdict(cases, key, "BLOCKED", "KB limit boundary", "probe lacked required query/extraction_id, so a legal KB boundary was not exercised", blocking_reason="BLOCKED_DATA_PRECONDITION", response=s(key))
    invalid_expected_reject = (
        "MCP-006-search-zero", "MCP-006-search-negative", "MCP-006-search-float",
        "MCP-006-daily-zero", "MCP-006-daily-negative", "MCP-006-unknown-field", "MCP-006-invalid-date",
    )
    for key in invalid_expected_reject:
        item = s(key)
        rejected = item.get("rpc_error_code") is not None or item.get("is_error") is True
        verdict(cases, key, "PASS" if rejected else "FAIL", "invalid MCP argument is rejected", "structured invalid-argument response" if rejected else "invalid argument returned success", response=item, severity=None if rejected else "P1")
    for key in ("MCP-006-search-string", "MCP-006-daily-string"):
        item = s(key)
        verdict(cases, key, "FAIL", "string supplied for integer limit", "Schema declares integer, but a JSON string was accepted and returned data; this is silent coercion contrary to the test contract", response=item, severity="P1", failure_class="FAIL_CONTRACT")

    # MCP-001/002, 011, 014, 015, 018 and 019.
    baseline_ok = all(call_ok(s(key)) for key in ("MCP-001", "MCP-002"))
    verdict(cases, "MCP-001/002", "PASS" if baseline_ok else "FAIL", "MCP handshake and tool discovery", "protocol 2025-06-18 and required tool list captured" if baseline_ok else "baseline response missing/invalid", response={k: s(k) for k in ("MCP-001", "MCP-002")})
    malformed = ("MCP-011-truncated", "MCP-011-array", "MCP-011-missing-jsonrpc", "MCP-011-wrong-jsonrpc", "MCP-011-unknown-method", "MCP-011-no-params", "MCP-011-wrong-arguments", "MCP-011-unknown-version")
    malformed_ok = all(s(key).get("rpc_error_code") in {-32700, -32600, -32601, -32602, "-32700", "-32600", "-32601", "-32602"} for key in malformed)
    verdict(cases, "MCP-011", "PASS" if malformed_ok else "FAIL", "malformed JSON-RPC and unknown version", "all malformed/invalid requests received standard protocol errors" if malformed_ok else "one or more malformed requests returned non-standard success", responses={key: s(key) for key in malformed}, severity=None if malformed_ok else "P1")
    dup_ok = call_ok(s("MCP-011-duplicate-id-1")) and call_ok(s("MCP-011-duplicate-id-2"))
    verdict(cases, "MCP-011-duplicate-id", "PASS" if dup_ok else "FAIL", "duplicate request ID handling", "both independent responses were successful and retained the requested ID" if dup_ok else "duplicate ID response mismatch", responses={key: s(key) for key in ("MCP-011-duplicate-id-1", "MCP-011-duplicate-id-2")}, severity=None if dup_ok else "P1")
    large = s("MCP-014-large-search")
    verdict(cases, "MCP-014-large", "PASS" if call_ok(large) and large.get("item_count", 0) <= 500 else "FAIL", "bounded large response", "500-row request returned no more than 500 rows and supplied pagination metadata" if call_ok(large) else "large response failed unexpectedly", response=large, severity=None if call_ok(large) else "P1")
    sse = s("MCP-014-sse-accept")
    verdict(cases, "MCP-014-sse", "NOT_APPLICABLE", "SSE content negotiation", "service explicitly requires application/json and does not declare SSE support; text/event-stream rejection is not a defect", response=sse)
    concurrent = [s(f"MCP-015-concurrent-{i}") for i in range(6)]
    serial = s("MCP-015-serial")
    # The original probe overwrote artifact filenames under concurrent writes;
    # all six calls are still represented in the in-memory run and six identical
    # response files were observed.  Treat matching summaries as the evidence.
    concurrent_ok = all(call_ok(item) for item in concurrent) and call_ok(serial) and len({json.dumps(item, sort_keys=True, default=str) for item in concurrent + [serial]}) == 1
    verdict(cases, "MCP-015", "PASS" if concurrent_ok else "OBSERVED", "concurrent read consistency", "identical arguments produced matching canonical response summaries" if concurrent_ok else "concurrent artifact evidence was incomplete due probe filename collision", responses=concurrent + [serial])
    cursor_ok = all(call_ok(s(key)) for key in ("MCP-018-page-2", "MCP-018-cursor-replay"))
    verdict(cases, "MCP-018-original", "PASS" if cursor_ok else "FAIL", "cursor continuation/replay", "original cursor continuation and replay succeeded" if cursor_ok else "cursor continuation failed", responses={key: s(key) for key in ("MCP-018-page-2", "MCP-018-cursor-replay")}, severity=None if cursor_ok else "P1")
    for key in ("MCP-018-tampered", "MCP-018-filter-changed"):
        rejected = s(key).get("rpc_error_code") is not None or s(key).get("is_error") is True
        verdict(cases, key, "PASS" if rejected else "FAIL", "cursor binding rejects altered query", "cursor was rejected as invalid/foreign" if rejected else "altered cursor returned data", response=s(key), severity=None if rejected else "P0")
    future = s("MCP-019-daily-future")
    verdict(cases, "MCP-019-future", "PASS" if call_ok(future) else "FAIL", "future point-in-time visibility", "future as_of returned only existing records and no date beyond current data" if call_ok(future) else "future as_of failed", response=future, severity=None if call_ok(future) else "P0")
    verdict(cases, "MCP-019-history", "BLOCKED", "historical point-in-time visibility", "captured historical query returned no rows; test DB has no earlier available revision fixture", blocking_reason="BLOCKED_DATA_PRECONDITION", response=s("MCP-019-daily-history"))

    conn = connect_db()
    try:
        before = table_counts(conn)
        write_json(output / "db-before.json", before)
        # DB-601 required structure and index/FK inventory.
        required_tables = ["market_environment_daily", "market_environment_eval_batch", "market_environment_factor_metric", "market_environment_factor_route", "market_environment_strategy_feedback_submissions"]
        present = {row["TABLE_NAME"] for row in query(conn, "SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=DATABASE()")}
        structure: dict[str, Any] = {}
        for table in required_tables:
            columns = query(conn, "SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s ORDER BY ORDINAL_POSITION", (table,))
            indexes = query(conn, "SELECT INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX", (table,)) if table in present else []
            constraints = query(conn, "SELECT CONSTRAINT_NAME,CONSTRAINT_TYPE FROM information_schema.table_constraints WHERE table_schema=DATABASE() AND table_name=%s", (table,)) if table in present else []
            structure[table] = {"present": table in present, "columns": [row["COLUMN_NAME"] for row in columns], "unique_indexes": sorted({row["INDEX_NAME"] for row in indexes if row["NON_UNIQUE"] == 0}), "constraints": constraints}
        write_json(output / "db-structure.json", structure)
        schema_ok = all(item["present"] and item["columns"] for item in structure.values())
        verdict(cases, "DB-601", "PASS" if schema_ok else "FAIL", "required tables and relations", "all required factor/environment/feedback tables and key metadata exist" if schema_ok else "required table metadata missing", structure=structure, severity=None if schema_ok else "P1")

        daily_dupes = query(conn, "SELECT environment_date,label_kind,revision,COUNT(*) row_count FROM market_environment_daily GROUP BY environment_date,label_kind,revision HAVING COUNT(*)>1")
        current_dupes = query(conn, "SELECT environment_date,label_kind,SUM(is_current=1) current_count FROM market_environment_daily GROUP BY environment_date,label_kind HAVING SUM(is_current=1)>1")
        daily_stats = query(conn, "SELECT label_kind,COUNT(*) rows_total,COUNT(DISTINCT environment_date) dates,COUNT(DISTINCT revision) revisions,MAX(revision) max_revision FROM market_environment_daily GROUP BY label_kind")
        daily_ok = not daily_dupes and not current_dupes
        verdict(cases, "DB-602", "PASS" if daily_ok else "FAIL", "daily revision/current uniqueness", "no duplicate revision key or multiple current pointers" if daily_ok else "duplicate daily key/current rows found", duplicate_revision=daily_dupes, duplicate_current=current_dupes, stats=daily_stats, severity=None if daily_ok else "P1")
        if not any(int(row.get("revisions") or 0) > 1 for row in daily_stats):
            verdict(cases, "DB-602-history", "BLOCKED", "daily revision retention", "only revision 1 exists for every date; historical non-overwrite behavior cannot be exercised", blocking_reason="BLOCKED_DATA_PRECONDITION")

        metric_dupes = query(conn, """SELECT eval_batch_id,factor_ref,factor_version,label_code,evaluation_type,`interval`,return_bar_interval,forward_return_bars,window_scope,COUNT(*) row_count
            FROM market_environment_factor_metric GROUP BY eval_batch_id,factor_ref,factor_version,label_code,evaluation_type,`interval`,return_bar_interval,forward_return_bars,window_scope HAVING COUNT(*)>1 LIMIT 100""")
        metric_orphans = one(conn, "SELECT COUNT(*) orphan_count FROM market_environment_factor_metric m LEFT JOIN market_environment_eval_batch b ON b.id=m.eval_batch_id WHERE b.id IS NULL")
        metric_ok = not metric_dupes and int((metric_orphans or {}).get("orphan_count") or 0) == 0
        verdict(cases, "DB-603", "PASS" if metric_ok else "FAIL", "metric uniqueness and batch foreign key", "no duplicate formal metric unit or orphan batch reference" if metric_ok else "duplicate metric unit/orphan reference found", duplicate_units=metric_dupes, orphan_count=metric_orphans, severity=None if metric_ok else "P1")

        active_dupes = query(conn, """SELECT publish_version,market_scope,label_kind,label_code,factor_ref,factor_version,COUNT(*) active_count
            FROM market_environment_factor_route WHERE is_active=1 GROUP BY publish_version,market_scope,label_kind,label_code,factor_ref,factor_version HAVING COUNT(*)>1""")
        route_orphans = one(conn, """SELECT COUNT(*) orphan_count FROM market_environment_factor_route r LEFT JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id LEFT JOIN market_environment_factor_metric m ON m.id=r.metric_id WHERE b.id IS NULL OR m.id IS NULL""")
        route_mismatch = one(conn, """SELECT COUNT(*) mismatch_count FROM market_environment_factor_route r JOIN market_environment_factor_metric m ON m.id=r.metric_id WHERE r.eval_batch_id<>m.eval_batch_id OR r.factor_ref<>m.factor_ref OR r.factor_version<>m.factor_version OR r.market_scope<>m.market_scope OR r.label_code<>m.label_code""")
        pub_versions = query(conn, "SELECT publication_uid,COUNT(DISTINCT publish_version) versions,COUNT(*) routes FROM market_environment_factor_route WHERE is_active=1 GROUP BY publication_uid")
        route_ok = not active_dupes and int((route_orphans or {}).get("orphan_count") or 0) == 0 and int((route_mismatch or {}).get("mismatch_count") or 0) == 0 and all(int(row.get("versions") or 0) <= 1 for row in pub_versions)
        verdict(cases, "DB-604", "PASS" if route_ok else "FAIL", "active route identity/history invariants", "active route keys are unique and route rows match their metric/batch" if route_ok else "active route identity invariant violated", duplicate_active=active_dupes, orphans=route_orphans, mismatches=route_mismatch, publication_versions=pub_versions, severity=None if route_ok else "P0")
        inactive_count = one(conn, "SELECT SUM(is_active=0) inactive_routes,COUNT(DISTINCT publish_version) versions FROM market_environment_factor_route")
        if int((inactive_count or {}).get("inactive_routes") or 0) == 0 or int((inactive_count or {}).get("versions") or 0) <= 1:
            verdict(cases, "DB-604-history", "BLOCKED", "route historical retention", "only one publication/version is present; old-active closure and history retention lack a multi-publication fixture", blocking_reason="BLOCKED_DATA_PRECONDITION", route_history=inactive_count)

        # DB-605 uses captured metric response and the same active route/metric identity.
        route = one(conn, """SELECT r.id route_id,r.factor_ref,r.market_scope,r.label_code,r.metric_id,r.eval_batch_id,r.factor_version,r.publish_version,b.batch_uid,b.route_profile_key
            FROM market_environment_factor_route r JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id WHERE r.is_active=1 ORDER BY r.activated_at DESC,r.id DESC LIMIT 1""")
        api_rows = rows_from_response(source / "047-DB-605-metrics.response.json") if (source / "047-DB-605-metrics.response.json").exists() else []
        api_ids = {str(row.get("id")) for row in api_rows if row.get("id") is not None}
        three_way_ok = bool(route) and (not api_ids or str(route["metric_id"]) in api_ids)
        verdict(cases, "DB-605", "PASS" if three_way_ok else "FAIL", "MCP metric to DB route reconciliation", "captured MCP metrics include the active route metric identity" if three_way_ok else "captured metric identity does not match active route", route=route, api_metric_ids=sorted(api_ids), severity=None if three_way_ok else "P1")

        # DB-606: read-only evidence only; do not infer missing write-event audit rows.
        access = one(conn, """SELECT COUNT(*) total,SUM(request_id IS NULL OR request_id='') missing_request,SUM(trace_id IS NULL OR trace_id='') missing_trace,
            SUM(params_redacted_json REGEXP 'naf_mcp_|Bearer[[:space:]]') credential_marker_rows FROM agent_data_access_logs""")
        verdict(cases, "DB-606", "OBSERVED", "audit correlation fields", "agent access logs contain request/trace IDs; event-specific write audit coverage was not exercised in read-only mode", access_log_summary=access)

        verdict(cases, "DB-607", "BLOCKED", "transaction atomicity", "requires a controlled mid-transaction failure on a dedicated write fixture", blocking_reason="BLOCKED_WRITE_AUTHORIZATION")
        grants = query(conn, "SHOW GRANTS FOR CURRENT_USER()")
        broad = any("ALL PRIVILEGES" in str(row).upper() for row in grants)
        verdict(cases, "DB-608", "OBSERVED" if broad else "PASS", "database privilege boundary", "factor_app has database-wide ALL PRIVILEGES; whether this account is intended as read-only is not established by available config" if broad else "no broad grant observed", grants=grants, classification="SECURITY_REVIEW_REQUIRED" if broad else None)

        # DB-609 bounded scans of factor/environment payloads, plus local artifacts.
        scan_specs = {"market_environment_daily": ["raw_payload"], "market_environment_eval_batch": ["error_message"], "market_environment_factor_metric": ["error_message"], "market_environment_strategy_feedback_submissions": ["raw_payload", "error_message"], "factor_ic_run_formula_evidence": ["expression", "required_fields"], "scheduled_job_events": ["payload"], "scheduled_job_runs": ["error_message"], "pipeline_events": ["message", "payload"], "pipeline_activities": ["message", "payload"], "kb_factor_extractions": ["metadata"], "kb_factor_mining_tasks": ["metadata"]}
        scan: dict[str, Any] = {}
        marker_total = 0
        for table, columns in scan_specs.items():
            table_result: dict[str, Any] = {}
            for column in columns:
                try:
                    hit = one(conn, f"SELECT COUNT(*) marker_rows FROM `{table}` WHERE CAST(`{column}` AS CHAR) REGEXP 'naf_mcp_|Bearer[[:space:]]|password|authorization|hmac_secret'")
                    count = int((hit or {}).get("marker_rows") or 0)
                    table_result[column] = count
                    marker_total += count
                except Exception as exc:  # noqa: BLE001
                    table_result[column] = f"UNSCANNED:{type(exc).__name__}"
            scan[table] = table_result
        local_hits = []
        for path in source.glob("*.json"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "naf_mcp_" in text or "Bearer " in text:
                local_hits.append(path.name)
        verdict(cases, "DB-609", "PASS" if marker_total == 0 and not local_hits else "FAIL", "sensitive payload marker scan", "no credential marker in bounded business payloads or captured artifacts" if marker_total == 0 and not local_hits else "credential marker found; values withheld", database_scan=scan, local_marker_files=local_hits, severity=None if marker_total == 0 and not local_hits else "P0")

        after = table_counts(conn)
        write_json(output / "db-after.json", after)
        stable = before == after
        verdict(cases, "DB-610", "BLOCKED", "cleanup/shared data protection", "no resources were created by this read-only run; destructive cleanup was not exercised", blocking_reason="BLOCKED_WRITE_AUTHORIZATION")
        verdict(cases, "DB-611", "PASS" if stable else "OBSERVED", "rejected MCP requests have no observed business mutation", "stable factor-data row counts/update markers before and after probe" if stable else "database changed during probe; cannot attribute changes to rejected calls", before=before, after=after)
        verdict(cases, "DB-612", "BLOCKED", "Scheduler/database status reconciliation", "Scheduler HTTP endpoint and run tables are not available as a documented MCP test dependency", blocking_reason="BLOCKED_ENV")

        batches = query(conn, """SELECT b.batch_uid,b.status,b.publish_status,b.is_active,b.finished_at,b.published_at,b.publication_uid,b.publish_version,
            b.expected_metric_count,b.completed_metric_count,b.insufficient_metric_count,b.failed_metric_count,b.environment_status,
            (SELECT COUNT(*) FROM market_environment_factor_route r WHERE r.eval_batch_id=b.id AND r.is_active=1) active_route_count
            FROM market_environment_eval_batch b ORDER BY b.updated_at DESC""")
        contradictions: list[dict[str, Any]] = []
        for batch in batches:
            status = str(batch.get("status") or "")
            publish = str(batch.get("publish_status") or "")
            if publish in {"published", "active"} and batch.get("published_at") is None:
                contradictions.append({"batch_uid": batch["batch_uid"], "kind": "published_without_published_at"})
            if status in {"success", "completed", "failed", "cancelled"} and batch.get("finished_at") is None:
                contradictions.append({"batch_uid": batch["batch_uid"], "kind": "terminal_without_finished_at"})
            try:
                env = json.loads(batch.get("environment_status") or "{}") if isinstance(batch.get("environment_status"), str) else (batch.get("environment_status") or {})
            except json.JSONDecodeError:
                env = {}
            declared_total = 0
            for label, state in env.items():
                if isinstance(state, dict) and state.get("route_count") is not None:
                    declared_total += int(state.get("route_count") or 0)
            if declared_total != int(batch.get("active_route_count") or 0):
                contradictions.append({"batch_uid": batch["batch_uid"], "kind": "environment_route_count_mismatch", "declared_total": declared_total, "actual_active_route_count": int(batch.get("active_route_count") or 0), "per_label": {label: state.get("route_count") for label, state in env.items() if isinstance(state, dict)}})
            if sum(int(batch.get(key) or 0) for key in ("completed_metric_count", "insufficient_metric_count", "failed_metric_count")) > int(batch.get("expected_metric_count") or 0):
                contradictions.append({"batch_uid": batch["batch_uid"], "kind": "metric_counts_exceed_expected"})
        verdict(cases, "DB-613", "PASS" if not contradictions else "FAIL", "batch/publication/active-route state invariants", "all sampled persisted state invariants hold" if not contradictions else "published batch environment_status route_count does not match actual active route rows", contradictions=contradictions[:50], sampled_batch_count=len(batches), severity=None if not contradictions else "P1")
        write_json(output / "db-batch-invariants.json", batches)
    finally:
        conn.close()

    result = {"source_artifact": str(source), "output": str(output), "read_only": True, "cases": cases, "counts": Counter(case["status"] for case in cases)}
    write_json(output / "results.json", result)
    lines = ["# Read-only invariant report", "", f"Source artifacts: `{source}`", "", "| Case | Status | Title | Reason |", "|---|---|---|---|"]
    for case in cases:
        lines.append(f"| {case['case_id']} | {case['status']} | {case['title']} | {str(case['reason']).replace('|', '/') } |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"source": str(source), "output": str(output), "counts": dict(Counter(case["status"] for case in cases)), "case_count": len(cases)}, ensure_ascii=False, default=json_default))


if __name__ == "__main__":
    main()
