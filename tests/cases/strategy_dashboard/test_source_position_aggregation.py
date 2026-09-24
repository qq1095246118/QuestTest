"""已有 M20 场景通过采集后原始持仓 → 已部署摘要 API 验收，不计采集器覆盖。"""

from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
import requests

from api.auth_api import AuthAPI, AuthResponsePayload
from api.client import HTTPClient
from api.strategy_dashboard_source_fixture_api import SourceFixtureAPI
from db.client import DatabaseClient
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository
from service.strategy_dashboard_formula_scenarios import load_formula_scenarios
from service.strategy_dashboard_source_fixture_flow import SourceFixtureFlow
from tests.strategy_dashboard_formula_routes import SOURCE_SUPPORTED_SCENARIOS


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]
_CASES = tuple(case for case in load_formula_scenarios(_ROOT / "tests/data/strategy_dashboard/formula_finance.json")
               if case.scenario_id in SOURCE_SUPPORTED_SCENARIOS)


def _client(settings):
    session = requests.Session()
    proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
    if proxy:
        session.trust_env = False
        session.proxies = {"http": proxy, "https": proxy}
    return HTTPClient(settings, session=session)


@pytest.fixture(scope="module")
def source_fixture_flow(settings, pytestconfig):
    """建立测试环境的专用身份输入与真实摘要客户端，逐条保存完整证据。"""
    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: requires --live --env test")
    assert pytestconfig.getoption("--env") == settings.environment == "test"
    assert urlparse(settings.api.base_url).hostname in settings.environment_safety.allowed_hosts
    assert settings.database.host in settings.environment_safety.allowed_database_hosts
    output = _ROOT / "reports" / ("quant-raw-position-" + uuid4().hex)
    output.mkdir(parents=True)
    login_client = _client(replace(settings.api, auth_token=None))
    try:
        credentials = settings.authentication.privileged
        response = AuthAPI(login_client).login(credentials.email, credentials.password)
        assert response.status_code == 200, "BLOCKED_AUTH"
        body = response.json()
        token = AuthResponsePayload.token(body)
        actor_id = int(body["data"]["user"]["id"])
    finally:
        login_client.close()
    client = _client(replace(settings.api, auth_token=token))
    repository = SourceFixtureRepository(DatabaseClient.from_settings(settings.database), environment=settings.environment)

    def save(evidence):
        (output / (evidence["fixture_id"] + ".json")).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        yield SourceFixtureFlow(repository, SourceFixtureAPI(client), actor_id, evidence_sink=save)
    finally:
        client.close()


@pytest.mark.parametrize("scenario", _CASES, ids=lambda item: item.scenario_id)
def test_source_position_formula(scenario, source_fixture_flow, record_property):
    """将原始零/负数/null逐条交给后端；不在测试侧过滤、去重或计算期望。"""
    record_property("formula_family", scenario.family)
    record_property("case_ids", ",".join(scenario.case_ids))
    with source_fixture_flow.execute(source_positions=scenario.inputs["positions"]) as evidence:
        evidence["scenario_id"] = scenario.scenario_id
        evidence["expected"] = dict(scenario.expected)
        record_property("fixture_id", evidence["fixture_id"])
        response = evidence["summary_response"]
        assert response["http_status"] == 200, f"BLOCKED_SOURCE_ADAPTER: HTTP {response['http_status']}: {response['body'].get('error')}"
        body = response["body"]
        assert body.get("success") is True
        assert body["meta"]["mock"] is False
        assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
        assert body["meta"]["generation"] == evidence["fixture_id"] + "_batch"
        assert body["data"]["context"]["subject_id"] == str(evidence["account_id"])
        assert body["data"]["context"]["scope"] == "lifetime"
        totals = body["data"]["exposure_summary"]
        mapping = {"gross_notional": "total_notional", "unrealized_pnl": "unrealized_pnl", "used_margin": "margin_used"}
        for expected_key, response_key in mapping.items():
            if expected_key not in scenario.expected:
                continue
            expected = scenario.expected[expected_key]
            metric = totals[response_key]
            if expected is None:
                assert metric["value"] is None, response_key
                assert metric["status"] == "unavailable", response_key
                assert metric["reason_code"], response_key
            else:
                assert metric["status"] == "complete", response_key
                assert metric["value"] is not None, response_key
                assert Decimal(metric["value"]) == Decimal(str(expected)), response_key
        if "position_count" in scenario.expected:
            assert totals["position_count"] == scenario.expected["position_count"]
        assert len(evidence["account_positions"]) == len(scenario.inputs["positions"])
    assert evidence["cleanup_verified_zero"] is True
