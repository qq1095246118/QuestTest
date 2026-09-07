"""Counterexamples for real-result formula bindings; no live success is implied."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from db.factor4_calculation_repository import CalculationAuditSnapshot
from db.factor4_schema_repository import ApprovedSchemaSnapshot, Factor4SchemaRepository
from service.factor4_formula_closure_service import Factor4FormulaClosureService, metric_raw_schema_version
from tests.unit.test_factor4_calculation_service import _formula, _metric, _route, _snapshot

pytestmark = pytest.mark.unit


def _bound() -> CalculationAuditSnapshot:
    metric = _metric(1, "sub_factor:10", "time_series", formula_links=True)
    metric = replace(metric, metric_identity={**metric.metric_identity, "raw_data_schema_version": "raw-v1"})
    return _snapshot(metrics=(metric,), formulas=(_formula(),), routes=(_route(1, "sub_factor:10", 1, 1, "80"),))


def _schema() -> ApprovedSchemaSnapshot:
    return ApprovedSchemaSnapshot("raw-v1", (
        {"field_name": "close", "source_dataset": "bars", "source_field": "close", "schema_version": "raw-v1"},
    ), (), ())


def test_complete_route_to_schema_chain_is_checked() -> None:
    result = Factor4FormulaClosureService().check_routes(_bound(), "WIDE_RANGE", schemas={"raw-v1": _schema()})
    assert result.checked_count == 1
    assert not result.issues and not result.evidence["blocked"]


@pytest.mark.parametrize("field,value", [
    ("metric_id", 99), ("factor_version", "new"), ("publication_uid", "other"),
    ("eval_batch_id", 999), ("score_rule_version", "v9"), ("market_scope", "spot"),
])
def test_route_identity_corruption_is_detected(field: str, value: object) -> None:
    snapshot = _bound()
    snapshot = replace(snapshot, routes=(replace(snapshot.routes[0], **{field: value}),))
    assert Factor4FormulaClosureService().check_routes(snapshot, "WIDE_RANGE").issues


def test_missing_formula_cannot_hide_route_identity_failure() -> None:
    snapshot = _bound()
    snapshot = replace(snapshot, formula_evidence=(), routes=(replace(snapshot.routes[0], publication_uid="bad"),))
    result = Factor4FormulaClosureService().check_routes(snapshot, "WIDE_RANGE", schemas={})
    assert result.issues and result.evidence["blocked"]


def test_formula_hash_not_found_blocks_instead_of_using_current_formula() -> None:
    snapshot = _bound()
    formula = replace(snapshot.formula_evidence[0], formula_hash="other-hash")
    result = Factor4FormulaClosureService().check_routes(replace(snapshot, formula_evidence=(formula,)), "WIDE_RANGE")
    assert result.checked_count == 0 and result.evidence["blocked"]


def test_no_route_is_not_a_pass() -> None:
    result = Factor4FormulaClosureService().check_routes(replace(_bound(), routes=()), "WIDE_RANGE")
    assert result.checked_count == 0 and result.evidence["blocked"]


@pytest.mark.parametrize("mutation", ["fallback", "missing_input", "wrong_version", "missing_source", "duplicate"])
def test_schema_corruption_is_detected(mutation: str) -> None:
    schema = _schema()
    if mutation == "fallback":
        schema = replace(schema, version="raw-v2")
    elif mutation == "missing_input":
        schema = replace(schema, mappings=())
    elif mutation == "wrong_version":
        schema = replace(schema, mappings=({**schema.mappings[0], "schema_version": "raw-v2"},))
    elif mutation == "missing_source":
        schema = replace(schema, mappings=({**schema.mappings[0], "source_dataset": None},))
    else:
        schema = replace(schema, mappings=schema.mappings * 2)
    result = Factor4FormulaClosureService().check_routes(_bound(), "WIDE_RANGE", schemas={"raw-v1": schema})
    assert result.issues


@pytest.mark.parametrize("cycle", [False, True])
def test_nested_dependencies_are_traversed_not_just_top_level(cycle: bool) -> None:
    snapshot = _bound()
    snapshot = replace(snapshot, formula_evidence=(replace(snapshot.formula_evidence[0], required_fields=("derived",)),))
    schema = replace(_schema(), resolutions=(
        {"field_name": "derived", "dependency_fields_json": '["mid"]'},
        {"field_name": "mid", "dependency_fields_json": '["derived"]' if cycle else '["close"]'},
    ))
    result = Factor4FormulaClosureService().check_routes(snapshot, "WIDE_RANGE", schemas={"raw-v1": schema})
    assert bool(result.issues) is cycle
    assert not result.evidence["blocked"]


def test_missing_raw_version_never_uses_latest_approved_schema() -> None:
    snapshot = _bound()
    metric = snapshot.evaluation_metrics[0]
    identity = {key: value for key, value in metric.metric_identity.items() if key != "raw_data_schema_version"}
    snapshot = replace(snapshot, evaluation_metrics=(replace(metric, metric_identity=identity),))
    result = Factor4FormulaClosureService().check_routes(snapshot, "WIDE_RANGE", schemas={"raw-v1": _schema()})
    assert not result.issues and result.evidence["blocked"]


@pytest.mark.parametrize("bad", [None, "", 1, []])
def test_malformed_explicit_raw_version_is_not_treated_as_absent(bad: object) -> None:
    metric = _bound().evaluation_metrics[0]
    metric = replace(metric, metric_identity={**metric.metric_identity, "raw_data_schema_version": bad})
    with pytest.raises(ValueError):
        metric_raw_schema_version(metric)


@pytest.mark.parametrize("bad", [None, "", 1, []])
def test_missing_formula_does_not_hide_malformed_raw_schema_binding(bad: object) -> None:
    snapshot = _bound()
    metric = snapshot.evaluation_metrics[0]
    metric = replace(metric, metric_identity={**metric.metric_identity, "raw_data_schema_version": bad})
    snapshot = replace(snapshot, evaluation_metrics=(metric,), formula_evidence=())
    result = Factor4FormulaClosureService().check_routes(snapshot, "WIDE_RANGE", schemas={})
    assert any("raw_schema_version_invalid_or_conflicting" in issue for issue in result.issues)
    assert result.evidence["blocked"]


def test_missing_formula_does_not_hide_route_evidence_version_corruption() -> None:
    snapshot = _bound()
    metric = snapshot.evaluation_metrics[0]
    route = snapshot.routes[0]
    route = replace(route, evidence={**route.evidence, "metric_identity": {**metric.metric_identity, "factor_version": "wrong-version"}})
    result = Factor4FormulaClosureService().check_routes(replace(snapshot, routes=(route,), formula_evidence=()), "WIDE_RANGE")
    assert any("METRIC_FACTOR_VERSION_MISMATCH" in issue for issue in result.issues)
    assert result.evidence["blocked"]


def test_metric_and_route_cannot_jointly_change_batch_label_kind() -> None:
    snapshot = _bound()
    snapshot = replace(snapshot, evaluation_metrics=(replace(snapshot.evaluation_metrics[0], label_kind="forecast"),),
                       routes=(replace(snapshot.routes[0], label_kind="forecast"),))
    result = Factor4FormulaClosureService().check_routes(snapshot, "WIDE_RANGE")
    assert any("batch_label_kind" in issue for issue in result.issues)


def test_metric_score_version_must_match_batch_even_if_route_matches() -> None:
    snapshot = _bound()
    snapshot = replace(snapshot, evaluation_metrics=(replace(snapshot.evaluation_metrics[0], scoring_version="old"),))
    result = Factor4FormulaClosureService().check_routes(snapshot, "WIDE_RANGE")
    assert any("metric_scoring_version" in issue for issue in result.issues)


def test_unhashable_schema_field_is_a_diagnostic_not_an_exception() -> None:
    schema = _schema()
    schema = replace(schema, mappings=(*schema.mappings, {"field_name": []}))
    result = Factor4FormulaClosureService().check_routes(_bound(), "WIDE_RANGE", schemas={"raw-v1": schema})
    assert any("schema_duplicate_or_missing_field_identity" in issue for issue in result.issues)


def test_exact_historical_schema_lookup_never_falls_back() -> None:
    client = MagicMock()
    tx = client.transaction.return_value.__enter__.return_value
    tx.fetch_one.return_value = None
    assert Factor4SchemaRepository(client).approved("old-version") is None
    query, params = tx.fetch_one.call_args.args
    assert "schema_version=%s" in query and params == ("old-version",)
    tx.fetch_all.assert_not_called()
    assert tx.execute.call_args.args == ("ROLLBACK",)


def test_exact_schema_children_use_bound_version_and_cleanup() -> None:
    client = MagicMock()
    tx = client.transaction.return_value.__enter__.return_value
    tx.fetch_one.return_value = {"schema_version": "old-version"}
    tx.fetch_all.return_value = []
    result = Factor4SchemaRepository(client).approved("old-version")
    assert result.version == "old-version"
    assert all(call.args[1] == ("old-version",) for call in tx.fetch_all.call_args_list)
    assert tx.execute.call_args.args == ("ROLLBACK",)
