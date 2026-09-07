"""Final temporal evidence migrated from temporal_oracle_closure.py, excluding raw replay."""

from collections.abc import Callable

import pytest

from db.factor4_lifecycle_repository import LifecycleSnapshot
from db.factor4_read_repository import Factor4ReadRepository
from service.factor4_read_service import LABELS, ReadCheck, ReadContractError, ReadPrecondition
from service.factor4_temporal_service import Factor4TemporalService

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


@pytest.mark.parametrize("label", LABELS)
def test_frozen_environment_dates_segments_and_metric_sample_counts(
    lifecycle_snapshot: LifecycleSnapshot, factor4_read_repository: Factor4ReadRepository, label: str,
) -> None:
    """CALC-502/503: independently rebuild final label membership, date segments and PIT revisions."""
    daily = factor4_read_repository.daily_snapshot()
    _verify(lambda: Factor4TemporalService().check_environment_partitions(lifecycle_snapshot, daily, label=label))


def test_final_oos_periods_and_direction_are_not_after_batch_asof(lifecycle_snapshot: LifecycleSnapshot) -> None:
    """CALC-503: period bounds, OOS non-overlap/continuity and direction freeze from final evidence."""
    _verify(lambda: Factor4TemporalService().check_result_time_boundaries(lifecycle_snapshot))
