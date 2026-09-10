"""Factor 4.0 calculation-end coverage for the six market environments."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest

from api.factor_data_mcp_api import FactorDataMCPAPI
from db.factor4_calculation_repository import CalculationAuditSnapshot, Factor4CalculationRepository
from service.factor4_calculation_service import CalculationCheckResult, Factor4CalculationService
from service.factor4_scoring_service import AdmissionAuditResult, Factor4ScoringService, ScoringAuditResult


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]

def _verify_calculation_results(results: Sequence[CalculationCheckResult]) -> None:
    failed = [finding for result in results for finding in result.findings if finding.status == "FAIL"]
    blocked = [finding for result in results for finding in result.findings if finding.status.startswith("BLOCKED")]
    diagnostics = {"checked_count": sum(result.checked_count for result in results),
                   "blocked_count": len(blocked),
                   "failures": [finding.code for finding in failed],
                   "blocks": [finding.code for finding in blocked]}
    assert not failed and all(result.status != "FAIL" for result in results), diagnostics
    if blocked or any(result.status != "PASS" for result in results) or not diagnostics["checked_count"]:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: {diagnostics}")


def _verify_score_results(results: Sequence[ScoringAuditResult]) -> None:
    failed = [result for result in results if result.status == "FAIL"]
    blocked = [result for result in results if result.blocked_reasons or result.status == "BLOCKED_DATA_PRECONDITION"]
    diagnostics = {
        "metric_count": len(results),
        "checked_count": sum(result.checked_count for result in results),
        "checked_metric_count": sum(result.checked_count > 0 for result in results),
        "blocked_metric_count": len(blocked),
        "failures": [{"metric_id": result.metric_id, "label_code": result.label_code,
                      "mismatches": result.mismatches} for result in failed],
        "blocks": [{"metric_id": result.metric_id, "reasons": result.blocked_reasons} for result in blocked[:20]],
    }
    assert not failed, diagnostics
    if blocked or not results:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: {diagnostics}")
    assert all(result.status == "PASS" for result in results) and diagnostics["checked_count"] > 0, diagnostics


def test_persisted_v1_scores_match_independent_decimal_oracle(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...],
) -> None:
    """Recompute every score with the independent Decimal oracle when inputs are published.

    The current product may expose only summary metrics for a publication.  Such
    a projection is recorded as a data-precondition block; it must not be
    treated as a formula pass.  Any metric for which all inputs are available
    is required to match the persisted score within one micro-unit.
    """

    results = []
    auditor = Factor4ScoringService()
    for snapshot in factor4_closure_snapshots:
        thresholds = auditor.thresholds_from_batch_config(snapshot.batch.evaluation_config)
        results.extend(auditor.audit_snapshot_scores(snapshot, thresholds=thresholds))
    _verify_score_results(results)


def test_six_label_profile_reconciles_ts_cs_weight_renormalization(
    factor4_calculation_repository: Factor4CalculationRepository,
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...],
) -> None:
    """Check existing routes in all partitions; absent pair/route samples are unverified, not invalid."""
    service = Factor4CalculationService(factor4_calculation_repository, cast(FactorDataMCPAPI, object()))
    _verify_calculation_results([
        service.check_route_score_recalculation(snapshot) for snapshot in factor4_closure_snapshots
    ])


def _verify_admission_results(results: Sequence[AdmissionAuditResult]) -> None:
    failures = [{"metric_id": result.metric_id, "mismatches": result.mismatches,
                 "expected_reasons": result.expected_reasons, "actual_reasons": result.actual_reasons}
                for result in results if result.status == "FAIL"]
    blocked = [{"metric_id": result.metric_id, "reasons": result.blocked_reasons}
               for result in results if result.blocked_reasons]
    assert not failures, failures
    if not results or blocked:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: admission not fully verified: {blocked[:20]}")
    assert all(result.status == "PASS" and result.checked_count > 0 for result in results)


@pytest.mark.parametrize("scope", ["time_series", "cross_sectional"], ids=["ts", "cs"])
def test_final_metric_admission_matches_all_frozen_reject_rules(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...], scope: str,
) -> None:
    """Recompute all final v1 admission reasons; invalid/unscored metrics remain in the sample."""
    auditor = Factor4ScoringService()
    results = []
    for snapshot in factor4_closure_snapshots:
        by_id = {metric.id: metric for metric in snapshot.evaluation_metrics}
        results.extend(result for result in auditor.audit_snapshot_admission(snapshot)
                       if by_id[result.metric_id].evaluation_type == scope)
    _verify_admission_results(results)


@pytest.mark.parametrize("scope", ["time_series", "cross_sectional"], ids=["ts", "cs"])
@pytest.mark.parametrize("group", ["admission_boundaries", "null_inputs", "rank_fallback", "clip_endpoints", "rounding"])
def test_natural_final_metrics_cover_admission_and_scoring_branches(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...], scope: str, group: str,
) -> None:
    """Validate existing boundary results; no manufactured live values or approximate equalities."""
    result = Factor4ScoringService().natural_branch_coverage(factor4_closure_snapshots, scope, group)
    failures = [{"metric_id": metric.metric_id, "mismatches": metric.mismatches}
                for metric in result.metrics if metric.status == "FAIL"]
    assert not failures, failures
    blocked = [{"metric_id": metric.metric_id, "reasons": metric.blocked_reasons}
               for metric in result.metrics if metric.blocked_reasons]
    if result.missing or blocked or not result.metrics:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: group={group}, scope={scope}, "
                    f"missing_natural_branches={result.missing}, blocked_metrics={blocked[:20]}")
    assert all(metric.status == "PASS" and metric.checked_count > 0 for metric in result.metrics)
