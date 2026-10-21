"""账号管理真实接口 Fixture。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from api.account_management_api import AccountManagementAPI
from api.client import HTTPClient


@pytest.fixture(scope="module")
def account_management_api_live(
    settings,
    pytestconfig,
    privileged_account,
) -> Iterator[AccountManagementAPI]:
    """返回只读真实账号管理 API 客户端。

    该 fixture 不创建账号，不写入派生表，也不清理持久化数据；数据集由独立
    seed/collector 生命周期准备，Case 只调用真实 GET。
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
def account_management_data_default_page(
    account_management_api_live: AccountManagementAPI,
    persistent_data_preflight,
) -> dict:
    """检查多账号持久化数据后读取目录。"""

    persistent_data_preflight.require(
        "account_management", "multi_account", "published_generation", case_id="AM-PREFLIGHT-PAGE"
    )
    return account_management_api_live.accounts_page()


@pytest.fixture(scope="module")
def account_management_data_default_summary(
    account_management_api_live: AccountManagementAPI,
    persistent_data_preflight,
) -> dict:
    """检查多账号持久化数据后读取 portfolio 摘要。"""

    persistent_data_preflight.require(
        "account_management", "multi_account", "performance_samples", "published_generation",
        case_id="AM-PREFLIGHT-SUMMARY",
    )
    return account_management_api_live.summary()
