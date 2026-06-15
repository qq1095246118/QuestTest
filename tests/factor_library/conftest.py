from __future__ import annotations

import pytest

from api.platform.admin_api import AdminAPI
from api.platform.approval_api import ApprovalAPI
from api.platform.auth_api import AuthAPI
from api.platform.factor_api import FactorAPI
from api.platform.factor_ic_api import FactorICAPI
from api.platform.factor_library_api import FactorLibraryAPI
from config.settings import settings
from service.common.db.mysql_client import ReadOnlyMySQLClient
from service.common.db.ssh_tunnel import DatabaseEndpointService
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.factor_library.common.resource_tracker import ResourceTracker
from service.factor_library.common.test_data_factory import TestDataFactory


@pytest.fixture(scope="module")
def token() -> str:
    """登录因子库后端并返回可复用 token。

    请求参数:
        使用 config/env.<env> 中配置的 base_url、factor_email 和 factor_password。
    返回值:
        登录接口返回的 JWT token 字符串；配置缺失时跳过依赖登录态的用例。
    """
    if not settings.base_url:
        pytest.skip("Factor Library API BASE_URL is not configured.")
    if not settings.factor_email or not settings.factor_password:
        pytest.skip("Factor Library login account is not configured.")

    response = AuthAPI().login()
    body = response.json()

    assert response.status_code == 200
    assert isinstance(body, dict), "login body must be dict"
    assert body.get("success") is True, "login success must be True"
    assert isinstance(body.get("data"), dict), "login data must be dict"

    token_value = body["data"].get("token")
    assert token_value, f"login response missing token: {body}"
    return token_value


@pytest.fixture(scope="module")
def factor_api(token: str) -> FactorLibraryAPI:
    """创建携带登录 token 的因子库 API 客户端。

    请求参数:
        token: token fixture 返回的 JWT token。
    返回值:
        已带 Authorization header 的 FactorLibraryAPI 实例。
    """
    return FactorLibraryAPI(token=token)


@pytest.fixture(scope="module")
def factor_resource_api(token: str) -> FactorAPI:
    """创建 factor 模块 API 客户端。

    请求参数:
        token: token fixture 返回的管理员 JWT。
    返回值:
        已带管理员 Authorization header 的 FactorAPI 实例。
    """
    return FactorAPI(token=token)


@pytest.fixture(scope="module")
def factor_ic_api(token: str) -> FactorICAPI:
    """创建 FactorIC 模块 API 客户端。

    请求参数:
        token: token fixture 返回的管理员 JWT。
    返回值:
        已带管理员 Authorization header 的 FactorICAPI 实例。
    """
    return FactorICAPI(token=token)


@pytest.fixture(scope="module")
def admin_api(token: str) -> AdminAPI:
    """创建 Admin 模块 API 客户端。

    请求参数:
        token: token fixture 返回的管理员 JWT。
    返回值:
        已带管理员 Authorization header 的 AdminAPI 实例。
    """
    return AdminAPI(token=token)


@pytest.fixture(scope="module")
def approval_api(token: str) -> ApprovalAPI:
    """创建 Approval 模块 API 客户端。

    请求参数:
        token: token fixture 返回的管理员 JWT。
    返回值:
        已带管理员 Authorization header 的 ApprovalAPI 实例。
    """
    return ApprovalAPI(token=token)


@pytest.fixture(scope="session")
def exchange_test_config() -> dict[str, str]:
    """读取交易所正向用例所需的测试凭证配置。

    请求参数:
        无，直接读取 config/env.<env> 中的 EXCHANGE_TEST_* 配置。
    返回值:
        交易所、账户类型、API key、API secret 和可选 passphrase 字典；配置缺失时跳过正向交易所用例。
    """
    required = {
        "exchange": settings.exchange_test_exchange,
        "account_type": settings.exchange_test_account_type,
        "api_key": settings.exchange_test_api_key,
        "api_secret": settings.exchange_test_api_secret,
    }
    if not all(required.values()):
        pytest.skip("Exchange positive test config is not complete.")
    return {**required, "api_passphrase": settings.exchange_test_api_passphrase}


@pytest.fixture
def test_data_factory() -> TestDataFactory:
    """创建当前用例可用的自动化测试数据工厂。

    请求参数:
        无，内部使用当前时间生成 run_id。
    返回值:
        TestDataFactory 实例，用于生成 auto_ 前缀的唯一测试数据。
    """
    return TestDataFactory()


@pytest.fixture
def resource_tracker() -> ResourceTracker:
    """创建当前用例的资源清理跟踪器。

    请求参数:
        无。
    返回值:
        ResourceTracker 实例；用例结束时自动逆序清理登记资源。
    """
    tracker = ResourceTracker()
    try:
        yield tracker
    finally:
        cleanup_errors = tracker.cleanup_all()
        if cleanup_errors:
            detail = JSONResponseAssertionService.attach_json("清理失败 JSON", cleanup_errors)
            pytest.fail(f"自动化资源清理失败，详见 Allure 附件。{detail}")


@pytest.fixture(scope="module")
def db_client():
    """创建因子库只读 DB 客户端并在用例结束后关闭连接。

    请求参数:
        使用 config/env.<env> 中配置的 factor_db_* 和可选 factor_ssh_* 连接信息。
    返回值:
        已连接到目标 DB endpoint 的 ReadOnlyMySQLClient；DB 配置缺失时跳过 DB 对账用例。
    """
    required = [
        settings.factor_db_host,
        settings.factor_db_name,
        settings.factor_db_user,
        settings.factor_db_password,
    ]
    if not all(required):
        pytest.skip("Factor Library DB config is not complete.")

    with DatabaseEndpointService.open_database_endpoint(settings) as endpoint:
        client = ReadOnlyMySQLClient.from_settings(host=endpoint.host, port=endpoint.port)
        try:
            yield client
        finally:
            client.close()
