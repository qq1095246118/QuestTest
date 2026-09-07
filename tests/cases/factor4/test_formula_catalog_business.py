"""Formal cases replacing historical formula/semantic/result probes."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from api.factor4_formula_api import Factor4FormulaAPI
from api.factor_data_mcp_api import FactorDataMCPAPI
from db.factor4_calculation_repository import Factor4CalculationRepository
from db.factor4_formula_repository import Factor4FormulaRepository, FormulaCatalogSnapshot
from service.factor4_formula_service import Factor4FormulaService
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.fixture(scope="module")
def formula_repository(factor4_calculation_repository: Factor4CalculationRepository) -> Factor4FormulaRepository:
    """Reuse the explicit live/test-gated database client without new credentials."""
    return Factor4FormulaRepository(factor4_calculation_repository.database_client)


@pytest.fixture(scope="module")
def formula_catalog(formula_repository: Factor4FormulaRepository) -> FormulaCatalogSnapshot:
    """Capture actual current/history catalog evidence in one read-only transaction."""
    return formula_repository.catalog()


@pytest.fixture(scope="module")
def formula_service(factor_data_mcp_api: FactorDataMCPAPI, formula_repository: Factor4FormulaRepository) -> Factor4FormulaService:
    """Use the test-gated MCP session; transport and DB errors are not swallowed."""
    factor_data_mcp_api.initialize()
    factor_data_mcp_api.notify_initialized()
    return Factor4FormulaService(Factor4FormulaAPI(factor_data_mcp_api), formula_repository)


def _check(action: Callable[[], ReadCheck]) -> None:
    try:
        result = action()
    except ReadPrecondition as error:
        pytest.skip(str(error))
    except ReadContractError as error:
        pytest.fail(str(error), pytrace=False)
    assert result.checked_count > 0
    assert not result.issues, result.issues[:30]
    blocked = result.evidence.get("blocked", [])
    if blocked:
        reasons = sorted({str(item.get("reason")) if isinstance(item, dict) else str(item) for item in blocked})
        pytest.skip(f"BLOCKED_SEMANTIC_OR_DATA: count={len(blocked)}; reasons={reasons[:15]}")


def test_all_active_formula_projections_and_approved_inputs(formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot) -> None:
    """Default result scope: refs/source/executable projection; no operator-input or family scan."""
    _check(lambda: formula_service.check_active_catalog(formula_catalog, include_internal_semantics=False))


@pytest.mark.parametrize("factor_id", [161104, 161106, 161108])
def test_dpo_latest_persisted_value_uses_completed_formula_run(formula_service: Factor4FormulaService, factor_id: int) -> None:
    """DPO source: independent newest value must point to same completed Run/window evidence."""
    _check(lambda: formula_service.check_persisted_value_run(factor_id))


@pytest.mark.parametrize("factor_id", [161104, 161106, 161108])
@pytest.mark.factor4_internal_calculation
def test_dpo_current_definition_uses_price_shift_and_declared_window(formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot, factor_id: int) -> None:
    """DPO source: current expression must shift price, not its moving average."""
    _check(lambda: formula_service.check_candidate_horizon(formula_catalog, factor_id))


@pytest.mark.parametrize("factor_id", [161628, 161629, 161630])
def test_iv_rv_all_detail_levels_and_global_input_schema(formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot, factor_id: int) -> None:
    """IV/RV output: summary/definition/executable metadata; mathematics is a separate internal Case."""
    _check(lambda: formula_service.check_detail_levels(formula_catalog, factor_id, include_internal_semantics=False))


@pytest.mark.parametrize("factor_id", [180, 181, 183, 274, 276, 156469, 88858])
@pytest.mark.factor4_internal_calculation
def test_fixed_horizon_declared_and_completed_formula_candidates(formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot, factor_id: int) -> None:
    """Fixed horizon/adjudication: exact projection and AST offsets; unknown feature contract blocks."""
    _check(lambda: formula_service.check_candidate_horizon(formula_catalog, factor_id))


@pytest.mark.parametrize("factor_id", [161104, 161106, 161108, 180, 181, 183, 274, 276, 156469, 88858])
def test_formula_family_current_and_completed_evidence_projections(
    formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot, factor_id: int,
) -> None:
    """Preserve the existing DPO/horizon Cases' output half without mathematical adjudication."""
    _check(lambda: formula_service.check_candidate_projection(formula_catalog, factor_id))


@pytest.mark.parametrize("factor_id", [1336092, 1482924, 5921, 161628, 161629, 161630])
def test_formula_summary_validity_chain_for_actual_completed_run(formula_service: Factor4FormulaService, factor_id: int) -> None:
    """Aggregate/5921/IV-RV: exact Run, scope/symbol, all summary fields, validity and wrong window."""
    _check(lambda: formula_service.check_exact_final_chain(factor_id, reject_other_window=factor_id == 5921))


def test_targeted_formula_current_definition_projects_its_current_window(formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot) -> None:
    """5921 source: current detail is checked separately from the immutable historical Run."""
    _check(lambda: formula_service.check_detail_levels(formula_catalog, 5921, include_internal_semantics=False))


@pytest.mark.factor4_internal_calculation
@pytest.mark.parametrize("factor_id", [161628, 161629, 161630, 5921])
def test_formula_detail_levels_internal_semantics(
    formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot, factor_id: int,
) -> None:
    """Preserve the four original detail Cases' AST/input/IV-RV mathematical checks explicitly."""
    _check(lambda: formula_service.check_detail_levels(formula_catalog, factor_id, include_internal_semantics=True))


@pytest.mark.parametrize("category", [
    "empty_formula", "syntax", "unresolved_inputs", "params_only_input_miss", "future_target",
    "annualization", "interval_mismatch", "dataset_frequency", "field_frequency", "negative_temporal",
    "center_or_backfill", "unbounded_aggregate", "temporal_constants", "independent_temporal_family",
    "fixed_formula_family",
])
@pytest.mark.factor4_internal_calculation
def test_catalog_semantic_candidates_are_conditionally_adjudicated(formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot, category: str) -> None:
    """Global semantic scan/family audit: inspect every branch and preserve unestablished contracts."""
    _check(lambda: formula_service.check_semantic_category(formula_catalog, category))


def test_all_formula_evidence_source_fields_intervals_and_warnings(formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot) -> None:
    """Persisted evidence source/interval/completeness; no operator or negative-offset interpretation."""
    _check(lambda: formula_service.check_evidence_catalog(formula_catalog, include_internal_semantics=False))


@pytest.mark.factor4_internal_calculation
def test_formula_catalog_and_evidence_internal_semantics(
    formula_service: Factor4FormulaService, formula_catalog: FormulaCatalogSnapshot,
) -> None:
    """Opt-in historical active-catalog/evidence AST and input-resolution checks."""
    active = formula_service.check_active_catalog(formula_catalog)
    evidence = formula_service.check_evidence_catalog(formula_catalog)
    assert not (*active.issues, *evidence.issues)
    _check(lambda: evidence)
