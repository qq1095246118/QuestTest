"""由 tmp 只读探针迁移的正式 Factor 4.0 Case。

这些 Case 不调用脚本、不读取历史报告，也不写数据库。fixture 只动态发现测试库中
存在的因子/批次/修订；没有自然样本时按数据前置阻断，而不是伪造 PASS。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import pytest

from db.factor4_read_repository import CatalogSubset, DailyReadSnapshot, EnvironmentReadMatrixSnapshot, Factor4ReadRepository
from service.factor4_read_service import (
    Factor4ReadService,
    ReadCheck,
    ReadContractError,
    ReadPrecondition,
    visible_daily_rows,
)

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]
_T = TypeVar("_T")


def _execute(action: Callable[[], _T]) -> _T:
    """前置缺失 skip，契约失败 fail；不让远端响应正文进入失败 traceback。"""
    try:
        return action()
    except ReadPrecondition as error:
        pytest.skip(str(error))
    except ReadContractError as error:
        pytest.fail(str(error), pytrace=False)


def _assert_check(check: ReadCheck) -> None:
    """统一断言正式 Case 的结构化差异；错误只含字段/ID，不输出响应正文。"""
    assert check.checked_count > 0
    assert not check.issues, "Factor 4.0 read-only reconciliation failed: " + ", ".join(check.issues[:20])


def _skip_precondition(error: ReadPrecondition) -> None:
    """把明确数据/依赖前置转成可检索的 pytest skip。"""
    pytest.skip(str(error))


@pytest.fixture(scope="module")
def read_service(factor4_read_service: Factor4ReadService) -> Factor4ReadService:
    """返回已握手的只读业务 Service；真实环境门禁由全局 fixture 负责。"""
    return factor4_read_service


@pytest.fixture(scope="module")
def catalog_subfactor(factor4_read_repository: Factor4ReadRepository) -> CatalogSubset:
    """动态选择 4～20 个 sub-factor 目录成员。"""
    subset = factor4_read_repository.catalog_subset("sub_factor")
    if subset is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no bounded sub_factor catalog subset")
    return subset


@pytest.fixture(scope="module", params=["sub_factor", "factor"], ids=["sub_factor", "factor"])
def catalog_subset(request: pytest.FixtureRequest, factor4_read_repository: Factor4ReadRepository) -> CatalogSubset:
    """每种实体类型独立发现样本；母因子缺样本不阻断子因子 Case。"""
    subset = factor4_read_repository.catalog_subset(request.param)
    if subset is None:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no bounded {request.param} catalog subset")
    return subset


@pytest.fixture(scope="module")
def daily_snapshot(factor4_read_repository: Factor4ReadRepository) -> DailyReadSnapshot:
    """捕获一次数据库环境快照，所有 daily Case 使用相同查询基线。"""
    return factor4_read_repository.daily_snapshot()


@pytest.fixture(scope="module")
def environment_output_matrix(factor4_read_repository: Factor4ReadRepository) -> EnvironmentReadMatrixSnapshot:
    """Capture all active partitions and real parent/subfactor shape representatives once."""
    return factor4_read_repository.environment_matrix_snapshot()


def _assert_environment_matrix(check: ReadCheck) -> None:
    """Report confirmed output differences before missing shapes or snapshot drift."""
    assert not check.issues, ", ".join(check.issues[:30])
    blocked = check.evidence.get("blocked")
    if blocked:
        pytest.skip(f"BLOCKED_DATA_OR_SNAPSHOT: {blocked}")
    assert check.checked_count > 0


def test_catalog_pagination_and_database_membership(
    read_service: Factor4ReadService,
    catalog_subset: CatalogSubset,
) -> None:
    """MCP-006/MCP-018：动态筛选完整分页、身份、状态和 category 与 DB 对账。"""
    subset = catalog_subset
    try:
        traversal = read_service.catalog_pages(subset)
    except ReadPrecondition as error:
        _skip_precondition(error)
    _assert_check(read_service.check_catalog_members(subset, traversal))


def test_catalog_stats_group_counts_are_internally_consistent(
    read_service: Factor4ReadService,
    catalog_subset: CatalogSubset,
) -> None:
    """目录统计各分组总和等于 total；不把 research count 与 library count 混用。"""
    subset = catalog_subset
    try:
        _assert_check(read_service.check_catalog_stats(subset))
    except ReadPrecondition as error:
        _skip_precondition(error)


def test_catalog_repeat_read_is_stable(read_service: Factor4ReadService, catalog_subfactor: CatalogSubset) -> None:
    """MCP-018 子项：同一筛选串行重读的排序、成员和字段一致；不是并发测试。"""
    try:
        initial = read_service.catalog_pages(catalog_subfactor)
        _assert_check(read_service.check_catalog_replay(catalog_subfactor, initial))
    except ReadPrecondition as error:
        _skip_precondition(error)


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_catalog_updated_after_boundary(
    read_service: Factor4ReadService, catalog_subfactor: CatalogSubset, offset: int,
) -> None:
    """MCP-006：updated_after 前/等/后边界严格按 > 过滤且不丢页。"""
    try:
        _assert_check(read_service.check_catalog_updated_boundary(catalog_subfactor, offset))
    except ReadPrecondition as error:
        _skip_precondition(error)


@pytest.mark.parametrize("detail_level", ["summary", "definition", "executable"], ids=["summary", "definition", "executable"])
def test_single_and_batch_factor_details_are_identical(
    read_service: Factor4ReadService, catalog_subfactor: CatalogSubset, detail_level: str,
) -> None:
    """MCP-005：single/batch 详情的因子身份和业务投影一致。"""
    _assert_check(_execute(lambda: read_service.check_details_batch(catalog_subfactor, detail_level)))


def test_executable_detail_common_fields_are_consistent(
    read_service: Factor4ReadService, catalog_subfactor: CatalogSubset,
) -> None:
    """MCP-005：executable 详情的公共定义字段在 single/batch envelope 中一致。"""
    _assert_check(_execute(lambda: read_service.check_details_executable_common_fields(catalog_subfactor)))


def test_catalog_exact_query_preserves_filter_identity(
    read_service: Factor4ReadService, catalog_subfactor: CatalogSubset,
) -> None:
    """MCP-006：按已发现名称精确 query 时必须返回该实体，不能退化成无筛选目录。"""
    row = catalog_subfactor.rows[0]
    query = row.get("name")
    if not isinstance(query, str) or not query:
        pytest.skip("BLOCKED_DATA_PRECONDITION: discovered catalog row has no searchable name")
    traversal = _execute(lambda: read_service.catalog_query(catalog_subfactor, query))
    _assert_check(read_service.check_catalog_query(catalog_subfactor, query, traversal))


@pytest.mark.parametrize("label_kind", ["fact", "forecast"], ids=["fact", "forecast"])
def test_environment_daily_is_point_in_time_and_db_consistent(
    read_service: Factor4ReadService, daily_snapshot: DailyReadSnapshot, label_kind: str,
) -> None:
    """ENV-101/102/104/107/109：环境分页只返回同 kind 的可见最高修订。"""
    if not visible_daily_rows(daily_snapshot, label_kind):
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no current {label_kind} rows")
    try:
        traversal = read_service.daily_pages(daily_snapshot, label_kind)  # type: ignore[arg-type]
        _assert_check(read_service.check_daily(daily_snapshot, label_kind, traversal))  # type: ignore[arg-type]
    except ReadPrecondition as error:
        _skip_precondition(error)


@pytest.mark.parametrize("label_kind", ["fact", "forecast"], ids=["fact", "forecast"])
def test_environment_date_filter_is_exact(
    read_service: Factor4ReadService, daily_snapshot: DailyReadSnapshot, label_kind: str,
) -> None:
    """ENV-103：动态日期过滤不能静默回退到最近日期或混入另一 kind。"""
    candidates = visible_daily_rows(daily_snapshot, label_kind)  # type: ignore[arg-type]
    if not candidates:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no {label_kind} daily row")
    day = str(candidates[0]["environment_date"])
    traversal = _execute(lambda: read_service.daily_pages(daily_snapshot, label_kind, environment_date=day))
    _assert_check(read_service.check_daily(daily_snapshot, label_kind, traversal, environment_date=day))  # type: ignore[arg-type]


@pytest.mark.parametrize("label_kind", ["fact", "forecast"])
@pytest.mark.parametrize("horizon", ["before-history", "future"])
def test_environment_daily_visibility_extremes_match_persisted_revisions(
    read_service: Factor4ReadService, daily_snapshot: DailyReadSnapshot, label_kind: str, horizon: str,
) -> None:
    """MCP-019: legal early/future as-of selects the exact DB-visible revision or empty result."""
    _assert_check(_execute(lambda: read_service.check_daily_visibility_horizon(daily_snapshot, label_kind, horizon)))


@pytest.mark.parametrize("label_kind", ["fact", "forecast"], ids=["fact", "forecast"])
def test_environment_current_pointer_and_revision_are_unique(
    read_service: Factor4ReadService, daily_snapshot: DailyReadSnapshot, label_kind: str,
) -> None:
    """ENV-105：同日同类 current 指针至多一个、revision 不重复。"""
    try:
        _assert_check(read_service.check_current_uniqueness(daily_snapshot, label_kind))  # type: ignore[arg-type]
    except ReadPrecondition as error:
        _skip_precondition(error)


@pytest.mark.parametrize("label_kind", ["fact", "forecast"], ids=["fact", "forecast"])
@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_environment_revision_three_point_boundary(
    read_service: Factor4ReadService, daily_snapshot: DailyReadSnapshot, label_kind: str, offset: int,
) -> None:
    """ENV-104-A：只有自然存在的多 revision 才执行三点 available_at 边界。"""
    try:
        _assert_check(read_service.check_revision_boundary(daily_snapshot, label_kind, offset))  # type: ignore[arg-type]
    except ReadPrecondition as error:
        _skip_precondition(error)


@pytest.mark.parametrize("evaluation_type", [None, "time_series", "cross_sectional"], ids=["all", "ts", "cs"])
def test_environment_metric_filter_and_final_fields(
    read_service: Factor4ReadService, environment_output_matrix: EnvironmentReadMatrixSnapshot, evaluation_type: str | None,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """MET-301/302/306/308/309：最终指标按批次、因子和 TS/CS 维度对账。"""
    _assert_environment_matrix(read_service.check_environment_output_matrix(
        environment_output_matrix, factor4_read_repository, mode="dimensions", evaluation_type=evaluation_type))


@pytest.mark.parametrize("label_code", ["UNILATERAL_UP", "CHOPPY_UP", "NARROW_RANGE", "WIDE_RANGE", "UNILATERAL_DOWN", "CHOPPY_DOWN"], ids=lambda value: value)
def test_environment_metric_label_filter_is_exact(
    read_service: Factor4ReadService, environment_output_matrix: EnvironmentReadMatrixSnapshot, label_code: str,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """MET-303：六种 4.0 环境标签逐一验证，不只覆盖 WIDE_RANGE。"""
    _assert_environment_matrix(read_service.check_environment_output_matrix(
        environment_output_matrix, factor4_read_repository, mode="labels", label_code=label_code))


def test_environment_metric_batch_omission_does_not_mix_publications(
    read_service: Factor4ReadService, environment_output_matrix: EnvironmentReadMatrixSnapshot,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """MET-304：省略可选 batch 时也必须返回唯一 active 批次，不能混批。"""
    _assert_environment_matrix(read_service.check_environment_output_matrix(
        environment_output_matrix, factor4_read_repository, mode="implicit"))


def test_environment_tags_match_active_routes(
    read_service: Factor4ReadService, environment_output_matrix: EnvironmentReadMatrixSnapshot,
    factor4_read_repository: Factor4ReadRepository,
) -> None:
    """MET-307/309：标签接口逐条回指同一 publication、metric 外键和最终分数。"""
    _assert_environment_matrix(read_service.check_environment_output_matrix(
        environment_output_matrix, factor4_read_repository, mode="tags"))
