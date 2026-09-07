"""最终结果候选集合对账，不把未发布的合格候选直接定性为 Bug。"""

import pytest
from collections.abc import Callable
from typing import Any

from db.factor4_calculation_repository import CalculationAuditSnapshot
from service.factor4_result_service import ENVIRONMENT_LABELS
from service.factor4_route_membership_service import Factor4RouteMembershipService


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.mark.parametrize("label_code", ENVIRONMENT_LABELS)
def test_final_metric_candidates_reconcile_with_published_routes(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...], label_code: str,
    record_property: Callable[[str, Any], None],
) -> None:
    """六环境从完整 metric 集合发现候选，逐分区保留差异和阻断原因。"""

    results = [Factor4RouteMembershipService.check_candidates(s, label_code) for s in factor4_closure_snapshots]
    record_property("route_candidate_evidence", [r.evidence for r in results])
    failures = [r for r in results if r.status == "FAIL"]
    assert not failures, [(r.summary, r.findings) for r in failures]
    blocked = [r for r in results if r.status != "PASS"]
    if blocked or not results:
        pytest.skip(f"BLOCKED: candidate membership not fully verified: {[(r.summary, r.findings) for r in blocked]}")
