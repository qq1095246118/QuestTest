"""Real final-result economics components; no fabricated raw-bar replay fixtures."""

from __future__ import annotations

import pytest

from db.factor4_calculation_repository import CalculationAuditSnapshot
from db.factor4_lifecycle_repository import LifecycleSnapshot
from service.factor4_economics_closure_service import Component, Factor4EconomicsClosureService


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.mark.parametrize("component", ["economics", "penalty", "oos"])
def test_final_economic_components_recompute_without_unrelated_ic_inputs(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...], component: Component,
) -> None:
    """Recompute E/P/OOS for every live partition; failures outrank missing evidence.

    The package fixture supplies test-gated, read-only active publications. No
    raw returns or fold rows are invented. Missing real component projections
    skip explicitly, and an actual arithmetic contradiction always fails first.
    """

    service = Factor4EconomicsClosureService()
    results = tuple(service.check_component(snapshot, component) for snapshot in factor4_closure_snapshots)
    issues = [issue for result in results for issue in result.issues]
    assert not issues, issues[:30]
    blocked = [reason for result in results for reason in result.blocked]
    if blocked:
        pytest.skip("BLOCKED_DATA_PRECONDITION: " + "; ".join(blocked[:10]))
    if not sum(result.checked_count for result in results):
        pytest.skip("BLOCKED_DATA_PRECONDITION: no comparable final economics component")


@pytest.mark.parametrize("scope", ["time_series", "cross_sectional"])
def test_persisted_oos_fold_counts_durations_and_directions_match_frozen_batch(
    lifecycle_snapshot: LifecycleSnapshot, scope: str,
) -> None:
    """Compare real fold members/config and directed IC, without inferring aggregate weights."""
    result = Factor4EconomicsClosureService().check_oos_fold_configuration(lifecycle_snapshot, scope)
    assert not result.issues, result.issues[:30]
    if result.blocked:
        status = "BLOCKED_DOC" if all(reason.startswith("BLOCKED_DOC:") for reason in result.blocked) else "BLOCKED_DATA_PRECONDITION"
        pytest.skip(status + ": " + "; ".join(result.blocked[:10]))
    assert result.checked_count > 0
