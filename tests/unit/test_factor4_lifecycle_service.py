"""Adversarial final lifecycle oracles, without database or network access."""

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from db.factor4_lifecycle_repository import LifecycleSnapshot
from service.factor4_lifecycle_service import Factor4LifecycleService
from service.factor4_read_service import ReadPrecondition

pytestmark = pytest.mark.unit


def _snapshot() -> LifecycleSnapshot:
    member = {"factor_ref": "sub_factor:10", "factor_type": "sub_factor", "factor_id": 10, "factor_version": "definition-v1"}
    batch = {"id": 1, "batch_uid": "b1", "status": "success", "publish_status": "published", "is_active": 1,
             "market_scope": "all", "route_profile_key": "default", "publication_uid": "p1", "publish_version": "v1",
             "factor_set_snapshot": {"members": [member], "factor_count": 1}, "factor_set_snapshot_hash": "frozen",
             "evaluation_config_version": "cfg1", "published_at": "2026-09-01T00:00:00Z"}
    metric = {"id": 2, "eval_batch_id": 1, "factor_ref": member["factor_ref"], "factor_type": "sub_factor", "factor_id": 10,
              "factor_version": "executable-v1", "market_scope": "all", "label_kind": "fact", "label_code": "CHOPPY_UP",
              "metric_payload": {"metric_identity": {"factor_version": "executable-v1", "definition_factor_version": "definition-v1",
                                                      "evaluation_config_version": "cfg1", "eval_batch_uid": "b1"}}}
    route = {"id": 3, "eval_batch_id": 1, "metric_id": 2, "factor_ref": member["factor_ref"], "factor_version": "executable-v1",
             "market_scope": "all", "label_kind": "fact", "label_code": "CHOPPY_UP", "publication_uid": "p1",
             "publish_version": "v1", "is_active": 1}
    return LifecycleSnapshot((batch,), (metric,), (route,), {"audit": {"total": 1, "missing_request_id": 0}})


def test_valid_final_lifecycle_has_independent_definition_and_execution_versions() -> None:
    snapshot = _snapshot()
    service = Factor4LifecycleService()
    assert not service.check_frozen_membership(snapshot).issues
    assert not service.check_route_history(snapshot).issues
    assert not service.check_terminal_replay(snapshot, deepcopy(snapshot)).issues
    assert not service.check_audit_fields(snapshot, "audit").issues


@pytest.mark.parametrize("key,value", [("factor_ref", "sub_factor:11"), ("label_code", "WIDE_RANGE"),
                                      ("publication_uid", "p2"), ("metric_id", 99), ("publish_version", "v2")])
def test_route_identity_detects_mutation_before_history_precondition(key: str, value: Any) -> None:
    snapshot = _snapshot()
    snapshot.routes[0][key] = value
    assert Factor4LifecycleService().check_route_history(snapshot, require_superseded=True).issues


def test_route_history_requires_real_old_records_and_inactive_old_routes() -> None:
    service = Factor4LifecycleService()
    current = _snapshot()
    with pytest.raises(ReadPrecondition, match="no superseded"):
        service.check_route_history(current, require_superseded=True)
    old = deepcopy(current.batches[0])
    old.update(id=2, is_active=0, publication_uid="p2", publish_version="v2")
    old_metric = {**current.metrics[0], "id": 20, "eval_batch_id": 2}
    old_route = {**current.routes[0], "id": 30, "eval_batch_id": 2, "metric_id": 20,
                 "publication_uid": "p2", "publish_version": "v2", "is_active": 0}
    history = replace(current, batches=(*current.batches, old), metrics=(*current.metrics, old_metric), routes=(*current.routes, old_route))
    assert not service.check_route_history(history, require_superseded=True).issues
    old_route["is_active"] = 1
    assert service.check_route_history(history, require_superseded=True).issues


@pytest.mark.parametrize("key,value", [("definition_factor_version", "executable-v1"), ("factor_version", "definition-v1"),
                                      ("eval_batch_uid", "b2"), ("evaluation_config_version", None)])
def test_frozen_membership_catches_wrong_reference_versions(key: str, value: Any) -> None:
    snapshot = _snapshot()
    snapshot.metrics[0]["metric_payload"]["metric_identity"][key] = value
    assert Factor4LifecycleService().check_frozen_membership(snapshot).issues


def test_cancelled_parent_batch_cannot_pass_parent_calculation() -> None:
    snapshot = _snapshot()
    snapshot.batches[0]["status"] = "cancelled"
    with pytest.raises(ReadPrecondition, match="terminal parent"):
        Factor4LifecycleService().check_frozen_membership(snapshot, parents=True)


def test_replay_ignores_new_unrelated_batch_but_detects_existing_data_mutation() -> None:
    before = _snapshot()
    after = deepcopy(before)
    after = replace(after, batches=(*after.batches, {"id": 99, "status": "running"}))
    service = Factor4LifecycleService()
    assert not service.check_terminal_replay(before, after).issues
    after.batches[0]["factor_set_snapshot_hash"] = "changed"
    assert service.check_terminal_replay(before, after).issues


def test_replay_publication_switch_is_snapshot_drift_not_mutation_failure() -> None:
    before, after = _snapshot(), _snapshot()
    after.batches[0]["is_active"] = 0
    with pytest.raises(ReadPrecondition, match="SNAPSHOT_DRIFT"):
        Factor4LifecycleService().check_terminal_replay(before, after)


def test_audit_checks_every_recorded_field_and_empty_stage_is_not_pass() -> None:
    snapshot = _snapshot()
    snapshot.audits["audit"]["missing_created_at"] = 1
    check = Factor4LifecycleService().check_audit_fields(snapshot, "audit")
    assert "audit:audit:missing_created_at:count=1" in check.issues
    snapshot.audits["audit"]["total"] = 0
    with pytest.raises(ReadPrecondition):
        Factor4LifecycleService().check_audit_fields(snapshot, "audit")


def test_publication_contract_gate_evaluates_declared_semantics_instead_of_fixed_block() -> None:
    snapshot = _snapshot()
    config = {"publication_mode": "atomic", "allowed_publish_states": ["published"],
              "partial_environment_visibility": False, "partial_factor_visibility": False,
              "route_admission_condition": "any_valid", "publication_identity_stability": "immutable",
              "history_retention": True, "repeat_publish_semantics": "same_version", "rollback_semantics": "old_active"}
    snapshot.batches[0]["evaluation_config"] = config
    service = Factor4LifecycleService()
    assert not service.check_publication_mode_contract(snapshot).issues
    config["partial_factor_visibility"] = True
    assert service.check_publication_mode_contract(snapshot).issues
    snapshot.batches[0]["evaluation_config"] = {"publication_mode": "per_factor_incremental"}
    with pytest.raises(ReadPrecondition, match="BLOCKED_DOC"):
        service.check_publication_mode_contract(snapshot)


def test_metric_uniqueness_uses_complete_formal_dimensions() -> None:
    snapshot = _snapshot()
    service = Factor4LifecycleService()
    distinct = {**snapshot.metrics[0], "id": 99, "evaluation_type": "cross_sectional"}
    assert not service.check_metric_units(replace(snapshot, metrics=(*snapshot.metrics, distinct))).issues
    duplicate = {**snapshot.metrics[0], "id": 99}
    assert service.check_metric_units(replace(snapshot, metrics=(*snapshot.metrics, duplicate))).issues
    assert service.check_metric_units(replace(snapshot, batches=())).issues


@pytest.mark.parametrize("key,value", [("finished_at", None), ("published_at", None),
                                      ("failed_metric_count", 2), ("expected_metric_count", -1)])
def test_terminal_counters_and_times_detect_contradictions(key: str, value: Any) -> None:
    snapshot = _snapshot()
    snapshot.batches[0].update(finished_at="2026-09-01", expected_metric_count=3,
                             completed_metric_count=2, insufficient_metric_count=1, failed_metric_count=0)
    snapshot = replace(snapshot, metrics=tuple(
        {**snapshot.metrics[0], "id": index, "metric_status": status}
        for index, status in enumerate(("success", "success", "insufficient_sample"), 1)
    ))
    service = Factor4LifecycleService()
    assert not service.check_batch_terminal_counts(snapshot).issues
    snapshot.batches[0][key] = value
    assert service.check_batch_terminal_counts(snapshot).issues


@pytest.mark.parametrize("field,status", [
    ("completed_metric_count", "success"),
    ("insufficient_metric_count", "insufficient_sample"),
    ("failed_metric_count", "failed"),
])
@pytest.mark.parametrize("batch_status", ["success", "completed", "partial_fail", "failed", "cancelled", "canceled"])
def test_terminal_counters_match_actual_same_batch_statuses(field: str, status: str, batch_status: str) -> None:
    """Range-valid zero counters cannot pass when the database contains terminal units."""
    snapshot = _snapshot()
    snapshot.batches[0].update(status=batch_status, finished_at="2026-09-01", expected_metric_count=5,
                             completed_metric_count=0, insufficient_metric_count=0, failed_metric_count=0)
    snapshot = replace(snapshot, metrics=(
        {**snapshot.metrics[0], "metric_status": status},
        {**snapshot.metrics[0], "id": 99, "eval_batch_id": 99, "metric_status": status},
    ))
    service = Factor4LifecycleService()
    assert f"batch:id=1:{field}:declared=0:actual=1" in service.check_batch_terminal_counts(snapshot).issues
    snapshot.batches[0][field] = 1
    assert not service.check_batch_terminal_counts(snapshot).issues


def test_cancelled_batch_may_have_unfinished_units_but_terminal_counts_must_match() -> None:
    """Cancellation does not require expected == terminal; running progress is not judged as final."""
    snapshot = _snapshot()
    snapshot.batches[0].update(status="cancelled", finished_at="2026-09-01", expected_metric_count=5,
                             completed_metric_count=1, insufficient_metric_count=0, failed_metric_count=0)
    snapshot.metrics[0]["metric_status"] = "success"
    service = Factor4LifecycleService()
    assert not service.check_batch_terminal_counts(snapshot).issues
    snapshot.batches[0]["completed_metric_count"] = 0
    assert service.check_batch_terminal_counts(snapshot).issues
    snapshot.batches[0]["status"] = "running"
    assert not service.check_batch_terminal_counts(snapshot).issues


def test_schema_detects_missing_entity_and_nonunique_revision_index() -> None:
    inventory = tuple({"table_name": "market_environment_daily", "column_name": name,
                       "index_name": "revision", "non_unique": 0, "sequence_no": i}
                      for i, name in enumerate(("environment_date", "label_kind", "revision"), 1))
    service = Factor4LifecycleService()
    check = service.check_entity_schema(inventory)
    assert check.issues
    assert "schema:daily:revision_unique_key_missing" not in check.issues
    bad = tuple({**row, "non_unique": 1} for row in inventory)
    assert "schema:daily:revision_unique_key_missing" in service.check_entity_schema(bad).issues


def test_double_invalid_discovery_requires_both_dimensions_and_checks_batch_detail_identity() -> None:
    import json
    from types import SimpleNamespace
    from api.factor_data_mcp_api import MCPResponse

    snapshot = _snapshot()
    ts = {**snapshot.metrics[0], "evaluation_type": "time_series", "metric_status": "success", "is_valid": 0}
    cs = {**ts, "id": 4, "evaluation_type": "cross_sectional"}
    snapshot = replace(snapshot, metrics=(ts, cs))
    items = [{"factor_ref": ts["factor_ref"], "data": {"factor_ref": ts["factor_ref"]}}]

    def detail(refs: list[str], **kwargs: object) -> MCPResponse:
        body = {"data": {"items": items}, "meta": {}}
        return MCPResponse(200, "application/json", {"jsonrpc": "2.0", "id": 1, "result": {
            "structuredContent": body, "content": [{"type": "text", "text": json.dumps(body)}], "isError": False}}, None)

    service = Factor4LifecycleService()
    api = SimpleNamespace(get_factor_details_batch=detail)
    assert not service.check_ineligible_factors_remain_queryable(snapshot, api).issues
    items[0]["data"]["factor_ref"] = "sub_factor:99"
    assert service.check_ineligible_factors_remain_queryable(snapshot, api).issues
    cs["is_valid"] = 1
    with pytest.raises(ReadPrecondition, match="double-invalid"):
        service.check_ineligible_factors_remain_queryable(snapshot, api)


def test_credential_scan_omissions_do_not_hide_hits_or_certify_clean() -> None:
    service = Factor4LifecycleService()
    clean = {"table": "audit", "column": "payload", "hits": 0}
    omitted = {"table": "large", "column": "payload", "omitted": True}
    assert not service.check_credential_exposure((clean,)).issues
    with pytest.raises(ReadPrecondition, match="incomplete"):
        service.check_credential_exposure((clean, omitted))
    check = service.check_credential_exposure(({**clean, "hits": 1}, omitted))
    assert check.issues == ("credential_shape:audit:payload:count=1",)


@pytest.mark.parametrize("reverse", [False, True])
def test_publication_drift_cannot_hide_other_batches_confirmed_replay_issues(reverse: bool) -> None:
    before = _snapshot()
    extra = {**before.batches[0], "id": 9}
    batches = (extra, *before.batches) if reverse else (*before.batches, extra)
    before = replace(before, batches=batches)
    after = deepcopy(before)
    for batch in after.batches:
        if batch["id"] == 9:
            batch["is_active"] = 0
        else:
            batch["factor_set_snapshot_hash"] = "changed"
    result = Factor4LifecycleService().check_terminal_replay(before, after)
    assert "batch:id=1:frozen_field=factor_set_snapshot_hash" in result.issues
