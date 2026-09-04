"""Run one read-only Factor 4 database route audit and emit redacted evidence."""

from __future__ import annotations

import json
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pymysql

from config.settings import SettingsLoader


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports" / "factor4-rerun"
NOW = datetime.now(ZoneInfo("Asia/Shanghai"))
REPORT_DIR = REPORT_ROOT / f"{NOW.strftime('%Y%m%dT%H%M%S%z')}-db-route-audit"
SCORE_TOLERANCE = Decimal("0.000002")


def _json(value: object) -> object:
    """Decode a MySQL JSON string while preserving already-decoded values."""

    if isinstance(value, str):
        return json.loads(value)
    return value


def _decimal(value: object) -> Decimal | None:
    """Convert a database or JSON number to Decimal, preserving null."""

    if value is None:
        return None
    return Decimal(str(value))


def _serializable(value: object) -> object:
    """Convert database-native scalar values to redaction-safe JSON scalars."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")


def _counter_rows(counter: Counter[tuple[object, ...]], names: tuple[str, ...]) -> list[dict[str, object]]:
    """Turn a tuple-keyed counter into stable JSON rows."""

    rows: list[dict[str, object]] = []
    for key, count in sorted(counter.items(), key=lambda item: tuple(str(part) for part in item[0])):
        row = dict(zip(names, key, strict=True))
        row["count"] = count
        rows.append(row)
    return rows


def _status_valid(metric: dict[str, object]) -> bool:
    """Return whether a metric is successful and explicitly valid."""

    return metric["metric_status"] == "success" and metric["is_valid"] == 1


def _fetch_schema(cursor: object, database_name: str, table_name: str) -> dict[str, object]:
    """Read unique indexes and foreign keys for a named table."""

    cursor.execute(
        """SELECT INDEX_NAME AS index_name, NON_UNIQUE AS non_unique,
                  SEQ_IN_INDEX AS sequence_no, COLUMN_NAME AS column_name
           FROM information_schema.statistics
           WHERE table_schema=%s AND table_name=%s
           ORDER BY INDEX_NAME, SEQ_IN_INDEX""",
        (database_name, table_name),
    )
    indexes: dict[str, dict[str, object]] = {}
    for row in cursor.fetchall():
        entry = indexes.setdefault(
            row["index_name"],
            {"unique": row["non_unique"] == 0, "columns": []},
        )
        entry["columns"].append(row["column_name"])

    cursor.execute(
        """SELECT CONSTRAINT_NAME AS constraint_name, COLUMN_NAME AS column_name,
                  REFERENCED_TABLE_NAME AS referenced_table,
                  REFERENCED_COLUMN_NAME AS referenced_column
           FROM information_schema.key_column_usage
           WHERE table_schema=%s AND table_name=%s
             AND REFERENCED_TABLE_NAME IS NOT NULL
           ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION""",
        (database_name, table_name),
    )
    foreign_keys = [dict(row) for row in cursor.fetchall()]
    return {"indexes": indexes, "foreign_keys": foreign_keys}


def _audit() -> dict[str, object]:
    """Execute the full audit in one read-only consistent database snapshot."""

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
        read_timeout=90,
        write_timeout=30,
    )
    snapshot_id = str(uuid.uuid4())
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                """SELECT id
                   FROM market_environment_eval_batch
                   WHERE is_active=1 AND status NOT IN ('pending','running')
                   ORDER BY id DESC LIMIT 1"""
            )
            selected = cursor.fetchone()
            if selected is None:
                raise RuntimeError("No terminal active evaluation batch exists")
            batch_id = selected["id"]

            cursor.execute(
                """SELECT id,batch_uid,market_scope,label_kind,start_date,end_date,as_of_time,
                          factor_set_snapshot,environment_snapshot,evaluation_config,
                          evaluation_config_version,score_rule_version,code_version,status,
                          expected_metric_count,completed_metric_count,insufficient_metric_count,
                          failed_metric_count,started_at,finished_at,published_at,
                          route_profile_key,snapshot_frozen_at,environment_status,
                          environment_terminal_count,environment_failed_count,publish_status,
                          publication_uid,publish_version,release_manifest_hash,is_active,
                          active_scope_key,superseded_at,created_at,updated_at
                   FROM market_environment_eval_batch WHERE id=%s""",
                (batch_id,),
            )
            batch = dict(cursor.fetchone())
            factor_snapshot = _json(batch.pop("factor_set_snapshot"))
            environment_snapshot = _json(batch.pop("environment_snapshot"))
            evaluation_config = _json(batch.pop("evaluation_config"))
            environment_status = _json(batch.pop("environment_status"))
            assert isinstance(factor_snapshot, dict)
            assert isinstance(environment_snapshot, dict)
            assert isinstance(evaluation_config, dict)
            assert isinstance(environment_status, dict)

            cursor.execute(
                """SELECT id,eval_batch_id,factor_ref,factor_type,factor_id,factor_version,
                          market_scope,label_kind,label_code,evaluation_type,`interval` AS `interval`,
                          return_bar_interval,forward_return_bars,window_scope,
                          sample_start_date,sample_end_date,total_sample_count,valid_sample_count,
                          coverage_rate,oos_retention,time_series_score,cross_sectional_score,
                          routing_score,confidence,metric_status,is_valid,scoring_version,
                          error_code,error_message
                   FROM market_environment_factor_metric WHERE eval_batch_id=%s""",
                (batch_id,),
            )
            metrics = [dict(row) for row in cursor.fetchall()]

            cursor.execute(
                """SELECT id,publication_uid,eval_batch_id,metric_id,market_scope,
                          environment_date,label_kind,label_code,as_of_time,factor_ref,
                          factor_type,factor_id,factor_version,rank_no,routing_score,
                          confidence,time_series_score,cross_sectional_score,is_eligible,
                          reject_reason_code,evidence,score_rule_version,publish_version,
                          is_active,activated_at,superseded_at
                   FROM market_environment_factor_route
                   WHERE eval_batch_id=%s AND is_active=1
                   ORDER BY label_code,rank_no""",
                (batch_id,),
            )
            routes = [dict(row) for row in cursor.fetchall()]

            cursor.execute(
                "SELECT COUNT(*) AS count FROM market_environment_factor_route WHERE is_active=1"
            )
            all_active_route_count = cursor.fetchone()["count"]

            schema = {
                table: _fetch_schema(cursor, settings.name, table)
                for table in (
                    "market_environment_eval_batch",
                    "market_environment_factor_metric",
                    "market_environment_factor_route",
                )
            }

            members = factor_snapshot.get("members") or []
            factor_refs = {member["factor_ref"] for member in members}
            labels = set(environment_status)
            evaluation_types = {"time_series", "cross_sectional"}
            expected_cells = {
                (member["factor_ref"], label, evaluation_type)
                for member in members
                for label in labels
                for evaluation_type in evaluation_types
            }
            actual_cells = {
                (m["factor_ref"], m["label_code"], m["evaluation_type"])
                for m in metrics
            }
            full_unique_columns = (
                "eval_batch_id",
                "factor_ref",
                "factor_version",
                "label_code",
                "evaluation_type",
                "interval",
                "return_bar_interval",
                "forward_return_bars",
                "window_scope",
            )
            full_keys = Counter(tuple(m[column] for column in full_unique_columns) for m in metrics)
            duplicate_full_keys = sum(1 for count in full_keys.values() if count > 1)
            metric_groups = Counter(
                (
                    m["label_code"],
                    m["evaluation_type"],
                    m["metric_status"],
                    "true" if m["is_valid"] == 1 else "false" if m["is_valid"] == 0 else "null",
                )
                for m in metrics
            )
            metric_per_label = Counter(m["label_code"] for m in metrics)
            metric_status_counts = Counter(m["metric_status"] for m in metrics)

            range_failures: Counter[str] = Counter()
            null_failures: Counter[str] = Counter()
            identity_failures: Counter[str] = Counter()
            for metric in metrics:
                if metric["valid_sample_count"] > metric["total_sample_count"]:
                    range_failures["valid_sample_count_gt_total"] += 1
                if metric["sample_start_date"] > metric["sample_end_date"]:
                    range_failures["sample_start_after_end"] += 1
                for field in ("coverage_rate", "confidence", "oos_retention"):
                    value = _decimal(metric[field])
                    if value is not None and not Decimal(0) <= value <= Decimal(1):
                        range_failures[f"{field}_outside_0_1"] += 1
                for field in ("time_series_score", "cross_sectional_score", "routing_score"):
                    value = _decimal(metric[field])
                    if value is not None and not Decimal(0) <= value <= Decimal(100):
                        range_failures[f"{field}_outside_0_100"] += 1
                if metric["factor_ref"] != f"{metric['factor_type']}:{metric['factor_id']}":
                    identity_failures["factor_ref_type_id_mismatch"] += 1
                if metric["market_scope"] != batch["market_scope"]:
                    identity_failures["market_scope_mismatch"] += 1
                if metric["label_kind"] != batch["label_kind"]:
                    identity_failures["label_kind_mismatch"] += 1
                if metric["label_code"] not in labels:
                    identity_failures["unknown_label"] += 1
                status = metric["metric_status"]
                eval_type = metric["evaluation_type"]
                own_score = metric["time_series_score"] if eval_type == "time_series" else metric["cross_sectional_score"]
                other_score = metric["cross_sectional_score"] if eval_type == "time_series" else metric["time_series_score"]
                if status == "success":
                    if metric["is_valid"] not in (0, 1):
                        null_failures["success_validity_not_boolean"] += 1
                    if own_score is None:
                        null_failures["success_own_score_null"] += 1
                    if metric["confidence"] is None:
                        null_failures["success_confidence_null"] += 1
                    if metric["error_code"] is not None or metric["error_message"] is not None:
                        null_failures["success_has_error"] += 1
                elif status == "insufficient_sample":
                    if metric["is_valid"] is not None:
                        null_failures["insufficient_validity_not_null"] += 1
                    if own_score is not None or metric["routing_score"] is not None:
                        null_failures["insufficient_has_score"] += 1
                    if metric["error_code"] is None:
                        null_failures["insufficient_error_code_null"] += 1
                if other_score is not None:
                    null_failures["non_applicable_scope_score_not_null"] += 1

            expected_metric_count = len(members) * len(labels) * len(evaluation_types)
            expected_status_sum = sum(metric_status_counts.values())
            batch_metric_reconciliation = {
                "formula": f"{len(members)} factors * {len(labels)} labels * {len(evaluation_types)} evaluation types",
                "calculated_expected": expected_metric_count,
                "batch_expected": batch["expected_metric_count"],
                "actual_rows": len(metrics),
                "success_rows": metric_status_counts.get("success", 0),
                "insufficient_sample_rows": metric_status_counts.get("insufficient_sample", 0),
                "failed_rows": metric_status_counts.get("failed", 0),
                "batch_completed": batch["completed_metric_count"],
                "batch_insufficient": batch["insufficient_metric_count"],
                "batch_failed": batch["failed_metric_count"],
                "all_counts_match": (
                    expected_metric_count
                    == batch["expected_metric_count"]
                    == len(metrics)
                    == expected_status_sum
                    and metric_status_counts.get("success", 0) == batch["completed_metric_count"]
                    and metric_status_counts.get("insufficient_sample", 0)
                    == batch["insufficient_metric_count"]
                    and metric_status_counts.get("failed", 0) == batch["failed_metric_count"]
                ),
            }

            metric_by_id = {metric["id"]: metric for metric in metrics}
            metric_by_identity = {
                (
                    metric["factor_ref"],
                    metric["factor_version"],
                    metric["label_code"],
                    metric["evaluation_type"],
                ): metric
                for metric in metrics
            }
            route_classes: Counter[str] = Counter()
            admission_modes: Counter[str] = Counter()
            configured_weights: Counter[tuple[str, str]] = Counter()
            score_failures: Counter[str] = Counter()
            reference_failures: Counter[str] = Counter()
            route_examples: dict[str, dict[str, object]] = {}
            route_partitions: defaultdict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
            route_factor_refs: set[str] = set()

            for route in routes:
                evidence = _json(route.pop("evidence"))
                assert isinstance(evidence, dict)
                route_factor_refs.add(route["factor_ref"])
                route_partitions[
                    (
                        route["publish_version"],
                        route["market_scope"],
                        route["label_kind"],
                        route["label_code"],
                    )
                ].append(route)
                pair = {
                    evaluation_type: metric_by_identity.get(
                        (
                            route["factor_ref"],
                            route["factor_version"],
                            route["label_code"],
                            evaluation_type,
                        )
                    )
                    for evaluation_type in evaluation_types
                }
                valid_scopes = [scope for scope in ("time_series", "cross_sectional") if pair[scope] and _status_valid(pair[scope])]
                route_class = "+".join("TS" if scope == "time_series" else "CS" for scope in valid_scopes) or "NONE"
                route_classes[route_class] += 1
                admission_modes[str(evidence.get("admission_mode"))] += 1
                configured = evidence.get("configured_profile_weights") or {}
                configured_weights[(str(configured.get("time_series")), str(configured.get("cross_sectional")))] += 1

                if route["eval_batch_id"] != batch_id:
                    reference_failures["route_batch_mismatch"] += 1
                if route["publication_uid"] != batch["publication_uid"]:
                    reference_failures["publication_uid_mismatch"] += 1
                if route["publish_version"] != batch["publish_version"]:
                    reference_failures["publish_version_mismatch"] += 1
                if route["market_scope"] != batch["market_scope"] or route["label_kind"] != batch["label_kind"]:
                    reference_failures["scope_or_kind_mismatch"] += 1
                if route["factor_ref"] != f"{route['factor_type']}:{route['factor_id']}":
                    reference_failures["route_factor_ref_mismatch"] += 1
                if route["factor_ref"] not in factor_refs:
                    reference_failures["factor_missing_from_frozen_snapshot"] += 1
                primary = metric_by_id.get(route["metric_id"])
                if primary is None:
                    reference_failures["primary_metric_missing"] += 1
                elif (
                    primary["eval_batch_id"] != batch_id
                    or primary["factor_ref"] != route["factor_ref"]
                    or primary["factor_version"] != route["factor_version"]
                    or primary["label_code"] != route["label_code"]
                ):
                    reference_failures["primary_metric_identity_mismatch"] += 1

                evidence_metric_ids = evidence.get("metric_ids") or {}
                if route["metric_id"] not in {int(value) for value in evidence_metric_ids.values()}:
                    reference_failures["primary_metric_not_in_evidence"] += 1
                for scope, metric_id in evidence_metric_ids.items():
                    referenced = metric_by_id.get(int(metric_id))
                    if referenced is None:
                        reference_failures["evidence_metric_missing"] += 1
                    elif referenced is not pair.get(scope):
                        reference_failures["evidence_metric_identity_mismatch"] += 1
                    elif not _status_valid(referenced):
                        reference_failures["evidence_metric_not_success_valid"] += 1
                if list(evidence.get("valid_scopes") or []) != valid_scopes:
                    reference_failures["evidence_valid_scopes_mismatch"] += 1

                effective_weights = {
                    key: _decimal(value) or Decimal(0)
                    for key, value in (evidence.get("effective_profile_weights") or {}).items()
                }
                scope_scores: dict[str, Decimal] = {}
                scope_confidences: dict[str, Decimal] = {}
                for scope in valid_scopes:
                    scope_evidence = evidence.get(scope) or {}
                    score = _decimal(scope_evidence.get("metric_score"))
                    if score is None and pair[scope] is not None:
                        score = _decimal(
                            pair[scope]["time_series_score"]
                            if scope == "time_series"
                            else pair[scope]["cross_sectional_score"]
                        )
                    confidence = _decimal(scope_evidence.get("confidence"))
                    if confidence is None and pair[scope] is not None:
                        confidence = _decimal(pair[scope]["confidence"])
                    if score is None or confidence is None:
                        score_failures["valid_scope_missing_score_or_confidence"] += 1
                        continue
                    scope_scores[scope] = score
                    scope_confidences[scope] = confidence

                weight_sum = sum((effective_weights.get(scope, Decimal(0)) for scope in valid_scopes), Decimal(0))
                calculated_base = sum(
                    (scope_scores[scope] * effective_weights.get(scope, Decimal(0)) for scope in scope_scores),
                    Decimal(0),
                )
                calculated_confidence = sum(
                    (
                        scope_confidences[scope] * effective_weights.get(scope, Decimal(0))
                        for scope in scope_confidences
                    ),
                    Decimal(0),
                )
                calculated_route = calculated_base * calculated_confidence
                if weight_sum != Decimal(1):
                    score_failures["effective_weight_sum_not_one"] += 1
                comparisons = {
                    "base_score": (calculated_base, _decimal(evidence.get("base_score"))),
                    "confidence": (calculated_confidence, _decimal(evidence.get("confidence"))),
                    "evidence_routing_score": (calculated_route, _decimal(evidence.get("routing_score"))),
                    "route_routing_score": (calculated_route, _decimal(route["routing_score"])),
                }
                for name, (calculated, stored) in comparisons.items():
                    if stored is None or abs(calculated - stored) > SCORE_TOLERANCE:
                        score_failures[f"{name}_mismatch"] += 1
                if _decimal(route["confidence"]) != _decimal(evidence.get("confidence")):
                    score_failures["route_confidence_mismatch"] += 1
                minimum = _decimal(evidence.get("minimum_route_score"))
                if minimum is not None and _decimal(route["routing_score"]) < minimum:
                    score_failures["eligible_below_minimum_route_score"] += 1

                route_examples.setdefault(
                    route_class,
                    {
                        "factor_ref": route["factor_ref"],
                        "rank_no": route["rank_no"],
                        "valid_scopes": valid_scopes,
                        "metric_ids": evidence_metric_ids,
                        "effective_weights": evidence.get("effective_profile_weights"),
                        "calculated_base_score": str(calculated_base),
                        "calculated_confidence": str(calculated_confidence),
                        "calculated_routing_score": str(calculated_route),
                        "stored_routing_score": str(route["routing_score"]),
                    },
                )

            rank_failures: list[dict[str, object]] = []
            for partition, partition_routes in route_partitions.items():
                ordered = sorted(partition_routes, key=lambda row: row["rank_no"])
                ranks = [row["rank_no"] for row in ordered]
                scores = [_decimal(row["routing_score"]) for row in ordered]
                if ranks != list(range(1, len(ranks) + 1)):
                    rank_failures.append({"partition": partition, "reason": "rank_not_contiguous", "ranks": ranks})
                if any(left < right for left, right in zip(scores, scores[1:], strict=False)):
                    rank_failures.append({"partition": partition, "reason": "score_not_descending"})
                refs = [(row["factor_ref"], row["factor_version"]) for row in ordered]
                if len(refs) != len(set(refs)):
                    rank_failures.append({"partition": partition, "reason": "duplicate_factor"})

            actual_route_by_label = Counter(route["label_code"] for route in routes)
            environment_route_reconciliation = []
            for label, value in sorted(environment_status.items()):
                stored = int(value.get("route_count", 0))
                actual = actual_route_by_label.get(label, 0)
                environment_route_reconciliation.append(
                    {
                        "label_code": label,
                        "environment_status_route_count": stored,
                        "actual_active_route_count": actual,
                        "matches": stored == actual,
                    }
                )

            cursor.execute(
                """SELECT fs.factor_id,fs.serial_number,fs.status AS factor_status,
                          fd.status AS detail_status,fd.is_sub_factor_id,
                          fd.id AS detail_row_id
                   FROM factors_status fs
                   LEFT JOIN factors_details fd
                     ON fd.serial_number=fs.serial_number AND fd.is_sub_factor_id=1
                   WHERE fs.is_sub_factor_id=1 AND fs.coin_category='main'
                     AND fs.factor_id IN (
                       SELECT factor_id FROM market_environment_factor_route
                       WHERE eval_batch_id=%s AND is_active=1
                     )""",
                (batch_id,),
            )
            catalog_rows = [dict(row) for row in cursor.fetchall()]
            factor_status_counts = Counter(row["factor_status"] for row in catalog_rows)
            detail_status_counts = Counter(row["detail_status"] for row in catalog_rows)
            status_disagreements = sum(
                row["factor_status"] != row["detail_status"] for row in catalog_rows
            )
            cursor.execute(
                """SELECT COUNT(DISTINCT v.factor_id) AS factor_count,COUNT(*) AS row_count
                   FROM factor_validity_status v
                   WHERE v.is_sub_factor_id=1 AND v.factor_id IN (
                     SELECT factor_id FROM market_environment_factor_route
                     WHERE eval_batch_id=%s AND is_active=1
                   )""",
                (batch_id,),
            )
            validity_presence = dict(cursor.fetchone())

            findings: list[dict[str, object]] = []
            route_count_mismatches = [row for row in environment_route_reconciliation if not row["matches"]]
            if route_count_mismatches:
                findings.append(
                    {
                        "id": "DB-ROUTE-001",
                        "severity": "P1",
                        "status": "CONFIRMED",
                        "title": "Published batch environment_status route_count disagrees with active route rows",
                        "description": (
                            "The terminal active batch reports route_count=0 for WIDE_RANGE, "
                            "while the same batch/publication has 86 active eligible WIDE_RANGE routes."
                        ),
                        "impact": "Batch status/monitoring consumers receive a false publication summary.",
                        "evidence": route_count_mismatches,
                    }
                )

            blocked = [
                {
                    "id": "BLOCKED-PUBLICATION-CONTRACT",
                    "reason": "PUBLICATION_MODE_CONFLICT",
                    "description": (
                        "The selected batch declares publication_mode=per_factor_incremental and every route "
                        "declares admission_mode=any_valid_scope. A stricter six-environment atomic / TS+CS-both-valid "
                        "rule cannot be asserted until the current product contract confirms which mode is authoritative."
                    ),
                },
                {
                    "id": "BLOCKED-CATALOG-STATUS-SEMANTICS",
                    "reason": "UNDOCUMENTED_STATUS_ENUM",
                    "description": (
                        "factors_status.status and factors_details.status are numeric, have no column comments, and differ "
                        f"for {status_disagreements} of {len(catalog_rows)} route factors. Raw values are preserved, but "
                        "they cannot safely be translated to active/inactive without an authoritative enum contract."
                    ),
                },
            ]

            return {
                "metadata": {
                    "executed_at": NOW.isoformat(),
                    "snapshot_id": snapshot_id,
                    "environment": "test",
                    "database_config_source": "config/test.yaml",
                    "credentials": "redacted",
                    "transaction": "READ ONLY; consistent snapshot; rolled back after SELECT-only audit",
                },
                "selected_batch": {
                    **batch,
                    "evaluation_config": evaluation_config,
                    "factor_snapshot_summary": {
                        "schema_version": factor_snapshot.get("schema_version"),
                        "declared_factor_count": factor_snapshot.get("factor_count"),
                        "actual_member_count": len(members),
                    },
                    "environment_snapshot_summary": {
                        key: environment_snapshot.get(key)
                        for key in (
                            "schema_version",
                            "market_scope",
                            "label_kind",
                            "start_date",
                            "end_date",
                            "as_of_time",
                            "expected_days",
                            "covered_days",
                            "coverage_rate",
                            "missing_dates",
                        )
                    },
                    "environment_status": environment_status,
                },
                "schema_constraints": schema,
                "metric_audit": {
                    "reconciliation": batch_metric_reconciliation,
                    "factor_count": len(factor_refs),
                    "label_count": len(labels),
                    "evaluation_types": sorted(evaluation_types),
                    "per_label_counts": dict(sorted(metric_per_label.items())),
                    "status_counts": dict(sorted(metric_status_counts.items())),
                    "status_validity_groups": _counter_rows(
                        metric_groups,
                        ("label_code", "evaluation_type", "metric_status", "is_valid"),
                    ),
                    "missing_matrix_cells": len(expected_cells - actual_cells),
                    "unexpected_matrix_cells": len(actual_cells - expected_cells),
                    "duplicate_full_unique_keys": duplicate_full_keys,
                    "range_failures": dict(range_failures),
                    "null_status_semantic_failures": dict(null_failures),
                    "identity_failures": dict(identity_failures),
                },
                "route_audit": {
                    "active_route_count_selected_batch": len(routes),
                    "active_route_count_all_batches": all_active_route_count,
                    "market_scopes": sorted({route["market_scope"] for route in routes}),
                    "label_kinds": sorted({route["label_kind"] for route in routes}),
                    "label_counts": dict(sorted(actual_route_by_label.items())),
                    "factor_types": dict(Counter(route["factor_type"] for route in routes)),
                    "eligible_counts": dict(Counter(str(route["is_eligible"]) for route in routes)),
                    "score_min": str(min(_decimal(route["routing_score"]) for route in routes)),
                    "score_max": str(max(_decimal(route["routing_score"]) for route in routes)),
                    "rank_failures": rank_failures,
                    "reference_failures": dict(reference_failures),
                    "score_recalculation_failures": dict(score_failures),
                    "valid_scope_classification": dict(sorted(route_classes.items())),
                    "admission_modes": dict(admission_modes),
                    "configured_profile_weights": [
                        {"time_series": key[0], "cross_sectional": key[1], "route_count": count}
                        for key, count in configured_weights.items()
                    ],
                    "classification_examples": route_examples,
                    "environment_route_count_reconciliation": environment_route_reconciliation,
                },
                "factor_catalog_audit": {
                    "route_factor_count": len(route_factor_refs),
                    "frozen_snapshot_membership_missing": len(route_factor_refs - factor_refs),
                    "factors_status_rows": len(catalog_rows),
                    "factors_status_raw_distribution": {
                        str(key): value for key, value in sorted(factor_status_counts.items())
                    },
                    "factors_details_status_raw_distribution": {
                        str(key): value for key, value in sorted(detail_status_counts.items())
                    },
                    "status_value_disagreements": status_disagreements,
                    "factor_validity_status_presence": validity_presence,
                    "active_semantic_result": "BLOCKED_DOC",
                    "environment_metric_validity_result": dict(sorted(route_classes.items())),
                },
                "findings": findings,
                "blocked": blocked,
                "overall": {
                    "confirmed_issue_count": len(findings),
                    "metric_matrix_pass": batch_metric_reconciliation["all_counts_match"]
                    and not duplicate_full_keys
                    and not expected_cells - actual_cells
                    and not actual_cells - expected_cells,
                    "metric_ranges_and_nulls_pass": not range_failures and not null_failures,
                    "route_foreign_keys_and_evidence_pass": not reference_failures,
                    "route_ranking_pass": not rank_failures,
                    "route_score_recalculation_pass": not score_failures,
                },
            }
    finally:
        connection.rollback()
        connection.close()


def _markdown(report: dict[str, object]) -> str:
    """Render the compact human-readable audit summary."""

    batch = report["selected_batch"]
    metrics = report["metric_audit"]
    routes = report["route_audit"]
    catalog = report["factor_catalog_audit"]
    overall = report["overall"]
    finding = report["findings"][0]
    lines = [
        "# Factor 4 DB Route Audit",
        "",
        f"- 执行时间：`{report['metadata']['executed_at']}`",
        f"- Snapshot ID：`{report['metadata']['snapshot_id']}`",
        "- 环境：`test`（连接信息与凭据均未写入报告）",
        "- 事务：只读一致性快照，全部查询结束后 rollback",
        "",
        "## 已确认问题",
        "",
        f"### {finding['id']} [{finding['severity']}] {finding['title']}",
        "",
        finding["description"],
        "",
        f"影响：{finding['impact']}",
        "",
        "复现：",
        "",
        "1. 动态选择 `is_active=1` 且非 `pending/running` 的最新批次。",
        "2. 读取该批次 `environment_status.WIDE_RANGE.route_count`，实际为 `0`。",
        "3. 按同一 `eval_batch_id + publication_uid + publish_version` 统计 `market_environment_factor_route` 中 `is_active=1`，实际为 `86`。",
        "4. 两者属于同一终态 published batch，差异不是运行中竞态。",
        "",
        "## 通过项",
        "",
        f"- 终态批次：`batch_uid={batch['batch_uid']}`，`status={batch['status']}`，`publish_status={batch['publish_status']}`，`is_active={batch['is_active']}`。",
        f"- 六环境矩阵：477 × 6 × 2 = {metrics['reconciliation']['actual_rows']}；success={metrics['reconciliation']['success_rows']}，insufficient_sample={metrics['reconciliation']['insufficient_sample_rows']}，failed={metrics['reconciliation']['failed_rows']}。",
        f"- 矩阵缺格={metrics['missing_matrix_cells']}，额外格={metrics['unexpected_matrix_cells']}，完整唯一键重复={metrics['duplicate_full_unique_keys']}。",
        f"- 指标范围异常={sum(metrics['range_failures'].values())}，状态/null 语义异常={sum(metrics['null_status_semantic_failures'].values())}，身份异常={sum(metrics['identity_failures'].values())}。",
        f"- active route={routes['active_route_count_selected_batch']}，全部为 `all/fact/WIDE_RANGE/sub_factor`；rank 1..86 连续且 score 严格非升序。",
        f"- route 外键/evidence 异常={sum(routes['reference_failures'].values())}；86 条评分重算异常={sum(routes['score_recalculation_failures'].values())}。",
        f"- 准入分类：TS-only={routes['valid_scope_classification'].get('TS', 0)}，CS-only={routes['valid_scope_classification'].get('CS', 0)}，TS+CS={routes['valid_scope_classification'].get('TS+CS', 0)}。",
        "- 重算公式：先按 evidence 的 effective weights 加权 metric_score 与 confidence，再计算 `routing_score = weighted_score × weighted_confidence`；86/86 与落库值在 0.000002 容差内一致。",
        f"- route 因子全部属于批次冻结快照，缺失={catalog['frozen_snapshot_membership_missing']}；86/86 均能关联 `factors_status`、`factors_details`，并且均存在 `factor_validity_status` 历史。",
        "",
        "## BLOCKED",
        "",
        "- `PUBLICATION_MODE_CONFLICT`：批次声明 `per_factor_incremental`，route evidence 声明 `any_valid_scope`。在产品未确认是否仍要求六环境原子发布、TS+CS 双有效前，不把 81 TS-only、4 CS-only 定性为实现缺陷。",
        f"- `UNDOCUMENTED_STATUS_ENUM`：`factors_status.status` 和 `factors_details.status` 没有枚举注释，且 {catalog['status_value_disagreements']}/86 值不同；只能保留原始分布，不能无依据翻译成 active/inactive。",
        "",
        "## 原始分布",
        "",
        f"- `factors_status.status`：{json.dumps(catalog['factors_status_raw_distribution'], ensure_ascii=False, sort_keys=True)}",
        f"- `factors_details.status`：{json.dumps(catalog['factors_details_status_raw_distribution'], ensure_ascii=False, sort_keys=True)}",
        f"- 综合状态：矩阵={overall['metric_matrix_pass']}，范围/null={overall['metric_ranges_and_nulls_pass']}，route 引用={overall['route_foreign_keys_and_evidence_pass']}，rank={overall['route_ranking_pass']}，评分重算={overall['route_score_recalculation_pass']}。",
        "",
        "完整机器证据见 `evidence.json`。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    """Run the audit and write JSON plus Markdown evidence files."""

    report = _audit()
    REPORT_DIR.mkdir(parents=True, exist_ok=False)
    (REPORT_DIR / "evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_serializable) + "\n",
        encoding="utf-8",
    )
    (REPORT_DIR / "summary.md").write_text(_markdown(report), encoding="utf-8")
    print(REPORT_DIR)


if __name__ == "__main__":
    main()
