"""Regression guards for existing Factor 4.0 cases, without live product claims."""

from dataclasses import replace
from inspect import signature
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from service.factor4_calculation_service import CalculationCheckResult, Factor4CalculationService
from service.factor4_read_service import ReadCheck
from tests.cases.factor4 import test_backend_three_way_business as backend_cases
from tests.cases.factor4 import test_final_results as final_cases
from tests.cases.factor4 import test_formula_closure_business as formula_cases
from tests.unit.test_factor4_calculation_service import _formula, _mcp_response

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("mode", ["direct", "aggregate"])
def test_formula_request_uses_exact_evidence_mode_run_and_window(mode: str) -> None:
    """Persisted aggregate evidence must never be requested as direct; transport errors propagate."""
    evidence = replace(_formula(), calculation_mode=mode, run_id="specific-run", factor_window_bars="48H")
    mcp = Mock()
    mcp.get_formula.return_value = _mcp_response({"formula": {}})
    service = Factor4CalculationService(Mock(), mcp)
    service._load_mcp_formula(evidence)
    mcp.get_formula.assert_called_once_with(
        evidence.factor_ref, "specific-run", evidence.factor_bar_interval, "48H",
        evidence.return_bar_interval, evidence.forward_return_bars, calculation_mode=mode,
    )
    service._load_mcp_formula(replace(evidence, calculation_mode="aggregate" if mode == "direct" else "direct"))
    assert mcp.get_formula.call_count == 2


@pytest.mark.parametrize("states", [
    ("BLOCKED_DATA_PRECONDITION",), ("PASS", "BLOCKED_DATA_PRECONDITION"),
    ("BLOCKED_DATA_PRECONDITION", "FAIL"), ("FAIL", "BLOCKED_DATA_PRECONDITION"), ("PASS",),
])
def test_ranking_case_uses_fail_then_block_then_pass_precedence(
    monkeypatch: pytest.MonkeyPatch, states: tuple[str, ...],
) -> None:
    """A changed publication cannot pass, or hide another partition's confirmed failure."""
    results = iter(CalculationCheckResult("test", "ranking", state, state, 1) for state in states)
    monkeypatch.setattr(Factor4CalculationService, "check_final_result_ranking", lambda *args: next(results))
    partition = SimpleNamespace(market_scope="all", route_profile_key="default")
    snapshots = tuple((partition, None) for _ in states)
    repository = Mock()
    action = lambda: final_cases.test_final_result_ranking_is_partitioned_and_repeatable(snapshots, repository)
    if "FAIL" in states:
        with pytest.raises(AssertionError):
            action()
    elif "BLOCKED_DATA_PRECONDITION" in states:
        with pytest.raises(pytest.skip.Exception, match="ranking not fully verified"):
            action()
    else:
        action()


@pytest.mark.parametrize("include_schema", [False, True])
def test_formula_only_branch_does_not_load_schema_fixture(
    monkeypatch: pytest.MonkeyPatch, include_schema: bool,
) -> None:
    """Versioned schema loading happens only for the schema closure parameter."""
    schemas = {"raw-v1": None}
    request = Mock()
    request.getfixturevalue.return_value = schemas
    check = Mock(return_value=ReadCheck(1, (), {"blocked": ()}))
    monkeypatch.setattr(formula_cases.Factor4FormulaClosureService, "check_routes", check)
    formula_cases.test_published_routes_bind_exact_formula_and_schema((None,), request, "WIDE_RANGE", include_schema)
    check.assert_called_once_with(None, "WIDE_RANGE", schemas=schemas if include_schema else None)
    if include_schema:
        request.getfixturevalue.assert_called_once_with("bound_raw_schemas")
    else:
        request.getfixturevalue.assert_not_called()


def test_cost_repository_requires_database_gate_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pure persisted-cost checks have no Backend authentication or MCP fixture dependency."""
    fixture = backend_cases.backend_repository.__wrapped__
    assert set(signature(fixture).parameters) == {"factor4_calculation_repository"}
    repository = Mock()
    factory = Mock()
    monkeypatch.setattr(backend_cases, "Factor4AuxiliaryRepository", factory)
    assert fixture(repository) is factory.return_value
    factory.assert_called_once_with(repository.database_client)
