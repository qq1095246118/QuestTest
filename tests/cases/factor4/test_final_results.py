"""Factor 4.0 最终结果级验收。

本文件故意不读取原始因子值、forward return、逐 bar 持仓或收益。它验证数据库已
发布的最终 metric/route 结果是否自洽，并把“能否独立重算”与“结果字段是否矛盾”分开。
"""

from __future__ import annotations

import pytest

from db.factor4_calculation_repository import Factor4CalculationRepository
from service.factor4_calculation_service import Factor4CalculationService
from service.factor4_result_service import Factor4FinalResultService


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.fixture(scope="module")
def published_snapshots(
    factor4_calculation_repository: Factor4CalculationRepository,
):
    """动态发现所有 active published 分区并读取最终结果快照。"""

    partitions = factor4_calculation_repository.list_active_published_partitions()
    if not partitions:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no active published result partition")
    return tuple(
        (
            partition,
            factor4_calculation_repository.read_calculation_snapshot(
                partition.market_scope, partition.route_profile_key
            ),
        )
        for partition in partitions
    )


def test_final_result_numeric_domains_are_valid(published_snapshots) -> None:
    """最终分数、置信度、覆盖率和 IC 摘要不能出现 NaN/Inf 或越界值。"""

    auditor = Factor4FinalResultService()
    failures = []
    checked = 0
    for partition, snapshot in published_snapshots:
        result = auditor.check_value_domains(snapshot)
        checked += result.checked_count
        if result.status == "FAIL":
            failures.append((partition.route_profile_key, result.summary, [f.code for f in result.findings]))
    assert checked > 0, "BLOCKED_DATA_PRECONDITION: no final metric or route result"
    assert not failures, failures


def test_final_route_evidence_matches_persisted_result_columns(published_snapshots) -> None:
    """验证最终 route evidence 与 route 列的分数、置信度和准入范围一致。"""

    auditor = Factor4FinalResultService()
    failures = []
    checked = 0
    for partition, snapshot in published_snapshots:
        result = auditor.check_route_evidence_consistency(snapshot)
        checked += result.checked_count
        if result.status == "FAIL":
            failures.append((partition.route_profile_key, result.summary, [f.code for f in result.findings]))
    assert checked > 0, "BLOCKED_DATA_PRECONDITION: no final route evidence"
    assert not failures, failures


def test_final_environment_matrix_uses_declared_labels(published_snapshots) -> None:
    """验证最终 route 标签只能来自当前批次声明成功的环境矩阵。"""

    auditor = Factor4FinalResultService()
    failures = []
    for partition, snapshot in published_snapshots:
        result = auditor.check_environment_matrix(snapshot)
        if result.status == "FAIL":
            failures.append((partition.route_profile_key, result.summary, [f.code for f in result.findings]))
    assert not failures, failures


def test_active_publication_selector_is_unique(published_snapshots) -> None:
    """验证动态发现的 active publication 选择器不重复。"""

    selectors = [
        (partition.market_scope, partition.route_profile_key)
        for partition, _snapshot in published_snapshots
    ]
    assert len(selectors) == len(set(selectors)), selectors


def test_final_result_ranking_is_partitioned_and_repeatable(
    published_snapshots,
    factor4_calculation_repository: Factor4CalculationRepository,
) -> None:
    """只用已发布 rank/score 结果验证分区、连续性、降序和重复读取一致。"""

    failures = []
    blocked = []
    checked = 0
    for partition, snapshot in published_snapshots:
        repeated = factor4_calculation_repository.read_published_route_snapshot(
            partition.market_scope, partition.route_profile_key
        )
        result = Factor4CalculationService.check_final_result_ranking(snapshot, repeated)
        checked += result.checked_count
        if result.status == "FAIL":
            failures.append((partition.route_profile_key, result.summary, [f.code for f in result.findings]))
        elif result.status != "PASS":
            blocked.append((partition.route_profile_key, result.summary, [f.code for f in result.findings]))
    assert not failures, failures
    if blocked:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: ranking not fully verified: {blocked}")
    if not checked:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no active eligible route ranking")
