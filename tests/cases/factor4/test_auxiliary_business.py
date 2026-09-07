"""旧 catalog/KB/universe 来源的正式字段断言，不依赖旧报告。"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from api.factor4_auxiliary_api import Factor4AuxiliaryAPI
from config.settings import Settings
from db.client import DatabaseClient
from db.factor4_auxiliary_repository import Factor4AuxiliaryRepository
from service.factor4_auxiliary_service import Factor4AuxiliaryService
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _verify(action: Callable[[], ReadCheck]) -> None:
    try:
        check = action()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))
    except ReadContractError as exc:
        pytest.fail(str(exc), pytrace=False)
    assert check.checked_count > 0
    assert not check.issues, ", ".join(check.issues)


@pytest.fixture(scope="module")
def auxiliary_service(factor4_read_service: Factor4ReadService) -> Factor4AuxiliaryService:
    """仅在全局门禁/握手完成后返回只读 Service；依赖失败透传。"""
    return Factor4AuxiliaryService(Factor4AuxiliaryAPI(factor4_read_service.api.mcp))


@pytest.fixture(scope="module")
def auxiliary_repository(auxiliary_service: Factor4AuxiliaryService, settings: Settings) -> Factor4AuxiliaryRepository:
    """依赖门禁成功后创建只读 Repository；构造异常透传。"""
    return Factor4AuxiliaryRepository(DatabaseClient.from_settings(settings.database))


@pytest.fixture(scope="module")
def candidate(auxiliary_repository: Factor4AuxiliaryRepository) -> dict[str, Any]:
    """动态发现真实 KB 候选；无样本明确数据阻断。"""
    result = auxiliary_repository.candidate_sample()
    if result is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no KB candidate")
    return result


@pytest.fixture(scope="module")
def universe_snapshot(auxiliary_repository: Factor4AuxiliaryRepository) -> tuple[datetime, tuple[dict[str, Any], ...]]:
    """捕获各集合成员和一个固定查询时点；不伪造历史数据。"""
    return datetime.now(timezone.utc), auxiliary_repository.universe_rows()


@pytest.mark.parametrize("query", [False, True], ids=["exact_id", "exact_id_and_name"])
def test_kb_exact_candidate_fields_match_database(auxiliary_service: Factor4AuxiliaryService, candidate: dict[str, Any], query: bool) -> None:
    """KB-001/002：精确 ID/名称组合返回唯一候选，所有核心字段与 DB 一致。"""
    _verify(lambda: auxiliary_service.check_candidate(candidate, query=query))


def test_kb_name_search_discovers_current_candidate_without_extraction_selector(auxiliary_service: Factor4AuxiliaryService, candidate: dict[str, Any]) -> None:
    """KB-002：只给候选完整名称也能发现同一 extraction，不能只验证 ID 旁路检索。"""
    _verify(lambda: auxiliary_service.check_candidate_name_discovery(candidate))


@pytest.mark.parametrize("filter_name", ["validation_status", "mapping_status", "min_confidence", "target_asset_class"])
@pytest.mark.parametrize("matching", [True, False], ids=["matching", "nonmatching"])
def test_kb_exact_candidate_applies_each_business_filter(auxiliary_service: Factor4AuxiliaryService, candidate: dict[str, Any], filter_name: str, matching: bool) -> None:
    """KB-003：匹配筛选必须保留候选，不匹配必须成功空集；非法枚举不能代替负向筛选。"""
    _verify(lambda: auxiliary_service.check_candidate(candidate, filter_name=filter_name, matching=matching))


@pytest.mark.parametrize("matching", [True, False], ids=["all_filters_match", "query_conflicts"])
def test_kb_all_filters_and_selectors_intersect(auxiliary_service: Factor4AuxiliaryService, candidate: dict[str, Any], matching: bool) -> None:
    """KB-COMBO：ID、名称、状态、置信度和资产类别求交；ID 不得绕过矛盾的 query。"""
    _verify(lambda: auxiliary_service.check_candidate(candidate, query=True, matching=matching, combined=True))


def test_kb_mapped_candidate_resolves_same_typed_catalog_entity(auxiliary_service: Factor4AuxiliaryService, auxiliary_repository: Factor4AuxiliaryRepository) -> None:
    """KB-MAPPED-DETAIL：候选映射和真实母/子因子 DB 以及 MCP 详情身份一致。"""
    row = auxiliary_repository.candidate_sample(mapped=True)
    if row is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no mapped KB candidate")
    entity = auxiliary_repository.mapped_entity(int(row["mapped_factor_id"]), bool(row["is_sub_factor_id"]))
    _verify(lambda: auxiliary_service.check_mapped_identity(row, entity))


@pytest.mark.parametrize("status", ["running", "claimed", "completed", "failed", "cancelled", None], ids=["running", "claimed", "completed", "failed", "cancelled", "no_task"])
def test_kb_current_task_state_and_lease_projection_match_database(auxiliary_service: Factor4AuxiliaryService, auxiliary_repository: Factor4AuxiliaryRepository, status: str | None) -> None:
    """KB-TASK-DB：逐状态独立执行，活动任务优先、lease 已过期不可仍报告 active_task_id。"""
    sample = auxiliary_repository.candidate_task_sample(status)
    if sample is None:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no KB current task sample for {status}")
    _verify(lambda: auxiliary_service.check_candidate_task(sample))


@pytest.mark.parametrize("universe_key", ["all", "main", "altcoin"])
@pytest.mark.parametrize("days_back", [0, 365], ids=["current", "historical"])
def test_universe_membership_order_and_fields_match_point_in_time_database(auxiliary_service: Factor4AuxiliaryService, universe_snapshot: tuple[datetime, tuple[dict[str, Any], ...]], universe_key: str, days_back: int) -> None:
    """UNIVERSE-001/002：三集合当前/历史按 NULL 无界和半开时间区间逐成员、排序、字段对账。"""
    as_of, rows = universe_snapshot
    _verify(lambda: auxiliary_service.check_universe(rows, universe_key, as_of - timedelta(days=days_back)))


def test_main_and_altcoin_form_exact_current_all_universe_partition(auxiliary_service: Factor4AuxiliaryService, universe_snapshot: tuple[datetime, tuple[dict[str, Any], ...]]) -> None:
    """UNIVERSE-PARTITION：使用同一当前时点的 MCP/DB 数据，不能把旧报告 all 集合混入。"""
    as_of, rows = universe_snapshot
    _verify(lambda: auxiliary_service.check_universe_partition(rows, as_of))
