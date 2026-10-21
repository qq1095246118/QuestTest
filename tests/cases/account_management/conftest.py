"""账号管理测试环境多账号 fixture。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from api.account_management_api import AccountManagementAPI
from api.client import HTTPClient
from db.account_management_fixture_repository import AccountManagementFixtureRepository
from db.client import DatabaseClient
from service.account_management_source_flow import AccountManagementSourceFlow


@pytest.fixture(scope="module")
def account_management_source_flow(settings, pytestconfig, privileged_account) -> Iterator[AccountManagementSourceFlow]:
    """提供测试环境专用的多账号源数据流程。"""

    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: account management source cases require --live")
    if settings.environment != "test":
        pytest.skip("BLOCKED_ENV: account management source writes require --env test")
    if settings.database.driver != "mysql":
        pytest.skip("BLOCKED_ENV: account management source cases require MySQL")
    client = HTTPClient(privileged_account.api_settings)
    database = DatabaseClient.from_settings(settings.database)
    repository = AccountManagementFixtureRepository(database, environment=settings.environment)
    try:
        yield AccountManagementSourceFlow(repository, AccountManagementAPI(client), privileged_account.user_id)
    finally:
        client.close()


@pytest.fixture(scope="module")
def account_management_api_live(settings, pytestconfig, privileged_account) -> Iterator[AccountManagementAPI]:
    """提供不写入数据库的真实账号管理 API 客户端。

    该 fixture 专门覆盖页面默认的无 ``snapshot_id`` 请求。它不创建 fixture
    账号，也不把 SQL 写入当作接口成功证据；仅在显式 test/live 门禁通过后发送
    真实 HTTP GET，并在模块结束时关闭连接。
    """

    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: account management API cases require --live")
    if settings.environment != "test":
        pytest.skip("BLOCKED_ENV: account management API cases require --env test")
    client = HTTPClient(privileged_account.api_settings)
    try:
        yield AccountManagementAPI(client)
    finally:
        client.close()


@pytest.fixture(scope="module")
def account_management_default_page(account_management_api_live: AccountManagementAPI) -> dict:
    """读取一次页面默认账号目录，供字段和状态断言复用。"""

    return account_management_api_live.accounts_page()


@pytest.fixture(scope="module")
def account_management_default_summary(account_management_api_live: AccountManagementAPI) -> dict:
    """读取一次页面默认 portfolio 摘要，供 16 个核心指标断言复用。"""

    return account_management_api_live.summary()
