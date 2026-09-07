"""Research metric search is tested independently of library-only search and factor_rank."""

from collections.abc import Callable

import pytest

from api.factor4_summary_api import Factor4SummaryAPI
from db.factor4_read_repository import Factor4ReadRepository, SummarySample
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition
from service.factor4_research_search_service import Factor4ResearchSearchService
from service.factor4_summary_service import Factor4SummaryService

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _verify(action: Callable[[], ReadCheck]) -> None:
    try:
        result = action()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))
    except ReadContractError as exc:
        pytest.fail(str(exc), pytrace=False)
    assert result.checked_count > 0
    assert not result.issues, ", ".join(result.issues[:20])


@pytest.fixture(scope="module")
def research_service(factor4_read_service: Factor4ReadService) -> Factor4ResearchSearchService:
    """Use the initialized shared live/test-gated MCP session; failures propagate."""
    return Factor4ResearchSearchService(Factor4SummaryService(Factor4SummaryAPI(factor4_read_service.api.mcp)))


@pytest.fixture(scope="module", params=["ts_symbol", "ts_aggregate", "cs_aggregate"])
def research_sample(request: pytest.FixtureRequest, factor4_read_repository: Factor4ReadRepository) -> SummarySample:
    """Discover complete small DB partitions separately for all three metric shapes."""
    sample = factor4_read_repository.summary_sample(request.param)
    if sample is None:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no complete {request.param} research partition")
    return sample


def test_research_search_pages_and_stats_match_latest_db_candidates(
    research_service: Factor4ResearchSearchService, research_sample: SummarySample,
) -> None:
    """Exact-scope factor_search and factor_catalog_stats must match the same latest DB set."""
    _verify(lambda: research_service.check_research_search(research_sample))


@pytest.mark.parametrize("threshold", ["min_icir", "min_rank_icir", "min_score"])
@pytest.mark.parametrize("above_maximum", [False, True], ids=["inclusive_threshold", "above_max_empty"])
def test_research_metric_thresholds_filter_exact_candidates_and_stats(
    research_service: Factor4ResearchSearchService, research_sample: SummarySample,
    threshold: str, above_maximum: bool,
) -> None:
    """Thresholds filter search inclusively/above-max; base stats remain independently reconciled."""
    _verify(lambda: research_service.check_research_search(research_sample, threshold=threshold,
                                                           above_maximum=above_maximum))


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_research_search_completion_boundary_uses_historical_latest_candidates(
    research_service: Factor4ResearchSearchService, research_sample: SummarySample,
    factor4_read_repository: Factor4ReadRepository, offset: int,
) -> None:
    """Research search and stats select the independently reconstructed visible history."""
    _verify(lambda: research_service.check_research_completion(factor4_read_repository, research_sample, offset))


@pytest.mark.parametrize("good_count", [2, 3])
def test_mixed_metric_batch_preserves_two_existing_factor_identities_and_local_error(
    research_service: Factor4ResearchSearchService, research_sample: SummarySample,
    factor4_read_repository: Factor4ReadRepository, good_count: int,
) -> None:
    """Two/three same-scope factors retain their own Run/values alongside one absent ref."""
    missing = factor4_read_repository.absent_factor_ref(research_sample.kind)
    _verify(lambda: research_service.check_mixed_metric_batch(research_sample, missing, good_count=good_count))


@pytest.fixture(scope="module")
def research_validity_counts(research_sample: SummarySample, factor4_read_repository: Factor4ReadRepository) -> tuple[dict[str, int], int]:
    """Read the complete interval-catalog left-join counts once for each exact research scope."""
    return factor4_read_repository.research_validity_counts(research_sample)


@pytest.mark.parametrize("validity", ["valid", "invalid", "unknown"])
def test_research_explicit_validity_statistics_include_unknown_catalog_entities(
    research_service: Factor4ResearchSearchService, research_sample: SummarySample,
    research_validity_counts: tuple[dict[str, int], int], validity: str,
) -> None:
    """Each explicit status must match total and grouped DB counts, including absent-summary unknowns."""
    counts, ambiguity = research_validity_counts
    _verify(lambda: research_service.check_explicit_validity_stats(research_sample, counts, validity,
                                                                  ambiguous_count=ambiguity))


@pytest.mark.parametrize("shape", ["ts_only", "cs_only"])
def test_overall_research_search_accepts_either_single_valid_dimension(
    research_service: Factor4ResearchSearchService, factor4_read_repository: Factor4ReadRepository, shape: str,
) -> None:
    """TS-only and CS-only final evidence independently qualify for overall valid research search."""
    discovered = factor4_read_repository.one_dimension_research_sample(shape)
    if discovered is None:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no latest one-dimension research fixture {shape}")
    sample, name = discovered
    _verify(lambda: research_service.check_overall_one_dimension(sample, name))
