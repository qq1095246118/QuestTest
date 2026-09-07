"""Counterexamples for persisted environment closure, never live acceptance."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from db.factor4_calculation_repository import CalculationAuditSnapshot, EnvironmentDailyRecord
from service.factor4_environment_closure_service import ADMISSION_BRANCHES, Factor4EnvironmentClosureService
from service.factor4_result_service import ENVIRONMENT_LABELS
from tests.cases.factor4.test_environment_closure_business import _verify
from tests.unit.test_factor4_calculation_service import _batch, _eligibility, _metric, _route, _snapshot

pytestmark = pytest.mark.unit
_SERVICE = Factor4EnvironmentClosureService()


def _calendar() -> CalculationAuditSnapshot:
    row = EnvironmentDailyRecord(1, date(2026, 9, 1), "fact", "WIDE_RANGE", 1, False,
                                 datetime(2026, 9, 1, 12), "v1")
    later = replace(row, id=2, revision=2, label_code="CHOPPY_UP", is_current=True,
                    available_at=datetime(2026, 9, 3))
    batch = _batch(start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
                   environment_snapshot={"as_of_time": "2026-09-02T01:17:00", "members": [{
                       "daily_id": 1, "environment_date": "2026-09-01", "label_code": "WIDE_RANGE",
                       "revision": 1, "schema_version": "v1", "available_at": "2026-09-01T12:00:00",
                   }], "missing_dates": ["2026-09-02"]})
    return replace(_snapshot(batch=batch), environment_daily=(row,), environment_daily_history=(row, later),
                   environment_daily_history_loaded=True)


def _admission(branch: str = "both", label: str = "WIDE_RANGE") -> CalculationAuditSnapshot:
    valid = {"time_series", "cross_sectional"} if branch == "both" else {"time_series"} if branch == "ts_only" else {"cross_sectional"} if branch == "cs_only" else set()
    eligibility = _eligibility(sorted(valid), routing_score="72" if valid else None)
    metrics = tuple(_metric(i + 1, "sub_factor:10", scope, valid=scope in valid, label_code=label,
                            score="80", confidence="0.9", eligibility=eligibility)
                    for i, scope in enumerate(("time_series", "cross_sectional")))
    evidence = {
        "admission_mode": "any_valid_scope", "valid_scopes": sorted(valid),
        "invalid_scopes": sorted({"time_series", "cross_sectional"} - valid),
        "metric_ids": {m.evaluation_type: m.id for m in metrics if m.is_valid},
        "configured_profile_weights": {"time_series": "0.5", "cross_sectional": "0.5"},
        "effective_profile_weights": {s: ("0.5" if branch == "both" else "1") if s in valid else "0"
                                      for s in ("time_series", "cross_sectional")},
    }
    routes = ()
    if valid:
        route = _route(1, "sub_factor:10", next(m.id for m in metrics if m.is_valid), 1, "72", label_code=label, evidence=evidence)
        routes = (replace(route, time_series_score=Decimal("80") if "time_series" in valid else None,
                          cross_sectional_score=Decimal("80") if "cross_sectional" in valid else None,
                          confidence=Decimal("0.9")),)
    return _snapshot(batch=_batch(environment_status={label: {"status": "success", "route_count": len(routes), "metric_count": 2}}),
                     metrics=metrics, routes=routes)


def _codes(result) -> set[str]:
    return {item.code for item in result.findings}


def _full_calendar() -> CalculationAuditSnapshot:
    snapshot = _calendar()
    return replace(snapshot, environment_daily_history=tuple(replace(row, label_status="ready") for row in snapshot.environment_daily_history),
                   environment_daily_history_range=(snapshot.batch.start_date, snapshot.batch.end_date, snapshot.batch.label_kind))


def test_missing_dates_are_verified_against_full_history_not_their_own_declaration() -> None:
    result = _SERVICE.check_frozen_missing_dates(_full_calendar())
    assert result.status == "PASS", result.findings
    assert result.evidence["verified_ready_date_count"] == 1
    assert result.evidence["verified_unavailable_date_count"] == 1


@pytest.mark.parametrize("available", [datetime(2026, 9, 2), datetime(2026, 9, 2, 1, 17)])
def test_already_visible_ready_row_cannot_be_declared_missing(available: datetime) -> None:
    snapshot = _full_calendar()
    row = replace(snapshot.environment_daily_history[0], id=3, environment_date=date(2026, 9, 2), available_at=available)
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, environment_daily_history=(*snapshot.environment_daily_history, row)))
    assert result.status == "FAIL"
    assert "ENV_FROZEN_MISSING_DATE_HAS_VISIBLE_ENVIRONMENT" in _codes(result)


def test_daily_row_only_published_after_as_of_does_not_invalidate_historical_missing_date() -> None:
    snapshot = _full_calendar()
    row = replace(snapshot.environment_daily_history[0], id=3, environment_date=date(2026, 9, 2), available_at=datetime(2026, 9, 2, 1, 17, 1))
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, environment_daily_history=(*snapshot.environment_daily_history, row)))
    assert result.status == "PASS", result.findings


@pytest.mark.parametrize("visible,code", [(True, "ENV_FROZEN_VISIBLE_DATE_OMITTED"), (False, "ENV_FROZEN_UNAVAILABLE_DATE_NOT_DECLARED")])
def test_date_omitted_from_both_declarations_is_detected(visible: bool, code: str) -> None:
    snapshot = _full_calendar()
    frozen = {**snapshot.batch.environment_snapshot, "missing_dates": []}
    history = snapshot.environment_daily_history
    if visible:
        history = (*history, replace(history[0], id=3, environment_date=date(2026, 9, 2), available_at=datetime(2026, 9, 2)))
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen), environment_daily_history=history))
    assert result.status == "FAIL"
    assert code in _codes(result)


def test_empty_members_with_verified_empty_history_and_all_dates_missing_is_valid() -> None:
    snapshot = _full_calendar()
    frozen = {**snapshot.batch.environment_snapshot, "members": [], "missing_dates": ["2026-09-01", "2026-09-02"]}
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen), environment_daily=(), environment_daily_history=()))
    assert result.status == "PASS", result.findings
    assert result.checked_count == 2


@pytest.mark.parametrize("marker", [None, (date(2026, 9, 1), date(2026, 9, 1), "fact"),
                                    (date(2026, 9, 1), date(2026, 9, 2), "forecast")])
def test_member_only_or_wrong_range_history_cannot_prove_absence(marker: object) -> None:
    result = _SERVICE.check_frozen_missing_dates(replace(_full_calendar(), environment_daily_history_range=marker))
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "ENV_FULL_CALENDAR_HISTORY_NOT_LOADED" in _codes(result)


@pytest.mark.parametrize("change", [{"environment_date": date(2026, 9, 3)}, {"label_kind": "forecast"}])
def test_full_history_rows_cannot_leak_from_another_query_partition(change: dict[str, object]) -> None:
    snapshot = _full_calendar()
    row = replace(snapshot.environment_daily_history[0], id=3, **change)
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, environment_daily_history=(*snapshot.environment_daily_history, row)))
    assert result.status == "FAIL"
    assert "ENV_FULL_CALENDAR_HISTORY_PARTITION_MISMATCH" in _codes(result)


@pytest.mark.parametrize("status", [None, "not_ready", "invalid"])
def test_visible_nonready_row_does_not_invent_missing_date_selection_contract(status: str | None) -> None:
    snapshot = _full_calendar()
    row = replace(snapshot.environment_daily_history[0], id=3, environment_date=date(2026, 9, 2),
                  label_status=status, label_code=None, available_at=datetime(2026, 9, 2))
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, environment_daily_history=(*snapshot.environment_daily_history, row)))
    assert result.status == "BLOCKED_DOC"
    assert "ENV_FROZEN_MISSING_DATE_SELECTION_POLICY_UNDEFINED" in _codes(result)


def test_missing_date_pit_does_not_guess_naive_database_timezone() -> None:
    snapshot = _full_calendar()
    row = replace(snapshot.environment_daily_history[0], id=3, environment_date=date(2026, 9, 2),
                  available_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, environment_daily_history=(*snapshot.environment_daily_history, row)))
    assert result.status == "BLOCKED_DOC"
    assert "ENV_REVISION_HISTORY_TIMEZONE_UNDEFINED" in _codes(result)


def test_blocked_missing_date_cannot_hide_another_proven_omitted_visible_date() -> None:
    snapshot = _full_calendar()
    frozen = {**snapshot.batch.environment_snapshot, "members": []}
    row = replace(snapshot.environment_daily_history[0], id=3, environment_date=date(2026, 9, 2),
                  available_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen),
                                                        environment_daily_history=(*snapshot.environment_daily_history, row)))
    assert result.status == "FAIL"
    assert "ENV_FROZEN_VISIBLE_DATE_OMITTED" in _codes(result)
    assert "ENV_REVISION_HISTORY_TIMEZONE_UNDEFINED" in _codes(result)


def test_member_date_without_visible_history_is_rejected_independently() -> None:
    snapshot = _full_calendar()
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, environment_daily_history=()))
    assert result.status == "FAIL"
    assert "ENV_FROZEN_MEMBER_DATE_NOT_VISIBLE" in _codes(result)


@pytest.mark.parametrize("missing", [None, ["not-a-date"]])
def test_missing_date_declaration_problem_cannot_hide_duplicate_members(missing: object) -> None:
    snapshot = _full_calendar()
    member = snapshot.batch.environment_snapshot["members"][0]
    frozen = {**snapshot.batch.environment_snapshot, "members": [dict(member), dict(member)], "missing_dates": missing}
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen)))
    assert result.status == "FAIL"
    assert "ENV_FROZEN_MISSING_DATES_CONFLICT" in _codes(result)
    assert any(item.status == "BLOCKED_DATA_PRECONDITION" for item in result.findings)


@pytest.mark.parametrize("identifier", [True, False, None, 0, -1, "1", 1.0])
def test_missing_date_member_reference_requires_a_positive_nonboolean_integer(identifier: object) -> None:
    snapshot = _full_calendar()
    member = {**snapshot.batch.environment_snapshot["members"][0], "daily_id": identifier}
    frozen = {**snapshot.batch.environment_snapshot, "members": [member]}
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen)))
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "ENV_FROZEN_MEMBER_IDENTITY_MISSING" in _codes(result)


def test_unknown_missing_declaration_does_not_invent_an_omitted_unavailable_day() -> None:
    snapshot = _full_calendar()
    frozen = {**snapshot.batch.environment_snapshot, "missing_dates": None}
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen)))
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "ENV_FROZEN_UNAVAILABLE_DATE_NOT_DECLARED" not in _codes(result)


def test_unknown_member_declaration_does_not_invent_an_omitted_visible_day() -> None:
    snapshot = _full_calendar()
    frozen = {**snapshot.batch.environment_snapshot, "members": None}
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen)))
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "ENV_FROZEN_VISIBLE_DATE_OMITTED" not in _codes(result)


def test_unknown_lower_revision_cannot_hide_known_ready_missing_date_contradiction() -> None:
    snapshot = _full_calendar()
    ready = replace(snapshot.environment_daily_history[0], id=4, revision=2,
                    environment_date=date(2026, 9, 2), available_at=datetime(2026, 9, 2))
    unknown = replace(ready, id=3, revision=1, available_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, environment_daily_history=(*snapshot.environment_daily_history, unknown, ready)))
    assert result.status == "FAIL"
    assert "ENV_FROZEN_MISSING_DATE_HAS_VISIBLE_ENVIRONMENT" in _codes(result)
    assert "ENV_REVISION_HISTORY_TIMEZONE_UNDEFINED" in _codes(result)


@pytest.mark.parametrize("revision", [2, 3])
def test_unknown_equal_or_higher_revision_blocks_choice_of_latest_visible_record(revision: int) -> None:
    snapshot = _full_calendar()
    ready = replace(snapshot.environment_daily_history[0], id=4, revision=2,
                    environment_date=date(2026, 9, 2), available_at=datetime(2026, 9, 2))
    unknown = replace(ready, id=3, revision=revision, available_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    result = _SERVICE.check_frozen_missing_dates(replace(snapshot, environment_daily_history=(*snapshot.environment_daily_history, unknown, ready)))
    assert result.status == "BLOCKED_DOC"
    assert "ENV_FROZEN_MISSING_DATE_HAS_VISIBLE_ENVIRONMENT" not in _codes(result)
    assert "ENV_REVISION_HISTORY_TIMEZONE_UNDEFINED" in _codes(result)


def test_historical_noncurrent_revision_is_valid_before_next_publication() -> None:
    result = _SERVICE.check_frozen_calendar(_calendar())
    assert result.status == "PASS", result.findings


def test_two_different_days_can_have_different_environment_labels() -> None:
    snapshot = _calendar()
    row = replace(snapshot.environment_daily[0], id=3, environment_date=date(2026, 9, 2),
                  label_code="CHOPPY_UP", available_at=datetime(2026, 9, 2))
    frozen = dict(snapshot.batch.environment_snapshot)
    frozen["members"] = [*frozen["members"], {"daily_id": 3, "environment_date": "2026-09-02", "label_code": "CHOPPY_UP",
                                             "revision": 1, "schema_version": "v1", "available_at": "2026-09-02T00:00:00"}]
    frozen["missing_dates"] = []
    result = _SERVICE.check_frozen_calendar(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen),
                                                    environment_daily=(*snapshot.environment_daily, row),
                                                    environment_daily_history=(*snapshot.environment_daily_history, row)))
    assert result.status == "PASS", result.findings


@pytest.mark.parametrize("mutation,code", [
    ("duplicate", "ENV_FROZEN_MEMBERS_NOT_EXCLUSIVE"),
    ("missing", "ENV_FROZEN_CALENDAR_NOT_COMPLETE"),
    ("overlap", "ENV_FROZEN_MISSING_DATES_CONFLICT"),
    ("outside", "ENV_FROZEN_CALENDAR_NOT_COMPLETE"),
    ("label", "ENV_FROZEN_LABEL_INVALID"),
    ("kind", "ENV_FROZEN_KIND_MISMATCH"),
])
def test_calendar_counterexamples_fail(mutation: str, code: str) -> None:
    snapshot = _calendar()
    frozen = {**snapshot.batch.environment_snapshot, "members": [dict(snapshot.batch.environment_snapshot["members"][0])]}
    if mutation == "duplicate":
        frozen["members"].append(dict(frozen["members"][0]))
    elif mutation == "missing":
        frozen["missing_dates"] = []
    elif mutation == "overlap":
        frozen["missing_dates"] = ["2026-09-01", "2026-09-02"]
    elif mutation == "outside":
        frozen["missing_dates"] = ["2026-09-02", "2026-09-03"]
    elif mutation == "label":
        frozen["members"][0]["label_code"] = "sideways"
    else:
        frozen["members"][0]["label_kind"] = "forecast"
    result = _SERVICE.check_frozen_calendar(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen)))
    assert result.status == "FAIL"
    assert code in _codes(result)


def test_missing_history_is_blocked_not_invented() -> None:
    result = _SERVICE.check_frozen_calendar(replace(_calendar(), environment_daily_history_loaded=False))
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "ENV_REVISION_HISTORY_NOT_LOADED" in _codes(result)


def test_new_revision_available_exactly_at_cutoff_invalidates_old_selection() -> None:
    snapshot = _calendar()
    later = replace(snapshot.environment_daily_history[1], available_at=snapshot.batch.as_of_time)
    result = _SERVICE.check_frozen_calendar(replace(snapshot, environment_daily_history=(snapshot.environment_daily_history[0], later)))
    assert result.status == "FAIL"
    assert "ENV_FROZEN_REVISION_NOT_LATEST_VISIBLE" in _codes(result)


def test_duplicate_visible_revision_is_not_arbitrarily_selected() -> None:
    snapshot = _calendar()
    duplicate = replace(snapshot.environment_daily[0], id=99, label_code="CHOPPY_DOWN")
    result = _SERVICE.check_frozen_calendar(replace(snapshot, environment_daily_history=(*snapshot.environment_daily_history, duplicate)))
    assert result.status == "FAIL"
    assert "ENV_VISIBLE_REVISION_AMBIGUOUS" in _codes(result)


@pytest.mark.parametrize("label", ENVIRONMENT_LABELS)
@pytest.mark.parametrize("branch", ADMISSION_BRANCHES)
def test_each_branch_audits_final_persisted_values(label: str, branch: str) -> None:
    result = _SERVICE.check_admission_branch(_admission(branch, label), label, branch)
    assert result.status == "PASS", result.findings
    assert result.checked_count == 1


def test_any_valid_does_not_imply_route_must_exist() -> None:
    snapshot = _admission("ts_only")
    metrics = tuple(replace(m, route_eligibility={**m.route_eligibility, "is_eligible": False}) for m in snapshot.evaluation_metrics)
    result = _SERVICE.check_admission_branch(replace(snapshot, routes=(), evaluation_metrics=metrics), "WIDE_RANGE", "ts_only")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert not any(item.status == "FAIL" for item in result.findings)


def test_neither_valid_cannot_be_marked_eligible() -> None:
    snapshot = _admission("neither")
    metric = replace(snapshot.evaluation_metrics[0], route_eligibility={**snapshot.evaluation_metrics[0].route_eligibility, "is_eligible": True})
    result = _SERVICE.check_admission_branch(replace(snapshot, evaluation_metrics=(metric, snapshot.evaluation_metrics[1])), "WIDE_RANGE", "neither")
    assert result.status == "FAIL"
    assert "ENV_ADMISSION_NEITHER_MARKED_ELIGIBLE" in _codes(result)


@pytest.mark.parametrize("value", [None, "false", 0])
def test_neither_missing_or_nonboolean_eligibility_is_not_a_pass(value: object) -> None:
    snapshot = _admission("neither")
    metric = snapshot.evaluation_metrics[0]
    eligibility = {name: original for name, original in metric.route_eligibility.items() if name != "is_eligible"}
    if value is not None:
        eligibility["is_eligible"] = value
    changed = replace(metric, route_eligibility=eligibility)
    result = _SERVICE.check_admission_branch(replace(snapshot, evaluation_metrics=(changed, snapshot.evaluation_metrics[1])), "WIDE_RANGE", "neither")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "ENV_ADMISSION_ELIGIBILITY_RESULT_MISSING" in _codes(result)


def test_eligible_route_cannot_contradict_same_primary_metric_rejection() -> None:
    snapshot = _admission("ts_only")
    metric = snapshot.evaluation_metrics[0]
    changed = replace(metric, route_eligibility={**metric.route_eligibility, "is_eligible": False})
    result = _SERVICE.check_admission_branch(replace(snapshot, evaluation_metrics=(changed, snapshot.evaluation_metrics[1])), "WIDE_RANGE", "ts_only")
    assert result.status == "FAIL"
    assert "ENV_ADMISSION_ELIGIBLE_ROUTE_REJECTED_BY_PRIMARY_METRIC" in _codes(result)


def test_primary_metric_rejection_does_not_compare_unrelated_route_partition() -> None:
    snapshot = _admission("ts_only")
    metric = snapshot.evaluation_metrics[0]
    changed = replace(metric, route_eligibility={**metric.route_eligibility, "is_eligible": False})
    route = replace(snapshot.routes[0], factor_version="another-version")
    result = _SERVICE.check_admission_branch(replace(snapshot, evaluation_metrics=(changed, snapshot.evaluation_metrics[1]), routes=(route,)), "WIDE_RANGE", "ts_only")
    assert result.status == "FAIL"
    assert "RESULT_ROUTE_METRIC_IDENTITY_MISMATCH" in _codes(result)
    assert "ENV_ADMISSION_ELIGIBLE_ROUTE_REJECTED_BY_PRIMARY_METRIC" not in _codes(result)


def test_final_route_may_reject_pair_that_passed_metric_admission_due_to_other_publication_gates() -> None:
    snapshot = _admission("ts_only")
    route = replace(snapshot.routes[0], is_eligible=False, reject_reason_code="PUBLICATION_GATE")
    result = _SERVICE.check_admission_branch(replace(snapshot, routes=(route,)), "WIDE_RANGE", "ts_only")
    assert result.status == "PASS", result.findings


@pytest.mark.parametrize("field,value,code", [
    ("effective_profile_weights", {"time_series": "0.5", "cross_sectional": "0.5"}, "ENV_ADMISSION_WEIGHT_MISMATCH"),
    ("configured_profile_weights", {"time_series": "0.2", "cross_sectional": "0.8"}, "ENV_ADMISSION_WEIGHT_MISMATCH"),
    ("metric_ids", {"time_series": 99}, "ENV_ADMISSION_METRIC_IDS_MISMATCH"),
    ("valid_scopes", ["cross_sectional"], "ENV_ADMISSION_SCOPES_MISMATCH"),
])
def test_persisted_route_evidence_mismatch_fails(field: str, value: object, code: str) -> None:
    snapshot = _admission("ts_only")
    route = replace(snapshot.routes[0], evidence={**snapshot.routes[0].evidence, field: value})
    result = _SERVICE.check_admission_branch(replace(snapshot, routes=(route,)), "WIDE_RANGE", "ts_only")
    assert result.status == "FAIL"
    assert code in _codes(result)


@pytest.mark.parametrize("field,value", [("routing_score", Decimal("71")), ("confidence", Decimal("0.8")),
                                         ("time_series_score", Decimal("70"))])
def test_final_score_columns_are_independently_recalculated(field: str, value: Decimal) -> None:
    snapshot = _admission("ts_only")
    result = _SERVICE.check_admission_branch(replace(snapshot, routes=(replace(snapshot.routes[0], **{field: value}),)), "WIDE_RANGE", "ts_only")
    assert result.status == "FAIL"


@pytest.mark.parametrize("score,expected_status", [("64", "PASS"), ("66", "FAIL")])
def test_both_scope_combination_multiplies_weighted_score_by_weighted_confidence(score: str, expected_status: str) -> None:
    snapshot = _admission()
    config = {**snapshot.batch.evaluation_config, "profile_weights": {"time_series": "1", "cross_sectional": "2"}}
    ts = replace(snapshot.evaluation_metrics[0], time_series_score=Decimal("60"), confidence=Decimal("0.6"))
    cs = replace(snapshot.evaluation_metrics[1], cross_sectional_score=Decimal("90"), confidence=Decimal("0.9"))
    evidence = {**snapshot.routes[0].evidence, "configured_profile_weights": config["profile_weights"],
                "effective_profile_weights": {"time_series": "0.333333", "cross_sectional": "0.666667"}}
    route = replace(snapshot.routes[0], time_series_score=Decimal("60"), cross_sectional_score=Decimal("90"),
                    confidence=Decimal("0.8"), routing_score=Decimal(score), evidence=evidence)
    result = _SERVICE.check_admission_branch(replace(snapshot, batch=replace(snapshot.batch, evaluation_config=config),
                                                     evaluation_metrics=(ts, cs), routes=(route,)), "WIDE_RANGE", "both")
    assert result.status == expected_status, result.findings


def test_invalid_dimension_cannot_retain_old_route_score() -> None:
    snapshot = _admission("cs_only")
    route = replace(snapshot.routes[0], time_series_score=Decimal("80"))
    result = _SERVICE.check_admission_branch(replace(snapshot, routes=(route,)), "WIDE_RANGE", "cs_only")
    assert result.status == "FAIL"
    assert "ENV_ADMISSION_INVALID_SCOPE_SCORE_RETAINED" in _codes(result)


def test_missing_config_cannot_be_replaced_with_route_self_reported_weights() -> None:
    snapshot = _admission("ts_only")
    result = _SERVICE.check_admission_branch(replace(snapshot, batch=replace(snapshot.batch, evaluation_config={})), "WIDE_RANGE", "ts_only")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "ENV_ADMISSION_FROZEN_WEIGHTS_MISSING" in _codes(result)


@pytest.mark.parametrize("mutation,code", [("metric_ids", "ENV_ADMISSION_METRIC_IDS_MISMATCH"),
                                          ("invalid_score", "ENV_ADMISSION_INVALID_SCOPE_SCORE_RETAINED"),
                                          ("valid_score", "ENV_ADMISSION_SCOPE_SCORE_MISMATCH")])
def test_missing_weights_cannot_hide_independent_route_binding_or_score_failure(mutation: str, code: str) -> None:
    snapshot = _admission("ts_only")
    route = snapshot.routes[0]
    if mutation == "metric_ids":
        route = replace(route, evidence={**route.evidence, "metric_ids": {"time_series": 99}})
    elif mutation == "invalid_score":
        route = replace(route, cross_sectional_score=Decimal("40"))
    else:
        route = replace(route, time_series_score=Decimal("40"))
    result = _SERVICE.check_admission_branch(replace(snapshot, batch=replace(snapshot.batch, evaluation_config={}), routes=(route,)), "WIDE_RANGE", "ts_only")
    assert result.status == "FAIL"
    assert code in _codes(result)
    assert "ENV_ADMISSION_FROZEN_WEIGHTS_MISSING" in _codes(result)


def test_other_batch_metric_cannot_supply_missing_dimension() -> None:
    snapshot = _admission()
    metrics = (snapshot.evaluation_metrics[0], replace(snapshot.evaluation_metrics[1], eval_batch_id=99))
    result = _SERVICE.check_admission_branch(replace(snapshot, evaluation_metrics=metrics), "WIDE_RANGE", "both")
    assert result.status == "FAIL"
    assert "ENV_ADMISSION_METRIC_PARTITION_MISMATCH" in _codes(result)
    assert result.checked_count == 0


def test_route_cross_batch_link_fails_even_with_missing_weight_evidence() -> None:
    snapshot = _admission()
    route = replace(snapshot.routes[0], eval_batch_id=99, evidence={})
    result = _SERVICE.check_admission_branch(replace(snapshot, routes=(route,)), "WIDE_RANGE", "both")
    assert result.status == "FAIL"
    assert "RESULT_ROUTE_BATCH_MISMATCH" in _codes(result)
    assert "ENV_ADMISSION_WEIGHT_EVIDENCE_MISSING" in _codes(result)


def test_valid_scopes_still_require_minimum_score_for_eligible_route() -> None:
    snapshot = _admission()
    batch = replace(snapshot.batch, evaluation_config={**snapshot.batch.evaluation_config, "minimum_route_score": "73"})
    result = _SERVICE.check_admission_branch(replace(snapshot, batch=batch), "WIDE_RANGE", "both")
    assert result.status == "FAIL"
    assert "ENV_ADMISSION_ELIGIBLE_BELOW_MINIMUM_SCORE" in _codes(result)


def test_unknown_scoring_rule_does_not_reuse_v1_math() -> None:
    snapshot = _admission()
    batch = replace(snapshot.batch, score_rule_version="env-score-vNext")
    metrics = tuple(replace(metric, scoring_version=batch.score_rule_version) for metric in snapshot.evaluation_metrics)
    routes = (replace(snapshot.routes[0], score_rule_version=batch.score_rule_version),)
    result = _SERVICE.check_admission_branch(replace(snapshot, batch=batch, evaluation_metrics=metrics, routes=routes), "WIDE_RANGE", "both")
    assert result.status == "BLOCKED_DOC"
    assert "ENV_ADMISSION_SCORE_VERSION_UNSUPPORTED" in _codes(result)


def test_unknown_result_label_is_not_silently_excluded_from_all_six_checks() -> None:
    snapshot = _admission()
    metric = replace(snapshot.evaluation_metrics[1], label_code="other")
    result = _SERVICE.check_label_results(replace(snapshot, evaluation_metrics=(snapshot.evaluation_metrics[0], metric)), "WIDE_RANGE")
    assert result.status == "FAIL"
    assert "ENV_RESULT_LABEL_INVALID" in _codes(result)


@pytest.mark.parametrize("label", ENVIRONMENT_LABELS)
def test_six_label_summary_and_full_identity_reconciliation(label: str) -> None:
    result = _SERVICE.check_label_results(_admission(label=label), label)
    assert result.status == "PASS", result.findings


def test_bad_metric_count_is_not_hidden_by_missing_route_count() -> None:
    snapshot = _admission()
    batch = replace(snapshot.batch, environment_status={"WIDE_RANGE": {"metric_count": 3}})
    result = _SERVICE.check_label_results(replace(snapshot, batch=batch), "WIDE_RANGE")
    assert result.status == "FAIL"
    assert "ENV_LABEL_METRIC_COUNT_MISMATCH" in _codes(result)
    assert "ENV_LABEL_ROUTE_SUMMARY_MISSING" in _codes(result)


@pytest.mark.parametrize("declared,status", [(1, "PASS"), (0, "FAIL")])
def test_insufficient_metric_counter_uses_persisted_insufficient_sample_status(declared: int, status: str) -> None:
    snapshot = _admission("neither")
    metrics = (replace(snapshot.evaluation_metrics[0], metric_status="insufficient_sample"),
               replace(snapshot.evaluation_metrics[1], metric_status="success"))
    batch = replace(snapshot.batch, environment_status={"WIDE_RANGE": {
        "route_count": 0, "metric_count": 2, "completed_metric_count": 1,
        "insufficient_metric_count": declared, "failed_metric_count": 0,
    }})
    result = _SERVICE.check_label_results(replace(snapshot, batch=batch, evaluation_metrics=metrics), "WIDE_RANGE")
    assert result.status == status, result.findings
    if status == "FAIL":
        assert "ENV_LABEL_METRIC_COUNT_MISMATCH" in _codes(result)


def test_missing_metric_count_is_reported_as_uncovered_optional_counter() -> None:
    snapshot = _admission()
    batch = replace(snapshot.batch, environment_status={"WIDE_RANGE": {"route_count": 1}})
    result = _SERVICE.check_label_results(replace(snapshot, batch=batch), "WIDE_RANGE")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert result.evidence["checked_metric_count_fields"] == []
    assert "ENV_LABEL_METRIC_COUNTER_NOT_PERSISTED" in _codes(result)


@pytest.mark.parametrize("field", ["as_of_time", "available_at"])
def test_mixed_timestamp_styles_are_documentation_blocked(field: str) -> None:
    snapshot = _calendar()
    frozen = {**snapshot.batch.environment_snapshot, "members": [dict(snapshot.batch.environment_snapshot["members"][0])]}
    if field == "as_of_time":
        frozen[field] += "+08:00"
    else:
        frozen["members"][0][field] += "+08:00"
    result = _SERVICE.check_frozen_calendar(replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=frozen)))
    assert result.status == "BLOCKED_DOC"
    assert not any(item.status == "FAIL" for item in result.findings)


def test_mixed_history_timestamps_cannot_select_an_arbitrary_latest_revision() -> None:
    snapshot = _calendar()
    history = (*snapshot.environment_daily_history[:1], replace(snapshot.environment_daily_history[1], available_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    result = _SERVICE.check_frozen_calendar(replace(snapshot, environment_daily_history=history))
    assert result.status == "BLOCKED_DOC"
    assert "ENV_REVISION_HISTORY_TIMEZONE_UNDEFINED" in _codes(result)


def test_missing_full_identities_cannot_establish_duplicate_metric_identity() -> None:
    snapshot = _admission()
    first = replace(snapshot.evaluation_metrics[0], metric_pair_identity_hash=None, metric_identity=None)
    second = replace(first, id=99)
    batch = replace(snapshot.batch, environment_status={"WIDE_RANGE": {"route_count": 1, "metric_count": 2}})
    result = _SERVICE.check_label_results(replace(snapshot, batch=batch, evaluation_metrics=(first, second)), "WIDE_RANGE")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "ENV_LABEL_METRIC_FULL_IDENTITY_MISSING" in _codes(result)
    assert "ENV_LABEL_METRIC_IDENTITY_DUPLICATE" not in _codes(result)


def test_case_reports_failure_before_another_partition_skip() -> None:
    blocked = _SERVICE.check_admission_branch(_admission(), "WIDE_RANGE", "neither")
    snapshot = _admission()
    failed = _SERVICE.check_label_results(replace(snapshot, routes=(replace(snapshot.routes[0], market_scope="other"),)), "WIDE_RANGE")
    with pytest.raises(AssertionError):
        _verify([blocked, failed])
