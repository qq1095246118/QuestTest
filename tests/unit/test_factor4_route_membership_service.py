"""Offline counterexamples for result-level route membership reconciliation."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from service.factor4_result_service import ENVIRONMENT_LABELS
from service.factor4_calculation_service import _check_route_admission_fields
from service.factor4_route_membership_service import Factor4RouteMembershipService
from tests.unit.test_factor4_calculation_service import _batch, _metric, _route, _snapshot


pytestmark = pytest.mark.unit


def _pair(*, label="WIDE_RANGE", ts=True, cs=True, score="80"):
    return (
        _metric(11, "sub_factor:10", "time_series", label_code=label, valid=ts, score=score),
        _metric(12, "sub_factor:10", "cross_sectional", label_code=label, valid=cs, score=score),
    )


@pytest.mark.parametrize("label", ENVIRONMENT_LABELS)
def test_candidates_are_derived_without_routes_or_summary(label: str) -> None:
    """Every label discovers omitted candidates independently of route summaries."""
    snapshot = _snapshot(metrics=_pair(label=label), batch=_batch(environment_status={label: {"route_count": 0}}))
    result = Factor4RouteMembershipService.check_candidates(snapshot, label)
    assert result.status == "BLOCKED_DOC"
    assert result.evidence["candidate_count"] == 1
    assert result.evidence["unpublished_candidates"][0]["metric_ids"] == [11, 12]
    assert result.evidence["actual_eligible_route_count"] == 0


@pytest.mark.parametrize("ts,cs", [(True, True), (True, False), (False, True)])
def test_either_valid_scope_can_produce_candidate(ts: bool, cs: bool) -> None:
    """Accept both and each single valid dimension without invalid weight dilution."""
    route = _route(1, "sub_factor:10", 11 if ts else 12, 1, "72")
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=_pair(ts=ts, cs=cs), routes=(route,)), "WIDE_RANGE")
    assert result.status == "PASS"
    assert result.evidence["candidates"][0]["routing_score"] == "72.000000"
    assert result.evidence["full_publication_policy_verified"] is False


def test_empty_route_is_legitimate_when_both_dimensions_are_invalid() -> None:
    """A known empty eligible set is a legitimate negative result."""
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=_pair(ts=False, cs=False)), "WIDE_RANGE")
    assert result.status == "PASS"
    assert result.evidence["candidate_count"] == 0
    assert result.evidence["excluded_pair_count"] == 1


@pytest.mark.parametrize("score,eligible", [("59.999998", False), ("59.9999996", True), ("60", True), ("60.000001", True)])
def test_candidate_threshold_uses_six_digit_final_score(score: str, eligible: bool) -> None:
    """Check below, equal and above the frozen minimum using final precision."""
    metrics = tuple(replace(m, confidence=Decimal(1)) for m in _pair(score=score))
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=metrics), "WIDE_RANGE")
    assert result.evidence["candidate_count"] == int(eligible)
    assert result.status == ("BLOCKED_DOC" if eligible else "PASS")


def test_existing_route_and_candidate_oracles_agree_after_score_rounding() -> None:
    """The v1 batch threshold follows the six-place score emitted by the scorer."""
    route = _route(1, "sub_factor:10", 11, 1, "60")
    issues = []
    _check_route_admission_fields(route, valid_scopes=("time_series",),
                                 independently_calculated_score=Decimal("59.9999996"),
                                 minimum_route_score=Decimal(60), issues=issues)
    assert not issues


def test_exact_rounding_midpoint_cannot_invent_candidate_admission() -> None:
    """A midpoint affecting membership needs a specified tie-rounding convention."""
    metrics = tuple(replace(m, confidence=Decimal(1)) for m in _pair(score="59.9999995"))
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=metrics), "WIDE_RANGE")
    assert result.status == "BLOCKED_DOC"
    assert result.evidence["candidate_count"] == 0
    issues = []
    _check_route_admission_fields(_route(1, "sub_factor:10", 11, 1, "60"), valid_scopes=("time_series",),
                                 independently_calculated_score=Decimal("59.9999995"),
                                 minimum_route_score=Decimal(60), issues=issues)
    assert issues[0].status == "BLOCKED_DOC"


def test_route_outside_candidates_is_failure_even_with_unpublished_candidates() -> None:
    """An established extra-route defect must survive missing publication rules."""
    extra = tuple(replace(m, id=m.id+20, factor_ref="sub_factor:20", factor_id=20,
                          metric_pair_identity_hash="pair-20") for m in _pair())
    result = Factor4RouteMembershipService.check_candidates(
        _snapshot(metrics=_pair(ts=False, cs=False)+extra, routes=(_route(1, "sub_factor:10", 11, 1, "99"),)), "WIDE_RANGE")
    assert result.status == "FAIL"
    assert any(f.code == "ROUTE_PUBLISHED_OUTSIDE_FINAL_CANDIDATES" for f in result.findings)
    assert any(f.code == "ROUTE_PUBLICATION_SELECTION_RULE_REQUIRED" for f in result.findings)


def test_route_referencing_other_pair_does_not_hide_missing_candidate() -> None:
    """Matching factor identity alone cannot replace the exact metric pair."""
    route = replace(_route(1, "sub_factor:10", 999, 1, "72"), factor_version="old-version")
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=_pair(), routes=(route,)), "WIDE_RANGE")
    assert result.status == "FAIL"
    assert len(result.evidence["unpublished_candidates"]) == 1


@pytest.mark.parametrize("changes", [
    {"factor_ref": "sub_factor:999", "factor_id": 999}, {"factor_version": "old-version"},
    {"publication_uid": "old-publication"}, {"eval_batch_id": 999},
    {"market_scope": "other"}, {"route_profile_key": "other"},
])
def test_wrong_route_identity_cannot_remove_an_unpublished_candidate(changes: dict[str, Any]) -> None:
    """Even a correct metric ID cannot hide a route's wrong public identity."""
    route = replace(_route(1, "sub_factor:10", 11, 1, "72"), **changes)
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=_pair(), routes=(route,)), "WIDE_RANGE")
    assert result.status == "FAIL"
    assert len(result.evidence["unpublished_candidates"]) == 1


def test_missing_scope_does_not_become_single_valid_scope_pass() -> None:
    """A missing TS/CS row is not an explicitly invalid dimension."""
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=_pair()[:1]), "WIDE_RANGE")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert result.checked_count == 0


def test_missing_config_does_not_fall_back_to_route_evidence() -> None:
    """Do not use the route being tested as its own selection rule."""
    snapshot = _snapshot(batch=_batch(evaluation_config={}), metrics=_pair(),
                         routes=(_route(1, "sub_factor:10", 11, 1, "72", evidence={"profile_weights": {"time_series": .5, "cross_sectional": .5}}),))
    result = Factor4RouteMembershipService.check_candidates(snapshot, "WIDE_RANGE")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert result.evidence["candidate_count"] == 0


@pytest.mark.parametrize("field,value", [("confidence", None), ("time_series_score", None), ("confidence", Decimal("NaN"))])
def test_missing_or_nonfinite_components_do_not_create_candidates(field: str, value: Any) -> None:
    """Unknown numeric inputs cannot silently produce a route candidate."""
    ts, cs = _pair()
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=(replace(ts, **{field: value}), cs)), "WIDE_RANGE")
    assert result.status != "PASS"
    assert result.evidence["candidate_count"] == 0


def test_duplicate_pair_does_not_choose_first_metric() -> None:
    """Ambiguous pairs block instead of silently selecting a row."""
    ts, cs = _pair()
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=(ts, cs, replace(ts, id=13))), "WIDE_RANGE")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert result.checked_count == 0


def test_unsupported_score_version_is_not_evaluated_with_v1_rules() -> None:
    """Do not apply env-score-v1 math to another declared rule version."""
    result = Factor4RouteMembershipService.check_candidates(_snapshot(batch=_batch(score_rule_version="env-score-v2"), metrics=_pair()), "WIDE_RANGE")
    assert result.status == "BLOCKED_DOC"


def test_empty_metric_set_is_not_an_empty_result_pass() -> None:
    """Absence of evidence cannot certify the empty output branch."""
    result = Factor4RouteMembershipService.check_candidates(_snapshot(), "WIDE_RANGE")
    assert result.status == "BLOCKED_DATA_PRECONDITION"


def test_nonterminal_metrics_are_not_certified_as_empty_eligible_set() -> None:
    """A pending unit cannot be treated as a final invalid result."""
    ts, cs = _pair(ts=False, cs=False)
    result = Factor4RouteMembershipService.check_candidates(
        _snapshot(metrics=(replace(ts, metric_status="running"), cs)), "WIDE_RANGE")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert result.checked_count == 0


def test_unrelated_environment_is_not_a_candidate_source() -> None:
    """Metrics from a different label must not supply the target candidate set."""
    result = Factor4RouteMembershipService.check_candidates(_snapshot(metrics=_pair(label="CHOPPY_UP")), "WIDE_RANGE")
    assert result.evidence["candidate_count"] == 0
    assert result.status == "BLOCKED_DATA_PRECONDITION"


def test_unknown_environment_is_rejected() -> None:
    """Reject unknown labels before performing any candidate checks."""
    with pytest.raises(ValueError, match="unknown environment"):
        Factor4RouteMembershipService.check_candidates(_snapshot(), "unknown")
