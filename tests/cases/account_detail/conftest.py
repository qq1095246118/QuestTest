"""账户详情自动化 Fixture。"""

from __future__ import annotations

import pytest

from api.account_detail_api import AccountDetailAPI
from api.client import HTTPClient


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
