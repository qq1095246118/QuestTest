"""Final-state lifecycle business cases; no creation, publication or DB mutation."""

from collections.abc import Callable

import pytest

from db.factor4_lifecycle_repository import Factor4LifecycleRepository, LifecycleSnapshot
from service.factor4_lifecycle_service import Factor4LifecycleService
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition

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


@pytest.mark.factor4_technical
def test_publication_mode_is_defined_before_atomicity_acceptance(lifecycle_snapshot: LifecycleSnapshot) -> None:
    """LIFE-400: use producer-declared mode/visibility/history semantics, never a fixed false gate."""
    _verify(lambda: Factor4LifecycleService().check_publication_mode_contract(lifecycle_snapshot))


@pytest.mark.parametrize("historical", [False, True], ids=["current-invariants", "superseded-history"])
def test_route_history_retains_identity_and_deactivates_old_versions(lifecycle_snapshot: LifecycleSnapshot, historical: bool) -> None:
    """DB-604: route/batch/metric identity plus real superseded history when available."""
    _verify(lambda: Factor4LifecycleService().check_route_history(lifecycle_snapshot, require_superseded=historical))


@pytest.mark.parametrize("parents", [False, True], ids=["direct-subfactors", "parent-children"])
def test_terminal_metrics_use_frozen_factor_membership(lifecycle_snapshot: LifecycleSnapshot, parents: bool) -> None:
    """CALC-511: final metrics use frozen definition identities and config versions."""
    _verify(lambda: Factor4LifecycleService().check_frozen_membership(lifecycle_snapshot, parents=parents))


@pytest.mark.parametrize("stage", ["market_environment_daily", "market_environment_eval_batch",
                                  "market_environment_factor_metric", "market_environment_factor_route", "permission_rejection"])
@pytest.mark.factor4_technical
def test_lifecycle_audit_correlation_actor_time_and_versions(lifecycle_snapshot: LifecycleSnapshot, stage: str) -> None:
    """DB-606: audit aggregate counts verify fields without exposing actor/request values."""
    _verify(lambda: Factor4LifecycleService().check_audit_fields(lifecycle_snapshot, stage))


def test_terminal_same_batch_results_are_stable_across_reads(
    lifecycle_repository: Factor4LifecycleRepository, lifecycle_snapshot: LifecycleSnapshot,
) -> None:
    """CALC-512 read branch only; does not claim a second computation was executed."""
    second = lifecycle_repository.snapshot()
    _verify(lambda: Factor4LifecycleService().check_terminal_replay(lifecycle_snapshot, second))


@pytest.mark.factor4_technical
def test_factor4_entity_schema_preserves_identity_and_revision_uniqueness(lifecycle_repository: Factor4LifecycleRepository) -> None:
    """DB-601/602: five entity identities and the physical daily revision key exist."""
    _verify(lambda: Factor4LifecycleService().check_entity_schema(lifecycle_repository.schema_inventory()))


def test_all_final_metric_units_are_unique_and_reference_existing_batches(lifecycle_snapshot: LifecycleSnapshot) -> None:
    """DB-603: actual formal metric dimensions, not just factor ID, define uniqueness."""
    _verify(lambda: Factor4LifecycleService().check_metric_units(lifecycle_snapshot))


def test_batch_terminal_timestamps_and_metric_counts_are_consistent(lifecycle_snapshot: LifecycleSnapshot) -> None:
    """DB-613: final times exist and terminal counters never exceed the frozen expected count."""
    _verify(lambda: Factor4LifecycleService().check_batch_terminal_counts(lifecycle_snapshot))


def test_double_invalid_factors_remain_available_in_catalog_details(
    lifecycle_snapshot: LifecycleSnapshot, factor4_read_service: Factor4ReadService,
) -> None:
    """MET-311: routing exclusion must not make the underlying factor definition disappear."""
    _verify(lambda: Factor4LifecycleService().check_ineligible_factors_remain_queryable(lifecycle_snapshot, factor4_read_service.api.mcp))


@pytest.mark.factor4_deferred
def test_payload_columns_do_not_expose_complete_mcp_token_shapes(lifecycle_repository: Factor4LifecycleRepository) -> None:
    """DB-609 deferred scan: credential-shaped values, not harmless authorization/password words."""
    _verify(lambda: Factor4LifecycleService().check_credential_exposure(lifecycle_repository.credential_exposure_counts()))
