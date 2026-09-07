"""Shared final-state fixtures for Factor 4.0 business cases."""

import pytest

from db.factor4_calculation_repository import CalculationAuditSnapshot, Factor4CalculationRepository
from db.factor4_lifecycle_repository import Factor4LifecycleRepository, LifecycleSnapshot


@pytest.fixture(scope="package")
def lifecycle_repository(factor4_calculation_repository: Factor4CalculationRepository) -> Factor4LifecycleRepository:
    """Reuse a live/test-gated DB client; construction performs no database access."""
    return Factor4LifecycleRepository(factor4_calculation_repository.database_client)


@pytest.fixture(scope="package")
def lifecycle_snapshot(lifecycle_repository: Factor4LifecycleRepository) -> LifecycleSnapshot:
    """Return one consistent lifecycle read; driver errors remain test errors."""
    return lifecycle_repository.snapshot()


@pytest.fixture(scope="package")
def factor4_closure_snapshots(
    factor4_calculation_repository: Factor4CalculationRepository,
) -> tuple[CalculationAuditSnapshot, ...]:
    """Read live/test-gated final partitions once; block publication drift, propagate DB errors.

    No calculation or publication is started. The complete frozen date range's
    environment history is read independently of the member list. Multiple active
    selector matches fail; publication drift cannot certify the old batch.
    """
    repository = factor4_calculation_repository
    partitions = repository.list_active_published_partitions()
    if not partitions:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no active published calculation partition")
    keys = [(part.market_scope, part.route_profile_key) for part in partitions]
    assert len(keys) == len(set(keys)), "multiple active publications for the same selector"
    snapshots = tuple(repository.read_calculation_snapshot(*key, include_full_environment_history=True) for key in keys)
    for part, snapshot in zip(partitions, snapshots, strict=True):
        if (part.id, part.publication_uid, part.publish_version) != (
            snapshot.batch.id, snapshot.batch.publication_uid, snapshot.batch.publish_version,
        ):
            pytest.skip("BLOCKED_DATA_PRECONDITION: publication changed during snapshot discovery")
    return snapshots
