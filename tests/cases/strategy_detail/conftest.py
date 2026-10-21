"""策略详情真实接口 Fixture。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from api.client import HTTPClient
from api.strategy_detail_api import StrategyDetailAPI


@pytest.fixture(scope="module")
def strategy_detail_api(
    settings,
    pytestconfig,
    privileged_account,
) -> Iterator[StrategyDetailAPI]:
    """返回只读策略详情 API 客户端。

    策略、账号归属和交易事实由独立持久化数据集提供；该 Fixture 不写数据库。
    """

    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: strategy detail API cases require --live")
    if settings.environment != "test":
        pytest.skip("BLOCKED_ENV: strategy detail API cases require --env test")
    client = HTTPClient(privileged_account.api_settings)
    try:
        yield StrategyDetailAPI(client)
    finally:
        client.close()


@pytest.fixture(scope="module")
def strategy_detail_subject_id(persistent_data_preflight) -> str:
    """返回数据集中的稳定策略主体，不创建或修改策略。"""

    persistent_data_preflight.require("strategy_detail", case_id="SD-PREFLIGHT")
    if not persistent_data_preflight.strategy_ids:
        pytest.skip("PERSISTENT_DATASET_MISSING: no strategy id")
    return str(persistent_data_preflight.strategy_ids[0])
