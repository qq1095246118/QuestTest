"""Adversarial offline checks for final lifecycle business assertions."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from db.factor4_lifecycle_repository import LifecycleSnapshot
from service.factor4_lifecycle_closure_service import (
    Factor4LifecycleClosureService as Service, _METRIC_KEY, _METRIC_VALUES, _ROUTE_KEY, _ROUTE_VALUES,
)
from service.factor4_read_service import ReadPrecondition

pytestmark = pytest.mark.unit


def _recalculations() -> LifecycleSnapshot:
    batches, metrics, routes = [], [], []
    for n in (1, 2):
        batch = {"id": n, "batch_uid": f"batch-{n}", "status": "success", "finished_at": f"2026-09-0{n}",
                 "market_scope": "all", "route_profile_key": "default", "label_kind": "fact",
                 "start_date": "2025-09-01", "end_date": "2026-09-01", "as_of_time": "2026-09-02T00:00:00Z",
                 "factor_set_snapshot": {"members": [{"factor_ref": "sub_factor:1", "factor_version": "definition-v1"}]},
                 "factor_set_snapshot_hash": "members-1", "environment_snapshot": {"schema_version": "env-v1", "members": [1]},
                 "environment_snapshot_hash": "env-1", "evaluation_config": {"input_snapshot_hash": "raw-market-data-1"},
                 "evaluation_config_version": "cfg-1", "score_rule_version": "score-1", "code_version": "code-1",
                 "publish_status": "published", "is_active": n == 2, "active_scope_key": "all-default" if n == 2 else None,
                 "publication_uid": f"publication-{n}", "publish_version": f"publish-{n}"}
        metric = {field: "value" for field in _METRIC_KEY}
        metric.update({field: Decimal("1") for field in _METRIC_VALUES})
        metric.update(id=10+n, eval_batch_id=n, factor_ref="sub_factor:1", factor_id=1, factor_type="sub_factor",
                      factor_version="formula-1", metric_payload={"metric_identity": {"formula_hash": "hash-1",
                      "formula_version": "formula-1", "definition_factor_version": "definition-v1", "factor_version": "formula-1",
                      "eval_batch_uid": batch["batch_uid"], "run_id": f"execution-{n}", "artifact_manifest_hash": "actual-input-v1"}, "direction": {"predictive_direction": 1}})
        route = {field: metric.get(field, "value") for field in _ROUTE_KEY}
        route.update({field: Decimal("1") for field in _ROUTE_VALUES})
        route.update(id=20+n, eval_batch_id=n, metric_id=metric["id"], rank_no=1,
                     publication_uid=batch["publication_uid"], publish_version=batch["publish_version"],
                     market_scope="all", route_profile_key="default", is_active=n == 2, is_eligible=True)
        batches.append(batch); metrics.append(metric); routes.append(route)
    return LifecycleSnapshot(tuple(batches), tuple(metrics), tuple(routes), {})


def test_independent_batches_compare_explicit_outputs_not_execution_identifiers() -> None:
    check = Service.check_independent_recalculations(_recalculations())
    assert check.checked_count == 1
    assert not check.issues


@pytest.mark.parametrize("kind,field,value", [
    ("metrics", "mean_ic", Decimal("0.2")), ("metrics", "net_return", Decimal("-0.1")),
    ("metrics", "confidence", Decimal("0.3")), ("metrics", "factor_version", "stale"),
    ("routes", "rank_no", 2), ("routes", "routing_score", Decimal("0.4")), ("routes", "metric_id", 999),
])
def test_independent_calculations_catch_output_membership_and_fk_mutations(kind: str, field: str, value: Any) -> None:
    snapshot = _recalculations()
    getattr(snapshot, kind)[1][field] = value
    assert Service.check_independent_recalculations(snapshot).issues


def test_missing_formula_identity_is_not_equal_none_success() -> None:
    snapshot = _recalculations()
    for metric in snapshot.metrics:
        metric["metric_payload"]["metric_identity"].pop("formula_hash")
    with pytest.raises(ReadPrecondition):
        Service.check_independent_recalculations(snapshot)


def test_one_missing_formula_hash_is_insufficient_evidence_not_a_confirmed_hash_mismatch() -> None:
    snapshot = _recalculations()
    snapshot.metrics[1]["metric_payload"]["metric_identity"].pop("formula_hash")
    with pytest.raises(ReadPrecondition):
        Service.check_independent_recalculations(snapshot)


def test_formula_hash_mismatch_wins_over_missing_formula_version() -> None:
    snapshot = _recalculations()
    snapshot.metrics[1]["metric_payload"]["metric_identity"]["formula_hash"] = "different"
    snapshot.metrics[0]["metric_payload"]["metric_identity"].pop("formula_version")
    assert Service.check_independent_recalculations(snapshot).issues


@pytest.mark.parametrize("field", ["code_version", "as_of_time", "evaluation_config_version", "environment_snapshot_hash"])
def test_different_frozen_inputs_are_not_recalculation_comparisons(field: str) -> None:
    snapshot = _recalculations()
    snapshot.batches[1][field] = "different"
    with pytest.raises(ReadPrecondition):
        Service.check_independent_recalculations(snapshot)


def test_environment_label_hash_alone_does_not_prove_same_raw_market_data() -> None:
    snapshot = _recalculations()
    for batch in snapshot.batches:
        batch["evaluation_config"] = {"weights": [1, 1]}
    with pytest.raises(ReadPrecondition, match="raw input identity"):
        Service.check_independent_recalculations(snapshot)


@pytest.mark.parametrize("value", [None, "", " ", "other-input", 123])
def test_config_hash_cannot_replace_a_missing_or_different_actual_manifest(value: Any) -> None:
    snapshot = _recalculations()
    snapshot.metrics[1]["metric_payload"]["metric_identity"]["artifact_manifest_hash"] = value
    snapshot.metrics[1]["mean_ic"] = Decimal("0.8")
    with pytest.raises(ReadPrecondition, match="artifact_manifest_hash"):
        Service.check_independent_recalculations(snapshot)


def test_all_metric_inputs_are_preflighted_before_any_output_comparison() -> None:
    snapshot = _recalculations()
    snapshot.metrics[1]["mean_ic"] = Decimal("0.8")
    extra = []
    for metric in snapshot.metrics:
        other = deepcopy(metric)
        other.update(id=metric["id"]+100, factor_ref="sub_factor:2", factor_id=2)
        if metric["eval_batch_id"] == 2:
            other["metric_payload"]["metric_identity"].pop("artifact_manifest_hash")
        extra.append(other)
    snapshot = replace(snapshot, metrics=(*snapshot.metrics, *extra))
    with pytest.raises(ReadPrecondition):
        Service.check_independent_recalculations(snapshot)


@pytest.mark.parametrize("second_schema", [None, "raw-v2"])
def test_environment_schema_cannot_replace_missing_or_changed_raw_schema_binding(second_schema: Any) -> None:
    snapshot = _recalculations()
    snapshot.metrics[0]["metric_payload"]["metric_identity"]["raw_schema_version"] = "raw-v1"
    snapshot.metrics[1]["metric_payload"]["metric_identity"]["raw_schema_version"] = second_schema
    snapshot.routes[1]["rank_no"] = 2
    with pytest.raises(ReadPrecondition):
        Service.check_independent_recalculations(snapshot)


def test_nonadjacent_matching_actual_input_pairs_are_still_checked() -> None:
    snapshot = _recalculations()
    third_batch, third_metric, third_route = (deepcopy(rows[1]) for rows in (snapshot.batches, snapshot.metrics, snapshot.routes))
    third_batch.update(id=3, batch_uid="batch-3")
    third_metric.update(id=13, eval_batch_id=3)
    third_metric["metric_payload"]["metric_identity"]["eval_batch_uid"] = "batch-3"
    third_route.update(id=23, eval_batch_id=3, metric_id=13, routing_score=Decimal("2"))
    snapshot.metrics[1]["metric_payload"]["metric_identity"]["artifact_manifest_hash"] = "other-input"
    snapshot = replace(snapshot, batches=(*snapshot.batches, third_batch), metrics=(*snapshot.metrics, third_metric), routes=(*snapshot.routes, third_route))
    check = Service.check_independent_recalculations(snapshot)
    assert check.checked_count == 1
    assert "recalculation:batches=1,3:routes:field=routing_score" in check.issues


def test_duplicate_batch_or_repeated_same_run_cannot_pass_independent_computation() -> None:
    snapshot = _recalculations()
    one = replace(snapshot, batches=(snapshot.batches[0], deepcopy(snapshot.batches[0])))
    with pytest.raises(ReadPrecondition):
        Service.check_independent_recalculations(one)


def test_known_recalculation_failure_survives_other_incomplete_rows() -> None:
    snapshot = _recalculations()
    snapshot.metrics[1]["mean_ic"] = Decimal("0.8")
    snapshot.routes[1].pop("confidence")
    assert Service.check_independent_recalculations(snapshot).issues


def _parent() -> LifecycleSnapshot:
    child = {"factor_ref": "sub_factor:1", "factor_version": "child-v1", "weight": "0.7", "direction": -1}
    parent = {"factor_ref": "factor:10", "factor_type": "factor", "factor_version": "parent-v1",
              "children": [child], "relation_version": "rel-v1"}
    batch = {"id": 1, "batch_uid": "batch-1", "status": "success", "factor_set_snapshot": {"members": [parent]}}
    metric = {"id": 1, "eval_batch_id": 1, "factor_ref": "factor:10", "metric_payload": {"metric_identity": {
        "children": [deepcopy(child)], "relation_version": "rel-v1", "eval_batch_uid": "batch-1", "definition_factor_version": "parent-v1"}}}
    return LifecycleSnapshot((batch,), (metric,), (), {})


def test_parent_children_version_weight_direction_are_compared_as_persisted() -> None:
    assert not Service.check_parent_relation_evidence(_parent()).issues


@pytest.mark.parametrize("field,value", [("factor_version", "old"), ("weight", "0.8"), ("direction", 1), ("factor_ref", "sub_factor:2")])
def test_parent_child_mutations_fail(field: str, value: Any) -> None:
    snapshot = _parent()
    snapshot.metrics[0]["metric_payload"]["metric_identity"]["children"][0][field] = value
    assert Service.check_parent_relation_evidence(snapshot).issues


@pytest.mark.parametrize("field", ["relation_version", "eval_batch_uid", "definition_factor_version"])
def test_parent_relation_evidence_cannot_mix_versions_or_batches(field: str) -> None:
    snapshot = _parent()
    snapshot.metrics[0]["metric_payload"]["metric_identity"][field] = "other"
    assert Service.check_parent_relation_evidence(snapshot).issues


def test_parent_summary_is_not_used_instead_of_missing_children_evidence() -> None:
    snapshot = _parent()
    snapshot.metrics[0]["metric_payload"] = {"mean_ic": 0.5, "aggregation": "weighted_mean"}
    with pytest.raises(ReadPrecondition):
        Service.check_parent_relation_evidence(snapshot)


def test_parent_failure_is_not_hidden_by_a_later_missing_evidence_metric() -> None:
    snapshot = _parent()
    snapshot.metrics[0]["metric_payload"]["metric_identity"]["relation_version"] = "old"
    snapshot = replace(snapshot, metrics=(*snapshot.metrics, {"id": 9, "eval_batch_id": 1, "factor_ref": "factor:10"}))
    assert Service.check_parent_relation_evidence(snapshot).issues


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "rolled_back"])
def test_unsuccessful_terminal_records_and_active_pointer_agree(outcome: str) -> None:
    snapshot = _recalculations()
    snapshot.batches[0]["publish_status"] = outcome
    assert not Service.check_unsuccessful_publication_final_state(snapshot, outcome).issues


def test_physical_route_without_profile_column_uses_owning_batch_profile() -> None:
    snapshot = _recalculations()
    snapshot.batches[0]["publish_status"] = "failed"
    for route in snapshot.routes:
        route.pop("route_profile_key")
    assert not Service.check_unsuccessful_publication_final_state(snapshot, "failed").issues
    assert not Service.check_independent_recalculations(snapshot).issues


@pytest.mark.parametrize("profile", [None, "other-profile"])
def test_explicit_route_profile_is_checked_instead_of_overwritten(profile: Any) -> None:
    snapshot = _recalculations()
    snapshot.batches[0]["publish_status"] = "failed"
    snapshot.routes[1]["route_profile_key"] = profile
    assert Service.check_unsuccessful_publication_final_state(snapshot, "failed").issues


@pytest.mark.parametrize("mutation", ["active_failed", "retained_pointer", "active_old_route", "duplicate_active", "publication_version"])
def test_terminal_pointer_mutations_fail_even_without_requested_outcome(mutation: str) -> None:
    snapshot = _recalculations()
    if mutation == "active_failed":
        snapshot.batches[1]["status"] = "failed"
    elif mutation == "retained_pointer":
        snapshot.batches[0]["active_scope_key"] = "stale"
    elif mutation == "active_old_route":
        snapshot.routes[0]["is_active"] = True
    elif mutation == "duplicate_active":
        snapshot.batches[0].update(is_active=True, active_scope_key="duplicate")
    else:
        snapshot.routes[1]["publish_version"] = "stale"
    assert Service.check_unsuccessful_publication_final_state(snapshot, "rolled_back").issues


def test_missing_terminal_outcome_is_not_claimed_as_atomicity_pass() -> None:
    with pytest.raises(ReadPrecondition, match="atomicity is not observed"):
        Service.check_unsuccessful_publication_final_state(_recalculations(), "cancelled")


def _ties() -> LifecycleSnapshot:
    snapshot = _recalculations()
    snapshot = replace(snapshot, batches=(snapshot.batches[1],), metrics=(snapshot.metrics[1],), routes=(snapshot.routes[1],))
    snapshot.batches[0]["evaluation_config"]["ranking_contract"] = {"tie_breaker": [{"field": "confidence", "direction": "desc"}]}
    snapshot.routes[0].update(confidence=Decimal("0.9"), factor_ref="sub_factor:9")
    second = {**snapshot.routes[0], "id": 99, "rank_no": 2, "confidence": Decimal("0.8"), "factor_ref": "sub_factor:1"}
    return replace(snapshot, routes=(*snapshot.routes, second))


def test_declared_confidence_tie_order_overrides_factor_ref_order() -> None:
    assert not Service.check_declared_tie_breaker(_ties()).issues


def test_wrong_declared_tie_order_fails() -> None:
    snapshot = _ties()
    snapshot.routes[1]["confidence"] = Decimal("0.95")
    assert Service.check_declared_tie_breaker(snapshot).issues


def test_absent_tie_contract_does_not_default_to_factor_id() -> None:
    snapshot = _ties()
    snapshot.batches[0]["evaluation_config"] = {}
    with pytest.raises(ReadPrecondition, match="BLOCKED_DOC"):
        Service.check_declared_tie_breaker(snapshot)


def test_insufficient_tie_contract_cannot_invent_additional_tie_keys() -> None:
    snapshot = _ties()
    snapshot.routes[1]["confidence"] = Decimal("0.9")
    with pytest.raises(ReadPrecondition, match="BLOCKED_DOC"):
        Service.check_declared_tie_breaker(snapshot)


def test_wrong_tie_order_survives_another_undocumented_batch() -> None:
    snapshot = _ties()
    snapshot.routes[1]["confidence"] = Decimal("0.95")
    snapshot = replace(snapshot, batches=(*snapshot.batches, {"id": 88}))
    assert Service.check_declared_tie_breaker(snapshot).issues


def test_untied_batches_do_not_need_an_unrelated_tie_contract() -> None:
    snapshot = _ties()
    snapshot = replace(snapshot, batches=(*snapshot.batches, {"id": 88}))
    assert not Service.check_declared_tie_breaker(snapshot).issues


def test_duplicate_rank_does_not_accidentally_pass_due_to_stable_input_order() -> None:
    snapshot = _ties()
    snapshot.routes[1]["rank_no"] = 1
    assert Service.check_declared_tie_breaker(snapshot).issues


def test_completely_missing_metric_identity_blocks_instead_of_inventing_version_conflict() -> None:
    snapshot = _recalculations()
    for metric in snapshot.metrics:
        metric["metric_payload"].pop("metric_identity")
    with pytest.raises(ReadPrecondition):
        Service.check_independent_recalculations(snapshot)


def test_missing_parent_batch_binding_is_evidence_block_not_wrong_batch() -> None:
    snapshot = _parent()
    snapshot.metrics[0]["metric_payload"]["metric_identity"].pop("eval_batch_uid")
    with pytest.raises(ReadPrecondition):
        Service.check_parent_relation_evidence(snapshot)
