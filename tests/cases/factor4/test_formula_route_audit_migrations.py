"""临时公式/route 审计脚本的正式业务断言入口。"""

from __future__ import annotations

import pytest

from api.factor_data_mcp_api import FactorDataMCPAPI
from db.factor4_calculation_repository import Factor4CalculationRepository
from service.factor4_calculation_service import Factor4CalculationService


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.fixture(scope="module")
def audit_snapshot(factor4_calculation_repository: Factor4CalculationRepository):
    """读取一个一致的测试环境发布快照；没有发布批次时明确阻断。"""
    partitions = factor4_calculation_repository.list_active_published_partitions()
    if not partitions:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no active published calculation snapshot")
    snapshot = factor4_calculation_repository.read_calculation_snapshot("all", "default")
    if not snapshot.routes:
        pytest.skip("BLOCKED_DATA_PRECONDITION: published snapshot contains no routes")
    return snapshot


def _calc_service(
    factor4_calculation_repository: Factor4CalculationRepository,
    factor_data_mcp_api: FactorDataMCPAPI,
) -> Factor4CalculationService:
    """组装计算审计 Service；所有业务断言均在 Service 中执行。"""

    return Factor4CalculationService(factor4_calculation_repository, factor_data_mcp_api)


@pytest.mark.factor4_internal_calculation
def test_formula_integrity_and_source_chain(
    audit_snapshot,
    factor4_calculation_repository: Factor4CalculationRepository,
    factor_data_mcp_api: FactorDataMCPAPI,
) -> None:
    """显式专项保留历史窗口/AST/归一化公式审计；默认来源链由 R0 结果入口覆盖。"""

    service = _calc_service(factor4_calculation_repository, factor_data_mcp_api)
    integrity = service.check_formula_integrity(audit_snapshot)
    static = service.check_formula_static_consistency(audit_snapshot)
    results = (integrity, static)
    failures = [result for result in results if result.status == "FAIL"]
    assert not failures, [(item.code, item.factor_ref) for result in failures for item in result.findings]
    for result in results:
        if result.status in {"BLOCKED_DATA_PRECONDITION", "BLOCKED_DOC"}:
            pytest.skip(f"{result.case_id}: {result.summary}")
        assert result.status == "PASS", result.status
        assert result.checked_count > 0


@pytest.mark.parametrize("family", ["dpo", "fixed_horizon", "iv_rv"])
@pytest.mark.factor4_internal_calculation
def test_each_known_formula_family_definition_and_exact_evidence(
    audit_snapshot,
    factor4_calculation_repository: Factor4CalculationRepository,
    factor_data_mcp_api: FactorDataMCPAPI,
    family: str,
) -> None:
    """独立验证 DPO、固定周期和 IV/RV 当前定义与 batch-bound 公式。

    已修复定义不会因未被当前指标引用的旧 evidence 误失败；精确公式响应的
    hash/version/run/表达式/输入字段仍必须与其 DB evidence 一致。数据或
    文档前置不足明确 skip，不把另一族的前置缺失归到本用例。
    """

    service = _calc_service(factor4_calculation_repository, factor_data_mcp_api)
    result = service.check_known_formula_regressions(audit_snapshot, family=family)
    if result.status in {"BLOCKED_DATA_PRECONDITION", "BLOCKED_DOC"}:
        reasons = sorted({item.code for item in result.findings})
        pytest.skip(f"{result.case_id}/{family}: {result.summary}; reasons={reasons}")
    assert result.checked_count == {"dpo": 3, "fixed_horizon": 5, "iv_rv": 3}[family]
    assert result.status == "PASS", [(item.code, item.factor_ref) for item in result.findings]


# Historical route-audit / DB-613 registration maps to test_final_results.py:
# identity, domains, evidence, partition, environment matrix/summary and ranking
# already execute these same assertions across all discovered active partitions.
