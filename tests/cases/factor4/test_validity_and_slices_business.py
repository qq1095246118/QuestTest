"""Validity and persisted metric-slice business assertions for Factor 4.0."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.factor4_summary_api import Factor4SummaryAPI
from db.factor4_read_repository import Factor4ReadRepository, SliceSample, ValiditySample
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition, read_tool_page
from service.factor4_summary_service import Factor4SummaryService
from service.factor4_run_selection_service import Factor4RunSelectionService

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _verify(action) -> None:
    """Fail on business mismatch; skip only when test data is genuinely absent."""
    try:
        result: ReadCheck = action()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))
    except ReadContractError as exc:
        pytest.fail(str(exc), pytrace=False)
    assert result.checked_count > 0
    assert not result.issues, ", ".join(result.issues[:20])


@pytest.fixture(scope="module")
def summary_service(factor4_read_service: Factor4ReadService) -> Factor4SummaryService:
    """Build summary service on the shared, initialized MCP session."""
    return Factor4SummaryService(Factor4SummaryAPI(factor4_read_service.api.mcp))


@pytest.fixture(scope="module", params=["any", "ts_only", "cs_only", "both"])
def validity_sample(request: pytest.FixtureRequest, factor4_read_repository: Factor4ReadRepository) -> ValiditySample:
    """Discover each available validity shape from the test database."""
    sample = factor4_read_repository.validity_sample(request.param)
    if sample is None:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no complete validity shape {request.param}")
    return sample


@pytest.fixture(scope="module", params=["time_series", "cross_sectional"])
def slice_sample(request: pytest.FixtureRequest, factor4_read_repository: Factor4ReadRepository) -> SliceSample:
    """Discover a persisted slice series for TS and CS independently."""
    sample = factor4_read_repository.slice_sample(request.param)
    if sample is None:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no persisted {request.param} slice series")
    return sample


@pytest.mark.parametrize("scope", ["ts", "cs"])
def test_validity_matches_database_identity_status_and_scores(
    summary_service: Factor4SummaryService, validity_sample: ValiditySample, scope: str,
) -> None:
    """factor_get_validity returns the selected DB row, same run and validity flags."""
    _verify(lambda: summary_service.check_validity(validity_sample, scope, as_of=datetime.now(timezone.utc).isoformat(), explicit_run=True))


def test_validity_omitted_run_id_selects_latest_record(
    summary_service: Factor4SummaryService, validity_sample: ValiditySample,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """Default Run is resolved independently of historical shape/valid-flag sampling."""
    def check() -> ReadCheck:
        as_of = datetime.now(timezone.utc)
        candidates = factor4_read_repository.validity_candidates(validity_sample, "ts", as_of)
        latest = Factor4RunSelectionService.latest_validity(candidates, validity_sample, "ts", as_of)
        return summary_service.check_validity(latest, "ts", as_of=as_of.isoformat())
    _verify(check)


def test_validity_batch_preserves_factor_membership(summary_service: Factor4SummaryService, validity_sample: ValiditySample) -> None:
    """Batch validity returns the requested factor and its same-row identity."""
    _verify(lambda: summary_service.check_validity_batch(validity_sample, explicit_run=True))


def test_validity_explicit_run_id_selects_requested_revision(summary_service: Factor4SummaryService, factor4_read_repository: Factor4ReadRepository) -> None:
    """When two historical rows exist, explicit run_id must not fall back to newest."""
    history = factor4_read_repository.validity_history()
    if len(history) < 2 or history[0].validity.get("factor_id") != history[1].validity.get("factor_id"):
        pytest.skip("BLOCKED_DATA_PRECONDITION: fewer than two validity revisions for one factor")
    _verify(lambda: summary_service.check_validity(history[1], "ts", explicit_run=True))


def test_metric_slices_match_database_membership_and_values(summary_service: Factor4SummaryService, slice_sample: SliceSample) -> None:
    """factor_get_metric_slices is reconciled against persisted rows, including boundaries and values."""
    _verify(lambda: summary_service.check_metric_slices(slice_sample))


def test_metric_slices_are_bounded_by_requested_limit(summary_service: Factor4SummaryService, slice_sample: SliceSample) -> None:
    """Limit one preserves exact Run/symbol identity and reconciles every continuation row."""
    _verify(lambda: summary_service.check_metric_slices(slice_sample, limit=1))
