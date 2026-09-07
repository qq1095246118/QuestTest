"""Run selection and three independent persisted slice scopes, migrated from probes."""

from collections.abc import Callable

import pytest

from api.factor4_summary_api import Factor4SummaryAPI
from db.factor4_read_repository import Factor4ReadRepository, SliceSample, ValiditySample
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition
from service.factor4_run_selection_service import Factor4RunSelectionService
from service.factor4_slice_service import Factor4SliceService
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
def summary_service(factor4_read_service: Factor4ReadService) -> Factor4SummaryService:
    """Use the shared live/test-gated MCP session; initialization errors propagate."""
    return Factor4SummaryService(Factor4SummaryAPI(factor4_read_service.api.mcp))


@pytest.fixture(scope="module")
def run_history(factor4_read_repository: Factor4ReadRepository) -> tuple[ValiditySample, ...]:
    """Discover a natural same-partition pair; DB errors propagate rather than skip."""
    return factor4_read_repository.validity_history()


@pytest.mark.parametrize("scope", ["ts", "cs"])
@pytest.mark.parametrize("older", [False, True], ids=["latest_implicit", "old_explicit"])
def test_same_partition_metrics_and_validity_select_expected_run(
    summary_service: Factor4SummaryService, run_history: tuple[ValiditySample, ...], scope: str, older: bool,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """Metrics and validity independently select default results; explicit old Run remains exact."""
    _verify(lambda: Factor4RunSelectionService(summary_service).check_run_selection(
        run_history, scope, older=older, repository=factor4_read_repository))


@pytest.mark.parametrize("scope,symbol_mode", [("time_series", "symbol"), ("time_series", "aggregate"),
                                              ("cross_sectional", "aggregate")],
                         ids=["ts_symbol", "ts_aggregate", "cs_aggregate"])
def test_three_exact_slice_scopes_reconcile_all_pages(
    summary_service: Factor4SummaryService, factor4_read_repository: Factor4ReadRepository,
    scope: str, symbol_mode: str,
) -> None:
    """Each exact scope must return all persisted members across bounded seven-row pages."""
    sample = factor4_read_repository.slice_sample(scope, symbol_mode=symbol_mode, min_rows=8)
    if sample is None or len(sample.rows) < 8:
        pytest.skip("BLOCKED_DATA_PRECONDITION: exact slice scope needs at least eight persisted rows")
    _verify(lambda: Factor4SliceService(summary_service).check_snapshot_pages(factor4_read_repository, sample, repeat=True))


def test_omitted_run_slices_belong_to_latest_same_partition_run(
    summary_service: Factor4SummaryService,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """Default slice Run follows its exact-symbol summary, independently of complete validity FKs."""
    _verify(lambda: Factor4RunSelectionService(summary_service).check_default_slice_run(factor4_read_repository))


@pytest.fixture(scope="module")
def related_slices(factor4_read_repository: Factor4ReadRepository) -> dict[str, SliceSample]:
    """Discover sibling scopes in the same factor/run; genuine absent shapes remain absent."""
    return factor4_read_repository.related_slice_samples()


@pytest.mark.parametrize("source,target", [("ts_symbol", "ts_aggregate"), ("ts_aggregate", "cs_aggregate"),
                                          ("cs_aggregate", "ts_symbol")])
@pytest.mark.factor4_deferred
def test_slice_cursor_cannot_cross_related_scopes(
    summary_service: Factor4SummaryService, related_slices: dict[str, SliceSample], source: str, target: str,
) -> None:
    """A real signed cursor cannot select rows from another TS/CS/symbol shape."""
    if source not in related_slices or target not in related_slices:
        pytest.skip("BLOCKED_DATA_PRECONDITION: related source or target slice scope is absent")
    _verify(lambda: Factor4SliceService(summary_service).check_cursor_binding(
        related_slices[source], target=related_slices[target]))


@pytest.mark.parametrize("mutation", ["limit", "symbol", "tamper"])
@pytest.mark.factor4_deferred
def test_slice_cursor_is_bound_to_query_and_signature(
    summary_service: Factor4SummaryService, related_slices: dict[str, SliceSample], mutation: str,
) -> None:
    """Limit changes, symbol changes and a modified signature must be explicitly rejected."""
    if "ts_symbol" not in related_slices:
        pytest.skip("BLOCKED_DATA_PRECONDITION: persisted TS-symbol slices are absent")
    _verify(lambda: Factor4SliceService(summary_service).check_cursor_binding(
        related_slices["ts_symbol"], mutation=mutation))


@pytest.mark.factor4_deferred
def test_exact_slice_end_equality_returns_contained_database_rows(
    summary_service: Factor4SummaryService, factor4_read_repository: Factor4ReadRepository,
) -> None:
    """Previously excluded end-time equality is implemented but not run by default."""
    sample = factor4_read_repository.slice_sample("time_series")
    if sample is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no persisted exact slice-end fixture")
    _verify(lambda: Factor4SliceService(summary_service).check_exact_slice_end_boundary(sample))
