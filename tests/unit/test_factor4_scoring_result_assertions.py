"""Offline regressions for independent score copies, frozen rules and partial evidence."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from service.factor4_calculation_oracles import environment_score_v1
from service.factor4_calculation_service import CalculationCheckResult, CalculationIssue
from service.factor4_result_service import ENVIRONMENT_LABELS
from service.factor4_scoring_service import Factor4ScoringService
from tests.cases.factor4 import test_scoring_environment_matrix as cases
from tests.unit.test_factor4_calculation_service import _batch, _metric as _stored_metric, _snapshot
from tests.unit.test_factor4_scoring_service import _complete_payload, _metric

pytestmark = pytest.mark.unit


def _scored(scope: str = "time_series", *, minimum_t_stat: str = "1.96") -> SimpleNamespace:
    payload = {**_complete_payload(), "oos": {"valid_fold_count": 3, "folds": [{}, {}, {}, {}]}}
    expected = environment_score_v1(payload, minimum_t_stat=Decimal(minimum_t_stat))
    metric = _metric(payload)
    metric.evaluation_type = scope
    metric.time_series_score = expected["metric_score"] if scope == "time_series" else None
    metric.cross_sectional_score = expected["metric_score"] if scope == "cross_sectional" else None
    metric.confidence = expected["confidence"]
    metric.score_components = {key: value for key, value in expected.items() if key != "confidence"}
    metric.metric_payload["score_components"] = dict(metric.score_components)
    metric.metric_payload["metric_score"] = expected["metric_score"]
    metric.metric_payload["confidence"] = expected["confidence"]
    metric.metric_payload["score_penalty"] = expected["penalty"]
    return metric


@pytest.mark.parametrize("scope", ["time_series", "cross_sectional"])
def test_dimension_column_cannot_hide_behind_correct_json_score(scope: str) -> None:
    """Correct JSON must not make a corrupt same-dimension database score pass."""
    metric = _scored(scope)
    setattr(metric, scope + "_score", Decimal("1"))
    result = Factor4ScoringService().audit_metric(metric)
    assert result.status == "FAIL"
    assert scope + "_score" in result.mismatches
    assert "score_components.metric_score" in result.actual_components
    assert result.checked_count > 1


@pytest.mark.parametrize("source", ["column", "components", "payload_components", "payload_score", "payload_confidence", "payload_penalty"])
def test_each_persisted_score_copy_is_independently_checked(source: str) -> None:
    """No merged representation may replace another explicitly saved numeric value."""
    metric = _scored()
    if source == "column":
        metric.confidence = Decimal("0.1")
    elif source == "components":
        metric.score_components["strength"] = Decimal("1")
    elif source == "payload_components":
        metric.metric_payload["score_components"]["stability"] = Decimal("1")
    else:
        key = {"payload_score": "metric_score", "payload_confidence": "confidence", "payload_penalty": "score_penalty"}[source]
        metric.metric_payload[key] = Decimal("1")
    result = Factor4ScoringService().audit_metric(metric)
    assert result.status == "FAIL", result
    assert result.mismatches


@pytest.mark.parametrize("value", ["NaN", "Infinity", True, "not-a-number"])
def test_malformed_saved_component_does_not_disappear_from_audit(value: object) -> None:
    """An explicit invalid score cannot be silently omitted from expected comparisons."""
    metric = _scored()
    metric.score_components["strength"] = value
    result = Factor4ScoringService().audit_metric(metric)
    assert result.status == "FAIL"
    assert "score_components.strength" in result.mismatches


def test_score_copies_contradiction_survives_missing_oracle_inputs() -> None:
    """Known column/JSON differences fail even when the complete score cannot be rebuilt."""
    metric = _scored()
    del metric.metric_payload["coverage_rate"]
    metric.time_series_score = Decimal("1")
    result = Factor4ScoringService().audit_metric(metric)
    assert result.status == "FAIL"
    assert "time_series_score" in result.mismatches
    assert any("coverage_rate" in reason for reason in result.blocked_reasons)
    assert result.checked_count > 0


def test_missing_scope_score_cannot_be_filled_from_components() -> None:
    """A missing dimension result is incomplete evidence even when JSON score exists."""
    metric = _scored()
    metric.time_series_score = None
    result = Factor4ScoringService().audit_metric(metric)
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert result.checked_count > 0
    assert any("time_series_score" in reason for reason in result.blocked_reasons)


def test_missing_confidence_column_cannot_be_filled_from_payload() -> None:
    """An explicit null column remains incomplete even when JSON contains confidence."""
    metric = _scored()
    metric.confidence = None
    result = Factor4ScoringService().audit_metric(metric)
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "metric_payload.confidence" in result.actual_components
    assert "confidence" not in result.actual_components
    assert "persisted score is missing: confidence" in result.blocked_reasons


def test_snapshot_uses_frozen_t_threshold_and_each_actual_fold_count() -> None:
    """The configured minimum must influence scoring, never substitute for measured folds."""
    first, second = _scored(minimum_t_stat="2.5"), _scored(minimum_t_stat="2.5")
    second.id = 43
    second.metric_payload["oos"]["valid_fold_count"] = 2
    snapshot = SimpleNamespace(batch=SimpleNamespace(evaluation_config={"min_directed_t_stat": "2.5", "min_oos_valid_folds": 3}),
                               evaluation_metrics=(first, second))
    results = Factor4ScoringService().audit_snapshot_scores(snapshot)
    assert [result.status for result in results] == ["PASS", "PASS"]
    assert [result.valid_oos_folds for result in results] == [3, 2]
    assert results[0].admission_reasons == ()
    assert results[1].admission_reasons == ("OOS_FOLDS_INCOMPLETE",)


@pytest.mark.parametrize("folds", [None, True, -1, "3", 5])
def test_missing_or_malformed_measured_folds_never_become_the_minimum(folds: object) -> None:
    """Absent result evidence blocks admission evidence while preserving numeric comparisons."""
    metric = _scored()
    if folds is None:
        del metric.metric_payload["oos"]["valid_fold_count"]
    else:
        metric.metric_payload["oos"]["valid_fold_count"] = folds
    result = Factor4ScoringService().audit_snapshot_scores(SimpleNamespace(
        batch=SimpleNamespace(evaluation_config={"min_oos_valid_folds": 3}), evaluation_metrics=(metric,),
    ))[0]
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert result.valid_oos_folds is None
    assert result.admission_reasons == ()
    assert result.checked_count > 0


def test_score_failure_is_not_masked_by_missing_fold_evidence() -> None:
    """Failure and missing admission evidence are both retained for the same metric."""
    metric = _scored()
    del metric.metric_payload["oos"]
    metric.time_series_score = Decimal("1")
    result = Factor4ScoringService().audit_metric(metric)
    assert result.status == "FAIL"
    assert result.blocked_reasons


@pytest.mark.parametrize("corrupt", [False, True])
def test_case_partial_evidence_never_passes_and_failure_wins(corrupt: bool) -> None:
    """One good result cannot absorb another metric's block; genuine mismatches still fail."""
    good, incomplete = _scored(), _scored()
    incomplete.id = 43
    del incomplete.metric_payload["oos"]
    if corrupt:
        good.time_series_score = Decimal("1")
    results = tuple(Factor4ScoringService().audit_metric(metric) for metric in (good, incomplete))
    with pytest.raises(AssertionError if corrupt else pytest.skip.Exception) as error:
        cases._verify_score_results(results)
    diagnostic = str(error.value)
    assert "checked_metric_count" in diagnostic and "blocked_metric_count" in diagnostic


def test_live_case_passes_frozen_thresholds_without_a_batch_fold_override() -> None:
    """Exercise the actual Case so an accidentally omitted threshold argument is detected."""
    metric = _scored(minimum_t_stat="2.5")
    snapshot = SimpleNamespace(batch=SimpleNamespace(evaluation_config={"min_directed_t_stat": "2.5"}), evaluation_metrics=(metric,))
    cases.test_persisted_v1_scores_match_independent_decimal_oracle((snapshot,))


def test_default_profile_six_labels_with_zero_routes_is_a_legal_result() -> None:
    """Normal profile names and zero routes do not require an artificial six-label fixture."""
    metrics = tuple(_stored_metric(index + 1, "sub_factor:10", "time_series", label_code=label)
                    for index, label in enumerate(ENVIRONMENT_LABELS))
    statuses = {label: {"status": "success", "route_count": 0, "metric_count": 1} for label in ENVIRONMENT_LABELS}
    snapshot = _snapshot(batch=_batch(environment_status=statuses), metrics=metrics, routes=())
    cases.test_six_environment_profile_has_routes_and_metrics_for_every_label((snapshot,))


def test_all_partitions_are_checked_before_environment_blocks_are_reported() -> None:
    """A sparse first partition must not hide a later partition's wrong summary."""
    first = _snapshot(batch=_batch(environment_status={}), metrics=(), routes=())
    second = _snapshot(batch=_batch(environment_status={label: {"status": "success", "route_count": 1} for label in ENVIRONMENT_LABELS}),
                       metrics=(), routes=())
    with pytest.raises(AssertionError, match="ROUTE_ENVIRONMENT_ROUTE_COUNT_MISMATCH"):
        cases.test_six_environment_profile_has_routes_and_metrics_for_every_label((first, second))


def test_weight_case_runs_all_profiles_and_does_not_stop_at_first_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route weight checks use every supplied publication and preserve later failures."""
    blocked = CalculationCheckResult("one", "one", "BLOCKED_DATA_PRECONDITION", "missing", 0,
                                     (CalculationIssue("BLOCKED_DATA_PRECONDITION", "MISSING_PAIR", "missing"),))
    failure = CalculationCheckResult("two", "two", "FAIL", "different", 1,
                                     (CalculationIssue("FAIL", "SCORE_DIFFERENT", "different"),))
    snapshots = (SimpleNamespace(), SimpleNamespace())
    called = []

    def check(self: Any, snapshot: Any) -> CalculationCheckResult:
        called.append(snapshot)
        return blocked if len(called) == 1 else failure

    monkeypatch.setattr(cases.Factor4CalculationService, "check_route_score_recalculation", check)
    with pytest.raises(AssertionError, match="SCORE_DIFFERENT"):
        cases.test_six_label_profile_reconciles_ts_cs_weight_renormalization(object(), snapshots)
    assert called == list(snapshots)
