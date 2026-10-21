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
