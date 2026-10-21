"""账户详情自动化 Fixture。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from api.account_detail_api import AccountDetailAPI
from api.client import HTTPClient
from db.account_detail_repository import AccountDetailFixtureRepository
from db.client import DatabaseClient
from service.account_detail_source_flow import AccountDetailSourceFlow


@pytest.fixture(scope="module")
def account_detail_api(privileged_account) -> AccountDetailAPI:
    """返回带当前有权限账号 JWT 的账户详情 API 客户端。"""

    client = HTTPClient(privileged_account.api_settings)
    try:
        yield AccountDetailAPI(client)
    finally:
        client.close()


@pytest.fixture(scope="module")
def account_detail_source_flow(settings, pytestconfig, privileged_account) -> AccountDetailSourceFlow:
    """建立测试环境的自有源数据流程；没有显式 live/test 时阻断。"""

    if not pytestconfig.getoption("--live"):
        pytest.skip("BLOCKED_ENV: account detail live source tests require --live")
    if settings.environment != "test":
        pytest.skip("BLOCKED_ENV: account detail source writes require --env test")
    if settings.database.driver != "mysql":
        pytest.skip("BLOCKED_ENV: account detail source tests require MySQL")
    client = HTTPClient(privileged_account.api_settings)
    database = DatabaseClient.from_settings(settings.database)
    repository = AccountDetailFixtureRepository(database, environment=settings.environment)
    try:
        yield AccountDetailSourceFlow(repository, AccountDetailAPI(client), privileged_account.user_id)
    finally:
        client.close()
