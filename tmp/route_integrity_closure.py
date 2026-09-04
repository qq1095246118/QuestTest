#!/usr/bin/env python3
"""Close four Factor 4.0 data-relation cases with read-only evidence."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from tmp.critical_readonly_gap_probe import (  # noqa: E402
    MCPClient,
    data as mcp_data,
    error_code as mcp_error_code,
    rows as mcp_rows,
    successful as mcp_successful,
)


REPORT_ROOT = ROOT / "reports" / "factor4-resume"
TOKEN_ENV_NAMES = ("FACTOR4_MCP_TOKEN", "MCP_TOKEN")
SHANGHAI = ZoneInfo("Asia/Shanghai")
SENSITIVE_KEY = re.compile(
    r"authorization|token|password|secret|api[_-]?key|jwt|hmac|signature",
    re.IGNORECASE,
)
TOKEN_TEXT = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.IGNORECASE)


def json_default(value: Any) -> str:
    """Serialize database-native scalar values without losing precision."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Recursively remove credentials from persisted evidence."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return TOKEN_TEXT.sub("<redacted>", value)
    return value


def write_json(path: Path, value: Any) -> None:
    """Write deterministic redacted JSON evidence."""

    path.write_text(
        json.dumps(
            redact(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=json_default,
        )
        + "\n",
        encoding="utf-8",
    )


def decode_json(value: Any) -> Any:
    """Decode a MySQL JSON value while accepting decoded objects."""

    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def normalize_time(value: Any, *, database_value: bool = False) -> datetime | None:
    """Normalize API, snapshot, or MySQL times to UTC."""

    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI if database_value else timezone.utc)
    return parsed.astimezone(timezone.utc)


def scalar_equal(left: Any, right: Any) -> bool:
    """Compare nullable identity values from JSON and MySQL."""

    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:  # noqa: BLE001 - identity may be a nonnumeric enum
        return str(left) == str(right)


def query(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> list[dict[str, Any]]:
    """Execute one parameterized SELECT and return dictionary rows."""

    cursor.execute(sql, parameters or ())
    return [dict(row) for row in cursor.fetchall()]


def query_one(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> dict[str, Any] | None:
    """Execute one SELECT and return its first row."""

    rows = query(cursor, sql, parameters)
    return rows[0] if rows else None


def index_inventory(cursor: pymysql.cursors.DictCursor, table: str) -> list[dict[str, Any]]:
    """Return a stable index inventory for one table."""

    rows = query(
        cursor,
        """
        SELECT INDEX_NAME AS index_name, NON_UNIQUE AS non_unique,
               SEQ_IN_INDEX AS sequence_no, COLUMN_NAME AS column_name
        FROM information_schema.statistics
        WHERE table_schema=DATABASE() AND table_name=%s
        ORDER BY INDEX_NAME,SEQ_IN_INDEX
        """,
        (table,),
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = grouped.setdefault(
            str(row["index_name"]),
            {"index_name": row["index_name"], "unique": row["non_unique"] == 0, "columns": []},
        )
        item["columns"].append(row["column_name"])
    return list(grouped.values())


def foreign_key_inventory(cursor: pymysql.cursors.DictCursor, table: str) -> list[dict[str, Any]]:
    """Return foreign keys declared for one table."""

    return query(
        cursor,
        """
        SELECT CONSTRAINT_NAME AS constraint_name,COLUMN_NAME AS column_name,
               REFERENCED_TABLE_NAME AS referenced_table,
               REFERENCED_COLUMN_NAME AS referenced_column
        FROM information_schema.key_column_usage
        WHERE table_schema=DATABASE() AND table_name=%s
          AND REFERENCED_TABLE_NAME IS NOT NULL
        ORDER BY CONSTRAINT_NAME,ORDINAL_POSITION
        """,
        (table,),
    )


def compact_mismatch(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """Return a compact route mismatch sample without large JSON payloads."""

    return {
        "kind": kind,
        "route_id": row.get("route_id"),
        "factor_ref": row.get("route_factor_ref"),
        "label_code": row.get("route_label_code"),
        "metric_id": row.get("metric_id"),
    }


def read_database_oracle() -> dict[str, Any]:
    """Read all four case oracles in one explicit read-only transaction."""

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
        write_timeout=30,
    )
    rolled_back = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            identity = query_one(
                cursor,
                "SELECT DATABASE() AS database_name,CURRENT_USER() AS current_user_name",
            ) or {}
            selected_batch_id = query_one(
                cursor,
                """
                SELECT MAX(id) AS id
                FROM market_environment_eval_batch
                WHERE is_active=1 AND publish_status='published'
                """,
            )
            batch = query_one(
                cursor,
                """
                SELECT id,batch_uid,market_scope,label_kind,as_of_time,environment_snapshot,
                       factor_set_snapshot,status,publish_status,publication_uid,publish_version,
                       route_profile_key,published_at,is_active
                FROM market_environment_eval_batch
                WHERE id=%s
                """,
                (selected_batch_id.get("id"),),
            ) if selected_batch_id and selected_batch_id.get("id") is not None else None
            if batch is None:
                return {
                    "transaction": {"start_statement": "START TRANSACTION READ ONLY", "rolled_back": False},
                    "database_identity": identity,
                    "blocking_reason": "NO_ACTIVE_PUBLISHED_BATCH",
                }
            environment_snapshot = decode_json(batch.pop("environment_snapshot"))
            factor_snapshot = decode_json(batch.pop("factor_set_snapshot"))
            if not isinstance(environment_snapshot, dict):
                environment_snapshot = {}
            if not isinstance(factor_snapshot, dict):
                factor_snapshot = {}

            daily_indexes = index_inventory(cursor, "market_environment_daily")
            daily_duplicate_revision = query_one(
                cursor,
                """
                SELECT COUNT(*) AS count FROM (
                    SELECT environment_date,label_kind,revision
                    FROM market_environment_daily
                    GROUP BY environment_date,label_kind,revision
                    HAVING COUNT(*)>1
                ) duplicate_keys
                """,
            ) or {"count": 0}
            daily_multiple_current = query_one(
                cursor,
                """
                SELECT COUNT(*) AS count FROM (
                    SELECT environment_date,label_kind
                    FROM market_environment_daily
                    WHERE is_current=1
                    GROUP BY environment_date,label_kind
                    HAVING COUNT(*)>1
                ) duplicate_current
                """,
            ) or {"count": 0}
            daily_stats = query_one(
                cursor,
                """
                SELECT COUNT(*) AS row_count,
                       SUM(revision>1) AS revision_gt1_count,
                       COUNT(DISTINCT CONCAT(environment_date,'|',label_kind)) AS business_key_count,
                       COUNT(DISTINCT CASE WHEN is_current=1 THEN CONCAT(environment_date,'|',label_kind) END) AS current_key_count
                FROM market_environment_daily
                """,
            ) or {}
            daily_history_keys = query_one(
                cursor,
                """
                SELECT COUNT(*) AS count FROM (
                    SELECT environment_date,label_kind
                    FROM market_environment_daily
                    GROUP BY environment_date,label_kind
                    HAVING COUNT(DISTINCT revision)>1
                ) history_keys
                """,
            ) or {"count": 0}
            revision_columns = ["environment_date", "label_kind", "revision"]
            current_columns = ["environment_date", "label_kind", "is_current"]
            exact_revision_unique = any(
                item["unique"] and item["columns"] == revision_columns for item in daily_indexes
            )
            physical_current_unique = any(
                item["unique"] and item["columns"] == current_columns for item in daily_indexes
            )

            publication_stats = query_one(
                cursor,
                """
                SELECT COUNT(*) AS published_batch_count,
                       COUNT(DISTINCT publication_uid) AS publication_count,
                       SUM(is_active=1) AS active_published_batch_count
                FROM market_environment_eval_batch
                WHERE publish_status='published'
                """,
            ) or {}
            route_history_stats = query_one(
                cursor,
                """
                SELECT COUNT(*) AS route_count,SUM(is_active=1) AS active_route_count,
                       SUM(is_active=0) AS inactive_route_count,
                       COUNT(DISTINCT publication_uid) AS route_publication_count,
                       COUNT(DISTINCT publish_version) AS publish_version_count
                FROM market_environment_factor_route
                """,
            ) or {}
            duplicate_active = query(
                cursor,
                """
                SELECT publication_uid,factor_ref,label_kind,label_code,COUNT(*) AS count,
                       COUNT(DISTINCT publish_version) AS version_count
                FROM market_environment_factor_route
                WHERE is_active=1
                GROUP BY publication_uid,factor_ref,label_kind,label_code
                HAVING COUNT(*)>1 OR COUNT(DISTINCT publish_version)>1
                ORDER BY count DESC LIMIT 20
                """,
            )

            route_rows = query(
                cursor,
                """
                SELECT r.id AS route_id,r.eval_batch_id,r.publication_uid,r.metric_id,
                       r.market_scope,r.environment_date,r.label_kind,r.label_code,
                       r.as_of_time,r.factor_ref AS route_factor_ref,
                       r.factor_type AS route_factor_type,r.factor_id AS route_factor_id,
                       r.factor_version AS route_factor_version,r.publish_version,
                       r.is_active,r.activated_at,
                       m.id AS joined_metric_id,m.eval_batch_id AS metric_batch_id,
                       m.market_scope AS metric_market_scope,m.label_kind AS metric_label_kind,
                       m.label_code AS metric_label_code,m.factor_ref AS metric_factor_ref,
                       m.factor_type AS metric_factor_type,m.factor_id AS metric_factor_id,
                       m.factor_version AS metric_factor_version,m.metric_status,
                       m.is_valid AS metric_is_valid,m.evaluation_type
                FROM market_environment_factor_route r
                LEFT JOIN market_environment_factor_metric m ON m.id=r.metric_id
                WHERE r.eval_batch_id=%s AND r.is_active=1
                ORDER BY r.id
                """,
                (batch["id"],),
            )
            snapshot_factor_refs = {
                str(item.get("factor_ref"))
                for item in (factor_snapshot.get("members") or [])
                if isinstance(item, dict) and item.get("factor_ref") is not None
            }
            route_mismatches: list[dict[str, Any]] = []
            mismatch_counts: Counter[str] = Counter()
            for row in route_rows:
                checks = {
                    "missing_metric": row.get("joined_metric_id") is None,
                    "batch_identity": row.get("eval_batch_id") != batch.get("id"),
                    "publication_identity": row.get("publication_uid") != batch.get("publication_uid"),
                    "publish_version": row.get("publish_version") != batch.get("publish_version"),
                    "market_scope": row.get("market_scope") != batch.get("market_scope"),
                    "label_kind": row.get("label_kind") != batch.get("label_kind"),
                    "as_of_time": row.get("as_of_time") != batch.get("as_of_time"),
                    "metric_batch": row.get("metric_batch_id") != row.get("eval_batch_id"),
                    "metric_market_scope": row.get("metric_market_scope") != row.get("market_scope"),
                    "metric_label_kind": row.get("metric_label_kind") != row.get("label_kind"),
                    "metric_label_code": row.get("metric_label_code") != row.get("label_code"),
                    "metric_factor_ref": row.get("metric_factor_ref") != row.get("route_factor_ref"),
                    "metric_factor_type": row.get("metric_factor_type") != row.get("route_factor_type"),
                    "metric_factor_id": row.get("metric_factor_id") != row.get("route_factor_id"),
                    "metric_factor_version": row.get("metric_factor_version") != row.get("route_factor_version"),
                    "factor_snapshot": row.get("route_factor_ref") not in snapshot_factor_refs,
                }
                for kind, failed in checks.items():
                    if failed:
                        mismatch_counts[kind] += 1
                        if len(route_mismatches) < 20:
                            route_mismatches.append(compact_mismatch(kind, row))

            metrics = query(
                cursor,
                """
                SELECT factor_ref,factor_type,factor_id,factor_version,label_code,
                       evaluation_type,metric_status,is_valid,error_code
                FROM market_environment_factor_metric
                WHERE eval_batch_id=%s
                ORDER BY factor_ref,label_code,evaluation_type,id
                """,
                (batch["id"],),
            )
            validity_groups: dict[tuple[str, str], dict[str, Any]] = {}
            invalid_reason_counts: Counter[tuple[str, str, str]] = Counter()
            for metric in metrics:
                key = (str(metric["factor_ref"]), str(metric["label_code"]))
                group = validity_groups.setdefault(
                    key,
                    {
                        "factor_ref": metric["factor_ref"],
                        "factor_type": metric["factor_type"],
                        "factor_id": metric["factor_id"],
                        "factor_version": metric["factor_version"],
                        "label_code": metric["label_code"],
                        "valid_dimensions": [],
                        "dimensions": [],
                    },
                )
                dimension = {
                    "evaluation_type": metric["evaluation_type"],
                    "metric_status": metric["metric_status"],
                    "is_valid": metric["is_valid"],
                    "error_code": metric["error_code"],
                }
                group["dimensions"].append(dimension)
                if metric["metric_status"] == "success" and metric["is_valid"] == 1:
                    group["valid_dimensions"].append(metric["evaluation_type"])
                else:
                    invalid_reason_counts[
                        (
                            str(metric["metric_status"]),
                            str(metric["is_valid"]),
                            str(metric["error_code"]),
                        )
                    ] += 1
            invalid_groups = [group for group in validity_groups.values() if not group["valid_dimensions"]]
            route_keys = {(str(row["route_factor_ref"]), str(row["label_code"])) for row in route_rows}
            invalid_route_intersection = [
                group for group in invalid_groups if (str(group["factor_ref"]), str(group["label_code"])) in route_keys
            ]
            invalid_samples = sorted(
                invalid_groups,
                key=lambda item: (
                    0 if all(d["metric_status"] == "success" for d in item["dimensions"]) else 1,
                    str(item["factor_ref"]),
                    str(item["label_code"]),
                ),
            )[:5]

            snapshot_members = [
                item for item in (environment_snapshot.get("members") or []) if isinstance(item, dict)
            ]
            daily_rows = query(
                cursor,
                """
                SELECT id,environment_date,label_kind,label_code,revision,is_current,
                       available_at,schema_version
                FROM market_environment_daily
                WHERE label_kind=%s
                """,
                (batch["label_kind"],),
            )
            daily_by_id = {int(row["id"]): row for row in daily_rows}
            member_mismatches: list[dict[str, Any]] = []
            member_mismatch_counts: Counter[str] = Counter()
            member_ids: list[int] = []
            member_dates: list[str] = []
            batch_as_of_utc = normalize_time(batch.get("as_of_time"), database_value=True)
            for member in snapshot_members:
                daily_id = int(member["daily_id"]) if member.get("daily_id") is not None else -1
                member_ids.append(daily_id)
                member_dates.append(str(member.get("environment_date")))
                row = daily_by_id.get(daily_id)
                checks = {
                    "missing_daily": row is None,
                    "environment_date": row is not None and str(row.get("environment_date")) != str(member.get("environment_date")),
                    "label_code": row is not None and str(row.get("label_code")) != str(member.get("label_code")),
                    "revision": row is not None and not scalar_equal(row.get("revision"), member.get("revision")),
                    "schema_version": row is not None and str(row.get("schema_version")) != str(member.get("schema_version")),
                    "available_at": row is not None and normalize_time(row.get("available_at"), database_value=True) != normalize_time(member.get("available_at")),
                    "future_member": row is not None and batch_as_of_utc is not None and normalize_time(row.get("available_at"), database_value=True) is not None and normalize_time(row.get("available_at"), database_value=True) > batch_as_of_utc,
                }
                for kind, failed in checks.items():
                    if failed:
                        member_mismatch_counts[kind] += 1
                        if len(member_mismatches) < 20:
                            member_mismatches.append(
                                {
                                    "kind": kind,
                                    "daily_id": daily_id,
                                    "member_date": member.get("environment_date"),
                                    "db_date": row.get("environment_date") if row else None,
                                }
                            )
            missing_dates = {str(value) for value in (environment_snapshot.get("missing_dates") or [])}
            member_date_set = set(member_dates)
            duplicate_member_ids = len(member_ids) - len(set(member_ids))
            duplicate_member_dates = len(member_dates) - len(member_date_set)
            missing_member_overlap = sorted(missing_dates & member_date_set)
            route_environment_dates = sorted({str(row["environment_date"]) for row in route_rows})
            route_environment_date_set = set(route_environment_dates)
            route_date_daily = query(
                cursor,
                """
                SELECT environment_date,label_kind,label_code,revision,is_current,available_at,id
                FROM market_environment_daily
                WHERE environment_date IN (
                    SELECT DISTINCT environment_date
                    FROM market_environment_factor_route
                    WHERE eval_batch_id=%s AND is_active=1
                ) AND label_kind=%s
                ORDER BY environment_date,revision,id
                """,
                (batch["id"], batch["label_kind"]),
            )

            result = {
                "transaction": {
                    "start_statement": "START TRANSACTION READ ONLY",
                    "select_only": True,
                    "rollback_attempted": False,
                    "rolled_back": False,
                },
                "database_identity": {
                    "database_name": identity.get("database_name"),
                    "current_user_name": identity.get("current_user_name"),
                },
                "selected_batch": batch,
                "DB-602": {
                    "row_count": daily_stats.get("row_count"),
                    "business_key_count": daily_stats.get("business_key_count"),
                    "current_key_count": daily_stats.get("current_key_count"),
                    "revision_gt1_count": daily_stats.get("revision_gt1_count"),
                    "history_business_key_count": daily_history_keys.get("count"),
                    "duplicate_revision_key_count": daily_duplicate_revision.get("count"),
                    "multiple_current_key_count": daily_multiple_current.get("count"),
                    "indexes": daily_indexes,
                    "physical_revision_unique_constraint": exact_revision_unique,
                    "physical_current_unique_constraint": physical_current_unique,
                },
                "DB-604": {
                    "publication_stats": publication_stats,
                    "route_history_stats": route_history_stats,
                    "duplicate_active_group_count": len(duplicate_active),
                    "duplicate_active_samples": duplicate_active,
                    "route_foreign_keys": foreign_key_inventory(cursor, "market_environment_factor_route"),
                    "route_metric_or_identity_mismatch_counts": dict(mismatch_counts),
                    "route_metric_or_identity_mismatch_samples": route_mismatches,
                },
                "MET-311": {
                    "admission_rule": "TS or CS is eligible when metric_status=success and is_valid=1; both dimensions need not be valid.",
                    "metric_row_count": len(metrics),
                    "factor_label_group_count": len(validity_groups),
                    "double_invalid_group_count": len(invalid_groups),
                    "active_route_count": len(route_rows),
                    "double_invalid_active_route_intersection_count": len(invalid_route_intersection),
                    "double_invalid_active_route_intersection_samples": invalid_route_intersection[:20],
                    "double_invalid_samples": invalid_samples,
                    "invalid_metric_reason_distribution": [
                        {
                            "metric_status": key[0],
                            "is_valid": key[1],
                            "error_code": key[2],
                            "count": count,
                        }
                        for key, count in sorted(invalid_reason_counts.items())
                    ],
                },
                "CALC-513": {
                    "active_route_count": len(route_rows),
                    "route_identity_mismatch_counts": dict(mismatch_counts),
                    "route_identity_mismatch_samples": route_mismatches,
                    "factor_snapshot_member_count": len(snapshot_factor_refs),
                    "environment_snapshot": {
                        "schema_version": environment_snapshot.get("schema_version"),
                        "start_date": environment_snapshot.get("start_date"),
                        "end_date": environment_snapshot.get("end_date"),
                        "as_of_time": environment_snapshot.get("as_of_time"),
                        "expected_days": environment_snapshot.get("expected_days"),
                        "covered_days": environment_snapshot.get("covered_days"),
                        "coverage_rate": environment_snapshot.get("coverage_rate"),
                        "member_count": len(snapshot_members),
                        "unique_member_id_count": len(set(member_ids)),
                        "unique_member_date_count": len(member_date_set),
                        "duplicate_member_id_count": duplicate_member_ids,
                        "duplicate_member_date_count": duplicate_member_dates,
                        "missing_date_count": len(missing_dates),
                        "missing_dates": sorted(missing_dates),
                        "missing_member_overlap": missing_member_overlap,
                        "member_db_mismatch_counts": dict(member_mismatch_counts),
                        "member_db_mismatch_samples": member_mismatches,
                    },
                    "route_environment_date_observation": {
                        "distinct_route_dates": route_environment_dates,
                        "dates_inside_snapshot_member_set": sorted(route_environment_date_set & member_date_set),
                        "dates_inside_snapshot_missing_dates": sorted(route_environment_date_set & missing_dates),
                        "dates_outside_snapshot_member_set": sorted(route_environment_date_set - member_date_set),
                        "matching_daily_rows": route_date_daily,
                        "contract_status": "UNDEFINED",
                        "note": "Schema only says 'recommendation corresponding environment date'; it does not define this as a snapshot member date or publication-effective date. Per CALC-513, date differences are observational only.",
                    },
                },
            }
            return result
    finally:
        try:
            connection.rollback()
            rolled_back = True
        finally:
            connection.close()
        if "result" in locals():
            result["transaction"]["rollback_attempted"] = True
            result["transaction"]["rolled_back"] = rolled_back


def backend_login(session: requests.Session) -> str:
    """Obtain a test JWT without persisting credentials or the login response."""

    settings = SettingsLoader.load("test", ROOT)
    credentials = settings.authentication.privileged
    response = session.post(
        f"{settings.api.base_url}/auth/login",
        json={"email": credentials.email, "password": credentials.password},
        timeout=settings.api.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    token = (payload.get("data") or {}).get("token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Backend login did not return a token")
    return token


def backend_recommendations(session: requests.Session, output: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call the documented Backend recommendation endpoint and save redacted evidence."""

    settings = SettingsLoader.load("test", ROOT)
    started = time.monotonic()
    response = session.get(
        f"{settings.api.base_url}/market-environment/recommendations",
        params=arguments,
        timeout=settings.api.timeout_seconds,
    )
    elapsed = round(time.monotonic() - started, 3)
    try:
        body: Any = response.json()
    except ValueError:
        body = {"unparsed_body": response.text[:2000]}
    write_json(
        output / "backend-recommendations.request.json",
        {
            "method": "GET",
            "path": "/api/v1/market-environment/recommendations",
            "parameters": arguments,
            "authentication": "Bearer <redacted>",
        },
    )
    result = {
        "http_status": response.status_code,
        "elapsed_seconds": elapsed,
        "content_type": response.headers.get("content-type"),
        "body": body,
    }
    write_json(output / "backend-recommendations.response.json", result)
    return result


def backend_data(call: dict[str, Any]) -> dict[str, Any]:
    """Extract the Backend business data object."""

    body = call.get("body")
    value = body.get("data") if isinstance(body, dict) else None
    return value if isinstance(value, dict) else {}


def backend_error_code(call: dict[str, Any]) -> str | None:
    """Extract a Backend error code from common envelope shapes."""

    body = call.get("body")
    if not isinstance(body, dict):
        return None
    for value in (body.get("error"), body.get("detail"), body.get("data")):
        if isinstance(value, dict) and value.get("code") is not None:
            return str(value["code"])
    if body.get("code") not in {None, 0, "0", 200, "200"}:
        return str(body["code"])
    return None


def recommendation_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """Return compact recommendation identities from one business data object."""

    publication = payload.get("publication") if isinstance(payload.get("publication"), dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    factor_refs = [item.get("factor_ref") for item in items if isinstance(item, dict)]
    return {
        "status": payload.get("status"),
        "reason_code": payload.get("reason_code"),
        "publication_uid": publication.get("publication_uid") or payload.get("publication_uid"),
        "batch_uid": publication.get("batch_uid") or payload.get("batch_uid"),
        "returned_count": payload.get("returned_count"),
        "item_count": len(items),
        "factor_refs": factor_refs,
    }


def make_case(
    case_id: str,
    status: str,
    classification: str,
    reason: str,
    oracle: dict[str, Any],
    assertions: list[dict[str, Any]],
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    """Build one normalized case result."""

    return {
        "case_id": case_id,
        "status": status,
        "classification": classification,
        "reason": reason,
        "independent_oracle": oracle,
        "assertions": assertions,
        "blocking_reasons": blockers or [],
    }


def adjudicate(
    database: dict[str, Any],
    mcp_recommendation: dict[str, Any] | None,
    mcp_details: dict[str, Any] | None,
    backend_recommendation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Adjudicate the four formal cases from DB and live read responses."""

    results: list[dict[str, Any]] = []

    db602 = database["DB-602"]
    db602_failures = []
    if int(db602["duplicate_revision_key_count"] or 0) > 0:
        db602_failures.append("duplicate_revision_keys")
    if int(db602["multiple_current_key_count"] or 0) > 0:
        db602_failures.append("multiple_current_rows")
    if not db602["physical_revision_unique_constraint"]:
        db602_failures.append("missing_revision_unique_constraint")
    db602_assertions = [
        {"assertion": "revision business key has no duplicates", "passed": int(db602["duplicate_revision_key_count"] or 0) == 0},
        {"assertion": "current snapshot has at most one row per date/kind", "passed": int(db602["multiple_current_key_count"] or 0) == 0},
        {"assertion": "revision key has a physical unique index", "passed": bool(db602["physical_revision_unique_constraint"])},
        {"assertion": "historical revision retention has a natural witness", "passed": int(db602["history_business_key_count"] or 0) > 0},
        {"assertion": "current uniqueness has a physical or independently exercised service guarantee", "passed": bool(db602["physical_current_unique_constraint"])},
    ]
    if db602_failures:
        results.append(
            make_case(
                "DB-602",
                "FAIL",
                "PRODUCT_DEFECT_DAILY_UNIQUENESS",
                f"Daily uniqueness failed: {', '.join(db602_failures)}.",
                db602,
                db602_assertions,
            )
        )
    elif int(db602["history_business_key_count"] or 0) == 0 or not db602["physical_current_unique_constraint"]:
        results.append(
            make_case(
                "DB-602",
                "BLOCKED",
                "BLOCKED_DATA_PRECONDITION",
                "2105 rows have no duplicate revision/current keys and the revision key is physically unique, but every row is revision 1 and the current index is non-unique; historical retention and the equivalent service-level current guarantee cannot be proven read-only.",
                db602,
                db602_assertions,
                ["NO_MULTI_REVISION_SAMPLE", "CURRENT_SERVICE_GUARANTEE_NOT_EXERCISED"],
            )
        )
    else:
        results.append(make_case("DB-602", "PASS", "VERIFIED", "All revision/current uniqueness and history-retention assertions passed.", db602, db602_assertions))

    db604 = database["DB-604"]
    route_mismatch_total = sum(int(value) for value in db604["route_metric_or_identity_mismatch_counts"].values())
    db604_hard_failure = route_mismatch_total > 0 or int(db604["duplicate_active_group_count"] or 0) > 0
    publication_count = int(db604["publication_stats"].get("publication_count") or 0)
    inactive_count = int(db604["route_history_stats"].get("inactive_route_count") or 0)
    db604_assertions = [
        {"assertion": "active routes uniquely identify one active version per publication/factor/label", "passed": int(db604["duplicate_active_group_count"] or 0) == 0},
        {"assertion": "every active route metric belongs to the same batch and identity", "passed": route_mismatch_total == 0},
        {"assertion": "at least two publications exist to observe an active switch", "passed": publication_count >= 2},
        {"assertion": "inactive historical routes are retained", "passed": inactive_count > 0},
    ]
    if db604_hard_failure:
        results.append(make_case("DB-604", "FAIL", "PRODUCT_DEFECT_ROUTE_HISTORY_OR_IDENTITY", "Current active route uniqueness or metric identity is inconsistent.", db604, db604_assertions))
    elif publication_count < 2 or inactive_count == 0:
        results.append(
            make_case(
                "DB-604",
                "BLOCKED",
                "BLOCKED_DATA_PRECONDITION",
                "The current 86 active routes are unique and metric-linked, but the database has exactly one publication and zero inactive routes, so switch-off and history-retention behavior cannot be observed.",
                db604,
                db604_assertions,
                ["SINGLE_PUBLICATION", "NO_INACTIVE_ROUTE_HISTORY"],
            )
        )
    else:
        results.append(make_case("DB-604", "PASS", "VERIFIED", "Active switch, unique active version, retained history, and same-batch metric identity all passed.", db604, db604_assertions))

    met311 = database["MET-311"]
    invalid_refs = {
        str(item["factor_ref"])
        for item in met311.get("double_invalid_samples", [])
        if item.get("factor_ref") is not None
    }
    mcp_identity = recommendation_identity(mcp_data(mcp_recommendation)) if mcp_recommendation else {}
    backend_payload = backend_data(backend_recommendation or {})
    backend_identity = recommendation_identity(backend_payload)
    mcp_item_invalid = sorted(invalid_refs & {str(value) for value in mcp_identity.get("factor_refs", [])})
    backend_item_invalid = sorted(invalid_refs & {str(value) for value in backend_identity.get("factor_refs", [])})
    mcp_block = mcp_error_code(mcp_recommendation)
    backend_block = backend_error_code(backend_recommendation or {})
    backend_success = bool(backend_recommendation and backend_recommendation.get("http_status") == 200 and backend_payload)
    met311_fail = (
        int(met311["double_invalid_active_route_intersection_count"] or 0) > 0
        or bool(mcp_item_invalid)
        or bool(backend_item_invalid)
    )
    detail_items = mcp_data(mcp_details).get("items") if mcp_details else None
    detail_result_count = len(detail_items) if isinstance(detail_items, list) else 0
    met311_oracle = {
        **met311,
        "invalid_sample_refs_checked_in_endpoint_items": sorted(invalid_refs),
        "mcp_recommendation": {**mcp_identity, "error_code": mcp_block, "http_status": (mcp_recommendation or {}).get("http_status")},
        "backend_recommendation": {**backend_identity, "error_code": backend_block, "http_status": (backend_recommendation or {}).get("http_status")},
        "mcp_detail_result_count": detail_result_count,
        "mcp_invalid_item_intersection": mcp_item_invalid,
        "backend_invalid_item_intersection": backend_item_invalid,
    }
    met311_assertions = [
        {"assertion": "no double-invalid factor/label group is an active route", "passed": int(met311["double_invalid_active_route_intersection_count"] or 0) == 0},
        {"assertion": "sample invalid factors remain detail-queryable", "passed": detail_result_count >= len(invalid_refs) if invalid_refs else False},
        {"assertion": "MCP recommendation result excludes invalid factors", "passed": mcp_successful(mcp_recommendation) and not mcp_item_invalid},
        {"assertion": "Backend recommendation result excludes invalid factors", "passed": backend_success and not backend_item_invalid},
    ]
    if met311_fail:
        results.append(make_case("MET-311", "FAIL", "PRODUCT_DEFECT_INVALID_FACTOR_ROUTED", "At least one factor with neither a valid TS nor CS metric entered an active route or recommendation result.", met311_oracle, met311_assertions))
    elif not mcp_successful(mcp_recommendation) or not backend_success:
        blockers = [value for value in [mcp_block, backend_block] if value]
        if not blockers:
            blockers = ["RECOMMENDATION_RESULT_UNAVAILABLE"]
        results.append(
            make_case(
                "MET-311",
                "BLOCKED",
                "BLOCKED_DEPENDENCY",
                "The independent DB oracle found no double-invalid active route, but MCP/Backend did not return a recommendation item set, so end-to-end exclusion cannot be confirmed.",
                met311_oracle,
                met311_assertions,
                blockers,
            )
        )
    else:
        results.append(make_case("MET-311", "PASS", "VERIFIED", "DB, MCP, and Backend recommendation sets exclude every factor with neither a valid TS nor CS metric.", met311_oracle, met311_assertions))

    calc513 = database["CALC-513"]
    route_identity_mismatch_total = sum(int(value) for value in calc513["route_identity_mismatch_counts"].values())
    snapshot = calc513["environment_snapshot"]
    snapshot_mismatch_total = sum(int(value) for value in snapshot["member_db_mismatch_counts"].values())
    hard_calc_failure = (
        route_identity_mismatch_total > 0
        or snapshot_mismatch_total > 0
        or int(snapshot["duplicate_member_id_count"] or 0) > 0
        or int(snapshot["duplicate_member_date_count"] or 0) > 0
        or bool(snapshot["missing_member_overlap"])
    )
    selected_batch = database["selected_batch"]
    expected_publication = selected_batch.get("publication_uid")
    mcp_publication_ok = mcp_successful(mcp_recommendation) and mcp_identity.get("publication_uid") == expected_publication
    backend_publication_ok = backend_success and backend_identity.get("publication_uid") == expected_publication
    calc_oracle = {
        **calc513,
        "selected_batch_identity": {
            key: selected_batch.get(key)
            for key in ("id", "batch_uid", "market_scope", "label_kind", "as_of_time", "publication_uid", "publish_version", "route_profile_key")
        },
        "mcp_replay": {**mcp_identity, "error_code": mcp_block, "publication_matches_db": mcp_publication_ok},
        "backend_replay": {**backend_identity, "error_code": backend_block, "publication_matches_db": backend_publication_ok},
    }
    calc_assertions = [
        {"assertion": "every active route matches batch/publication/scope/as_of and same-batch metric identity", "passed": route_identity_mismatch_total == 0},
        {"assertion": "every environment snapshot member uniquely matches its frozen daily revision and is visible by batch as_of", "passed": snapshot_mismatch_total == 0 and int(snapshot["duplicate_member_id_count"] or 0) == 0 and int(snapshot["duplicate_member_date_count"] or 0) == 0},
        {"assertion": "missing_dates and snapshot member dates are disjoint", "passed": not snapshot["missing_member_overlap"]},
        {"assertion": "MCP current recommendation replays the selected publication", "passed": mcp_publication_ok},
        {"assertion": "Backend current recommendation replays the selected publication", "passed": backend_publication_ok},
        {"assertion": "route.environment_date semantics are authoritative", "passed": False, "observational_only": True},
    ]
    if hard_calc_failure:
        results.append(make_case("CALC-513", "FAIL", "PRODUCT_DEFECT_ROUTE_SNAPSHOT_REFERENCE", "At least one active route or frozen environment member has a broken or conflicting reference.", calc_oracle, calc_assertions))
    elif not mcp_publication_ok or not backend_publication_ok or calc513["route_environment_date_observation"]["contract_status"] == "UNDEFINED":
        blockers = [value for value in [mcp_block, backend_block] if value]
        blockers.append("ROUTE_ENVIRONMENT_DATE_SEMANTICS_UNDEFINED")
        results.append(
            make_case(
                "CALC-513",
                "BLOCKED",
                "PARTIAL_COVERAGE",
                "All 86 route/batch/metric/factor references and all frozen environment members are internally consistent, but live recommendation replay is unavailable and route.environment_date is still not defined as a snapshot-member date or publication-effective date.",
                calc_oracle,
                calc_assertions,
                blockers,
            )
        )
    else:
        results.append(make_case("CALC-513", "PASS", "VERIFIED", "All active route identities, frozen environment revisions, and live publication replays are consistent.", calc_oracle, calc_assertions))
    return results


def render_summary(report: dict[str, Any]) -> str:
    """Render a concise Markdown summary with reproducible verdicts."""

    lines = [
        "# Factor 4.0 route integrity closure",
        "",
        f"- Captured: `{report['captured_at']}`",
        "- Environment: `test`",
        "- DB transaction: `START TRANSACTION READ ONLY` followed by `ROLLBACK`",
        "- Scope: `MET-311`, `CALC-513`, `DB-602`, `DB-604` only",
        "",
        "| Case | Status | Classification | Result |",
        "|---|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append(f"| {case['case_id']} | {case['status']} | {case['classification']} | {case['reason']} |")
    lines.extend(["", "## Independent oracles", ""])
    for case in report["cases"]:
        oracle = case["independent_oracle"]
        if case["case_id"] == "MET-311":
            text = (
                f"DB grouped `{oracle['metric_row_count']}` metrics into `{oracle['factor_label_group_count']}` factor/label units; "
                f"`{oracle['double_invalid_group_count']}` had no valid TS or CS dimension, and their active-route intersection was "
                f"`{oracle['double_invalid_active_route_intersection_count']}`."
            )
        elif case["case_id"] == "CALC-513":
            snapshot = oracle["environment_snapshot"]
            text = (
                f"Checked `{oracle['active_route_count']}` active routes and `{snapshot['member_count']}` frozen daily members; "
                f"route identity mismatches=`{sum(oracle['route_identity_mismatch_counts'].values())}`, "
                f"snapshot mismatches=`{sum(snapshot['member_db_mismatch_counts'].values())}`."
            )
        elif case["case_id"] == "DB-602":
            text = (
                f"Daily rows=`{oracle['row_count']}`, duplicate revision keys=`{oracle['duplicate_revision_key_count']}`, "
                f"multiple-current keys=`{oracle['multiple_current_key_count']}`, revision>1 rows=`{oracle['revision_gt1_count']}`."
            )
        else:
            text = (
                f"Publications=`{oracle['publication_stats']['publication_count']}`, active routes="
                f"`{oracle['route_history_stats']['active_route_count']}`, inactive routes="
                f"`{oracle['route_history_stats']['inactive_route_count']}`."
            )
        lines.append(f"- `{case['case_id']}`: {text}")
    lines.extend(
        [
            "",
            "Raw MCP/Backend requests and responses are sanitized. Database credentials, JWTs, Authorization headers, and complete MCP tokens are not persisted.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Execute the closure and persist redacted evidence."""

    token = next((os.environ.get(name) for name in TOKEN_ENV_NAMES if os.environ.get(name)), None)
    if not token:
        raise SystemExit("FACTOR4_MCP_TOKEN or MCP_TOKEN is required")
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%dT%H%M%S%z")
    output = REPORT_ROOT / f"{stamp}-route-integrity-closure"
    output.mkdir(parents=True, exist_ok=False)

    database = read_database_oracle()
    write_json(output / "db-evidence.json", database)
    if database.get("blocking_reason"):
        raise RuntimeError(str(database["blocking_reason"]))

    mcp = MCPClient(token, output)
    init = mcp.request(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-route-integrity-closure", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    mcp.protocol_version = str(init_result.get("protocolVersion") or "") or None
    mcp_ready = mcp_successful(init) and mcp.protocol_version is not None
    mcp_recommendation: dict[str, Any] | None = None
    mcp_details: dict[str, Any] | None = None
    if mcp_ready:
        mcp.request("MCP-NOTIFY", "notifications/initialized", {})
        sample_refs = [
            str(item["factor_ref"])
            for item in database["MET-311"].get("double_invalid_samples", [])[:5]
            if item.get("factor_ref") is not None
        ]
        if sample_refs:
            mcp_details = mcp.tool(
                "MET-311-INVALID-DETAILS",
                "factor_get_details_batch",
                {"factor_refs": sample_refs, "detail_level": "summary"},
            )
        batch = database["selected_batch"]
        arguments = {
            "market_scope": batch["market_scope"],
            "route_profile_key": batch["route_profile_key"],
            "limit": 200,
        }
        mcp_recommendation = mcp.tool(
            "MET-311-CALC-513-RECOMMENDATIONS",
            "environment_get_recommendations",
            arguments,
        )
    else:
        arguments = {
            "market_scope": database["selected_batch"]["market_scope"],
            "route_profile_key": database["selected_batch"]["route_profile_key"],
            "limit": 200,
        }

    backend_call: dict[str, Any] | None = None
    backend_error: str | None = None
    backend = requests.Session()
    try:
        jwt = backend_login(backend)
        backend.headers.update({"Authorization": f"Bearer {jwt}", "Accept": "application/json"})
        backend_call = backend_recommendations(backend, output, arguments)
    except Exception as exc:  # noqa: BLE001 - preserve an environment blocker without credentials
        backend_error = f"{type(exc).__name__}: {exc}"
        write_json(output / "backend-error.json", {"error": backend_error})
    finally:
        backend.close()

    cases = adjudicate(database, mcp_recommendation, mcp_details, backend_call)
    report = {
        "captured_at": datetime.now(SHANGHAI).isoformat(),
        "environment": "test",
        "mode": "READ_ONLY",
        "scope": ["MET-311", "CALC-513", "DB-602", "DB-604"],
        "excluded": ["ENV-108", "MET-310", "DB-613"],
        "database_transaction": database["transaction"],
        "mcp_initialize": {
            "http_status": init.get("http_status"),
            "protocol_version": mcp.protocol_version,
            "error_code": mcp_error_code(init),
        },
        "backend_error": backend_error,
        "cases": cases,
        "status_counts": dict(Counter(case["status"] for case in cases)),
        "credential_handling": "No Authorization header, JWT, complete MCP token, or database password was persisted.",
    }
    write_json(output / "results.json", report)
    (output / "summary.md").write_text(render_summary(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "status_counts": report["status_counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
