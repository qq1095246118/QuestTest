"""Complete validity, partial evidence, mixed batches and temporal request boundaries."""

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api.factor4_summary_api import Factor4SummaryAPI
from db.factor4_read_repository import Factor4ReadRepository, ValiditySample
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition
from service.factor4_summary_service import Factor4SummaryService
from service.factor4_slice_service import Factor4SliceService
from service.factor4_validity_service import Factor4ValidityService, Endpoint
from service.factor4_run_selection_service import Factor4RunSelectionService

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
def validity_service(factor4_read_service: Factor4ReadService) -> Factor4ValidityService:
    """Use the shared initialized test-only MCP session; initialization failures propagate."""
    return Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(factor4_read_service.api.mcp)))


@pytest.fixture(scope="module", params=["ts_only", "cs_only", "both"])
def validity_shape(request: pytest.FixtureRequest, factor4_read_repository: Factor4ReadRepository) -> ValiditySample:
    """Discover each natural valid shape without requiring both dimensions to pass."""
    sample = factor4_read_repository.validity_sample(request.param)
    if sample is None:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: missing complete validity shape {request.param}")
    return sample


@pytest.fixture(scope="module")
def aggregate_latest(factor4_read_repository: Factor4ReadRepository) -> ValiditySample:
    """Use the newest member of an independently discovered same-partition multi-run pair."""
    history = factor4_read_repository.validity_history()
    if not history:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no completed aggregate validity history")
    return history[0]


@pytest.mark.parametrize("endpoint", ["metrics", "validity"])
@pytest.mark.parametrize("scope", ["ts", "cs"])
def test_each_validity_shape_preserves_exact_ts_cs_evidence(
    validity_service: Factor4ValidityService, validity_shape: ValiditySample, scope: str, endpoint: Endpoint,
) -> None:
    """TS-only, CS-only and both-valid samples expose the exact persisted metric/status/score."""
    _verify(lambda: validity_service.check_exact_evidence(validity_shape, scope, endpoint))


def test_primary_valid_dimension_slices_match_same_run_database(
    validity_service: Factor4ValidityService, validity_shape: ValiditySample,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """Each validity shape's usable dimension is linked to actual persisted slice evidence."""
    scope = "ts" if validity_shape.validity.get("time_series_is_valid") else "cs"
    sample = factor4_read_repository.slices_for_summary(validity_shape.summaries[scope], discover_symbol=scope == "ts")
    if sample is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: primary valid dimension has no persisted slices")
    _verify(lambda: Factor4SliceService(validity_service.summaries).check_snapshot_pages(factor4_read_repository, sample))


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_explicit_slice_run_visibility_at_completion(
    validity_service: Factor4ValidityService, aggregate_latest: ValiditySample,
    factor4_read_repository: Factor4ReadRepository, offset: int,
) -> None:
    """Slice lookup follows the same explicit Run's completion availability, independently of validity."""
    sample = factor4_read_repository.slices_for_summary(aggregate_latest.summaries["ts"], discover_symbol=True)
    completed = aggregate_latest.validity.get("run_completed_at")
    if sample is None or not isinstance(completed, datetime):
        pytest.skip("BLOCKED_DATA_PRECONDITION: no exact slice series with run completion time")
    completed = completed.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if completed.tzinfo is None else completed
    _verify(lambda: Factor4SliceService(validity_service.summaries).check_run_completion(sample, completed, offset))


@pytest.mark.parametrize("endpoint", ["metrics", "validity"])
@pytest.mark.parametrize("variant", ["symbol_omitted", "run_omitted", "run_null"])
def test_optional_query_fields_preserve_aggregate_and_latest_identity(
    validity_service: Factor4ValidityService, aggregate_latest: ValiditySample, endpoint: Endpoint, variant: str,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """Omitting aggregate symbol or optional run does not mix factor, scope or historical cycles."""
    if variant in {"run_omitted", "run_null"}:
        _verify(lambda: Factor4RunSelectionService(validity_service.summaries).check_default_argument_variant(
            factor4_read_repository, aggregate_latest, endpoint, variant))
    else:
        _verify(lambda: validity_service.check_argument_variant(aggregate_latest, endpoint, variant))


@pytest.mark.factor4_deferred
@pytest.mark.parametrize("endpoint", ["metrics", "validity"])
@pytest.mark.parametrize("variant", ["symbol_null", "symbol_wrong", "run_wrong", "as_of_omitted", "as_of_null",
                                     "version_wrong", "factor_ref_omitted", "scope_bad", "unexpected_argument"])
def test_invalid_query_and_nullable_symbol_do_not_expose_unrequested_evidence(
    validity_service: Factor4ValidityService, aggregate_latest: ValiditySample, endpoint: Endpoint, variant: str,
) -> None:
    """Deferred invalid/compatibility matrix has real requests and rejection/data assertions."""
    _verify(lambda: validity_service.check_argument_variant(aggregate_latest, endpoint, variant))


@pytest.mark.parametrize("endpoint", ["metrics", "validity"])
@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_metric_and_validity_completion_point_in_time_boundary(
    validity_service: Factor4ValidityService, aggregate_latest: ValiditySample, endpoint: Endpoint, offset: int,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """Metrics use completion; validity uses max(completion, validity publication), at -1/0/+1us."""
    _verify(lambda: validity_service.check_completion_boundary(
        aggregate_latest, endpoint, offset, repository=factor4_read_repository))


@pytest.mark.parametrize("endpoint", ["metrics", "validity"])
def test_existing_and_absent_factor_batch_keep_item_identity_and_error_isolation(
    validity_service: Factor4ValidityService, aggregate_latest: ValiditySample, endpoint: Endpoint,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """Valid batch item matches DB while a verified absent factor remains a local not-found."""
    missing = factor4_read_repository.absent_factor_ref()
    _verify(lambda: validity_service.check_batch_error_isolation(aggregate_latest, endpoint, missing))


@pytest.mark.factor4_deferred
@pytest.mark.parametrize("duplicate", [False, True], ids=["empty", "duplicate"])
def test_metric_batch_empty_and_duplicate_input_contract(
    validity_service: Factor4ValidityService, aggregate_latest: ValiditySample, duplicate: bool,
) -> None:
    """Deferred malformed-batch contracts send actual requests and verify reject/dedup semantics."""
    _verify(lambda: validity_service.check_metric_batch_input(aggregate_latest, duplicate=duplicate))


def test_two_existing_validity_factors_and_absent_ref_preserve_single_batch_values(
    validity_service: Factor4ValidityService, aggregate_latest: ValiditySample,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """A mixed CS validity batch must retain two independent factor/Run/score identities."""
    peers = factor4_read_repository.validity_peers(aggregate_latest)
    missing = factor4_read_repository.absent_factor_ref()
    _verify(lambda: validity_service.check_validity_batch_peers(peers, missing))


@pytest.mark.parametrize("scope", ["ts", "cs"])
@pytest.mark.parametrize("explicit_run", [True, False], ids=["explicit_run", "latest_run"])
def test_incomplete_validity_never_exposes_unsupported_valid_evidence(
    validity_service: Factor4ValidityService, factor4_read_repository: Factor4ReadRepository,
    scope: str, explicit_run: bool,
) -> None:
    """Historical missing FKs are observations unless MCP exposes unsupported summary evidence."""
    samples = factor4_read_repository.incomplete_route_validity()
    if not samples:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no naturally incomplete active-route validity rows")
    for sample in samples:
        _verify(lambda: validity_service.check_incomplete_visibility(factor4_read_repository, sample, scope,
                                                                    explicit_run=explicit_run))


@pytest.mark.parametrize("scope", ["ts", "cs"])
def test_metrics_remain_independent_of_incomplete_validity_foreign_keys(
    validity_service: Factor4ValidityService, factor4_read_repository: Factor4ReadRepository, scope: str,
) -> None:
    """Missing validity foreign keys neither manufacture nor erase independent same-run metrics."""
    samples = factor4_read_repository.incomplete_route_validity()
    if not samples:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no naturally incomplete active-route validity rows")
    for sample in samples:
        _verify(lambda: validity_service.check_incomplete_metrics(factor4_read_repository, sample, scope))


def test_incomplete_validity_and_absent_factor_remain_isolated_in_batch(
    validity_service: Factor4ValidityService, factor4_read_repository: Factor4ReadRepository,
) -> None:
    """An incomplete item may be correctly hidden, but must not hide/corrupt another batch result."""
    samples = factor4_read_repository.incomplete_route_validity()
    if not samples:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no naturally incomplete active-route validity rows")
    missing = factor4_read_repository.absent_factor_ref()
    for sample in samples:
        _verify(lambda: validity_service.check_incomplete_batch(factor4_read_repository, sample, missing))
