"""Version-bound route, metric, immutable formula and approved input reconciliation."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from db.factor4_calculation_repository import CalculationAuditSnapshot, EvaluationMetric, FormulaEvidence
from db.factor4_schema_repository import ApprovedSchemaSnapshot
from service.factor4_calculation_service import CalculationIssue, _check_metric_formula_link
from service.factor4_read_service import ReadCheck
from service.factor4_result_service import ENVIRONMENT_LABELS


def metric_raw_schema_version(metric: EvaluationMetric) -> str | None:
    """Return the metric's explicit raw-data schema version, never a current-schema default.

    Missing bindings return None. Conflicting aliases or malformed values raise
    ValueError. A generic environment schema version does not prove raw input identity.
    """
    identity = metric.metric_identity
    if identity is None:
        return None
    if not isinstance(identity, Mapping):
        raise ValueError("invalid metric identity")
    values = [identity[key] for key in ("raw_data_schema_version",) if key in identity]
    for key in ("formula", "formula_identity", "formula_evidence"):
        nested = identity.get(key)
        if isinstance(nested, Mapping) and "raw_data_schema_version" in nested:
            values.append(nested["raw_data_schema_version"])
    if not values:
        return None
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("invalid raw data schema version")
    if len(set(values)) != 1:
        raise ValueError("conflicting raw data schema versions")
    return values[0]


def _dependencies(schema: ApprovedSchemaSnapshot, required: tuple[Any, ...]) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    blocked: list[str] = []
    indexes: list[dict[str, Mapping[str, Any]]] = []
    for rows in (schema.mappings, schema.resolutions):
        valid = [row for row in rows if isinstance(row, Mapping) and isinstance(row.get("field_name"), str) and row["field_name"]]
        names = [row["field_name"] for row in valid]
        if len(valid) != len(rows) or len(set(names)) != len(names):
            issues.append("schema_duplicate_or_missing_field_identity")
        if any(row.get("schema_version", schema.version) != schema.version for row in valid):
            issues.append("schema_row_version_mismatch")
        indexes.append({row["field_name"]: row for row in valid})
    raw, derived = indexes
    visited: set[str] = set()

    def visit(name: str, path: frozenset[str]) -> None:
        if name in path:
            issues.append("schema_dependency_cycle")
            return
        if name in visited:
            return
        if name in raw and name in derived:
            blocked.append("schema_mapping_resolution_precedence_undefined")
            return
        if name in raw:
            source = raw[name]
            if not source.get("source_dataset") or not source.get("source_field"):
                issues.append("schema_raw_source_missing")
            visited.add(name)
            return
        if name not in derived:
            issues.append("formula_input_not_in_bound_schema")
            return
        row = derived[name]
        if "dependency_fields_json" not in row:
            blocked.append("schema_dependency_evidence_missing")
            return
        dependencies = row["dependency_fields_json"]
        if isinstance(dependencies, str):
            try:
                dependencies = json.loads(dependencies)
            except ValueError:
                issues.append("schema_dependency_invalid_json")
                return
        if not isinstance(dependencies, list) or not dependencies or any(
            not isinstance(value, str) or not value for value in dependencies
        ):
            issues.append("schema_dependency_invalid_fields")
            return
        for dependency in dependencies:
            visit(dependency, path | {name})
        visited.add(name)

    if not required:
        blocked.append("formula_required_fields_missing")
    for name in required:
        if not isinstance(name, str) or not name:
            issues.append("formula_required_field_invalid")
        else:
            visit(name, frozenset())
    return issues, blocked


class Factor4FormulaClosureService:
    """Audit final references without executing expressions or reading mutable formula defaults."""

    def check_routes(
        self, snapshot: CalculationAuditSnapshot, label: str,
        *, schemas: Mapping[str, ApprovedSchemaSnapshot | None] | None = None,
    ) -> ReadCheck:
        """Compare every active eligible route in a label with its exact formula chain.

        Schemas=None checks the route/metric/formula segment. A supplied mapping
        additionally checks the exact raw-schema version and dependency closure.
        Return issues and blocked evidence together so missing data cannot mask FAIL.
        Unknown labels raise ValueError. No I/O or mutation is performed.
        """
        if label not in ENVIRONMENT_LABELS:
            raise ValueError("unknown environment label")
        routes = [row for row in snapshot.routes if row.label_code == label and row.is_active and row.is_eligible]
        metrics = {row.id: row for row in snapshot.evaluation_metrics}
        counts = Counter(row.id for row in snapshot.evaluation_metrics)
        issues: list[str] = []
        blocked: list[str] = []
        checked = 0
        for route in routes:
            prefix = f"route={route.id}:"
            metric = metrics.get(route.metric_id)
            if metric is None or counts[route.metric_id] != 1:
                issues.append(prefix + "metric_reference_missing_or_duplicate")
                continue
            for name in ("eval_batch_id", "factor_ref", "factor_type", "factor_id", "factor_version", "market_scope", "label_kind", "label_code"):
                if getattr(route, name) != getattr(metric, name):
                    issues.append(prefix + "metric_" + name)
            batch = snapshot.batch
            for name, expected in (
                ("eval_batch_id", batch.id), ("publication_uid", batch.publication_uid),
                ("publish_version", batch.publish_version), ("score_rule_version", batch.score_rule_version),
                ("as_of_time", batch.as_of_time), ("market_scope", batch.market_scope),
                ("route_profile_key", batch.route_profile_key), ("label_kind", batch.label_kind),
            ):
                if getattr(route, name) != expected:
                    issues.append(prefix + "batch_" + name)
            if metric.metric_status != "success" or metric.is_valid is not True:
                issues.append(prefix + "metric_not_valid")
            if metric.scoring_version != batch.score_rule_version:
                issues.append(prefix + "metric_scoring_version")
            linked: list[CalculationIssue] = []
            formula = _check_metric_formula_link(metric, snapshot.formula_evidence, linked)
            issues.extend(prefix + item.code for item in linked if item.status == "FAIL")
            blocked.extend(prefix + item.code for item in linked if item.status != "FAIL")
            checked += int(formula is not None)
            route_identity = route.evidence.get("metric_identity")
            if route_identity is not None:
                trace_issues: list[CalculationIssue] = []
                other = _check_metric_formula_link(replace(metric, metric_identity=route_identity), snapshot.formula_evidence, trace_issues)
                issues.extend(prefix + item.code for item in trace_issues if item.status == "FAIL")
                blocked.extend(prefix + item.code for item in trace_issues if item.status != "FAIL")
                if formula is not None and other is not None and other.id != formula.id:
                    issues.append(prefix + "route_formula_evidence_mismatch")
            if schemas is not None:
                self._schema_chain(metric, formula, schemas, prefix, issues, blocked)
        if not routes:
            blocked.append("no_active_eligible_route_for_label")
        return ReadCheck(checked, tuple(issues), {
            "blocked": tuple(sorted(set(blocked))), "route_count": len(routes), "label": label,
        })

    @staticmethod
    def _schema_chain(
        metric: EvaluationMetric, formula: FormulaEvidence | None,
        schemas: Mapping[str, ApprovedSchemaSnapshot | None], prefix: str,
        issues: list[str], blocked: list[str],
    ) -> None:
        try:
            version = metric_raw_schema_version(metric)
        except ValueError:
            issues.append(prefix + "raw_schema_version_invalid_or_conflicting")
            return
        if version is None:
            blocked.append(prefix + "metric_raw_schema_version_unbound")
            return
        schema = schemas.get(version)
        if schema is None:
            blocked.append(prefix + "bound_approved_schema_unavailable")
            return
        if schema.version != version:
            issues.append(prefix + "schema_version_fallback")
            return
        if formula is None:
            return
        field_issues, field_blocks = _dependencies(schema, formula.required_fields)
        issues.extend(prefix + item for item in field_issues)
        blocked.extend(prefix + item for item in field_blocks)
