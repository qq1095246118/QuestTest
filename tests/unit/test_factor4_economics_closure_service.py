"""Independent expected constants and mutation counterexamples for final E/P/OOS."""

from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from decimal import Decimal

import pytest

from service.factor4_economics_closure_service import Factor4EconomicsClosureService
from db.factor4_calculation_repository import CalculationAuditSnapshot
from db.factor4_lifecycle_repository import LifecycleSnapshot
from tests.unit.test_factor4_calculation_service import _metric, _snapshot


pytestmark = pytest.mark.unit


def _sample() -> CalculationAuditSnapshot:
    metric = replace(_metric(11, "sub_factor:10", "time_series"),
                     sharpe=Decimal(1), net_return=Decimal("0.05"),
                     max_drawdown=Decimal("-0.25"), turnover_rate=Decimal("0.60"),
                     oos_retention=Decimal("0.75"),
                     score_components={"economic": Decimal(50), "penalty": Decimal("4.5"), "oos": Decimal(75)},
                     metric_payload={})
    return replace(_snapshot(), evaluation_metrics=(metric,))


@pytest.mark.parametrize("component", ["economics", "penalty", "oos"])
def test_known_final_components_have_independent_exact_expectations(component: str) -> None:
    """Each genuine component is compared to an independent hand-computed constant."""
    result = Factor4EconomicsClosureService().check_component(_sample(), component)
    assert result.checked_count == 1
    assert result.issues == result.blocked == ()


@pytest.mark.parametrize("component,key", [("economics", "economic"), ("penalty", "penalty"), ("oos", "oos")])
def test_changed_persisted_component_fails(component: str, key: str) -> None:
    """Mutating a persisted output must cause arithmetic failure."""
    snapshot = _sample()
    metric = snapshot.evaluation_metrics[0]
    metric = replace(metric, score_components={**metric.score_components, key: Decimal(99)})
    result = Factor4EconomicsClosureService().check_component(replace(snapshot, evaluation_metrics=(metric,)), component)
    assert any("recalculation_mismatch" in issue for issue in result.issues)


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "wrong"])
def test_exposed_null_or_malformed_component_is_failure(value: object) -> None:
    """An invalid published component cannot hide behind an unrelated missing input."""
    snapshot = _sample()
    metric = replace(snapshot.evaluation_metrics[0], score_components={"economic": value}, sharpe=None)
    result = Factor4EconomicsClosureService().check_component(replace(snapshot, evaluation_metrics=(metric,)), "economics")
    assert result.issues and result.blocked


def test_missing_component_and_inputs_are_blocked_without_fabricated_zero() -> None:
    """Unknown evidence is distinct from a genuine numeric zero."""
    snapshot = _sample()
    metric = replace(snapshot.evaluation_metrics[0], score_components={}, sharpe=None)
    result = Factor4EconomicsClosureService().check_component(replace(snapshot, evaluation_metrics=(metric,)), "economics")
    assert not result.issues
    assert result.checked_count == 0
    assert any("component_not_exposed" in reason for reason in result.blocked)
    assert any("missing_sharpe" in reason for reason in result.blocked)


def test_known_failure_is_retained_when_later_metric_has_no_evidence() -> None:
    """One metric's missing inputs cannot discard another metric's arithmetic failure."""
    snapshot = _sample()
    wrong = replace(snapshot.evaluation_metrics[0], score_components={"economic": Decimal(1)})
    absent = replace(wrong, id=12, score_components={}, sharpe=None)
    result = Factor4EconomicsClosureService().check_component(replace(snapshot, evaluation_metrics=(wrong, absent)), "economics")
    assert result.issues and result.blocked
    assert result.checked_count == 1


def test_conflicting_aliases_are_not_silently_overridden() -> None:
    """Two published aliases must agree instead of last-value-wins merging."""
    snapshot = _sample()
    metric = replace(snapshot.evaluation_metrics[0], score_components={"economic": Decimal(50), "economics": Decimal(60)})
    result = Factor4EconomicsClosureService().check_component(replace(snapshot, evaluation_metrics=(metric,)), "economics")
    assert any("component_projections_disagree" in issue for issue in result.issues)


@pytest.mark.parametrize("field,value,component", [("net_return", Decimal(0), "economics"), ("sharpe", Decimal(2), "economics"), ("turnover_rate", Decimal("0.8"), "penalty"), ("oos_retention", Decimal("0.5"), "oos")])
def test_changed_final_input_recomputes_and_fails_stale_component(field: str, value: Decimal, component: str) -> None:
    """Changes to an actual input change expected arithmetic, not only metadata."""
    snapshot = _sample()
    metric = replace(snapshot.evaluation_metrics[0], **{field: value})
    result = Factor4EconomicsClosureService().check_component(replace(snapshot, evaluation_metrics=(metric,)), component)
    assert any("recalculation_mismatch" in issue for issue in result.issues)


def _fold_snapshot() -> LifecycleSnapshot:
    """Use only fields observed in real OOS MCP projection and lifecycle DB query."""
    config = {"oos_fold_count": 2, "oos_fold_days": 1, "min_oos_valid_folds": 2}
    folds = [{"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z", "mean_ic": "-0.1", "directed_mean_ic": "0.1", "mean_rank_ic": "-0.2", "directed_mean_rank_ic": "0.2"},
             {"start": "2026-01-02T00:00:00Z", "end": "2026-01-03T00:00:00Z", "mean_ic": "-0.2", "directed_mean_ic": "0.2", "mean_rank_ic": "-0.3", "directed_mean_rank_ic": "0.3"}]
    metric = {"id": 1, "eval_batch_id": 2, "evaluation_type": "time_series", "metric_status": "success", "metric_payload": {"oos": {"folds": folds, "requested_fold_count": 2, "required_fold_count": 2, "valid_fold_count": 2}, "direction": {"predictive_direction": -1}}}
    return LifecycleSnapshot(({"id": 2, "status": "success", "evaluation_config": config},), (metric,), (), {})


def test_real_fold_projection_matches_frozen_cardinality_duration_and_signed_values() -> None:
    """All observed fold identities, durations and directed values are consistent."""
    result = Factor4EconomicsClosureService().check_oos_fold_configuration(_fold_snapshot(), "time_series")
    assert result.checked_count == 1 and result.issues == result.blocked == ()


@pytest.mark.parametrize("mutation,issue", [("duplicate", "duplicate_fold"), ("shorter", "duration_differs"), ("wrong_count", "fold_count_differs"), ("requested", "requested_fold_count_differs"), ("required", "required_fold_count_differs"), ("valid", "invalid_valid_fold_count"), ("direction", "direction_mismatch"), ("null", "invalid_directed"), ("bad", "invalid_directed")])
def test_real_fold_mutations_are_detected_even_with_missing_other_evidence(mutation: str, issue: str) -> None:
    """Each corruption independently fails despite simultaneous absent configuration."""
    snapshot = deepcopy(_fold_snapshot())
    oos = snapshot.metrics[0]["metric_payload"]["oos"]
    if mutation == "duplicate":
        oos["folds"][1] = deepcopy(oos["folds"][0])
    elif mutation == "shorter":
        oos["folds"][1]["end"] = "2026-01-02T12:00:00Z"
    elif mutation == "wrong_count":
        oos["folds"].pop()
    elif mutation in {"requested", "required", "valid"}:
        oos[mutation + "_fold_count"] = 9
    else:
        oos["folds"][0]["directed_mean_ic"] = {"direction": "-0.1", "null": None, "bad": "NaN"}[mutation]
    del snapshot.batches[0]["evaluation_config"]["min_oos_valid_folds"]
    result = Factor4EconomicsClosureService().check_oos_fold_configuration(snapshot, "time_series")
    # Required-count mutation needs its configuration to remain comparable.
    if mutation == "required":
        snapshot.batches[0]["evaluation_config"]["min_oos_valid_folds"] = 2
        del oos["valid_fold_count"]
        result = Factor4EconomicsClosureService().check_oos_fold_configuration(snapshot, "time_series")
    assert any(issue in value for value in result.issues), result
    assert result.blocked


def test_fold_scope_isolation_and_missing_evidence_do_not_claim_pass() -> None:
    """A TS-only fixture cannot serve as CS evidence or pass with absent OOS data."""
    snapshot = _fold_snapshot()
    result = Factor4EconomicsClosureService().check_oos_fold_configuration(snapshot, "cross_sectional")
    assert result.checked_count == 0 and result.blocked
    del snapshot.metrics[0]["metric_payload"]["oos"]
    result = Factor4EconomicsClosureService().check_oos_fold_configuration(snapshot, "time_series")
    assert result.checked_count == 0 and result.blocked and not result.issues


@pytest.mark.parametrize("start,end", [("2026-01-01T00:00:00", "2026-01-02T00:00:00"), ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00")])
def test_undefined_fold_timezone_is_document_blocked_not_assumed(start: str, end: str) -> None:
    """Naive/mixed timestamps must not acquire an invented UTC or Shanghai timezone."""
    snapshot = _fold_snapshot()
    fold = snapshot.metrics[0]["metric_payload"]["oos"]["folds"][0]
    fold.update(start=start, end=end)
    result = Factor4EconomicsClosureService().check_oos_fold_configuration(snapshot, "time_series")
    assert not result.issues
    assert any(reason.startswith("BLOCKED_DOC:") for reason in result.blocked)


def test_equal_fold_instants_with_different_explicit_offsets_are_equivalent() -> None:
    """Timezone offsets normalize by instant without treating local wall time as UTC."""
    snapshot = _fold_snapshot()
    snapshot.metrics[0]["metric_payload"]["oos"]["folds"][0].update(start="2026-01-01T08:00:00+08:00", end="2026-01-02T08:00:00+08:00")
    result = Factor4EconomicsClosureService().check_oos_fold_configuration(snapshot, "time_series")
    assert result.issues == result.blocked == ()


def test_invalid_fold_date_does_not_hide_independent_direction_failure() -> None:
    """Invalid instant format is a failure, and another numeric violation is retained."""
    snapshot = _fold_snapshot()
    snapshot.metrics[0]["metric_payload"]["oos"]["folds"][0].update(start="2026-01-01", directed_mean_ic="-0.1")
    result = Factor4EconomicsClosureService().check_oos_fold_configuration(snapshot, "time_series")
    assert any("invalid_fold_time" in issue for issue in result.issues)
    assert any("direction_mismatch" in issue for issue in result.issues)
