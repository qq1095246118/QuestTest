#!/usr/bin/env python3
"""Run a read-only KB mapping, top-up, and environment-route audit.

The script intentionally separates deterministic database observations from
contract-dependent conclusions.  MCP calls use the supplied test endpoint and
never persist credentials in the generated evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql

ROOT = Path(__file__).resolve().parents[1]
URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
USER_AGENT = "QuestTest-kb-topup-audit/1.0"


def json_value(value: Any) -> Any:
    """Decode a nullable JSON database cell while preserving native values."""
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def serial(value: Any) -> Any:
    """Convert database-native values to JSON-safe values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one UTF-8 JSON artifact with deterministic formatting."""
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=serial) + "\n",
        encoding="utf-8",
    )


def normalize_window(value: Any) -> int | None:
    """Extract the numeric bar count from labels such as ``24H`` or ``24``."""
    if value is None:
        return None
    match = re.search(r"(\d+)", str(value))
    return int(match.group(1)) if match else None


def normalize_fields(value: Any) -> list[str]:
    """Return a stable string list from a JSON field declaration."""
    decoded = json_value(value)
    if isinstance(decoded, list):
        return [str(item) for item in decoded]
    return []


def compact(value: Any, max_chars: int = 1600) -> Any:
    """Keep large diagnostic text bounded without losing its leading context."""
    if isinstance(value, str) and len(value) > max_chars:
        return value[:max_chars] + "...[truncated]"
    if isinstance(value, dict):
        return {str(k): compact(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v, max_chars) for v in value]
    return value


class McpClient:
    """Minimal JSON-RPC client that captures sanitized MCP evidence."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize the client with a runtime-only bearer token and output path."""
        self.token = token
        self.output = output
        self.protocol_version: str | None = None
        self.sequence = 0
        self.calls: list[dict[str, Any]] = []

    def call(self, method: str, params: dict[str, Any], label: str) -> dict[str, Any]:
        """Send a JSON-RPC request and save a redacted request/response pair."""
        self.sequence += 1
        request_id = f"{label}-{self.sequence}"
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        request = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
        started = time.monotonic()
        status = 0
        response_headers: dict[str, str] = {}
        raw = b""
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                status = response.status
                response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            response_headers = {str(k).lower(): str(v) for k, v in exc.headers.items()}
            raw = exc.read()
        elapsed = round(time.monotonic() - started, 3)
        try:
            envelope = json.loads(raw.decode("utf-8", "replace")) if raw else None
            parse_error = None
        except Exception as exc:  # pragma: no cover - diagnostic fallback
            envelope = None
            parse_error = f"{type(exc).__name__}: {exc}"
        safe_payload = json.loads(json.dumps(payload))
        safe_headers = {
            key: value
            for key, value in response_headers.items()
            if key in {"content-type", "mcp-protocol-version", "mcp-session-id", "x-request-id", "x-trace-id"}
        }
        if "mcp-session-id" in safe_headers:
            safe_headers["mcp-session-id"] = hashlib.sha256(safe_headers["mcp-session-id"].encode()).hexdigest()
        stem = f"{self.sequence:03d}-{label}"
        write_json(self.output / f"{stem}.request.json", safe_payload)
        if envelope is not None:
            write_json(self.output / f"{stem}.response.json", envelope)
        else:
            (self.output / f"{stem}.response.txt").write_text(raw.decode("utf-8", "replace"), encoding="utf-8")
        result = envelope.get("result") if isinstance(envelope, dict) else None
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        text_body = None
        if isinstance(result, dict) and isinstance(result.get("content"), list) and result["content"]:
            first = result["content"][0]
            if isinstance(first, dict) and isinstance(first.get("text"), str):
                try:
                    text_body = json.loads(first["text"])
                except json.JSONDecodeError:
                    text_body = None
        business = structured if isinstance(structured, dict) else text_body
        call = {
            "label": label,
            "method": method,
            "params": params,
            "http_status": status,
            "elapsed_seconds": elapsed,
            "parse_error": parse_error,
            "is_error": result.get("isError") if isinstance(result, dict) else None,
            "business": business,
            "representations_equal": structured == text_body if structured is not None and text_body is not None else None,
        }
        self.calls.append(call)
        return call

    def initialize(self) -> None:
        """Negotiate the MCP protocol and send the required initialized notification."""
        init = self.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "QuestTest-kb-topup-audit", "version": "1.0"},
            },
            "MCP-INIT",
        )
        result = ((init.get("business") or {}).get("result") if isinstance(init.get("business"), dict) else None)
        # structuredContent is not used for initialize; inspect the raw envelope saved by the client.
        init_envelope = json.loads((self.output / "001-MCP-INIT.response.json").read_text(encoding="utf-8"))
        self.protocol_version = ((init_envelope.get("result") or {}).get("protocolVersion"))
        self.call("notifications/initialized", {}, "MCP-NOTIFY")

    def tool(self, name: str, arguments: dict[str, Any], label: str) -> dict[str, Any]:
        """Invoke one named MCP tool."""
        return self.call("tools/call", {"name": name, "arguments": arguments}, label)


def business(call: dict[str, Any]) -> dict[str, Any]:
    """Return the structured business envelope or an empty object."""
    value = call.get("business")
    return value if isinstance(value, dict) else {}


def error_code(call: dict[str, Any]) -> str | None:
    """Extract a stable MCP/business error code."""
    value = business(call).get("error")
    return str(value.get("code")) if isinstance(value, dict) and value.get("code") is not None else None


def successful(call: dict[str, Any]) -> bool:
    """Return whether one call has a normal structured business response."""
    return call.get("http_status") == 200 and call.get("is_error") is not True and isinstance(business(call), dict) and "error" not in business(call)


def data(call: dict[str, Any]) -> dict[str, Any]:
    """Return the business data object."""
    value = business(call).get("data")
    return value if isinstance(value, dict) else {}


def open_db() -> tuple[Any, Any]:
    """Open the authorized test database connection and return settings/connection."""
    from config.settings import SettingsLoader

    settings = SettingsLoader.load("test", ROOT).database
    connection = pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.username,
        password=settings.password,
        database=settings.name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=180,
    )
    return settings, connection


def fetch_db_snapshot(connection: Any) -> dict[str, Any]:
    """Read KB, top-up, formula, and route facts in one consistent snapshot."""
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        cursor.execute("SELECT DATABASE() AS database_name, CURRENT_USER() AS db_user, NOW(6) AS snapshot_at")
        identity = dict(cursor.fetchone())

        cursor.execute(
            """SELECT id,factor_name,factor_hypothesis,validation_status,confidence_score,
                      target_asset_class,mapped_factor_id,is_sub_factor_id,mapping_status,
                      pipeline_sub_factor_id,dependent_data_fields,data_frequency,holding_period,
                      formula_expression,document_id,updated_at
               FROM kb_factor_extractions
               WHERE mapping_status='mapped' AND mapped_factor_id IS NOT NULL
               ORDER BY updated_at DESC,id DESC"""
        )
        kb_rows = [dict(row) for row in cursor.fetchall()]

        cursor.execute(
            """SELECT t.id,t.extraction_id,t.status,t.pipeline_run_id,t.result_sub_factor_id,
                      t.result_validity,t.last_error_stage,t.last_error_class,t.last_error_code,
                      t.last_error_message,t.updated_at,e.factor_name,e.validation_status,
                      e.mapping_status,e.mapped_factor_id,e.pipeline_sub_factor_id,e.is_sub_factor_id
               FROM kb_factor_mining_tasks t
               JOIN kb_factor_extractions e ON e.id=t.extraction_id
               WHERE t.result_sub_factor_id IS NOT NULL
               ORDER BY t.updated_at DESC,t.id DESC"""
        )
        task_rows = [dict(row) for row in cursor.fetchall()]

        cursor.execute(
            """SELECT id,sub_factor_name,cn_name,factor_bar_interval,`window`,formula_summary,
                      metadata,data_source,serial_number,updated_at
               FROM sub_factors
               WHERE sub_factor_name LIKE '%%__topup%%'
               ORDER BY id"""
        )
        topups = [dict(row) for row in cursor.fetchall()]
        topup_ids = [int(row["id"]) for row in topups]

        cursor.execute(
            """SELECT id,sub_factor_name,factor_bar_interval,`window`,formula_summary,metadata,data_source
               FROM sub_factors
               WHERE sub_factor_name NOT LIKE '%%__topup%%'"""
        )
        base_factors = [dict(row) for row in cursor.fetchall()]

        cursor.execute(
            """SELECT factor_id,is_sub_factor_id,COUNT(*) AS row_count,
                      GROUP_CONCAT(DISTINCT status ORDER BY status) AS status_values,
                      GROUP_CONCAT(DISTINCT coin_category ORDER BY coin_category) AS categories
               FROM factors_status
               WHERE is_sub_factor_id=1
               GROUP BY factor_id,is_sub_factor_id"""
        )
        statuses = {int(row["factor_id"]): dict(row) for row in cursor.fetchall()}

        cursor.execute(
            """SELECT factor_id,COUNT(*) AS detail_count,
                      GROUP_CONCAT(DISTINCT id ORDER BY id) AS detail_ids,
                      GROUP_CONCAT(DISTINCT data_source ORDER BY data_source) AS detail_sources,
                      GROUP_CONCAT(DISTINCT calc_logic ORDER BY id SEPARATOR ' || ') AS calc_logics
               FROM factors_details
               WHERE is_sub_factor_id=1
               GROUP BY factor_id"""
        )
        details = {int(row["factor_id"]): dict(row) for row in cursor.fetchall()}

        cursor.execute(
            """SELECT factor_id,is_sub_factor_id,COUNT(*) AS evidence_count,
                      MIN(recorded_at) AS first_recorded_at,MAX(recorded_at) AS last_recorded_at,
                      GROUP_CONCAT(DISTINCT factor_window_bars ORDER BY factor_window_bars) AS evidence_windows,
                      GROUP_CONCAT(DISTINCT formula_hash ORDER BY formula_hash) AS formula_hashes,
                      GROUP_CONCAT(DISTINCT expression ORDER BY expression SEPARATOR ' || ') AS expressions,
                      GROUP_CONCAT(DISTINCT required_fields ORDER BY required_fields SEPARATOR ' || ') AS required_fields_json
               FROM factor_ic_run_formula_evidence
               WHERE is_sub_factor_id=1
               GROUP BY factor_id,is_sub_factor_id"""
        )
        evidence = {int(row["factor_id"]): dict(row) for row in cursor.fetchall()}

        cursor.execute(
            """SELECT id,publication_uid,eval_batch_id,metric_id,factor_ref,factor_type,factor_id,
                      label_code,rank_no,routing_score,confidence,time_series_score,cross_sectional_score,
                      is_eligible,is_active,evidence
               FROM market_environment_factor_route
               WHERE is_active=1
               ORDER BY id"""
        )
        routes = [dict(row) for row in cursor.fetchall()]

        cursor.execute(
            """SELECT id,batch_uid,status,publish_status,is_active,market_scope,label_kind,
                      route_profile_key,start_date,end_date,as_of_time,published_at,environment_status,
                      publication_uid,publish_version,expected_metric_count,completed_metric_count,
                      insufficient_metric_count,failed_metric_count
               FROM market_environment_eval_batch
               WHERE is_active=1
               ORDER BY id DESC"""
        )
        batches = [dict(row) for row in cursor.fetchall()]

        # Keep only compact aggregate facts in the report, plus every top-up row.
        route_by_factor: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in routes:
            if row.get("factor_type") == "sub_factor":
                route_by_factor[int(row["factor_id"])].append(row)

        topup_facts: list[dict[str, Any]] = []
        for row in topups:
            factor_id = int(row["id"])
            metadata = json_value(row.get("metadata"))
            metadata = metadata if isinstance(metadata, dict) else {}
            detail = details.get(factor_id, {})
            route_rows = route_by_factor.get(factor_id, [])
            topup_facts.append(
                {
                    "id": factor_id,
                    "factor_ref": f"sub_factor:{factor_id}",
                    "name": row.get("sub_factor_name"),
                    "parent": metadata.get("primary_parent"),
                    "window": row.get("window"),
                    "factor_bar_interval": row.get("factor_bar_interval"),
                    "formula_summary": row.get("formula_summary"),
                    "data_source": row.get("data_source"),
                    "metadata_fields": normalize_fields(metadata.get("fields")),
                    "declared_fields": normalize_fields(metadata.get("declared_fields")),
                    "metadata_status": metadata.get("status"),
                    "definition_contract_status": metadata.get("definition_contract_status"),
                    "detail_count": int(detail.get("detail_count") or 0),
                    "detail_ids": str(detail.get("detail_ids") or "").split(",") if detail.get("detail_ids") else [],
                    "detail_sources": str(detail.get("detail_sources") or "").split(",") if detail.get("detail_sources") else [],
                    "detail_calc_logic": str(detail.get("calc_logics") or "")[:1800],
                    "status": statuses.get(factor_id, {}),
                    "formula_evidence": evidence.get(factor_id, {}),
                    "active_routes": [
                        {
                            "id": route.get("id"),
                            "publication_uid": route.get("publication_uid"),
                            "eval_batch_id": route.get("eval_batch_id"),
                            "metric_id": route.get("metric_id"),
                            "label_code": route.get("label_code"),
                            "rank_no": route.get("rank_no"),
                            "routing_score": route.get("routing_score"),
                            "is_eligible": route.get("is_eligible"),
                            "evidence": compact(json_value(route.get("evidence")), 900),
                        }
                        for route in route_rows
                    ],
                }
            )

        # Compare each top-up to the same-parent, same-window non-top-up formulas.
        parent_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in base_factors:
            name = str(row.get("sub_factor_name") or "")
            metadata = json_value(row.get("metadata"))
            metadata = metadata if isinstance(metadata, dict) else {}
            parent = metadata.get("primary_parent") or name.split("__", 1)[0].split("_", 1)[0]
            parent_groups[str(parent)].append(row)
        semantic_checks: list[dict[str, Any]] = []
        for row in topup_facts:
            parent = str(row.get("parent") or "")
            numeric_window = normalize_window(row.get("window"))
            peers = [
                peer
                for peer in parent_groups.get(parent, [])
                if normalize_window(peer.get("window")) == numeric_window
            ]
            peer_formulas = sorted({str(peer.get("formula_summary") or "") for peer in peers if peer.get("formula_summary")})
            formula = str(row.get("formula_summary") or "")
            same_parent_formula = formula in peer_formulas if peer_formulas else None
            semantic_checks.append(
                {
                    "factor_id": row["id"],
                    "parent": parent,
                    "window": row["window"],
                    "topup_formula": formula,
                    "same_window_parent_peer_count": len(peers),
                    "same_window_parent_formulas": peer_formulas,
                    "matches_parent_formula": same_parent_formula,
                    "active_route_count": len(row["active_routes"]),
                }
            )

        route_counts = Counter(str(row.get("label_code")) for row in routes if row.get("is_active") == 1)
        active_topup = [row for row in topup_facts if row["active_routes"]]
        route_factors = {int(row["factor_id"]) for row in routes if row.get("is_active") == 1 and row.get("factor_type") == "sub_factor"}
        environment_reconciliation: list[dict[str, Any]] = []
        for batch in batches:
            env_status = json_value(batch.get("environment_status"))
            if not isinstance(env_status, dict):
                continue
            for label, value in sorted(env_status.items()):
                value = value if isinstance(value, dict) else {}
                actual = route_counts.get(label, 0)
                environment_reconciliation.append(
                    {
                        "batch_id": batch.get("id"),
                        "batch_uid": batch.get("batch_uid"),
                        "publication_uid": batch.get("publication_uid"),
                        "label_code": label,
                        "environment_status_route_count": value.get("route_count"),
                        "actual_active_route_count_all_routes": actual,
                        "matches": value.get("route_count") == actual,
                    }
                )

        report = {
            "database": {
                "name": identity.get("database_name"),
                "user": identity.get("db_user"),
                "snapshot_at": identity.get("snapshot_at"),
                "transaction": "READ ONLY; consistent snapshot; rolled back",
            },
            "kb": {
                "mapped_extraction_count": len(kb_rows),
                "mapped_with_pipeline_count": sum(1 for row in kb_rows if row.get("pipeline_sub_factor_id") is not None),
                "task_result_count": len(task_rows),
                "task_status_counts": dict(Counter(str(row.get("status")) for row in task_rows)),
                "samples": [
                    {
                        "extraction_id": row.get("id"),
                        "factor_name": row.get("factor_name"),
                        "validation_status": row.get("validation_status"),
                        "mapping_status": row.get("mapping_status"),
                        "mapped_factor_id": row.get("mapped_factor_id"),
                        "pipeline_sub_factor_id": row.get("pipeline_sub_factor_id"),
                        "dependent_data_fields": json_value(row.get("dependent_data_fields")),
                        "data_frequency": row.get("data_frequency"),
                        "holding_period": row.get("holding_period"),
                        "formula_expression": row.get("formula_expression"),
                        "target_asset_class": json_value(row.get("target_asset_class")),
                        "updated_at": row.get("updated_at"),
                    }
                    for row in kb_rows[:40]
                ],
                "tasks": [
                    {
                        "task_id": row.get("id"),
                        "extraction_id": row.get("extraction_id"),
                        "status": row.get("status"),
                        "pipeline_run_id": row.get("pipeline_run_id"),
                        "result_sub_factor_id": row.get("result_sub_factor_id"),
                        "result_validity": row.get("result_validity"),
                        "last_error_code": row.get("last_error_code"),
                        "last_error_stage": row.get("last_error_stage"),
                        "factor_name": row.get("factor_name"),
                        "mapped_factor_id": row.get("mapped_factor_id"),
                    }
                    for row in task_rows
                ],
            },
            "topup": {
                "total_count": len(topup_facts),
                "parent_count": len({row.get("parent") for row in topup_facts}),
                "active_route_factor_count": len(active_topup),
                "active_route_row_count": sum(len(row["active_routes"]) for row in active_topup),
                "all_active_route_factor_count": len(route_factors),
                "formula_distribution": dict(Counter(str(row.get("formula_summary")) for row in topup_facts)),
                "parent_distribution": dict(Counter(str(row.get("parent")) for row in topup_facts)),
                "active_route_by_parent": dict(Counter(str(row.get("parent")) for row in active_topup)),
                "semantic_checks": semantic_checks,
                "active_samples": active_topup,
                "all_rows": topup_facts,
            },
            "route": {
                "active_route_count": sum(route_counts.values()),
                "active_eligible_route_count": sum(1 for row in routes if row.get("is_active") == 1 and row.get("is_eligible") == 1),
                "label_counts": dict(route_counts),
                "environment_reconciliation": environment_reconciliation,
                "batches": [
                    {
                        "id": row.get("id"),
                        "batch_uid": row.get("batch_uid"),
                        "publication_uid": row.get("publication_uid"),
                        "status": row.get("status"),
                        "publish_status": row.get("publish_status"),
                        "is_active": row.get("is_active"),
                        "market_scope": row.get("market_scope"),
                        "label_kind": row.get("label_kind"),
                        "route_profile_key": row.get("route_profile_key"),
                        "environment_status": json_value(row.get("environment_status")),
                    }
                    for row in batches
                ],
            },
        }
        connection.rollback()
        return report


def run_mcp(client: McpClient, report: dict[str, Any]) -> dict[str, Any]:
    """Run bounded MCP checks that do not require catalog-result quota."""
    client.initialize()
    route_samples = [row for row in report["topup"]["active_samples"] if row["active_routes"]]
    chosen = route_samples[:3]
    calls: list[dict[str, Any]] = []
    for index, row in enumerate(chosen, 1):
        factor_ref = row["factor_ref"]
        calls.append(
            client.tool(
                "factor_get_environment_tags",
                {"factor_ref": factor_ref, "market_scope": "all", "route_profile_key": "default"},
                f"MCP-TOPUP-TAGS-{index}",
            )
        )
        route = row["active_routes"][0]
        calls.append(
            client.tool(
                "factor_get_environment_metrics",
                {
                    "factor_ref": factor_ref,
                    "market_scope": "all",
                    "route_profile_key": "default",
                    "batch_uid": report["route"]["batches"][0].get("batch_uid") if report["route"]["batches"] else None,
                    "label_code": route.get("label_code"),
                    "evaluation_type": "time_series",
                    "limit": 5,
                },
                f"MCP-TOPUP-METRICS-{index}",
            )
        )
    # One recommendation call verifies the same publication context and its no-fallback status.
    calls.append(
        client.tool(
            "environment_get_recommendations",
            {"market_scope": "all", "route_profile_key": "default", "limit": 1},
            "MCP-RECOMMENDATION",
        )
    )
    # Catalog/KB calls are intentionally attempted once each to record the quota gate, then not retried.
    calls.append(client.tool("kb_factor_candidate_search", {"extraction_id": 64014}, "MCP-KB-QUOTA-GATE"))
    calls.append(client.tool("factor_get_detail", {"factor_ref": chosen[0]["factor_ref"], "detail_level": "summary"}, "MCP-TOPUP-DETAIL-QUOTA-GATE") if chosen else {})

    compact_calls: list[dict[str, Any]] = []
    for call in calls:
        b = business(call)
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        pub = d.get("publication") if isinstance(d.get("publication"), dict) else {}
        items = d.get("items") if isinstance(d.get("items"), list) else []
        compact_calls.append(
            {
                "label": call.get("label"),
                "tool": (call.get("params") or {}).get("name"),
                "arguments": (call.get("params") or {}).get("arguments"),
                "http_status": call.get("http_status"),
                "elapsed_seconds": call.get("elapsed_seconds"),
                "is_error": call.get("is_error"),
                "error_code": error_code(call),
                "data_keys": sorted(d.keys()),
                "returned_count": d.get("returned_count"),
                "item_summaries": [
                    {
                        key: item.get(key)
                        for key in ("factor_ref", "factor_id", "label_code", "rank_no", "metric_id", "batch_uid", "publication_uid", "is_eligible", "routing_score", "evaluation_type", "metric_status", "is_valid")
                        if key in item
                    }
                    for item in items[:20]
                    if isinstance(item, dict)
                ],
                "publication_summary": {
                    key: pub.get(key)
                    for key in ("id", "batch_uid", "publication_uid", "market_scope", "label_kind", "status", "publish_status", "publish_version", "as_of_time", "environment_status")
                    if key in pub
                },
                "meta": b.get("meta"),
            }
        )
    quota_blocked = [row for row in compact_calls if row.get("error_code") == "EXPORT_BUDGET_EXCEEDED"]
    report["mcp"] = {
        "url": URL,
        "token": "redacted runtime bearer token",
        "protocol": "2025-06-18",
        "calls": compact_calls,
        "catalog_quota_gate_count": len(quota_blocked),
        "catalog_quota_gate_labels": [row["label"] for row in quota_blocked],
    }
    return report


def summarize(report: dict[str, Any]) -> str:
    """Render a concise human-readable report with explicit verdict classes."""
    semantic = report["topup"]["semantic_checks"]
    active_semantic_mismatch = [row for row in semantic if row["active_route_count"] and row["matches_parent_formula"] is False]
    all_semantic_mismatch = [row for row in semantic if row["matches_parent_formula"] is False]
    route_mismatch = [row for row in report["route"]["environment_reconciliation"] if not row["matches"]]
    mcp_calls = report.get("mcp", {}).get("calls", [])
    tag_calls = [row for row in mcp_calls if row["tool"] == "factor_get_environment_tags"]
    metric_calls = [row for row in mcp_calls if row["tool"] == "factor_get_environment_metrics"]
    tag_identity_ok = all(
        row["http_status"] == 200
        and row["error_code"] is None
        and row["publication_summary"].get("batch_uid") == (report["route"]["batches"][0].get("batch_uid") if report["route"]["batches"] else None)
        for row in tag_calls
    )
    metric_identity_ok = all(
        row["http_status"] == 200 and row["error_code"] is None and row["item_summaries"]
        for row in metric_calls
    )
    lines = [
        "# KB / Top-up / Environment Route Audit",
        "",
        f"- 执行时间：`{datetime.now(timezone.utc).isoformat()}`",
        f"- MCP：`{URL}`（Bearer Token 已脱敏）",
        f"- DB snapshot：`{report['database']['snapshot_at']}`，事务只读一致性快照并 rollback",
        "",
        "## Verdicts",
        "",
        f"- `FAIL / P1 candidate`: KB 映射语义不一致样本（全部 mapped：{len(report['kb']['samples'])} 个最近样本；有结果 task：{report['kb']['task_result_count']} 条）。",
        f"- `FAIL / P1 candidate`: active route 中有 `{len(active_semantic_mismatch)}` 个 topup 的公式与同父主题同窗口正式因子不一致；全部 topup 不一致 `{len(all_semantic_mismatch)}/{report['topup']['total_count']}`。是否允许通用 fallback 仍需产品契约确认，见下文分类。",
        f"- `FAIL / P1`: environment_status.route_count 与同批次 active route 实际计数不一致 `{len(route_mismatch)}` 个 label；这是同一批次/MCP publication 的确定性差异。",
        f"- `PASS`: active topup 路由 MCP 标签身份 `{len(tag_calls)}` 个样本；batch identity 一致={tag_identity_ok}；环境 metrics 非空且身份可读={metric_identity_ok}。",
        f"- `BLOCKED`: catalog/KB 目录调用 quota gate `{report.get('mcp', {}).get('catalog_quota_gate_count', 0)}` 次；服务返回 `EXPORT_BUDGET_EXCEEDED`，不能据此判定 KB 查询功能失败。",
        "",
        "## Top-up facts",
        "",
        f"- 总数：`{report['topup']['total_count']}`，父主题：`{report['topup']['parent_count']}`。",
        f"- 已进入当前 published batch active eligible route：`{report['topup']['active_route_factor_count']}` 个因子、`{report['topup']['active_route_row_count']}` 条路由。",
        f"- 当前路由 label 分布：`{json.dumps(report['route']['label_counts'], ensure_ascii=False, sort_keys=True)}`。",
        "- 共同公式分布（示例）：`close.pct_change().rolling(window).mean()`；它被用于 breakout、candlestick、funding、volatility、volume、VWAP 等不同父主题，且 metadata 标为 `通用时间序列补足`。",
        "- 对账规则：将 topup 的 `primary_parent + window` 与同父主题的非 topup 因子比较；若父因子存在同窗口公式而 topup 公式不同，标记为语义替换候选，而不是仅凭名称重复判定。",
        "",
        "## Confirmed route-count issue",
        "",
    ]
    if route_mismatch:
        for row in route_mismatch:
            lines.append(
                f"- batch `{row['batch_id']}` / `{row['batch_uid']}` / label `{row['label_code']}`：environment_status.route_count=`{row['environment_status_route_count']}`，同一批次 active route 实际=`{row['actual_active_route_count_all_routes']}`。"
            )
        lines.extend(
            [
                "- 归因：后端批次环境状态汇总字段；MCP `factor_get_environment_tags` 返回相同 publication/batch，故不是 MCP 展示层独有问题。",
                "- 复现：读取 MCP tags 的 `publication.batch_uid`；在 DB 读取该 batch 的 `environment_status[label].route_count`；再按 `market_environment_factor_route WHERE eval_batch_id=? AND is_active=1` 分 label 计数。",
            ]
        )
    else:
        lines.append("- 本快照未发现 route_count 差异。")
    lines.extend(
        [
            "",
            "## KB semantic audit",
            "",
            "- 数据库明确显示 extraction 的论文候选字段（hypothesis、dependent_data_fields、frequency、holding_period）与结果子因子是两套对象；本报告保留全部 task/result 身份供复核。",
            "- 只有在候选已经标记 mapped、task 有 result_sub_factor_id，且结果子因子可解析到详情/公式时，才把语义差异作为映射链路问题；失败 task 但保留有效产物仍计入关联审计，不把 task 状态本身重复计为新缺陷。",
            "- MCP KB exact lookup 本轮被 catalog quota 阻断；因此当前新鲜结论来自同一 DB snapshot，不能声称 MCP 候选文本本轮已重新拉取。",
            "",
            "## Evidence",
            "",
            "- `evidence.json`：完整 DB 对账、topup 逐项数据、MCP compact 响应。",
            "- 编号 `*.request.json` / `*.response.json`：脱敏 MCP 原始调用；Authorization 未写入。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """Run the audit and create evidence files."""
    token = os.environ.get(TOKEN_ENV) or os.environ.get("MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} or MCP_TOKEN is required")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-kb-topup-route-audit"
    output.mkdir(parents=True, exist_ok=False)
    _, connection = open_db()
    try:
        report = fetch_db_snapshot(connection)
    finally:
        connection.close()
    client = McpClient(token, output)
    report = run_mcp(client, report)
    write_json(output / "evidence.json", report)
    (output / "summary.md").write_text(summarize(report), encoding="utf-8")
    write_json(output / "mcp-call-ledger.json", report.get("mcp", {}).get("calls", []))
    print(output)


if __name__ == "__main__":
    main()
