"""Additional business branches retained from cross-module historical scripts."""

from collections.abc import Callable

import pytest

from api.factor4_formula_api import Factor4FormulaAPI
from api.factor4_summary_api import Factor4SummaryAPI
from db.factor4_calculation_repository import Factor4CalculationRepository
from db.factor4_formula_repository import Factor4FormulaRepository
from db.factor4_publication_repository import Factor4PublicationRepository
from db.factor4_read_repository import Factor4ReadRepository
from service.factor4_cross_read_service import Factor4CrossReadService, check_completed_formula_replay, check_research_library_warning
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadPrecondition

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _check(action: Callable[[], ReadCheck]) -> None:
    try:
        result = action()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))
    assert result.checked_count > 0
    assert not result.issues, result.issues[:30]


@pytest.mark.parametrize("kind", ["fact", "forecast"])
@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_daily_first_availability_hides_future_and_includes_equal_boundary(factor4_read_service: Factor4ReadService, factor4_read_repository: Factor4ReadRepository, kind: str, offset: int) -> None:
    """ENV-PIT-BEFORE/AT: single revision needs no replacement-revision fixture."""
    snapshot = factor4_read_repository.daily_snapshot()
    _check(lambda: Factor4CrossReadService(factor4_read_service).check_daily_availability(snapshot, kind, offset))


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_recommendations_at_forecast_boundary_keep_visible_publication_and_label(factor4_read_service: Factor4ReadService, factor4_read_repository: Factor4ReadRepository, factor4_calculation_repository: Factor4CalculationRepository, offset: int) -> None:
    """Deep endpoint REC-PIT: three forecast points and every market/profile partition."""
    history = Factor4PublicationRepository(factor4_calculation_repository.database_client).history()
    daily = factor4_read_repository.daily_snapshot()
    _check(lambda: Factor4CrossReadService(factor4_read_service).check_forecast_recommendation_boundary(daily, history, offset))


def test_exact_completed_formula_three_uncached_reads_match_database(factor4_read_service: Factor4ReadService, factor4_calculation_repository: Factor4CalculationRepository) -> None:
    """FORMULA-STABLE: actual requests, same factor/Run/hash/expression and full data replay."""
    snapshot = Factor4FormulaRepository(factor4_calculation_repository.database_client).catalog()
    _check(lambda: check_completed_formula_replay(Factor4FormulaAPI(factor4_read_service.api.mcp), snapshot))


@pytest.mark.factor4_deferred
@pytest.mark.parametrize("variant", ["batch", "scope", "recommendation_scope"])
def test_environment_unknown_selectors_do_not_substitute_active_data(factor4_read_service: Factor4ReadService, factor4_read_repository: Factor4ReadRepository, variant: str) -> None:
    """ENV-WRONG-BATCH/SCOPE: retain excluded unknown-selector scenarios as executable Cases."""
    sample = factor4_read_repository.metric_sample("sub_factor")
    if sample is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no active environment metric sample")
    _check(lambda: Factor4CrossReadService(factor4_read_service).check_environment_missing_selector(sample, variant))


@pytest.mark.factor4_deferred
@pytest.mark.parametrize("shape", ["ts_aggregate", "cs_aggregate"])
@pytest.mark.parametrize("future", [False, True], ids=["current", "future"])
def test_research_current_library_status_has_explicit_point_in_time_warning(factor4_read_service: Factor4ReadService, factor4_read_repository: Factor4ReadRepository, shape: str, future: bool) -> None:
    """MCP-019 warning-only branch remains separately executable outside normal acceptance."""
    sample = factor4_read_repository.summary_sample(shape)
    if sample is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: research summary partition missing")
    _check(lambda: check_research_library_warning(Factor4SummaryAPI(factor4_read_service.api.mcp), sample, future=future))
