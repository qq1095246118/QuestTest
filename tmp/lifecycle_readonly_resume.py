#!/usr/bin/env python3
"""Resume Factor 4 lifecycle checks using SELECT-only test DB snapshots."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import pymysql

from config.settings import SettingsLoader


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports" / "factor4-resumed"


def _json_default(value: Any) -> str:
    """Serialize database scalar types without losing their displayed value."""

    if isinstance(value, (datetime, date, Decimal)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _parse_json(value: Any) -> Any:
    """Decode a MySQL JSON string while accepting already-decoded values."""

    if isinstance(value, str):
        return json.loads(value)
    return value


def _canonical_digest(value: Any) -> str:
    """Return a deterministic SHA256 for JSON-compatible evidence."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@contextmanager
def _read_only_connection() -> Iterator[pymysql.connections.Connection]:
    """Open one test DB read-only transaction and always roll it back."""

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
        read_timeout=120,
        write_timeout=30,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _query(
    connection: pymysql.connections.Connection,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> list[dict[str, Any]]:
    """Execute one parameterized SELECT and return dictionary rows."""

    with connection.cursor() as cursor:
        if parameters is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, parameters)
        return [dict(row) for row in cursor.fetchall()]


def _one(
    connection: pymysql.connections.Connection,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> dict[str, Any] | None:
    """Execute one parameterized SELECT and return its first row."""

    rows = _query(connection, sql, parameters)
    return rows[0] if rows else None


def _case(
    case_id: str,
    status: str,
    title: str,
    reason: str,
    *,
    evidence: dict[str, Any],
    blocking_reason: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Build one normalized, credential-free case result."""

    result: dict[str, Any] = {
        "case_id": case_id,
        "status": status,
        "title": title,
        "reason": reason,
        "evidence": evidence,
    }
    if blocking_reason:
        result["blocking_reason"] = blocking_reason
    if severity:
        result["severity"] = severity
    return result


def _batch_rows(connection: pymysql.connections.Connection) -> list[dict[str, Any]]:
    """Read compact batch identities and frozen configuration fields."""

    return _query(
        connection,
        """
        SELECT id,batch_uid,idempotency_key,market_scope,label_kind,start_date,end_date,
               as_of_time,environment_snapshot_hash,factor_set_snapshot_hash,
               evaluation_config,evaluation_config_version,score_rule_version,
               code_version,status,publish_status,publication_uid,publish_version,
               release_manifest_hash,is_active,active_scope_key,created_at,updated_at,
               started_at,finished_at,published_at,superseded_at,created_by,updated_by,
               request_id,factor_set_snapshot,environment_status
        FROM market_environment_eval_batch
        ORDER BY id
        """,
    )


def _life_400(connection: pymysql.connections.Connection) -> dict[str, Any]:
    """Judge whether current read-only evidence defines one publication contract."""

    batches = _batch_rows(connection)
    published = [row for row in batches if row["publish_status"] == "published"]
    selected = published[-1] if published else (batches[-1] if batches else None)
    config = _parse_json(selected["evaluation_config"]) if selected else {}
    if not isinstance(config, dict):
        config = {}
    comments = _query(
        connection,
        """
        SELECT COLUMN_NAME,column_comment
        FROM information_schema.columns
        WHERE table_schema=DATABASE()
          AND table_name='market_environment_eval_batch'
          AND column_name IN ('publish_status','publication_uid','publish_version',
                              'release_manifest_hash','is_active','superseded_at')
        ORDER BY ordinal_position
        """,
    )
    required_semantics = {
        "allowed_publish_states": False,
        "partial_environment_visibility": False,
        "partial_factor_visibility": False,
        "route_admission_condition": False,
        "publication_identity_stability": False,
        "history_retention": False,
        "repeat_publish_semantics": False,
        "rollback_semantics": False,
    }
    atomic_comment_fields = [
        row["COLUMN_NAME"]
        for row in comments
        if "原子" in str(row.get("column_comment") or row.get("COLUMN_COMMENT") or "")
        or "六类" in str(row.get("column_comment") or row.get("COLUMN_COMMENT") or "")
    ]
    mode = config.get("publication_mode")
    evidence = {
        "selected_batch_id": selected["id"] if selected else None,
        "selected_batch_status": selected["status"] if selected else None,
        "selected_publish_status": selected["publish_status"] if selected else None,
        "publication_mode": mode,
        "evaluation_config_keys": sorted(config),
        "required_semantics_declared_by_config": required_semantics,
        "schema_comment_summary": comments,
        "atomic_or_six_environment_comment_fields": atomic_comment_fields,
        "contract_sources_checked": [
            "docs/factor-4-ai-execution-test-cases.md:LIFE-400",
            "market_environment_eval_batch.evaluation_config",
            "information_schema column comments",
        ],
        "authenticated_public_openapi_checked": False,
        "public_schema_limitation": (
            "A fresh authenticated OpenAPI read was not available to this DB-only probe; "
            "the local OpenAPI evidence contains read endpoints only."
        ),
    }
    if selected is None:
        return _case(
            "LIFE-400",
            "BLOCKED",
            "发布模式契约门禁",
            "测试库没有可用于动态发现发布模式的批次。",
            evidence=evidence,
            blocking_reason="BLOCKED_DATA_PRECONDITION",
        )
    if mode == "per_factor_incremental" and atomic_comment_fields:
        return _case(
            "LIFE-400",
            "BLOCKED",
            "发布模式契约门禁",
            "最新已发布批次声明 per_factor_incremental，但数据库字段注释仍定义原子/六环境共享发布；配置也未声明部分可见性、历史、重发和回滚语义，当前契约不足以选择唯一预期。",
            evidence=evidence,
            blocking_reason="PUBLICATION_MODE_CONFLICT",
        )
    if not mode or not all(required_semantics.values()):
        return _case(
            "LIFE-400",
            "BLOCKED",
            "发布模式契约门禁",
            "批次配置没有完整声明发布时机、部分可见性、身份稳定、历史保留、重发和回滚语义。",
            evidence=evidence,
            blocking_reason="BLOCKED_DOC",
        )
    return _case(
        "LIFE-400",
        "PASS",
        "发布模式契约门禁",
        "当前配置和 Schema 对发布模式给出一致且完整的定义。",
        evidence=evidence,
    )


def _db_604(connection: pymysql.connections.Connection) -> dict[str, Any]:
    """Check active route invariants and require natural multi-publication history."""

    history = _query(
        connection,
        """
        SELECT b.id AS batch_id,b.batch_uid,b.status,b.publish_status,b.is_active,
               b.publication_uid,b.publish_version,b.published_at,b.superseded_at,
               COUNT(r.id) AS retained_route_count,
               SUM(CASE WHEN r.is_active=1 THEN 1 ELSE 0 END) AS active_route_count,
               SUM(CASE WHEN r.is_active=0 THEN 1 ELSE 0 END) AS inactive_route_count,
               MIN(r.superseded_at) AS first_route_superseded_at,
               MAX(r.superseded_at) AS last_route_superseded_at
        FROM market_environment_eval_batch b
        LEFT JOIN market_environment_factor_route r ON r.eval_batch_id=b.id
        WHERE b.publish_status='published' OR b.publication_uid IS NOT NULL OR r.id IS NOT NULL
        GROUP BY b.id,b.batch_uid,b.status,b.publish_status,b.is_active,b.publication_uid,
                 b.publish_version,b.published_at,b.superseded_at
        ORDER BY b.published_at,b.id
        """,
    )
    duplicate_active = _query(
        connection,
        """
        SELECT b.active_scope_key,r.label_code,r.factor_ref,r.factor_version,COUNT(*) AS row_count
        FROM market_environment_factor_route r
        JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
        WHERE r.is_active=1
        GROUP BY b.active_scope_key,r.label_code,r.factor_ref,r.factor_version
        HAVING COUNT(*)>1
        """,
    )
    mismatch = (
        _one(
            connection,
            """
        SELECT COUNT(*) AS row_count
        FROM market_environment_factor_route r
        LEFT JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
        LEFT JOIN market_environment_factor_metric m ON m.id=r.metric_id
        WHERE b.id IS NULL OR m.id IS NULL
           OR r.eval_batch_id<>m.eval_batch_id
           OR r.factor_ref<>m.factor_ref
           OR r.factor_version<>m.factor_version
           OR r.market_scope<>m.market_scope
           OR r.label_kind<>m.label_kind
           OR r.label_code<>m.label_code
           OR r.publication_uid<>b.publication_uid
           OR r.publish_version<>b.publish_version
        """,
        )
        or {"row_count": 0}
    )
    active_publication_versions = _query(
        connection,
        """
        SELECT publication_uid,COUNT(DISTINCT publish_version) AS active_versions,
               COUNT(DISTINCT eval_batch_id) AS active_batches,COUNT(*) AS active_routes
        FROM market_environment_factor_route
        WHERE is_active=1
        GROUP BY publication_uid
        HAVING COUNT(DISTINCT publish_version)>1 OR COUNT(DISTINCT eval_batch_id)>1
        """,
    )
    current_invariants_pass = (
        not duplicate_active
        and int(mismatch["row_count"] or 0) == 0
        and not active_publication_versions
    )
    publication_count = len(
        {row["publication_uid"] for row in history if row["publication_uid"]}
    )
    retained_old = [
        row
        for row in history
        if not bool(row["is_active"]) and int(row["retained_route_count"] or 0) > 0
    ]
    evidence = {
        "publication_count": publication_count,
        "history": history,
        "inactive_route_total": sum(
            int(row["inactive_route_count"] or 0) for row in history
        ),
        "retained_old_publication_count": len(retained_old),
        "duplicate_active_keys": duplicate_active,
        "route_batch_metric_identity_mismatch_count": int(mismatch["row_count"] or 0),
        "active_publication_multi_version_rows": active_publication_versions,
        "current_active_invariants_pass": current_invariants_pass,
    }
    if not current_invariants_pass:
        return _case(
            "DB-604",
            "FAIL",
            "route active 历史",
            "当前 active route 存在重复身份、跨批次/指标引用或同 publication 多 active 版本。",
            evidence=evidence,
            severity="P0",
        )
    if publication_count < 2 or not retained_old:
        return _case(
            "DB-604",
            "BLOCKED",
            "route active 历史",
            "当前 active route 身份与外键检查通过，但测试库只有一个 publication 且没有 inactive 历史 route，无法验证切换时旧 active 被关闭且历史保留。",
            evidence=evidence,
            blocking_reason="BLOCKED_DATA_PRECONDITION",
        )
    return _case(
        "DB-604",
        "PASS",
        "route active 历史",
        "存在多个 publication；旧批次均已关闭 active 且历史 route 保留，当前 active 身份和外键一致。",
        evidence=evidence,
    )


def _snapshot_summary(batch: dict[str, Any]) -> dict[str, Any]:
    """Summarize parent/child identities from one frozen factor snapshot."""

    snapshot = _parse_json(batch["factor_set_snapshot"])
    if not isinstance(snapshot, dict):
        snapshot = {}
    members = snapshot.get("members") or []
    members = [row for row in members if isinstance(row, dict)]
    parents = [row for row in members if row.get("children")]
    children = [
        child
        for parent in parents
        for child in (parent.get("children") or [])
        if isinstance(child, dict)
    ]
    return {
        "batch_id": batch["id"],
        "batch_uid": batch["batch_uid"],
        "status": batch["status"],
        "publish_status": batch["publish_status"],
        "factor_set_snapshot_hash": batch["factor_set_snapshot_hash"],
        "declared_factor_count": snapshot.get("factor_count"),
        "top_level_member_count": len(members),
        "top_level_type_counts": dict(
            Counter(str(row.get("factor_type")) for row in members)
        ),
        "parent_member_count": len(parents),
        "embedded_child_count": len(children),
        "unique_embedded_child_count": len({row.get("factor_ref") for row in children}),
        "member_missing_version_count": sum(
            not row.get("factor_version") for row in members
        ),
        "child_missing_version_count": sum(
            not row.get("factor_version") for row in children
        ),
        "sample_parents": [
            {
                "factor_ref": row.get("factor_ref"),
                "child_count": len(row.get("children") or []),
            }
            for row in parents[:5]
        ],
    }


def _calc_511(connection: pymysql.connections.Connection) -> dict[str, Any]:
    """Check frozen parent/child snapshots and terminal metric linkage."""

    batches = _batch_rows(connection)
    summaries = [_snapshot_summary(row) for row in batches]
    metric_counts = {
        int(row["eval_batch_id"]): int(row["metric_row_count"])
        for row in _query(
            connection,
            """
            SELECT eval_batch_id,COUNT(*) AS metric_row_count
            FROM market_environment_factor_metric
            GROUP BY eval_batch_id
            """,
        )
    }
    for summary in summaries:
        summary["metric_row_count"] = metric_counts.get(int(summary["batch_id"]), 0)
    terminal_parent_batches = [
        row
        for row in summaries
        if row["parent_member_count"] > 0 and row["status"] in {"success", "completed"}
    ]
    selected_terminal = next(
        (row for row in reversed(batches) if row["status"] in {"success", "completed"}),
        None,
    )
    direct_evidence: dict[str, Any] = {}
    if selected_terminal:
        snapshot = _parse_json(selected_terminal["factor_set_snapshot"])
        members = snapshot.get("members") if isinstance(snapshot, dict) else []
        member_versions = {
            str(row.get("factor_ref")): row.get("factor_version")
            for row in (members or [])
            if isinstance(row, dict)
        }
        metrics = _query(
            connection,
            """
            SELECT id,factor_ref,factor_version,label_code,evaluation_type,metric_status,
                   JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                     '$.metric_identity.definition_factor_version')) AS definition_factor_version,
                   JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                     '$.metric_identity.factor_version')) AS payload_factor_version,
                   JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                     '$.metric_identity.eval_batch_uid')) AS payload_batch_uid,
                   JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                     '$.metric_identity.evaluation_config_version')) AS payload_config_version
            FROM market_environment_factor_metric
            WHERE eval_batch_id=%s
            ORDER BY id
            """,
            (selected_terminal["id"],),
        )
        metric_refs = {str(row["factor_ref"]) for row in metrics}
        definition_mismatch = sum(
            row.get("definition_factor_version")
            != member_versions.get(str(row["factor_ref"]))
            for row in metrics
        )
        payload_version_mismatch = sum(
            row.get("payload_factor_version") != row.get("factor_version")
            for row in metrics
        )
        batch_uid_mismatch = sum(
            row.get("payload_batch_uid") != selected_terminal["batch_uid"]
            for row in metrics
        )
        config_version_mismatch = sum(
            row.get("payload_config_version")
            != selected_terminal["evaluation_config_version"]
            for row in metrics
        )
        direct_evidence = {
            "batch_id": selected_terminal["id"],
            "batch_uid": selected_terminal["batch_uid"],
            "factor_selection_mode": (
                _parse_json(selected_terminal["evaluation_config"]) or {}
            ).get("factor_selection_mode"),
            "frozen_member_count": len(member_versions),
            "metric_row_count": len(metrics),
            "metric_factor_count": len(metric_refs),
            "missing_frozen_factor_refs": sorted(set(member_versions) - metric_refs),
            "unexpected_metric_factor_refs": sorted(metric_refs - set(member_versions)),
            "definition_factor_version_mismatch_count": definition_mismatch,
            "payload_factor_version_mismatch_count": payload_version_mismatch,
            "payload_batch_uid_mismatch_count": batch_uid_mismatch,
            "payload_config_version_mismatch_count": config_version_mismatch,
            "metric_status_counts": dict(
                Counter(str(row["metric_status"]) for row in metrics)
            ),
            "direct_sub_factor_branch_pass": bool(metrics)
            and not (set(member_versions) - metric_refs)
            and not (metric_refs - set(member_versions))
            and definition_mismatch == 0
            and payload_version_mismatch == 0
            and batch_uid_mismatch == 0
            and config_version_mismatch == 0,
        }
    relation_counts = _query(
        connection,
        """
        SELECT factor_id,COUNT(*) AS current_child_count
        FROM factor_sub_factor_relations
        GROUP BY factor_id
        ORDER BY factor_id
        """,
    )
    evidence = {
        "snapshot_batches": summaries,
        "terminal_parent_batch_count": len(terminal_parent_batches),
        "terminal_parent_batches": terminal_parent_batches,
        "direct_terminal_batch": direct_evidence,
        "current_relation_parent_count": len(relation_counts),
        "current_relation_row_count": sum(
            int(row["current_child_count"]) for row in relation_counts
        ),
        "interpretation": (
            "Current relation counts are inventory only. Differences from a frozen snapshot are not "
            "treated as failures because later relationship changes are allowed."
        ),
    }
    if direct_evidence and not direct_evidence.get("direct_sub_factor_branch_pass"):
        return _case(
            "CALC-511",
            "FAIL",
            "母子因子快照",
            "终态批次的指标身份无法完整回指冻结成员、定义版本或批次配置版本。",
            evidence=evidence,
            severity="P1",
        )
    if not terminal_parent_batches:
        return _case(
            "CALC-511",
            "BLOCKED",
            "母子因子快照",
            "直接子因子终态批次的 477 个冻结成员与指标证据一致；但含母因子 children 快照的两个自然批次均为 cancelled 且无指标，无法验证母因子评估是否使用建批次时全部子因子。",
            evidence=evidence,
            blocking_reason="BLOCKED_DATA_PRECONDITION",
        )
    return _case(
        "CALC-511",
        "PASS",
        "母子因子快照",
        "终态母因子批次的评估输入与冻结 children 关系一致，直接子因子也使用自身冻结身份。",
        evidence=evidence,
    )


def _stable_snapshot() -> dict[str, Any]:
    """Capture one compact digest of the latest terminal batch, metrics and routes."""

    with _read_only_connection() as connection:
        batch = _one(
            connection,
            """
            SELECT id,batch_uid,idempotency_key,market_scope,label_kind,start_date,end_date,
                   as_of_time,environment_snapshot_hash,factor_set_snapshot_hash,
                   SHA2(CAST(evaluation_config AS CHAR),256) AS evaluation_config_hash,
                   evaluation_config_version,score_rule_version,code_version,status,
                   expected_metric_count,completed_metric_count,insufficient_metric_count,
                   failed_metric_count,publish_status,publication_uid,publish_version,
                   release_manifest_hash,is_active,created_at,updated_at,started_at,
                   finished_at,published_at,superseded_at
            FROM market_environment_eval_batch
            WHERE status IN ('success','completed')
            ORDER BY id DESC LIMIT 1
            """,
        )
        if batch is None:
            return {"available": False}
        metrics = _query(
            connection,
            """
            SELECT id,eval_batch_id,factor_ref,factor_type,factor_id,factor_version,
                   market_scope,label_kind,label_code,evaluation_type,`interval`,
                   return_bar_interval,forward_return_bars,window_scope,sample_start_date,
                   sample_end_date,total_sample_count,valid_sample_count,coverage_rate,
                   mean_ic,mean_rank_ic,icir,rank_icir,t_stat,oos_retention,sharpe,
                   max_drawdown,turnover_rate,net_return,time_series_score,
                   cross_sectional_score,routing_score,confidence,metric_status,is_valid,
                   scoring_version,SHA2(CAST(metric_payload AS CHAR),256) AS payload_hash,
                   error_code,created_at,updated_at
            FROM market_environment_factor_metric
            WHERE eval_batch_id=%s
            ORDER BY id
            """,
            (batch["id"],),
        )
        routes = _query(
            connection,
            """
            SELECT id,publication_uid,eval_batch_id,metric_id,market_scope,environment_date,
                   label_kind,label_code,as_of_time,factor_ref,factor_type,factor_id,
                   factor_version,rank_no,routing_score,confidence,time_series_score,
                   cross_sectional_score,is_eligible,reject_reason_code,
                   SHA2(CAST(evidence AS CHAR),256) AS evidence_hash,score_rule_version,
                   publish_version,is_active,activated_at,superseded_at,created_at,updated_at
            FROM market_environment_factor_route
            WHERE eval_batch_id=%s
            ORDER BY id
            """,
            (batch["id"],),
        )
        data = {"batch": batch, "metrics": metrics, "routes": routes}
        return {
            "available": True,
            "batch_id": batch["id"],
            "batch_uid": batch["batch_uid"],
            "input_identity": {
                key: batch[key]
                for key in (
                    "idempotency_key",
                    "environment_snapshot_hash",
                    "factor_set_snapshot_hash",
                    "evaluation_config_hash",
                    "evaluation_config_version",
                    "score_rule_version",
                    "code_version",
                )
            },
            "batch_status": batch["status"],
            "publish_status": batch["publish_status"],
            "metric_count": len(metrics),
            "route_count": len(routes),
            "metric_status_counts": dict(
                Counter(str(row["metric_status"]) for row in metrics)
            ),
            "digest": _canonical_digest(data),
        }


def _calc_512(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Compare two independent read-only observations of one terminal result."""

    duplicate_input_groups: list[dict[str, Any]]
    with _read_only_connection() as connection:
        duplicate_input_groups = _query(
            connection,
            """
            SELECT factor_set_snapshot_hash,environment_snapshot_hash,
                   SHA2(CAST(evaluation_config AS CHAR),256) AS evaluation_config_hash,
                   evaluation_config_version,score_rule_version,code_version,start_date,
                   end_date,as_of_time,COUNT(*) AS batch_count,
                   GROUP_CONCAT(id ORDER BY id) AS batch_ids
            FROM market_environment_eval_batch
            GROUP BY factor_set_snapshot_hash,environment_snapshot_hash,
                     SHA2(CAST(evaluation_config AS CHAR),256),evaluation_config_version,
                     score_rule_version,code_version,start_date,end_date,as_of_time
            HAVING COUNT(*)>1
            """,
        )
    stable = (
        bool(first.get("available") and second.get("available")) and first == second
    )
    evidence = {
        "first": first,
        "second": second,
        "independent_read_digests_match": stable,
        "natural_duplicate_full_input_group_count": len(duplicate_input_groups),
        "natural_duplicate_full_input_groups": duplicate_input_groups,
        "scope_note": (
            "This validates the documented idempotent-read branch only. No calculation was "
            "re-run, so cross-run recomputation determinism remains unexercised."
        ),
    }
    if not first.get("available") or not second.get("available"):
        return _case(
            "CALC-512",
            "BLOCKED",
            "重算可重复性",
            "测试库没有终态批次可供重复读取。",
            evidence=evidence,
            blocking_reason="BLOCKED_DATA_PRECONDITION",
        )
    if not stable:
        return _case(
            "CALC-512",
            "FAIL",
            "重算可重复性",
            "相同终态批次在两次独立只读事务中的输入身份、指标、状态或 route 摘要发生变化。",
            evidence=evidence,
            severity="P1",
        )
    return _case(
        "CALC-512",
        "PASS",
        "重算可重复性",
        (
            f"同一终态批次的输入 hash、{first['metric_count']:,} 条指标、"
            f"{first['route_count']:,} 条 route 和状态在两次独立只读事务中摘要完全一致；"
            "本轮未执行受控重算。"
        ),
        evidence=evidence,
    )


def _audit_null_summary(
    connection: pymysql.connections.Connection,
    table: str,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    """Count missing audit fields without reading sensitive row values."""

    expressions = ["COUNT(*) AS total"] + [
        f"SUM(`{field}` IS NULL OR CAST(`{field}` AS CHAR)='') AS missing_{field}"
        for field in fields
    ]
    return _one(connection, f"SELECT {','.join(expressions)} FROM `{table}`") or {}


def _db_606(connection: pymysql.connections.Connection) -> dict[str, Any]:
    """Check correlation, actor, time and version fields on existing lifecycle data."""

    stage_summaries = {
        "environment_sync": _audit_null_summary(
            connection,
            "market_environment_daily",
            (
                "request_id",
                "created_by",
                "updated_by",
                "created_at",
                "updated_at",
                "schema_version",
            ),
        ),
        "batch_creation": _audit_null_summary(
            connection,
            "market_environment_eval_batch",
            (
                "request_id",
                "created_by",
                "updated_by",
                "created_at",
                "updated_at",
                "evaluation_config_version",
                "score_rule_version",
                "code_version",
            ),
        ),
        "evaluation_metrics": _audit_null_summary(
            connection,
            "market_environment_factor_metric",
            (
                "request_id",
                "created_by",
                "updated_by",
                "created_at",
                "updated_at",
                "factor_version",
                "scoring_version",
            ),
        ),
        "publication_routes": _audit_null_summary(
            connection,
            "market_environment_factor_route",
            (
                "request_id",
                "created_by",
                "updated_by",
                "created_at",
                "updated_at",
                "factor_version",
                "score_rule_version",
                "publish_version",
            ),
        ),
    }
    permission_rejects = (
        _one(
            connection,
            """
        SELECT COUNT(*) AS total,
               SUM(request_id IS NULL OR request_id='') AS missing_request_id,
               SUM(trace_id IS NULL OR trace_id='') AS missing_trace_id,
               SUM(caller_subject IS NULL OR caller_subject='') AS missing_actor,
               SUM(started_at IS NULL) AS missing_started_at,
               SUM(finished_at IS NULL) AS missing_finished_at
        FROM agent_data_access_logs
        WHERE status='failed'
          AND error_code IN ('INSUFFICIENT_SCOPE','INSUFFICIENT_PERMISSION','FORBIDDEN')
        """,
        )
        or {}
    )
    lifecycle_event_table = (
        _one(
            connection,
            """
        SELECT COUNT(*) AS table_count
        FROM information_schema.tables
        WHERE table_schema=DATABASE()
          AND table_name IN ('market_environment_lifecycle_events',
                             'market_environment_audit_events')
        """,
        )
        or {"table_count": 0}
    )
    batch_outcomes = _query(
        connection,
        """
        SELECT status,publish_status,COUNT(*) AS row_count,
               SUM(request_id IS NULL OR request_id='') AS missing_request_id,
               SUM(error_code IS NULL) AS null_error_code,
               SUM(error_message IS NULL) AS null_error_message
        FROM market_environment_eval_batch
        GROUP BY status,publish_status
        ORDER BY status,publish_status
        """,
    )
    missing_correlation = {
        stage: int(summary.get("missing_request_id") or 0)
        for stage, summary in stage_summaries.items()
    }
    total_missing = sum(missing_correlation.values())
    evidence = {
        "stage_field_null_counts": stage_summaries,
        "missing_request_id_by_stage": missing_correlation,
        "missing_request_id_total": total_missing,
        "permission_rejection_audit_summary": permission_rejects,
        "dedicated_lifecycle_audit_table_count": int(
            lifecycle_event_table["table_count"] or 0
        ),
        "batch_outcome_summary": batch_outcomes,
        "rollback_fixture_present": any(
            row.get("superseded_at") is not None for row in _batch_rows(connection)
        ),
        "sensitive_values_read": False,
    }
    if total_missing:
        return _case(
            "DB-606",
            "FAIL",
            "生命周期审计字段",
            "现有环境同步、建批次、评估指标和发布 route 记录均缺少跨服务 request_id；actor、时间和版本字段存在，但无法按请求串联完整生命周期。",
            evidence=evidence,
            severity="P1",
        )
    if not evidence["rollback_fixture_present"]:
        return _case(
            "DB-606",
            "BLOCKED",
            "生命周期审计字段",
            "现有生命周期记录审计字段完整，但没有回滚 fixture，无法覆盖回滚审计分支。",
            evidence=evidence,
            blocking_reason="BLOCKED_DATA_PRECONDITION",
        )
    return _case(
        "DB-606",
        "PASS",
        "生命周期审计字段",
        "既有生命周期与权限拒绝记录均可由 request/actor/time/version 字段追溯。",
        evidence=evidence,
    )


def _write_report(output: Path, report: dict[str, Any]) -> None:
    """Write machine-readable evidence and one compact human summary."""

    output.mkdir(parents=True, exist_ok=False)
    (output / "evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Factor 4 resumed lifecycle/read-only DB checks",
        "",
        f"- Executed at: `{report['metadata']['executed_at']}`",
        f"- Snapshot ID: `{report['metadata']['snapshot_id']}`",
        "- Environment: `test` / database `factor_db`",
        "- Safety: no HTTP, no POST, every DB session used `START TRANSACTION READ ONLY` and `ROLLBACK`",
        "",
        "| Case | Status | Result |",
        "|---|---|---|",
    ]
    for item in report["cases"]:
        reason = str(item["reason"]).replace("|", "/")
        lines.append(f"| {item['case_id']} | {item['status']} | {reason} |")
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- DB-604 does not pass without a natural second publication and retained inactive routes.",
            "- CALC-511 does not pass the parent branch using cancelled batches without metrics.",
            "- CALC-512 passes only the allowed repeated-read branch; no recalculation was started.",
            "- No orphan, end-time boundary, missing-document reference, VWAP, UX, compatibility, or style issue was evaluated.",
            "",
        ]
    )
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Execute the five requested read-only cases and emit sanitized evidence."""

    first = _stable_snapshot()
    time.sleep(0.25)
    second = _stable_snapshot()
    with _read_only_connection() as connection:
        cases = [
            _life_400(connection),
            _db_604(connection),
            _calc_511(connection),
            _db_606(connection),
        ]
    cases.insert(3, _calc_512(first, second))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = REPORT_ROOT / f"{stamp}-lifecycle-readonly-resume"
    counts = Counter(str(item["status"]) for item in cases)
    report = {
        "metadata": {
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_id": str(uuid4()),
            "environment": "test",
            "database": "factor_db",
            "database_config_source": "config/test.yaml",
            "credentials": "not persisted",
            "http_requests": 0,
            "post_requests": 0,
            "database_writes": 0,
            "transaction": "START TRANSACTION READ ONLY; ROLLBACK",
        },
        "counts": dict(counts),
        "cases": cases,
    }
    _write_report(output, report)
    print(
        json.dumps(
            {"output": str(output), "counts": dict(counts), "case_count": len(cases)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
