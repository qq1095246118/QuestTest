"""同一显式环境 Backend / MCP / DB 三方业务结果，不执行内部计算或写入。"""

from collections.abc import Callable

import pytest

from api.client import HTTPClient
from api.factor4_backend_api import Factor4BackendAPI
from config.settings import ApiSettings, Settings
from db.client import DatabaseClient
from db.factor4_auxiliary_repository import Factor4AuxiliaryRepository
from db.factor4_calculation_repository import Factor4CalculationRepository
from db.factor4_publication_repository import Factor4PublicationRepository
from service.factor4_backend_reconciliation_service import Factor4BackendReconciliationService
from service.factor4_cost_result_service import check_published_cost_results
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
    assert not check.issues, ", ".join(check.issues[:30])


@pytest.fixture(scope="module")
def backend_service(factor4_backend_api_settings: ApiSettings, factor4_read_service: Factor4ReadService) -> Factor4BackendReconciliationService:
    """接收同环境独立认证配置与 MCP 服务；返回三方对账入口，缺 Backend 配置由前置跳过。"""
    return Factor4BackendReconciliationService(Factor4BackendAPI(HTTPClient(factor4_backend_api_settings)), factor4_read_service)


@pytest.fixture(scope="module")
def backend_repository(factor4_calculation_repository: Factor4CalculationRepository) -> Factor4AuxiliaryRepository:
    """复用经显式 live/环境门禁的只读连接；不登录 Backend 或初始化 MCP，异常透传。"""
    return Factor4AuxiliaryRepository(factor4_calculation_repository.database_client)


@pytest.mark.parametrize("label_kind", ["fact", "forecast"])
def test_backend_daily_exact_date_and_pit_match_mcp_database(backend_service: Factor4BackendReconciliationService, backend_repository: Factor4AuxiliaryRepository, label_kind: str) -> None:
    """ENV-108：Backend 日期筛选（有/无 as_of）与 MCP/DB 同一可见修订结果一致。"""
    snapshot = backend_repository.daily_snapshot()
    _verify(lambda: backend_service.check_daily(snapshot, label_kind))


@pytest.mark.parametrize("ic_scope", ["time_series", "cross_sectional"])
def test_backend_summary_identity_values_and_period_instants_match_mcp_database(backend_service: Factor4BackendReconciliationService, backend_repository: Factor4AuxiliaryRepository, ic_scope: str) -> None:
    """MET-310：精确 Run 汇总身份/数值及 period 的真实瞬间与 DB 显式时区 payload 一致。"""
    _verify(lambda: backend_service.check_summary_sample(backend_repository, ic_scope))


@pytest.mark.factor4_technical
def test_backend_openapi_declares_internal_hmac_headers_not_user_bearer(backend_service: Factor4BackendReconciliationService) -> None:
    """DB-608 静态合约子项：只读实时 OpenAPI，不将声明通过当 JWT/HMAC 运行越权验证。"""
    _verify(backend_service.check_hmac_declaration)


def test_backend_recommendations_publication_order_and_scores_match_mcp_database(backend_service: Factor4BackendReconciliationService, backend_repository: Factor4AuxiliaryRepository, settings: Settings) -> None:
    """CALC-513：同一 PIT Backend/MCP 必须绑定 DB publication 及最终 eligible route 的有序完整投影。"""
    history = Factor4PublicationRepository(DatabaseClient.from_settings(settings.database)).history()
    daily = backend_repository.daily_snapshot()
    _verify(lambda: backend_service.check_recommendations(history, daily))


def test_published_cost_fields_and_route_scope_evidence_match_final_metrics(backend_repository: Factor4AuxiliaryRepository) -> None:
    """CALC-508：费用字段 success空值规则、DB/payload一致及全部route的TS/CS成本证据同身份回指。"""
    metrics, routes = backend_repository.published_cost_results()
    _verify(lambda: check_published_cost_results(metrics, routes))
