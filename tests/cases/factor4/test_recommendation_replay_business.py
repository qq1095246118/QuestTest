"""Actual recommendation-to-metric/formula replay within one published version."""

from collections.abc import Callable

import pytest

from db.factor4_calculation_repository import CalculationAuditSnapshot, Factor4CalculationRepository
from db.factor4_read_repository import Factor4ReadRepository
from service.factor4_read_service import Factor4ReadService
from service.factor4_recommendation_replay_service import Factor4RecommendationReplayService

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.mark.parametrize("kind", ["factor", "sub_factor"])
def test_recommended_factors_replay_formula_and_metrics_in_the_same_publication(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...],
    factor4_read_service: Factor4ReadService, factor4_read_repository: Factor4ReadRepository,
    factor4_calculation_repository: Factor4CalculationRepository, kind: str,
    record_property: Callable[[str, object], None],
) -> None:
    """Follow public daily to recommendations, exact batch metrics and exact Run formulas.

    The same as_of and actual daily forecast drive the user chain, with an independent
    DB revision oracle. Every discovered published market/profile participates.
    Missing natural kind or immutable evidence blocks rather than fabricating a successful replay; known
    failures in any partition take precedence over all missing-evidence reasons.
    """
    service = Factor4RecommendationReplayService(
        factor4_read_service, factor4_read_repository, factor4_calculation_repository)
    result = service.check_kind(factor4_closure_snapshots, factor4_read_repository.daily_snapshot(), kind)
    record_property("recommendation_public_forecast_id", result.evidence["public_forecast_id"])
    record_property("recommendation_fixed_as_of", result.evidence["as_of"])
    record_property("recommendation_chain_blocked", result.evidence["blocked"])
    assert not result.issues, result.issues[:30]
    if result.evidence["blocked"]:
        pytest.skip("BLOCKED_DATA_PRECONDITION: recommendation replay: " + str(result.evidence["blocked"][:15]))
    assert result.checked_count > 0
