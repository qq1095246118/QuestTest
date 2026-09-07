"""临时排名/scope 探针的正式业务替代；没有脚本包装或历史报告依赖。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from api.factor4_summary_api import Factor4SummaryAPI, RankingMode
from db.factor4_read_repository import Factor4ReadRepository, MetricScopeSnapshot, SummarySample
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition
from service.factor4_summary_service import Factor4SummaryService
from service.factor4_rank_filter_service import Factor4RankFilterService

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _verify(action: Callable[[], ReadCheck]) -> None:
    """执行一个业务检查；只将明确前置阻断记 skip，结果错误 fail，报告不含远端正文。"""
    try:
        check = action()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))
    except ReadContractError as exc:
        pytest.fail(str(exc), pytrace=False)
    assert check.checked_count > 0
    assert not check.issues, ", ".join(check.issues[:25])


@pytest.fixture(scope="module")
def summary_service(factor4_read_service: Factor4ReadService) -> Factor4SummaryService:
    """使用全局测试环境门禁及已握手会话；返回 summary Service，初始化异常透传。"""
    return Factor4SummaryService(Factor4SummaryAPI(factor4_read_service.api.mcp))


@pytest.fixture(scope="module", params=["ts_symbol", "ts_aggregate", "cs_aggregate"])
def summary_sample(request: pytest.FixtureRequest, factor4_read_repository: Factor4ReadRepository) -> SummarySample:
    """按三种形态动态发现完整分区；无数据显式阻断，DB 错误不转换为 skip。"""
    sample = factor4_read_repository.summary_sample(request.param)
    if sample is None:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no bounded {request.param} summary partition")
    return sample


@pytest.fixture(scope="module", params=["time_series", "cross_sectional"])
def scope_snapshot(request: pytest.FixtureRequest, factor4_read_repository: Factor4ReadRepository) -> MetricScopeSnapshot:
    """TS/CS 独立读取历史 scope；返回数据库基线，缺自然数据明确阻断。"""
    sample = factor4_read_repository.metric_scope_snapshot(request.param)
    if sample is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no completed metric scopes")
    return sample


@pytest.mark.parametrize("mode", ["signed", "raw_signed", "absolute_diagnostic"])
def test_factor_rank_values_order_identity_and_latest_run(
    summary_service: Factor4SummaryService, summary_sample: SummarySample, mode: RankingMode,
) -> None:
    """MCP-016：三种排名模式核对数值/顺序/身份/最新 Run；raw 与 abs 独立验证极值选择。"""
    _verify(lambda: summary_service.check_rank(summary_sample, ranking_mode=mode))


@pytest.mark.parametrize("metric", ["mean_rank_ic", "icir", "rank_icir", "ic_t_stat", "rank_ic_t_stat",
                                   "final_score", "icir_oos_retention", "rank_icir_oos_retention"])
def test_factor_rank_each_supported_metric_matches_db(
    summary_service: Factor4SummaryService, summary_sample: SummarySample, metric: str,
) -> None:
    """八种非 mean_ic 排名指标分别与同 scope DB 最新结果对账，不能偷换指标列。"""
    _verify(lambda: summary_service.check_rank(summary_sample, ranking_mode="raw_signed", metric=metric))


@pytest.mark.parametrize("top_k,bottom_k", [(2, 0), (0, 2)], ids=["top_only", "bottom_only"])
@pytest.mark.parametrize("ranking_mode", ["raw_signed", "absolute_diagnostic"])
def test_factor_rank_requested_sides_and_repeat_are_stable(
    summary_service: Factor4SummaryService, summary_sample: SummarySample, top_k: int, bottom_k: int, ranking_mode: RankingMode,
) -> None:
    """单侧请求和固定时点重放；另一侧为零必须保持空集合。"""
    _verify(lambda: summary_service.check_rank(summary_sample, ranking_mode=ranking_mode,
                                               top_k=top_k, bottom_k=bottom_k, repeat=True))


@pytest.mark.factor4_deferred
def test_factor_rank_zero_both_sides_is_rejected(summary_service: Factor4SummaryService, summary_sample: SummarySample) -> None:
    """双侧均为零属于已裁决的 INVALID_ARGUMENT，不再误报成功空集缺陷。"""
    _verify(lambda: Factor4RankFilterService(summary_service).check_no_requested_side_rejected(summary_sample))


def test_factor_rank_signed_repeated_request_is_stable(summary_service: Factor4SummaryService, summary_sample: SummarySample) -> None:
    """固定 as-of signed 模式重复请求也须保持相同 ID、方向和数值。"""
    _verify(lambda: summary_service.check_rank(summary_sample, ranking_mode="signed", repeat=True))


@pytest.mark.parametrize("variant", ["baseline", "slices_equal", "slices_above", "final_score_empty", "coverage_median", "coverage_one", "require_oos", "theme_hit", "theme_miss"])
def test_factor_rank_final_metric_filters_and_parent_theme_membership(
    summary_service: Factor4SummaryService, summary_sample: SummarySample,
    factor4_read_repository: Factor4ReadRepository, variant: str,
) -> None:
    """仅按最新 metric 重算门槛/候选数/排序；主题筛选由母子关系与主题 DB 交集证明。"""
    memberships = factor4_read_repository.rank_theme_memberships(summary_sample) if variant.startswith("theme_") else None
    _verify(lambda: Factor4RankFilterService(summary_service).check_filters(summary_sample, variant, memberships=memberships))


@pytest.mark.parametrize("mode", ["auto", "explicit_run", "batch"])
def test_summary_final_fields_single_explicit_and_batch(
    summary_service: Factor4SummaryService, summary_sample: SummarySample, mode: str,
) -> None:
    """逐项/显式 Run/批量分别核对完整小分区全部字段、null、周期和最新主键。"""
    _verify(lambda: summary_service.check_metrics(summary_sample, explicit_run=mode == "explicit_run", batch=mode == "batch"))


@pytest.mark.factor4_deferred
def test_three_concurrent_exact_metric_requests_keep_same_database_identity(
    summary_service: Factor4SummaryService, summary_sample: SummarySample,
) -> None:
    """单独暂缓的三并发只读契约；每份响应都与同 Run DB 实体逐字段比较。"""
    _verify(lambda: summary_service.check_concurrent_metrics_repeatability(summary_sample))


@pytest.mark.parametrize("scope", ["ts_aggregate", "cs_aggregate"])
@pytest.mark.parametrize("mode", ["auto", "explicit_run", "batch"])
def test_parent_child_aggregate_summary_matches_exact_database_fields(
    summary_service: Factor4SummaryService, factor4_read_repository: Factor4ReadRepository, scope: str, mode: str,
) -> None:
    """母因子 child_aggregate 不能用子因子 direct 用例代替；单次/显式Run/批量独立对账。"""
    sample = factor4_read_repository.summary_sample(scope, kind="factor", calculation_mode="child_aggregate", minimum_factors=1)
    if sample is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no complete parent child_aggregate summary partition")
    _verify(lambda: summary_service.check_metrics(sample, explicit_run=mode == "explicit_run", batch=mode == "batch"))


@pytest.mark.parametrize("limit", [1, 100])
def test_metric_scopes_match_distinct_factor_union_and_page_limit(
    summary_service: Factor4SummaryService, scope_snapshot: MetricScopeSnapshot, limit: int,
) -> None:
    """MCP-019：scope 因子数是可见历史 Run 的去重并集；核对完成时间、周期、limit 与 truncation。"""
    _verify(lambda: summary_service.check_scopes(scope_snapshot, limit=limit))


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_metric_scope_completion_is_inclusive_point_in_time(
    summary_service: Factor4SummaryService, scope_snapshot: MetricScopeSnapshot, offset: int,
) -> None:
    """两次完成时间的同 scope 在前/等/后一微秒真实可见；未观察到目标不能判通过。"""
    _verify(lambda: summary_service.check_scopes(scope_snapshot, offset=offset))


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "equal", "after"])
def test_formula_evidence_visibility_at_run_completion(
    summary_service: Factor4SummaryService, summary_sample: SummarySample, offset: int,
) -> None:
    """公式完成前隐藏、等/后包含同 Run 的不可变 hash 和表达式；不读历史 fixture ID。"""
    _verify(lambda: summary_service.check_formula_completion(summary_sample, offset))
