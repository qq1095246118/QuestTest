"""Formal Factor 4.0 CALC-513 route/environment reference check."""

from __future__ import annotations

from typing import cast

import pytest

from api.factor_data_mcp_api import FactorDataMCPAPI
from db.factor4_calculation_repository import Factor4CalculationRepository
from service.factor4_calculation_service import (
    CalculationCheckResult,
    Factor4CalculationService,
)


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.fixture(scope="module")
def calc513_check(
    factor4_calculation_repository: Factor4CalculationRepository,
) -> CalculationCheckResult:
    """读取一个一致性 DB 快照并执行 CALC-513 Service 检查。"""

    snapshot = factor4_calculation_repository.read_calculation_snapshot(
        market_scope="all",
        route_profile_key="default",
    )
    # CALC-513 is a DB-backed reference check and does not need an MCP call.
    # The Service constructor is intentionally side-effect free; a typed
    # placeholder keeps this Case independent from the MCP quota/session gate.
    service = Factor4CalculationService(
        factor4_calculation_repository,
        cast(FactorDataMCPAPI, object()),
    )
    return service.check_route_environment_references(snapshot)


def test_factor4_route_environment_references(
    calc513_check: CalculationCheckResult,
) -> None:
    """明确冲突必须失败；缺少正文或未定义语义必须保留结构化阻断。"""

    codes = sorted({finding.code for finding in calc513_check.findings})
    diagnostic = f"{calc513_check.status}: CALC-513; finding_codes={codes}"
    if calc513_check.status in {"BLOCKED_DATA_PRECONDITION", "BLOCKED_DOC"}:
        pytest.skip(diagnostic)
    if calc513_check.status == "FAIL":
        pytest.fail(diagnostic, pytrace=False)
    if calc513_check.status != "PASS":
        pytest.fail(f"unknown CALC-513 status: {calc513_check.status!r}", pytrace=False)
    assert calc513_check.checked_count > 0, diagnostic
