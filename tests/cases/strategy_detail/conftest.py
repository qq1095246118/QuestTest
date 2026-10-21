"""策略详情真实测试环境 fixture。"""
from __future__ import annotations
from collections.abc import Iterator
from dataclasses import replace
import pytest
from api.client import HTTPClient
from api.strategy_detail_api import StrategyDetailAPI
from db.client import DatabaseClient
from db.strategy_detail_fixture_repository import StrategyDetailFixtureRepository
from service.strategy_detail_source_flow import StrategyDetailSourceFlow


@pytest.fixture(scope="module")
def strategy_detail_source_flow(
    settings,
    pytestconfig,
    privileged_account,
    persistent_data_preflight,
) -> Iterator[StrategyDetailSourceFlow]:
    """仅 --live --env test 时提供真实数据库到接口的流程。"""
    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: strategy detail source cases require --live")
    if settings.environment != "test" or settings.database.driver != "mysql":
        pytest.skip("BLOCKED_ENV: strategy detail source cases require MySQL test environment")
    http = HTTPClient(privileged_account.api_settings)
    db = DatabaseClient.from_settings(replace(settings.database, read_only=True))
    repo = StrategyDetailFixtureRepository(db, environment=settings.environment)
    try:
        yield StrategyDetailSourceFlow(
            repo,
            StrategyDetailAPI(http),
            privileged_account.user_id,
            persistent_data_preflight,
        )
    finally:
        http.close()
