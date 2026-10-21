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
def account_detail_subject_id(persistent_data_preflight) -> int:
    """返回持久化策略数据集中可见的账户主体，不创建或修改账号。"""

    persistent_data_preflight.require(
        "account_detail", "performance_samples", "published_generation", case_id="ADC-PREFLIGHT"
    )
    return int(persistent_data_preflight.account_ids[0])


@pytest.fixture(scope="module")
def account_detail_source_flow(
    settings,
    pytestconfig,
    privileged_account,
) -> AccountDetailSourceFlow:
    """建立测试环境的自有源数据流程；没有显式 live/test 时阻断。"""

    if not pytestconfig.getoption("--live"):
        pytest.skip("BLOCKED_ENV: account detail live source tests require --live")
    if settings.environment != "test":
        pytest.skip("BLOCKED_ENV: account detail source writes require --env test")
    if settings.database.driver != "mysql":
        pytest.skip("BLOCKED_ENV: account detail source tests require MySQL")
    client = HTTPClient(privileged_account.api_settings)
    database = DatabaseClient.from_settings(replace(settings.database, read_only=True))
    repository = AccountDetailFixtureRepository(database, environment=settings.environment)
    try:
        yield AccountDetailSourceFlow(repository, AccountDetailAPI(client), privileged_account.user_id)
    finally:
        client.close()
