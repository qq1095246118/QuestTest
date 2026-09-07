"""Keep executable formulas, metadata, and input-field declarations distinct."""

from __future__ import annotations

from dataclasses import replace

import pytest

from service.factor4_calculation_service import CalculationIssue, _compare_definition_projection
from tests.unit.test_factor4_calculation_service import (
    TestKnownFormulaRegressions as _KnownFormulaFixture,
    _definition,
    _detail,
    _mcp_detail,
    _service,
)

pytestmark = pytest.mark.unit


def test_stale_normalized_dpo_does_not_become_an_execution_failure() -> None:
    """Correct current/evidence expressions must ignore stale provenance as execution."""
    snapshot, api = _KnownFormulaFixture._complete_fixture()
    api.details["sub_factor:161104"]["metadata"] = {
        "normalized_formula": "-(close - mean(close, 60).shift(31))",
    }
    service, _ = _service(snapshot, mcp=api)
    result = service.check_known_formula_regressions(snapshot, family="dpo")
    assert result.status == "PASS", result.findings


def test_stale_normalized_dpo_is_still_reported_as_metadata() -> None:
    """Separating source layers must retain a proven metadata semantic conflict."""
    definition = _definition(expression="mean(close, 60) - close.shift(31)", window="60H")
    detail = _detail(expression="mean(close, window) - close.shift(window // 2 + 1)", window=60)
    payload = _mcp_detail(definition, detail)
    payload["metadata"] = {"normalized_formula": "-(close - mean(close, 60).shift(31))"}
    issues: list[CalculationIssue] = []
    _compare_definition_projection(definition, detail, payload, issues)
    assert [issue.code for issue in issues] == ["F4-NORMALIZED-FORMULA-STALE"]
    assert issues[0].message == "公式已更新但 normalized_formula 元数据仍保留旧表达式"
    assert issues[0].evidence["evidence_layer"] == "current_metadata_not_run_execution"


@pytest.mark.parametrize("temporal_unit", [None, "hours"])
def test_period_normalization_without_actual_cadence_is_blocked(temporal_unit: str | None) -> None:
    """Period-only differences cannot prove incorrect metadata or executable values."""
    definition = _definition(expression="mean(close, 24)")
    detail = _detail(expression="mean(close, 24)")
    payload = _mcp_detail(definition, detail)
    payload["metadata"] = {"normalized_formula": "mean(close, 1)", "temporal_unit": temporal_unit}
    issues: list[CalculationIssue] = []
    _compare_definition_projection(definition, detail, payload, issues)
    assert [issue.code for issue in issues] == ["NORMALIZED_FORMULA_TEMPORAL_BASIS_UNRESOLVED"]
    assert issues[0].status == "BLOCKED_DOC"


def test_equivalent_normalized_metadata_passes() -> None:
    """A proved equal parameterized normalized expression remains valid."""
    definition, detail = _definition(), _detail()
    payload = _mcp_detail(definition, detail)
    payload["metadata"] = {"normalized_formula": "factor = close.pct_change(window + 0)"}
    issues: list[CalculationIssue] = []
    _compare_definition_projection(definition, detail, payload, issues)
    assert not issues


def test_equivalent_dpo_sign_wrapper_is_not_stale_metadata() -> None:
    """The family oracle recognizes an equivalent sign-reversed DPO wrapper."""
    definition = _definition(expression="mean(close, 60) - close.shift(31)", window="60H")
    detail = _detail(expression="mean(close, window) - close.shift(window // 2 + 1)", window=60)
    payload = _mcp_detail(definition, detail)
    payload["metadata"] = {"normalized_formula": "-(close.shift(31) - mean(close, 60))"}
    issues: list[CalculationIssue] = []
    _compare_definition_projection(definition, detail, payload, issues)
    assert not issues


def test_actual_execution_mismatch_is_not_hidden_by_correct_metadata() -> None:
    """A wrong MCP executable must fail even if metadata echoes the DB formula."""
    definition, detail = _definition(), _detail()
    payload = _mcp_detail(definition, detail)
    payload["calc_logic"] = "close.pct_change(12)"
    payload["metadata"] = {"normalized_formula": "close.pct_change(24)"}
    issues: list[CalculationIssue] = []
    _compare_definition_projection(definition, detail, payload, issues)
    assert any(issue.code == "MCP_DETAIL_FORMULA_MISMATCH" and issue.status == "FAIL" for issue in issues)


def test_definition_summary_does_not_mask_wrong_executable() -> None:
    """Family checks still inspect executable detail independently of summaries."""
    snapshot, api = _KnownFormulaFixture._complete_fixture()
    api.details["sub_factor:161104"]["calc_logic"] = "-(close - mean(close, 60).shift(31))"
    service, _ = _service(snapshot, mcp=api)
    result = service.check_known_formula_regressions(snapshot, family="dpo")
    assert any(issue.code == "F4-DPO-FORMULA" and issue.status == "FAIL" for issue in result.findings)


def test_extra_metadata_input_closure_is_not_a_projection_mismatch() -> None:
    """Derived fields and raw dependency closure are not one input declaration."""
    definition, detail = _definition(), _detail()
    payload = _mcp_detail(definition, detail)
    payload["metadata"] = {"required_fields": ["ret"], "resolved_raw_fields": ["close"]}
    issues: list[CalculationIssue] = []
    _compare_definition_projection(definition, detail, payload, issues)
    assert not issues


def test_same_layer_field_projection_mismatch_still_fails() -> None:
    """Changing a copied required_fields declaration remains a real mismatch."""
    definition, detail = _definition(), _detail()
    payload = _mcp_detail(definition, detail)
    payload["data_source_metadata"] = {**detail.data_source_metadata, "required_fields": ["open"]}
    issues: list[CalculationIssue] = []
    _compare_definition_projection(definition, detail, payload, issues)
    assert [issue.code for issue in issues] == ["CURRENT_FORMULA_FIELDS_MISMATCH"]
    assert issues[0].evidence["field"] == "data_source_metadata.required_fields"


def test_summary_only_is_not_sufficient_execution_evidence() -> None:
    """A parseable summary cannot falsely pass a known execution-family check."""
    snapshot, api = _KnownFormulaFixture._complete_fixture()
    snapshot = replace(snapshot, details=(), formula_evidence=(), evaluation_metrics=())
    for value in api.details.values():
        value.pop("calc_logic", None)
    service, _ = _service(snapshot, mcp=api)
    result = service.check_known_formula_regressions(snapshot, family="dpo")
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(issue.code == "KNOWN_FORMULA_CURRENT_SOURCE_MISSING" for issue in result.findings)
