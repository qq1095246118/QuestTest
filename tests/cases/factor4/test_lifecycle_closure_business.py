"""Read-only lifecycle gaps using real repository records, never local fabricated evidence."""

from collections.abc import Callable

import pytest

from db.factor4_lifecycle_repository import LifecycleSnapshot
from service.factor4_lifecycle_closure_service import Factor4LifecycleClosureService
from service.factor4_read_service import ReadCheck, ReadPrecondition

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _verify(action: Callable[[], ReadCheck]) -> None:
    try:
        check = action()
    except ReadPrecondition as error:
        pytest.skip(str(error))
    assert check.checked_count > 0
    assert not check.issues, ", ".join(check.issues[:30])


@pytest.mark.factor4_internal_calculation
def test_distinct_successful_batches_with_same_frozen_inputs_produce_equal_results(lifecycle_snapshot: LifecycleSnapshot) -> None:
    """Compare independent completed calculations only after all actual input manifests match."""
    _verify(lambda: Factor4LifecycleClosureService.check_independent_recalculations(lifecycle_snapshot))


def test_parent_metrics_reconcile_frozen_child_membership_and_relation_versions(lifecycle_snapshot: LifecycleSnapshot) -> None:
    """Compare persisted parent evidence to the same batch's immutable children and relation version."""
    _verify(lambda: Factor4LifecycleClosureService.check_parent_relation_evidence(lifecycle_snapshot))


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "rolled_back"])
def test_unsuccessful_publication_terminal_states_do_not_own_active_pointers(lifecycle_snapshot: LifecycleSnapshot, outcome: str) -> None:
    """Check real final state only; this case does not claim observation of transaction atomicity."""
    _verify(lambda: Factor4LifecycleClosureService.check_unsuccessful_publication_final_state(lifecycle_snapshot, outcome))


def test_tied_routes_follow_only_the_producer_declared_ranking_contract(lifecycle_snapshot: LifecycleSnapshot) -> None:
    """Use explicit frozen tie-breaking rules; do not define a factor-ID fallback."""
    _verify(lambda: Factor4LifecycleClosureService.check_declared_tie_breaker(lifecycle_snapshot))
