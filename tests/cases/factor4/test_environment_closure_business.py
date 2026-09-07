"""Live final-result environment closure; absent natural branches are explicit."""

from collections.abc import Sequence
from collections import Counter

import pytest

from db.factor4_calculation_repository import CalculationAuditSnapshot
from service.factor4_calculation_service import CalculationCheckResult
from service.factor4_environment_closure_service import ADMISSION_BRANCHES, Factor4EnvironmentClosureService
from service.factor4_result_service import ENVIRONMENT_LABELS

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _verify(results: Sequence[CalculationCheckResult]) -> None:
    failed = [f for r in results for f in r.findings if f.status == "FAIL"]
    blocked = [f for r in results for f in r.findings if f.status.startswith("BLOCKED")]
    diagnostics = {"checked_count": sum(r.checked_count for r in results), "failure_count": len(failed), "blocked_count": len(blocked)}
    for key, findings in (("failures", failed), ("blocks", blocked)):
        diagnostics[key] = {"by_reason": dict(Counter(f.code for f in findings)),
                            "samples": [(f.code, {name: value for name, value in f.evidence.items()
                                                  if name in {"metric_id", "route_id", "daily_id", "batch_id", "label", "branch", "field", "environment_date"}})
                                        for f in findings[:15]]}
    assert not failed, diagnostics
    if any(result.status != "PASS" for result in results):
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: {diagnostics}")
    assert results and sum(r.checked_count for r in results) > 0, "no final-result evidence audited"


def test_frozen_environment_calendar_is_exclusive_complete_and_point_in_time(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...],
) -> None:
    """Compare frozen date/kind/revision against immutable daily history, not current labels."""
    _verify([Factor4EnvironmentClosureService.check_frozen_calendar(s) for s in factor4_closure_snapshots])


def test_frozen_missing_dates_match_full_range_daily_history_at_batch_as_of(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...],
) -> None:
    """Independently find omitted visible days and falsely declared missing days."""
    _verify([Factor4EnvironmentClosureService.check_frozen_missing_dates(s) for s in factor4_closure_snapshots])


@pytest.mark.parametrize("label", ENVIRONMENT_LABELS)
def test_each_label_metrics_routes_and_summary_share_full_publication_identity(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...], label: str,
) -> None:
    """Reconcile each environment's metrics, summary counts and route foreign keys."""
    _verify([Factor4EnvironmentClosureService.check_label_results(s, label) for s in factor4_closure_snapshots])


@pytest.mark.parametrize("label", ENVIRONMENT_LABELS)
@pytest.mark.parametrize("branch", ADMISSION_BRANCHES)
def test_each_label_ts_cs_admission_branch_and_renormalized_final_route_weights(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...], label: str, branch: str,
) -> None:
    """Audit branch-specific final evidence; any-valid alone does not require a route."""
    _verify([Factor4EnvironmentClosureService.check_admission_branch(s, label, branch) for s in factor4_closure_snapshots])
