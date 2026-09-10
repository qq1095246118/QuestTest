"""Follow actual recommendations to publication-bound MCP metrics and formulas."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
import json
from typing import Any

from api.factor4_formula_api import Factor4FormulaAPI
from db.factor4_calculation_repository import (
    CalculationAuditSnapshot, CalculationRepositoryError, Factor4CalculationRepository,
    PublishedRouteSnapshot,
)
from db.factor4_read_repository import DailyReadSnapshot, Factor4ReadRepository
from service.factor4_calculation_service import (
    CalculationIssue, _check_metric_formula_link, _compare_formula_projection,
)
from service.factor4_read_service import (
    Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition, _METRIC_FIELDS, read_tool_page,
    visible_daily_rows,
)
from service.factor4_recommendation_service import compare_recommendation_page, lifecycle_time, read_public_forecast


def _publication_identity(snapshot: CalculationAuditSnapshot | PublishedRouteSnapshot) -> tuple[Any, ...]:
    batch = snapshot.batch if isinstance(snapshot, CalculationAuditSnapshot) else snapshot
    return (batch.id if isinstance(snapshot, CalculationAuditSnapshot) else batch.batch_id,
            batch.publication_uid, batch.publish_version, batch.market_scope, batch.route_profile_key)


def _record_checks(checks: list[CalculationIssue], prefix: str, issues: list[str], blocked: list[str]) -> None:
    for check in checks:
        (issues if check.status == "FAIL" else blocked).append(prefix + check.code)


class Factor4RecommendationReplayService:
    """Reuse final-result checks while retaining the original public recommendation identity."""

    def __init__(self, reads: Factor4ReadService, repository: Factor4ReadRepository,
                 calculations: Factor4CalculationRepository) -> None:
        """Store gated read clients; construction performs no I/O and returns nothing."""
        self.reads, self.repository, self.calculations = reads, repository, calculations
        self.formulas = Factor4FormulaAPI(reads.api.mcp)

    def check_kind(self, snapshots: tuple[CalculationAuditSnapshot, ...], daily: DailyReadSnapshot,
                   kind: str) -> ReadCheck:
        """Replay actual recommendations for one factor kind in every published partition.

        MCP daily at the fixed as_of supplies the public forecast before recommendation
        and exact-Run formula reads; DB visibility remains the independent oracle.
        Environment metrics instead use the explicit batch because their API has no
        as_of parameter. Return failures and missing/drift evidence together so the
        caller can fail before blocking. Invalid kind raises ValueError. Unexpected
        transport errors propagate; declared data/contract errors are safely aggregated.
        """
        if kind not in {"factor", "sub_factor"}:
            raise ValueError("unsupported recommendation factor kind")
        issues: list[str] = []
        blocked: list[str] = []
        checked = 0
        absent: list[str] = []
        keys = [(snapshot.batch.market_scope, snapshot.batch.route_profile_key) for snapshot in snapshots]
        if len(keys) != len(set(keys)):
            issues.append("recommendation_chain:duplicate_publication_partition")
        forecasts = [row for row in visible_daily_rows(daily, "forecast", as_of=daily.as_of)
                     if row.get("label_status") == "ready"]
        expected_forecast = forecasts[0] if forecasts else None
        forecast, daily_check = read_public_forecast(self.reads, daily, daily.as_of)
        issues.extend(daily_check.issues)
        blocked.extend(daily_check.evidence["blocked"])
        for snapshot in snapshots:
            prefix = f"batch={snapshot.batch.id}:"
            try:
                result = self._partition(snapshot, forecast, expected_forecast, daily.as_of, kind)
            except ReadContractError as error:
                issues.append(prefix + str(error))
                continue
            except (ReadPrecondition, CalculationRepositoryError) as error:
                blocked.append(prefix + str(error))
                continue
            checked += result.checked_count
            issues.extend(prefix + issue for issue in result.issues)
            blocked.extend(prefix + reason for reason in result.evidence["blocked"])
            if result.evidence.get("kind_absent"):
                absent.append(prefix + kind)
                blocked.append(prefix + "recommended_kind_sample_absent=" + kind)
        if not snapshots:
            blocked.append("no_published_partitions")
        if not checked:
            blocked.append("no_recommended_factor_with_completed_formula_metric_replay")
        return ReadCheck(checked, tuple(dict.fromkeys(issues)), {
            "blocked": tuple(dict.fromkeys(blocked)), "kind": kind, "as_of": daily.as_of.isoformat(),
            "partition_count": len(snapshots), "kind_absent_partitions": tuple(absent),
            "public_forecast_id": (forecast or {}).get("id"),
        })

    def _partition(self, snapshot: CalculationAuditSnapshot, forecast: Mapping[str, Any] | None,
                   expected_forecast: Mapping[str, Any] | None, as_of: datetime, kind: str) -> ReadCheck:
        batch = snapshot.batch
        selectors = (batch.market_scope, batch.route_profile_key)
        expected_publication = _publication_identity(snapshot)
        before = self.calculations.read_published_route_snapshot(*selectors)
        if _publication_identity(before) != expected_publication:
            return ReadCheck(0, (), {"blocked": ("publication_drift_before_recommendation",)})
        if lifecycle_time(batch.published_at, database=True) > as_of:
            return ReadCheck(0, (), {"blocked": ("snapshot_publication_not_visible_at_fixed_asof",)})
        request = {"as_of": as_of.isoformat(), "limit": 200}
        page = read_tool_page(self.reads.api.recommendations(*selectors, **request))
        route_rows = tuple(asdict(route) for route in snapshot.routes)
        recommendation_issues = list(compare_recommendation_page(
            page, asdict(batch), route_rows, expected_forecast, limit=200).issues)
        publication = page.data.get("publication")
        same_publication = True
        for field in ("batch_uid", "publication_uid", "publish_version", "market_scope", "route_profile_key"):
            if not isinstance(publication, Mapping) or publication.get(field) != getattr(batch, field):
                if forecast is not None or page.items:
                    recommendation_issues.append("recommendation_chain:publication_field=" + field)
                same_publication = False
        issues: list[str] = []
        blocked: list[str] = []
        checked = 0
        selected = ([item for item in page.items if item.get("factor_type") == kind]
                    if same_publication and forecast is not None else [])
        for item in selected:
            prefix = "factor=" + str(item.get("factor_id")) + ":"
            try:
                result = self._factor(snapshot, item, forecast, as_of)
            except ReadContractError as error:
                issues.append(prefix + str(error))
                continue
            except ReadPrecondition as error:
                blocked.append(prefix + str(error))
                continue
            checked += result.checked_count
            issues.extend(prefix + issue for issue in result.issues)
            blocked.extend(prefix + reason for reason in result.evidence["blocked"])
        replay = None
        try:
            replay = read_tool_page(self.reads.api.recommendations(*selectors, **request))
        except ReadContractError as error:
            issues.append(str(error))
        except ReadPrecondition as error:
            blocked.append(str(error))
        after = None
        try:
            after = self.calculations.read_published_route_snapshot(*selectors)
        except CalculationRepositoryError:
            blocked.append("publication_pointer_unavailable_after_recommendation")
        if after is None or _publication_identity(after) != expected_publication:
            blocked.append("publication_drift_during_recommendation_replay")
        else:
            issues.extend(recommendation_issues)
            if replay is not None and page.data != replay.data:
                issues.append("recommendation_chain:fixed_asof_replay_changed")
        if forecast is None:
            blocked.append("no_visible_ready_forecast_for_recommendation_replay")
        return ReadCheck(checked, tuple(dict.fromkeys(issues)), {
            "blocked": tuple(dict.fromkeys(blocked)), "kind_absent": not selected,
        })

    def _factor(self, snapshot: CalculationAuditSnapshot, item: Mapping[str, Any],
                forecast: Mapping[str, Any] | None, as_of: datetime) -> ReadCheck:
        batch = snapshot.batch
        issues: list[str] = []
        blocked: list[str] = []
        routes = [route for route in snapshot.routes if route.factor_ref == item.get("factor_ref")
                  and route.factor_version == item.get("factor_version") and route.is_active and route.is_eligible
                  and forecast is not None and route.label_code == forecast["label_code"]]
        if len(routes) != 1:
            return ReadCheck(0, ("recommendation_chain:returned_factor_route_binding",), {"blocked": ()})
        route = routes[0]
        metrics = [metric for metric in snapshot.evaluation_metrics if metric.id == route.metric_id]
        if len(metrics) != 1:
            return ReadCheck(0, ("recommendation_chain:metric_reference_missing_or_duplicate",), {"blocked": ()})
        metric = metrics[0]
        for field, expected in (("eval_batch_id", batch.id), ("publication_uid", batch.publication_uid),
                                ("publish_version", batch.publish_version), ("market_scope", batch.market_scope),
                                ("route_profile_key", batch.route_profile_key), ("label_kind", batch.label_kind)):
            if getattr(route, field) != expected:
                issues.append("recommendation_chain:route_publication_field=" + field)
        for field in ("eval_batch_id", "factor_ref", "factor_type", "factor_id", "factor_version", "market_scope", "label_kind", "label_code"):
            if getattr(metric, field) != getattr(route, field):
                issues.append("recommendation_chain:metric_route_field=" + field)
        try:
            sample = self.repository.metric_sample(item["factor_type"], batch_uid=batch.batch_uid,
                                                   factor_ref=item["factor_ref"])
        except CalculationRepositoryError:
            sample = None
            blocked.append("recommended_factor_metric_sample_unavailable")
        if sample is None:
            blocked.append("recommended_factor_exact_metric_sample_missing")
        elif (sample.factor_ref != item["factor_ref"] or any(sample.batch.get(field) != getattr(batch, field)
              for field in ("id", "batch_uid", "publication_uid", "publish_version", "market_scope", "route_profile_key"))):
            blocked.append("recommended_factor_metric_sample_publication_drift")
        else:
            # Use the recommendation's actual ref and the original publication; this
            # call retains the existing full metric field and cardinality assertions.
            try:
                check = self.reads.check_environment_metrics(sample, label_code=route.label_code)
                issues.extend(check.issues)
            except ReadContractError as error:
                issues.append(str(error))
            except ReadPrecondition as error:
                blocked.append(str(error))
            matching = [row for row in sample.metrics if row.get("id") == metric.id]
            # Both reads must retain the original metric's persisted values,
            # not just its ID, before this can count as one coherent replay.
            stable_fields = [field for field in _METRIC_FIELDS
                             if field != "metric_payload" and hasattr(metric, field)]
            if len(matching) != 1 or any(matching[0].get(field) != getattr(metric, field)
                                       for field in stable_fields):
                blocked.append("recommended_factor_metric_snapshot_changed")
            elif isinstance(metric.metric_identity, Mapping):
                payload = matching[0].get("metric_payload")
                if isinstance(payload, (str, bytes)):
                    try:
                        payload = json.loads(payload)
                    except (TypeError, ValueError):
                        payload = None
                identity = payload.get("metric_identity") if isinstance(payload, Mapping) else None
                if identity != metric.metric_identity:
                    blocked.append("recommended_factor_formula_link_changed_between_db_reads")
        link_checks: list[CalculationIssue] = []
        formula = _check_metric_formula_link(metric, snapshot.formula_evidence, link_checks)
        _record_checks(link_checks, "recommendation_chain:", issues, blocked)
        if formula is None:
            return ReadCheck(0, tuple(issues), {"blocked": tuple(blocked)})
        if formula.factor_ref != item["factor_ref"]:
            issues.append("recommendation_chain:formula_recommended_factor_mismatch")
        if lifecycle_time(formula.run_completed_at, database=True) > as_of:
            blocked.append("bound_formula_run_not_visible_at_recommendation_asof")
            return ReadCheck(0, tuple(issues), {"blocked": tuple(blocked)})
        try:
            returned = read_tool_page(self.formulas.formula(asdict(formula), as_of=as_of.isoformat())).data
            formula_checks: list[CalculationIssue] = []
            _compare_formula_projection(formula, returned, formula_checks, include_internal_semantics=False)
            _record_checks(formula_checks, "recommendation_chain:", issues, blocked)
        except ReadContractError as error:
            issues.append(str(error))
        except ReadPrecondition as error:
            blocked.append(str(error))
        return ReadCheck(int(not blocked), tuple(dict.fromkeys(issues)), {"blocked": tuple(dict.fromkeys(blocked))})
