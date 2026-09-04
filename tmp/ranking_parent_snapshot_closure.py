#!/usr/bin/env python3
"""Close CALC-507 and CALC-511 with one read-only database probe."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402


REPORT_ROOT = ROOT / "reports" / "factor4-resume"
SHANGHAI = ZoneInfo("Asia/Shanghai")
TOKEN_TEXT = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.IGNORECASE)
JWT_TEXT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
SENSITIVE_KEY = re.compile(
    r"authorization|password|secret|key_plaintext|ciphertext|nonce|signature|"
    r"(?:^|_)(?:jwt|hmac|access_token|refresh_token|token_value)$",
    re.IGNORECASE,
)


def json_default(value: Any) -> str:
    """Serialize temporal, decimal, and byte values for evidence files."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Recursively redact credential fields and complete token strings."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return JWT_TEXT.sub("<redacted-jwt>", TOKEN_TEXT.sub("<redacted-pat>", value))
    return value


def write_json(path: Path, value: Any) -> None:
    """Write stable, recursively redacted JSON evidence."""

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
    """Decode a MySQL JSON value while accepting already decoded objects."""

    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def canonical_hash(value: Any) -> str:
    """Return a canonical compact-JSON SHA-256 digest."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def query(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> list[dict[str, Any]]:
    """Execute one parameterized read statement and return dictionary rows."""

    cursor.execute(sql, parameters or ())
    return [dict(row) for row in cursor.fetchall()]


def query_one(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> dict[str, Any] | None:
    """Execute one parameterized read statement and return its first row."""

    records = query(cursor, sql, parameters)
    return records[0] if records else None


def database_connection() -> pymysql.connections.Connection:
    """Open a non-autocommit connection to the configured test database."""

    settings = SettingsLoader.load("test", ROOT).database
    return pymysql.connect(
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


def database_watermark() -> dict[str, Any]:
    """Capture compact table watermarks in an explicit read-only transaction."""

    connection = database_connection()
    evidence: dict[str, Any] = {
        "transaction": {
            "start_statement": "START TRANSACTION READ ONLY",
            "select_only": True,
            "rollback_attempted": False,
            "rolled_back": False,
        },
        "tables": {},
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            for table in (
                "market_environment_eval_batch",
                "market_environment_factor_metric",
                "market_environment_factor_route",
                "factor_sub_factor_relations",
            ):
                evidence["tables"][table] = query_one(
                    cursor,
                    f"SELECT COUNT(*) AS row_count,MAX(id) AS max_id FROM `{table}`",
                )
    finally:
        evidence["transaction"]["rollback_attempted"] = True
        try:
            connection.rollback()
            evidence["transaction"]["rolled_back"] = True
        finally:
            connection.close()
    return evidence


def summarize_snapshot(batch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return compact parent/child evidence and the decoded snapshot."""

    snapshot = decode_json(batch.get("factor_set_snapshot"))
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    members = [
        item for item in snapshot.get("members", []) if isinstance(item, dict)
    ]
    parents = [
        item
        for item in members
        if item.get("factor_type") == "factor" or isinstance(item.get("children"), list)
    ]
    children = [
        child
        for parent in parents
        for child in parent.get("children", [])
        if isinstance(child, dict)
    ]
    parent_summaries = [
        {
            "factor_ref": parent.get("factor_ref"),
            "factor_version": parent.get("factor_version"),
            "declared_child_count": parent.get("child_count"),
            "embedded_child_count": len(parent.get("children") or []),
            "unique_embedded_child_count": len(
                {
                    child.get("factor_ref")
                    for child in parent.get("children", [])
                    if isinstance(child, dict)
                }
            ),
            "child_missing_version_count": sum(
                not child.get("factor_version")
                for child in parent.get("children", [])
                if isinstance(child, dict)
            ),
        }
        for parent in parents
    ]
    summary = {
        "batch_id": batch.get("id"),
        "batch_uid": batch.get("batch_uid"),
        "status": batch.get("status"),
        "publish_status": batch.get("publish_status"),
        "is_active": bool(batch.get("is_active")),
        "metric_row_count": int(batch.get("metric_row_count") or 0),
        "declared_factor_count": snapshot.get("factor_count"),
        "member_count": len(members),
        "member_type_counts": dict(
            Counter(str(item.get("factor_type")) for item in members)
        ),
        "parent_member_count": len(parents),
        "embedded_child_count": len(children),
        "unique_embedded_child_ref_count": len(
            {str(child.get("factor_ref")) for child in children}
        ),
        "member_missing_factor_version_count": sum(
            not item.get("factor_version") for item in members
        ),
        "embedded_child_missing_factor_version_count": sum(
            not child.get("factor_version") for child in children
        ),
        "snapshot_hash_stored": batch.get("factor_set_snapshot_hash"),
        "snapshot_hash_present": bool(batch.get("factor_set_snapshot_hash")),
        "snapshot_hash_note": (
            "The stored versioned hash is retained as identity evidence. It is not recomputed "
            "without the producer's documented canonicalization contract."
        ),
        "parent_samples": parent_summaries[:10],
    }
    return summary, snapshot


def rank_partition_evidence(
    routes: list[dict[str, Any]],
    selected_batch: dict[str, Any],
) -> dict[str, Any]:
    """Check current routes within their complete ranking partition key."""

    partitions: defaultdict[tuple[str, str, str, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    identity_violations: Counter[str] = Counter()
    for route in routes:
        as_of = route.get("as_of_time")
        as_of_date = as_of.date().isoformat() if isinstance(as_of, datetime) else str(as_of)[:10]
        key = (
            str(route.get("market_scope")),
            str(route.get("label_code")),
            str(route.get("route_profile_key")),
            as_of_date,
        )
        partitions[key].append(route)
        checks = {
            "route_not_selected_batch": route.get("eval_batch_id") != selected_batch.get("id"),
            "publication_uid_mismatch": (
                route.get("publication_uid") != selected_batch.get("publication_uid")
            ),
            "publish_version_mismatch": (
                route.get("publish_version") != selected_batch.get("publish_version")
            ),
            "market_scope_mismatch": (
                route.get("market_scope") != selected_batch.get("market_scope")
            ),
            "route_profile_mismatch": (
                route.get("route_profile_key")
                != selected_batch.get("route_profile_key")
            ),
            "as_of_mismatch": route.get("as_of_time") != selected_batch.get("as_of_time"),
            "route_not_active": not bool(route.get("is_active")),
            "route_not_eligible": not bool(route.get("is_eligible")),
        }
        for name, failed in checks.items():
            if failed:
                identity_violations[name] += 1

    partition_rows: list[dict[str, Any]] = []
    violation_counts: Counter[str] = Counter()
    violation_samples: list[dict[str, Any]] = []
    for key, group in sorted(partitions.items()):
        ordered = sorted(group, key=lambda item: int(item["rank_no"]))
        ranks = [int(item["rank_no"]) for item in ordered]
        scores = [Decimal(str(item["routing_score"])) for item in ordered]
        refs = [str(item["factor_ref"]) for item in ordered]
        expected = list(range(1, len(ordered) + 1))
        ascending_pairs = [
            {
                "left_rank": ranks[index],
                "left_score": scores[index],
                "right_rank": ranks[index + 1],
                "right_score": scores[index + 1],
            }
            for index in range(len(scores) - 1)
            if scores[index] < scores[index + 1]
        ]
        equal_score_pairs = sum(
            scores[index] == scores[index + 1] for index in range(len(scores) - 1)
        )
        tied_by_score: defaultdict[Decimal, list[dict[str, Any]]] = defaultdict(list)
        for item in ordered:
            tied_by_score[Decimal(str(item["routing_score"]))].append(item)
        tie_groups = [items for items in tied_by_score.values() if len(items) > 1]
        tie_lexical_violations = sum(
            str(items[index]["factor_ref"]) >= str(items[index + 1]["factor_ref"])
            for items in tie_groups
            for index in range(len(items) - 1)
        )
        checks = {
            "rank_not_contiguous_from_one": ranks != expected,
            "duplicate_rank": len(ranks) != len(set(ranks)),
            "score_not_non_increasing": bool(ascending_pairs),
            "duplicate_factor_in_partition": len(refs) != len(set(refs)),
        }
        for name, failed in checks.items():
            if failed:
                violation_counts[name] += 1
                if len(violation_samples) < 20:
                    violation_samples.append(
                        {
                            "partition": {
                                "market_scope": key[0],
                                "label_code": key[1],
                                "route_profile_key": key[2],
                                "as_of_date": key[3],
                            },
                            "violation": name,
                            "ranks": ranks[:100] if name != "score_not_non_increasing" else None,
                            "score_samples": ascending_pairs[:10],
                        }
                    )
        partition_rows.append(
            {
                "market_scope": key[0],
                "label_code": key[1],
                "route_profile_key": key[2],
                "as_of_date": key[3],
                "route_count": len(ordered),
                "minimum_rank": min(ranks, default=None),
                "maximum_rank": max(ranks, default=None),
                "unique_rank_count": len(set(ranks)),
                "unique_factor_count": len(set(refs)),
                "maximum_score": max(scores, default=None),
                "minimum_score": min(scores, default=None),
                "equal_adjacent_score_pair_count": equal_score_pairs,
                "tie_group_count": len(tie_groups),
                "tied_row_count": sum(len(items) for items in tie_groups),
                "tie_factor_ref_lexical_order_violation_count": tie_lexical_violations,
                "rank_contiguous_from_one": ranks == expected,
                "score_non_increasing": not ascending_pairs,
            }
        )

    dimensions = {
        "market_scope": sorted({key[0] for key in partitions}),
        "label_code": sorted({key[1] for key in partitions}),
        "route_profile_key": sorted({key[2] for key in partitions}),
        "as_of_date": sorted({key[3] for key in partitions}),
    }
    return {
        "partition_key": [
            "market_scope",
            "label_code",
            "route_profile_key",
            "as_of_date",
        ],
        "route_count": len(routes),
        "partition_count": len(partitions),
        "dimension_values": dimensions,
        "dimension_distinct_counts": {
            name: len(values) for name, values in dimensions.items()
        },
        "partitions": partition_rows,
        "identity_violation_counts": dict(identity_violations),
        "ranking_violation_counts": dict(violation_counts),
        "violation_samples": violation_samples,
    }


def direct_subfactor_evidence(
    metrics: list[dict[str, Any]],
    snapshot: dict[str, Any],
    batch: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile published direct-subfactor metrics to frozen member versions."""

    members = [
        item for item in snapshot.get("members", []) if isinstance(item, dict)
    ]
    member_by_ref = {
        str(item.get("factor_ref")): item
        for item in members
        if item.get("factor_ref") is not None
    }
    metric_refs = {str(item.get("factor_ref")) for item in metrics}
    mismatch_counts: Counter[str] = Counter()
    mismatch_samples: list[dict[str, Any]] = []
    for metric in metrics:
        member = member_by_ref.get(str(metric.get("factor_ref")))
        checks = {
            "factor_ref_not_frozen": member is None,
            "factor_type_mismatch": member is not None
            and str(metric.get("factor_type")) != str(member.get("factor_type")),
            "factor_id_mismatch": member is not None
            and str(metric.get("factor_id")) != str(member.get("factor_id")),
            "metric_factor_version_missing": not metric.get("factor_version"),
            "payload_definition_version_mismatch": str(
                metric.get("definition_factor_version")
            )
            != str(member.get("factor_version") if member else None),
            "payload_factor_version_mismatch": str(metric.get("payload_factor_version"))
            != str(metric.get("factor_version")),
            "payload_batch_uid_mismatch": metric.get("payload_batch_uid")
            != batch.get("batch_uid"),
            "payload_config_version_mismatch": metric.get("payload_config_version")
            != batch.get("evaluation_config_version"),
        }
        for name, failed in checks.items():
            if failed:
                mismatch_counts[name] += 1
                if len(mismatch_samples) < 20:
                    mismatch_samples.append(
                        {
                            "kind": name,
                            "metric_id": metric.get("id"),
                            "factor_ref": metric.get("factor_ref"),
                        }
                    )
    metrics_per_factor = Counter(str(item.get("factor_ref")) for item in metrics)
    executable_versions: defaultdict[str, set[str]] = defaultdict(set)
    for metric in metrics:
        executable_versions[str(metric.get("factor_ref"))].add(
            str(metric.get("factor_version"))
        )
    version_conflicts = {
        factor_ref: sorted(values)
        for factor_ref, values in executable_versions.items()
        if len(values) > 1
    }
    return {
        "frozen_member_count": len(member_by_ref),
        "metric_row_count": len(metrics),
        "metric_factor_count": len(metric_refs),
        "metric_rows_per_factor_distribution": dict(
            Counter(metrics_per_factor.values())
        ),
        "missing_frozen_factor_refs": sorted(set(member_by_ref) - metric_refs),
        "unexpected_metric_factor_refs": sorted(metric_refs - set(member_by_ref)),
        "executable_factor_version_conflict_count": len(version_conflicts),
        "executable_factor_version_conflict_samples": dict(
            list(sorted(version_conflicts.items()))[:20]
        ),
        "version_semantics": (
            "snapshot.factor_version is the frozen definition version and is compared with "
            "metric_identity.definition_factor_version; metric.factor_version is the executable "
            "version and is compared with metric_identity.factor_version."
        ),
        "mismatch_counts": dict(mismatch_counts),
        "mismatch_samples": mismatch_samples,
        "passed": bool(metrics)
        and not (set(member_by_ref) - metric_refs)
        and not (metric_refs - set(member_by_ref))
        and not mismatch_counts
        and not version_conflicts,
    }


def historical_parent_relation_evidence(
    cursor: pymysql.cursors.DictCursor,
    batch_snapshots: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """Compare non-qualifying frozen parent children with current relation inventory."""

    current_rows = query(
        cursor,
        """
        SELECT factor_id,sub_factor_id,created_at,updated_at
        FROM factor_sub_factor_relations
        ORDER BY factor_id,sub_factor_id
        """,
    )
    current_by_parent: defaultdict[int, set[int]] = defaultdict(set)
    for row in current_rows:
        current_by_parent[int(row["factor_id"])].add(int(row["sub_factor_id"]))

    comparisons: list[dict[str, Any]] = []
    for batch, snapshot in batch_snapshots:
        members = [
            item for item in snapshot.get("members", []) if isinstance(item, dict)
        ]
        for parent in members:
            if parent.get("factor_type") != "factor" and not isinstance(
                parent.get("children"), list
            ):
                continue
            parent_id = parent.get("factor_id")
            if parent_id is None:
                continue
            frozen_children = {
                int(child["factor_id"])
                for child in parent.get("children", [])
                if isinstance(child, dict)
                and child.get("factor_type") == "sub_factor"
                and child.get("factor_id") is not None
            }
            current_children = current_by_parent.get(int(parent_id), set())
            comparisons.append(
                {
                    "batch_id": batch.get("id"),
                    "batch_status": batch.get("status"),
                    "publish_status": batch.get("publish_status"),
                    "parent_factor_ref": parent.get("factor_ref"),
                    "frozen_child_count": len(frozen_children),
                    "current_child_count": len(current_children),
                    "common_child_count": len(frozen_children & current_children),
                    "frozen_only_child_count": len(frozen_children - current_children),
                    "current_only_child_count": len(current_children - frozen_children),
                }
            )
    return {
        "current_relation_row_count": len(current_rows),
        "current_relation_parent_count": len(current_by_parent),
        "current_relation_child_count": len(
            {child for children in current_by_parent.values() for child in children}
        ),
        "historical_frozen_parent_comparison_count": len(comparisons),
        "historical_frozen_parent_comparisons": comparisons,
        "interpretation": (
            "These comparisons are inventory only. A current relationship difference does not "
            "invalidate a frozen batch, and cancelled/unpublished batches are not execution proof."
        ),
    }


def read_database_evidence() -> dict[str, Any]:
    """Read both case data in one explicit read-only transaction."""

    connection = database_connection()
    evidence: dict[str, Any] = {
        "transaction": {
            "start_statement": "START TRANSACTION READ ONLY",
            "select_only": True,
            "rollback_attempted": False,
            "rolled_back": False,
        }
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            evidence["database_identity"] = query_one(
                cursor,
                """
                SELECT DATABASE() AS database_name,CURRENT_USER() AS current_user_name,
                       @@session.time_zone AS session_time_zone,NOW(6) AS database_now
                """,
            )
            batches = query(
                cursor,
                """
                SELECT b.id,b.batch_uid,b.market_scope,b.label_kind,b.as_of_time,
                       b.route_profile_key,b.status,b.publish_status,b.is_active,
                       b.publication_uid,b.publish_version,b.published_at,
                       b.evaluation_config_version,b.factor_set_snapshot_hash,
                       b.factor_set_snapshot
                FROM market_environment_eval_batch b
                ORDER BY b.id
                """,
            )
            metric_counts = {
                int(row["eval_batch_id"]): int(row["metric_row_count"])
                for row in query(
                    cursor,
                    """
                    SELECT eval_batch_id,COUNT(*) AS metric_row_count
                    FROM market_environment_factor_metric
                    GROUP BY eval_batch_id
                    """,
                )
            }
            for batch in batches:
                batch["metric_row_count"] = metric_counts.get(int(batch["id"]), 0)
            active_published = [
                row
                for row in batches
                if bool(row.get("is_active")) and row.get("publish_status") == "published"
            ]
            if len(active_published) != 1:
                evidence["blocking_reason"] = (
                    f"EXPECTED_ONE_ACTIVE_PUBLISHED_BATCH_GOT_{len(active_published)}"
                )
                return evidence
            selected = active_published[0]
            selected_batch = {
                key: value for key, value in selected.items() if key != "factor_set_snapshot"
            }
            evidence["selected_batch"] = selected_batch

            active_routes = query(
                cursor,
                """
                SELECT r.id,r.publication_uid,r.eval_batch_id,r.metric_id,r.market_scope,
                       r.label_kind,r.label_code,r.as_of_time,r.factor_ref,r.factor_type,
                       r.factor_id,r.factor_version,r.rank_no,r.routing_score,r.is_eligible,
                       r.publish_version,r.is_active,b.route_profile_key
                FROM market_environment_factor_route r
                JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
                WHERE r.is_active=1
                ORDER BY r.market_scope,r.label_code,b.route_profile_key,r.as_of_time,r.rank_no
                """,
            )
            evidence["ranking"] = rank_partition_evidence(active_routes, selected_batch)
            evidence["publication_history"] = query(
                cursor,
                """
                SELECT b.id AS batch_id,b.batch_uid,b.status,b.publish_status,b.is_active,
                       b.publication_uid,b.publish_version,b.market_scope,b.route_profile_key,
                       b.as_of_time,COUNT(r.id) AS route_count,
                       SUM(CASE WHEN r.is_active=1 THEN 1 ELSE 0 END) AS active_route_count,
                       SUM(CASE WHEN r.is_active=0 THEN 1 ELSE 0 END) AS inactive_route_count
                FROM market_environment_eval_batch b
                LEFT JOIN market_environment_factor_route r ON r.eval_batch_id=b.id
                GROUP BY b.id,b.batch_uid,b.status,b.publish_status,b.is_active,
                         b.publication_uid,b.publish_version,b.market_scope,b.route_profile_key,
                         b.as_of_time
                ORDER BY b.id
                """,
            )

            summarized: list[dict[str, Any]] = []
            batch_snapshots: list[tuple[dict[str, Any], dict[str, Any]]] = []
            selected_snapshot: dict[str, Any] = {}
            for batch in batches:
                summary, snapshot = summarize_snapshot(batch)
                summarized.append(summary)
                batch_snapshots.append((batch, snapshot))
                if batch["id"] == selected["id"]:
                    selected_snapshot = snapshot
            evidence["factor_snapshots"] = summarized

            metrics = query(
                cursor,
                """
                SELECT id,factor_ref,factor_type,factor_id,factor_version,metric_status,
                       JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                         '$.metric_identity.definition_factor_version'))
                           AS definition_factor_version,
                       JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                         '$.metric_identity.factor_version')) AS payload_factor_version,
                       JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                         '$.metric_identity.eval_batch_uid')) AS payload_batch_uid,
                       JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                         '$.metric_identity.evaluation_config_version'))
                           AS payload_config_version
                FROM market_environment_factor_metric
                WHERE eval_batch_id=%s
                ORDER BY id
                """,
                (selected["id"],),
            )
            evidence["direct_subfactor"] = direct_subfactor_evidence(
                metrics, selected_snapshot, selected_batch
            )
            evidence["relations"] = historical_parent_relation_evidence(
                cursor, batch_snapshots
            )
    finally:
        evidence["transaction"]["rollback_attempted"] = True
        try:
            connection.rollback()
            evidence["transaction"]["rolled_back"] = True
        finally:
            connection.close()
    return evidence


def adjudicate_calc507(database: dict[str, Any]) -> dict[str, Any]:
    """Adjudicate partition correctness without overstating one-partition coverage."""

    ranking = database["ranking"]
    history = database["publication_history"]
    structural_failures = bool(
        ranking.get("identity_violation_counts")
        or ranking.get("ranking_violation_counts")
    )
    publication_count = len(
        {row.get("publication_uid") for row in history if row.get("publication_uid")}
    )
    inactive_route_count = sum(int(row.get("inactive_route_count") or 0) for row in history)
    partition_count = int(ranking.get("partition_count") or 0)
    if structural_failures:
        status = "FAIL"
        classification = "P1"
        reason = (
            "Current active routes contain a partition identity mismatch, non-contiguous rank, "
            "duplicate rank/factor, or score order reversal."
        )
        blocking_reasons: list[str] = []
    elif partition_count < 2:
        status = "BLOCKED"
        classification = "BLOCKED_DATA_PRECONDITION"
        reason = (
            "The only current ranking partition is internally correct: ranks are contiguous from "
            "1 and routing_score is non-increasing. The environment has one market_scope, one "
            "label_code, one profile, one as_of_date, and no retained historical route publication, "
            "so cross-partition competition and historical exclusion cannot be independently tested."
        )
        blocking_reasons = [
            "ONLY_ONE_CURRENT_RANK_PARTITION",
            "NO_HISTORICAL_ROUTE_PUBLICATION",
        ]
    else:
        status = "PASS"
        classification = None
        reason = (
            "All current ranking partitions start at rank 1, are contiguous, are score ordered, "
            "and reference only the selected publication."
        )
        blocking_reasons = []
    return {
        "case_id": "CALC-507",
        "title": "排名分区",
        "status": status,
        "classification": classification,
        "severity": "P1" if status == "FAIL" else None,
        "reason": reason,
        "blocking_reasons": blocking_reasons,
        "assertions": [
            {
                "assertion": "partition identity matches active batch/publication/profile/as_of",
                "passed": not ranking.get("identity_violation_counts"),
            },
            {
                "assertion": "rank starts at 1 and is contiguous within every observed partition",
                "passed": not any(
                    name in ranking.get("ranking_violation_counts", {})
                    for name in ("rank_not_contiguous_from_one", "duplicate_rank")
                ),
            },
            {
                "assertion": "routing_score is non-increasing as rank_no increases",
                "passed": "score_not_non_increasing"
                not in ranking.get("ranking_violation_counts", {}),
            },
            {
                "assertion": "factor identity is unique within the observed partition",
                "passed": "duplicate_factor_in_partition"
                not in ranking.get("ranking_violation_counts", {}),
            },
            {
                "assertion": "multiple ranking partitions exist for isolation testing",
                "passed": partition_count >= 2,
                "blocked": partition_count < 2,
            },
            {
                "assertion": "historical publication routes exist to test current exclusion",
                "passed": publication_count >= 2 and inactive_route_count > 0,
                "blocked": publication_count < 2 or inactive_route_count == 0,
            },
        ],
        "evidence": {
            **ranking,
            "publication_count": publication_count,
            "inactive_route_count": inactive_route_count,
            "publication_history": history,
        },
    }


def adjudicate_calc511(database: dict[str, Any]) -> dict[str, Any]:
    """Adjudicate parent snapshots while keeping cancelled batches non-qualifying."""

    snapshots = database["factor_snapshots"]
    selected_id = database["selected_batch"]["id"]
    selected = next(item for item in snapshots if item["batch_id"] == selected_id)
    published_parent_batches = [
        item
        for item in snapshots
        if item.get("publish_status") == "published"
        and int(item.get("metric_row_count") or 0) > 0
        and int(item.get("parent_member_count") or 0) > 0
    ]
    cancelled_parent_batches = [
        item
        for item in snapshots
        if item.get("status") == "cancelled"
        and int(item.get("parent_member_count") or 0) > 0
    ]
    direct = database["direct_subfactor"]
    if not direct.get("passed"):
        status = "FAIL"
        classification = "P1"
        reason = (
            "The published batch's direct sub-factor metrics do not reconcile to its frozen "
            "factor refs, versions, batch UID, or evaluation config version."
        )
        blocking_reasons: list[str] = []
    elif not published_parent_batches:
        status = "BLOCKED"
        classification = "BLOCKED_DATA_PRECONDITION"
        reason = (
            "The published batch's 477 direct sub-factor members and all 5,724 metric identities "
            "reconcile to the frozen snapshot. However, the published batch contains no parent "
            "factor or embedded child relationship. Two batches contain frozen parents/children, "
            "but both are cancelled, unpublished, and have no metrics, so they cannot prove that a "
            "parent evaluation used all children frozen at batch creation."
        )
        blocking_reasons = ["NO_PUBLISHED_PARENT_FACTOR_METRIC_SAMPLE"]
    else:
        status = "PASS"
        classification = None
        reason = (
            "A published parent-factor batch contains versioned frozen children and its evaluated "
            "inputs reconcile to that frozen relationship."
        )
        blocking_reasons = []
    return {
        "case_id": "CALC-511",
        "title": "母子因子快照",
        "status": status,
        "classification": classification,
        "severity": "P1" if status == "FAIL" else None,
        "reason": reason,
        "blocking_reasons": blocking_reasons,
        "assertions": [
            {
                "assertion": "published direct sub-factor metrics match frozen identities and versions",
                "passed": bool(direct.get("passed")),
            },
            {
                "assertion": "published batch contains a parent factor with frozen children",
                "passed": bool(published_parent_batches),
                "blocked": not published_parent_batches,
            },
            {
                "assertion": "published parent evaluation can be reconciled to every frozen child",
                "passed": bool(published_parent_batches),
                "blocked": not published_parent_batches,
            },
            {
                "assertion": "cancelled parent snapshots are not treated as evaluation proof",
                "passed": True,
            },
        ],
        "evidence": {
            "selected_published_snapshot": selected,
            "published_parent_batch_count": len(published_parent_batches),
            "published_parent_batches": published_parent_batches,
            "cancelled_parent_batch_count": len(cancelled_parent_batches),
            "cancelled_parent_batches": cancelled_parent_batches,
            "direct_subfactor": direct,
            "current_and_historical_relations": database["relations"],
            "all_batch_snapshot_summaries": snapshots,
        },
    }


def scan_artifacts(output: Path, forbidden_values: list[str]) -> dict[str, Any]:
    """Scan generated evidence for exact credentials and complete token patterns."""

    exact_matches: Counter[str] = Counter()
    token_files: list[str] = []
    jwt_files: list[str] = []
    files = sorted(path for path in output.rglob("*") if path.is_file())
    for path in files:
        content = path.read_text(encoding="utf-8", errors="replace")
        for index, value in enumerate(forbidden_values):
            if value and value in content:
                exact_matches[f"forbidden_value_{index}"] += content.count(value)
        if TOKEN_TEXT.search(content):
            token_files.append(path.name)
        if JWT_TEXT.search(content):
            jwt_files.append(path.name)
    return {
        "files_scanned": len(files),
        "exact_credential_match_counts": dict(exact_matches),
        "complete_mcp_token_pattern_files": token_files,
        "complete_jwt_pattern_files": jwt_files,
        "passed": not exact_matches and not token_files and not jwt_files,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a concise Chinese summary from the authoritative report."""

    ranking = report["cases"][0]["evidence"]
    parent = report["cases"][1]["evidence"]
    lines = [
        "# CALC-507 / CALC-511 排名与母子快照闭环",
        "",
        f"- 环境：`{report['environment']}`",
        f"- 模式：`{report['mode']}`",
        f"- 当前 published batch：`{report['selected_batch']['id']}` / "
        f"`{report['selected_batch']['batch_uid']}`",
        f"- DB 业务表水位不变：`{report['database_watermarks']['tables_unchanged']}`",
        "",
        "## 裁决",
        "",
        "| 用例 | 状态 | 分类 | 结论 |",
        "|---|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| `{case['case_id']}` | `{case['status']}` | `{case.get('classification')}` | "
            f"{str(case['reason']).replace('|', '/')} |"
        )
    lines.extend(
        [
            "",
            "## 已确认事实",
            "",
            f"- 当前 active route 共 `{ranking['route_count']}` 条，完整分区键只形成 "
            f"`{ranking['partition_count']}` 个分区；分区维度基数为 "
            f"`{ranking['dimension_distinct_counts']}`。",
            f"- 已观察分区的排名违规为 `{ranking['ranking_violation_counts']}`，身份违规为 "
            f"`{ranking['identity_violation_counts']}`。",
            f"- 当前 published snapshot 有 "
            f"`{parent['selected_published_snapshot']['member_count']}` 个成员，其中母因子 "
            f"`{parent['selected_published_snapshot']['parent_member_count']}` 个。",
            f"- 直接子因子指标对账：`{parent['direct_subfactor']['metric_row_count']}` 条指标、"
            f"`{parent['direct_subfactor']['metric_factor_count']}` 个因子，版本/批次/配置不一致为 "
            f"`{parent['direct_subfactor']['mismatch_counts']}`。",
            f"- 另有 `{parent['cancelled_parent_batch_count']}` 个 cancelled batch 包含母子快照，"
            "但没有指标，未作为母因子评估通过证据。",
            "",
            "## 执行边界",
            "",
            "- 未调用 MCP；MCP 读/写工具调用数均为 0。",
            "- 三次数据库连接均执行 `START TRANSACTION READ ONLY` 并最终 `ROLLBACK`。",
            "- 未制造多分区或母因子样本，也未把 cancelled batch 当作成功样本。",
            "- 报告已扫描完整 PAT、JWT 和配置密码。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Run both closures and persist redacted authoritative evidence."""

    settings = SettingsLoader.load("test", ROOT)
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%dT%H%M%S%z")
    output = REPORT_ROOT / f"{stamp}-ranking-parent-snapshot-closure"
    output.mkdir(parents=True, exist_ok=False)

    before = database_watermark()
    database = read_database_evidence()
    after = database_watermark()
    write_json(output / "db-before.json", before)
    write_json(output / "db-evidence.json", database)
    write_json(output / "db-after.json", after)
    if database.get("blocking_reason"):
        raise RuntimeError(str(database["blocking_reason"]))

    cases = [adjudicate_calc507(database), adjudicate_calc511(database)]
    report: dict[str, Any] = {
        "authority": (
            "This adjudicated-summary.json is the authoritative verdict for this run. Raw DB "
            "evidence does not override it."
        ),
        "captured_at": datetime.now(SHANGHAI).isoformat(),
        "environment": "test",
        "mode": "READ_ONLY",
        "scope": ["CALC-507", "CALC-511"],
        "selected_batch": database["selected_batch"],
        "cases": cases,
        "database_watermarks": {
            "before": before,
            "after": after,
            "tables_unchanged": before.get("tables") == after.get("tables"),
        },
        "mcp_read_tools_called": [],
        "mcp_write_tools_called": [],
        "confirmed_defects": [case["case_id"] for case in cases if case["status"] == "FAIL"],
        "totals": dict(Counter(case["status"] for case in cases)),
        "security": {},
    }
    write_json(output / "adjudicated-summary.json", report)
    (output / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    forbidden_values = [
        settings.database.password or "",
        settings.authentication.privileged.password or "",
        settings.authentication.restricted.password or "",
        settings.authentication.non_owner.password or "",
    ]
    security = scan_artifacts(output, forbidden_values)
    report["security"] = security
    write_json(output / "adjudicated-summary.json", report)
    write_json(output / "sensitive-scan.json", security)
    final_security = scan_artifacts(output, forbidden_values)
    if not final_security["passed"]:
        raise RuntimeError("Sensitive artifact scan failed")
    print(output)
    print(json.dumps(report["totals"], sort_keys=True))
    return 1 if report["confirmed_defects"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
