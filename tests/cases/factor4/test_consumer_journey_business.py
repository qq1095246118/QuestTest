"""Factor 4.0 user-selected results, using public selectors from each preceding step."""

from collections.abc import Callable

import pytest

from api.factor4_summary_api import Factor4SummaryAPI
from db.factor4_calculation_repository import Factor4CalculationRepository
from db.factor4_consumer_repository import Factor4ConsumerRepository
from db.factor4_read_repository import SummarySample
from service.factor4_consumer_journey_service import Entry, Factor4ConsumerJourneyService
from service.factor4_read_service import Factor4ReadService, ReadContractError, ReadPrecondition
from service.factor4_summary_service import Factor4SummaryService

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.fixture(scope="module")
def consumer_service(factor4_read_service: Factor4ReadService,
                     factor4_calculation_repository: Factor4CalculationRepository) -> Factor4ConsumerJourneyService:
    """Reuse the initialized test MCP and DB; setup failures propagate without writes."""
    return Factor4ConsumerJourneyService(
        Factor4SummaryService(Factor4SummaryAPI(factor4_read_service.api.mcp)),
        Factor4ConsumerRepository(factor4_calculation_repository.database_client))


@pytest.fixture(scope="module", params=["ts_symbol", "ts_aggregate", "cs_aggregate"])
def consumer_seed(request: pytest.FixtureRequest, consumer_service: Factor4ConsumerJourneyService) -> SummarySample:
    """Discover initial user filters; absent natural shapes skip, DB failures propagate."""
    sample = consumer_service.repository.initial_scope(request.param)
    if sample is None:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no initial consumer scope for {request.param}")
    return sample


@pytest.mark.parametrize("entry", ["search", "rank"])
def test_public_scope_selection_replays_selected_result_and_evidence(
    consumer_service: Factor4ConsumerJourneyService, consumer_seed: SummarySample, entry: Entry,
    record_property: Callable[[str, object], None],
) -> None:
    """Consume public scope/ref/Run through metrics, validity, formula and exact-scope slices."""
    try:
        result = consumer_service.check_discovered_selection(consumer_seed, entry)
    except ReadPrecondition as error:
        pytest.skip(str(error))
    except ReadContractError as error:
        pytest.fail(str(error), pytrace=False)
    for name in ("entry", "factor_ref", "run_id", "scope", "stages", "blocked"):
        record_property("consumer_" + name, str(result.evidence.get(name, "")))
    assert not result.issues, ", ".join(result.issues)
    if result.evidence.get("blocked"):
        pytest.skip("; ".join(result.evidence["blocked"]))
    assert {"scope_discovery", entry, "metrics", "validity", "formula", "slices"} <= set(result.evidence["stages"])
