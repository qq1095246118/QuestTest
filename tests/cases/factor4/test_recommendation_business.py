"""Formal recommendation replay and final-result cases migrated from PIT probes."""

from collections.abc import Callable

import pytest

from db.factor4_publication_repository import Factor4PublicationRepository, PublicationHistory
from db.factor4_calculation_repository import Factor4CalculationRepository
from db.factor4_read_repository import Factor4ReadRepository
from service.factor4_read_service import LABELS, Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition, visible_daily_rows
from service.factor4_recommendation_service import Factor4RecommendationService, check_forecast_probabilities

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _verify(action: Callable[[], ReadCheck]) -> None:
    try:
        check = action()
    except ReadPrecondition as error:
        pytest.skip(str(error))
    except ReadContractError as error:
        pytest.fail(str(error), pytrace=False)
    assert check.checked_count > 0
    assert not check.issues, ", ".join(check.issues[:30])


@pytest.fixture(scope="module")
def publication_history(factor4_calculation_repository: Factor4CalculationRepository) -> PublicationHistory:
    """Read final publication state using the existing live/test-gated client."""
    return Factor4PublicationRepository(factor4_calculation_repository.database_client).history()


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_recommendation_publication_visibility_boundary(
    factor4_read_service: Factor4ReadService, publication_history: PublicationHistory, offset: int,
) -> None:
    """REC-210: every scope/profile uses its latest publication visible at as_of."""
    service = Factor4RecommendationService(factor4_read_service.api)
    _verify(lambda: service.check_publication_boundary(publication_history, offset))


@pytest.mark.parametrize("limit", [1, 20, 200])
def test_recommendation_current_routes_values_order_and_replay(
    factor4_read_service: Factor4ReadService, factor4_read_repository: Factor4ReadRepository,
    publication_history: PublicationHistory, limit: int,
) -> None:
    """REC-203/204/205/208: final active route identity, score, order and replay."""
    daily = factor4_read_repository.daily_snapshot()
    service = Factor4RecommendationService(factor4_read_service.api)
    _verify(lambda: service.check_current_routes(publication_history, daily, limit=limit))


def test_ready_forecast_has_six_normalized_probabilities(
    factor4_read_service: Factor4ReadService, factor4_read_repository: Factor4ReadRepository,
) -> None:
    """ENV-108: every visible ready forecast has six finite normalized probabilities."""
    snapshot = factor4_read_repository.daily_snapshot()
    if not visible_daily_rows(snapshot, "forecast"):
        pytest.skip("BLOCKED_DATA_PRECONDITION: no forecast")
    try:
        traversal = factor4_read_service.daily_pages(snapshot, "forecast")
    except ReadContractError as error:
        pytest.fail(str(error), pytrace=False)
    _verify(lambda: check_forecast_probabilities(traversal.rows))


@pytest.mark.parametrize("label", LABELS)
def test_each_forecast_label_recommends_only_its_visible_historical_routes(
    factor4_read_service: Factor4ReadService, factor4_read_repository: Factor4ReadRepository,
    publication_history: PublicationHistory, label: str, record_property: Callable[[str, object], None],
) -> None:
    """Read one real forecast/publication intersection per label and market/profile.

    Correct empty recommendations are valid business outcomes; another environment's
    routes cannot substitute. Missing samples skip only after all output failures are
    asserted. Service/API failures propagate, without live writes or raw recomputation.
    """
    result = Factor4RecommendationService(factor4_read_service.api).check_forecast_label_history(
        publication_history, factor4_read_repository.daily_snapshot(), label)
    record_property("forecast_label", label)
    record_property("forecast_history_samples", result.evidence["samples"])
    record_property("forecast_history_blocked", result.evidence["blocked"])
    assert not result.issues, result.issues[:30]
    if result.evidence["blocked"]:
        pytest.skip("BLOCKED_DATA_PRECONDITION: forecast label history: " + str(result.evidence["blocked"][:15]))
    assert result.checked_count > 0
