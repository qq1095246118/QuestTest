"""目录完整正常功能分支，替代历史脚本中的只记录响应和人工 PASS。"""

from collections.abc import Callable
from typing import Any

import pytest

from api.factor4_auxiliary_api import Factor4AuxiliaryAPI
from config.settings import Settings
from db.client import DatabaseClient
from db.factor4_auxiliary_repository import Factor4AuxiliaryRepository
from service.factor4_catalog_extension_service import Factor4CatalogExtensionService
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _verify(action: Callable[[], ReadCheck], record_property: Callable[[str, object], None] | None = None) -> None:
    try:
        result = action()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))
    except ReadContractError as exc:
        pytest.fail(str(exc), pytrace=False)
    blocked = result.evidence.get("blocked", ())
    if record_property is not None:
        for name in (
            "catalog_traversal", "catalog_returned_count", "catalog_returned_unique_count",
            "catalog_database_unique_count", "catalog_page_count", "catalog_full_membership_verified",
            "catalog_completeness", "catalog_budget_code", "catalog_statistics",
        ):
            record_property(name, result.evidence[name])
        record_property("catalog_blocked", "; ".join(blocked))
        record_property("catalog_acceptance", "FAILED" if result.issues else (
            "BLOCKED" if blocked else "BOUNDED_READ_ACCEPTED_NOT_FULL_EXPORT" if result.evidence["catalog_traversal"] == "bounded"
            else "COMPLETE_CATALOG_ACCEPTED"
        ))
    assert not result.issues, ", ".join(result.issues)
    if blocked:
        pytest.skip("BLOCKED_DEPENDENCY: catalog checks incomplete: " + "; ".join(blocked))
    assert result.checked_count > 0


@pytest.fixture(scope="module")
def catalog_service(factor4_read_service: Factor4ReadService) -> Factor4CatalogExtensionService:
    """复用已门禁且已握手的测试 MCP；构造异常透传。"""
    return Factor4CatalogExtensionService(Factor4AuxiliaryAPI(factor4_read_service.api.mcp))


@pytest.fixture(scope="module")
def catalog_repository(catalog_service: Factor4CatalogExtensionService, settings: Settings) -> Factor4AuxiliaryRepository:
    """测试门禁成功后创建只读 Repository；不执行写操作。"""
    return Factor4AuxiliaryRepository(DatabaseClient.from_settings(settings.database))


@pytest.fixture(scope="module")
def filter_seed(catalog_service: Factor4CatalogExtensionService) -> dict[str, Any]:
    """动态种子；缺失自然业务字段才 skip，不捕获产品失败。"""
    try:
        return catalog_service.filter_seed()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))


@pytest.mark.parametrize("field", ["query", "theme", "tags", "data_source", "interval", "library_coin_category", "combined"])
def test_catalog_matching_filters_keep_seed_and_exclude_nonmatching_rows(catalog_service: Factor4CatalogExtensionService, filter_seed: dict[str, Any], field: str) -> None:
    """SEARCH-002/FILTER-COMBO：单项及全部目录筛选均实际校验成员，不允许空集假通过。"""
    _verify(lambda: catalog_service.check_filter(filter_seed, field))


def test_catalog_all_total_equals_factor_and_subfactor_totals(catalog_service: Factor4CatalogExtensionService) -> None:
    """CAT-001/002/003：全目录和按 kind 统计均合法且满足总数加和恒等式。"""
    _verify(catalog_service.check_total_additivity)


def test_catalog_conflicting_filter_intersection_returns_successful_empty_page(catalog_service: Factor4CatalogExtensionService, filter_seed: dict[str, Any]) -> None:
    """FILTER-COMBO-EMPTY：名称与不匹配主题求交，不能忽略其中之一。"""
    _verify(lambda: catalog_service.check_filter(filter_seed, "combined", matching=False))


@pytest.mark.parametrize("field", ["query", "theme", "tags", "data_source", "interval", "library_coin_category"])
def test_catalog_each_conflicting_filter_is_not_silently_ignored(catalog_service: Factor4CatalogExtensionService, filter_seed: dict[str, Any], field: str) -> None:
    """每个筛选独立以合法不匹配值求交，避免正向种子天然满足条件导致忽略筛选也通过。"""
    _verify(lambda: catalog_service.check_filter(filter_seed, field, matching=False))


@pytest.mark.parametrize("kind", ["factor", "sub_factor"])
def test_detail_levels_preserve_database_identity_and_approved_projection(catalog_service: Factor4CatalogExtensionService, catalog_repository: Factor4AuxiliaryRepository, kind: str) -> None:
    """DETAIL-001/002/LEVEL-SHAPE：逐类型三个 detail level 共享身份并按层级公开定义/代码。"""
    rows = catalog_repository.detail_entities(kind, limit=1)
    if not rows:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: no {kind} detail entity")
    _verify(lambda: catalog_service.check_detail_levels(kind, rows[0]))


def test_detail_batch_preserves_mixed_kinds_input_order_and_duplicates(catalog_service: Factor4CatalogExtensionService, catalog_repository: Factor4AuxiliaryRepository) -> None:
    """BATCH-MIXED-DUPLICATE 正常分支：混合母/子因子与重复 ref 保留全部位置并核对 DB。"""
    entities = tuple((kind, row) for kind in ("factor", "sub_factor") for row in catalog_repository.detail_entities(kind, limit=1))
    if len(entities) != 2:
        pytest.skip("BLOCKED_DATA_PRECONDITION: both factor kinds are required")
    _verify(lambda: catalog_service.check_batch(entities, duplicate=True))


def test_detail_batch_accepts_fifty_valid_refs_without_loss(catalog_service: Factor4CatalogExtensionService, catalog_repository: Factor4AuxiliaryRepository) -> None:
    """BATCH-MAX-50：合法最大批量功能边界，逐项 identity 与顺序，不是吞吐性能测试。"""
    rows = catalog_repository.detail_entities("sub_factor", limit=50)
    if len(rows) != 50:
        pytest.skip("BLOCKED_DATA_PRECONDITION: fewer than fifty sub factors")
    _verify(lambda: catalog_service.check_batch(tuple(("sub_factor", row) for row in rows)))


def test_same_numeric_id_remains_separate_factor_and_subfactor_identity(catalog_service: Factor4CatalogExtensionService, catalog_repository: Factor4AuxiliaryRepository) -> None:
    """DETAIL-KIND-IDENTITY：母/子同数值 ID 不得串型，分别比对各自表中的真实身份。"""
    entities = catalog_repository.overlapping_id_entities()
    if not entities:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no overlapping factor/subfactor numeric ID")
    _verify(lambda: catalog_service.check_batch(entities))


@pytest.mark.parametrize("maximum_page", [False, True], ids=["two_per_page", "two_hundred_per_page"])
def test_parent_children_pagination_exhausts_exact_database_relations(catalog_service: Factor4CatalogExtensionService, catalog_repository: Factor4AuxiliaryRepository, maximum_page: bool) -> None:
    """DETAIL-010/011/PARENT-FULL：小页和合法最大页独立全量遍历母子关系并回查子引用。"""
    sample = catalog_repository.parent_children(maximum_page=maximum_page)
    if sample is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no cross-page parent sample for requested page size")
    _verify(lambda: catalog_service.check_children(sample, page_size=200 if maximum_page else 2))


@pytest.mark.parametrize("kind", ["factor", "sub_factor"])
@pytest.mark.parametrize("status", ["inactive", "new", "valid", "invalid", "deleted"])
def test_catalog_every_kind_and_status_returns_database_members(catalog_service: Factor4CatalogExtensionService, catalog_repository: Factor4AuxiliaryRepository, kind: str, status: str, record_property: Callable[[str, object], None]) -> None:
    """CAT-STATUS：核对预算内返回成员；只有自然完结验全集，JUnit 明示受限验收范围。"""
    rows = catalog_repository.catalog_status_members(kind, status)
    _verify(lambda: catalog_service.check_status_category(kind, status, None, rows), record_property)


@pytest.mark.parametrize("category", ["all", "main", "altcoin", "custom"])
def test_catalog_valid_subfactor_each_coin_category_matches_database(catalog_service: Factor4CatalogExtensionService, catalog_repository: Factor4AuxiliaryRepository, category: str, record_property: Callable[[str, object], None]) -> None:
    """CAT-CATEGORY：四分类预算内成员核DB；自然完结验全集，空分类仍须成功空页。"""
    rows = catalog_repository.catalog_status_members("sub_factor", "valid", category)
    _verify(lambda: catalog_service.check_status_category("sub_factor", "valid", category, rows), record_property)


@pytest.mark.parametrize("kind", ["factor", "sub_factor"])
def test_catalog_chinese_name_query_finds_exact_database_seed(catalog_service: Factor4CatalogExtensionService, catalog_repository: Factor4AuxiliaryRepository, kind: str) -> None:
    """中文cn_name查询不能只匹配英文name；动态种子，不读取历史报告。"""
    rows = catalog_repository.catalog_status_members(kind, "valid")
    _verify(lambda: catalog_service.check_chinese_query(kind, rows))
