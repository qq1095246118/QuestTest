"""组合账户原始金额经真实后端汇总；明确不声称覆盖 Prometheus 采集链路。"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse
from uuid import uuid4

import pytest
import requests

from api.auth_api import AuthAPI, AuthResponsePayload
from api.client import HTTPClient
from api.strategy_dashboard_portfolio_fixture_api import PortfolioFixtureAPI
from db.client import DatabaseClient
from db.strategy_dashboard_portfolio_fixture_repository import PortfolioFixtureRepository
from service.strategy_dashboard_portfolio_source_flow import PortfolioSourceFlow


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]


_CASES = json.loads((_ROOT / "tests/data/strategy_dashboard/portfolio_source_profiles.json").read_text(encoding="utf-8"))


def _client(settings: Any) -> HTTPClient:
    session = requests.Session()
    proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
    if proxy:
        session.trust_env = False
        session.proxies = {"http": proxy, "https": proxy}
    return HTTPClient(settings, session=session)


@pytest.fixture(scope="module")
def portfolio_source_runtime(settings: Any, pytestconfig: Any) -> Iterator[tuple[HTTPClient, PortfolioFixtureRepository, int]]:
    """仅在显式 test/live 门禁下准备真实后端与独立账户清理器。"""
    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: requires --live --env test")
    assert pytestconfig.getoption("--env") == settings.environment == "test"
    assert urlparse(settings.api.base_url).hostname in settings.environment_safety.allowed_hosts
    assert settings.database.host in settings.environment_safety.allowed_database_hosts
    login = _client(replace(settings.api, auth_token=None))
    try:
        credentials = settings.authentication.privileged
        response = AuthAPI(login).login(credentials.email, credentials.password)
        assert response.status_code == 200, "BLOCKED_AUTH"
        body = response.json()
        token, actor_id = AuthResponsePayload.token(body), int(body["data"]["user"]["id"])
    finally:
        login.close()
    client = _client(replace(settings.api, auth_token=token))
    repository = PortfolioFixtureRepository(DatabaseClient.from_settings(settings.database), environment="test")
    try:
        yield client, repository, actor_id
    finally:
        client.close()


@pytest.mark.parametrize("case", _CASES, ids=lambda row: row["id"])
def test_portfolio_amounts_from_raw_accounts(case: dict[str, Any], portfolio_source_runtime: Any,
                                            record_property: Any) -> None:
    """逐项对比真实 portfolio 数值/缺失传播、身份和清理，禁止伪造汇总输出。"""
    client, repository, actor_id = portfolio_source_runtime
    report = _ROOT / "reports" / ("quant-portfolio-source-" + case["id"] + "-" + uuid4().hex + ".json")

    def save(evidence: dict[str, Any]) -> None:
        report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps({"case_id": case["id"], "title": case["title"], **evidence},
                                     ensure_ascii=False, indent=2), encoding="utf-8")

    record_property("formula_family", case["family"])
    flow = PortfolioSourceFlow(repository, PortfolioFixtureAPI(client), actor_id, evidence_sink=save)
    inputs = case["inputs"]
    with flow.execute(inputs["accounts"], selected_indexes=inputs.get("selected_indexes"),
                      duplicate_members=inputs.get("duplicate_members", False),
                      duplicate_account_bindings=inputs.get("duplicate_account_bindings", False)) as evidence:
        response = evidence["summary_response"]
        assert response["http_status"] == 200, f"BLOCKED_SOURCE_ADAPTER: HTTP {response['http_status']}"
        body = response["body"]
        assert body["success"] is True
        assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
        assert body["meta"]["mock"] is False
        assert body["data"]["context"]["subject_type"] == "portfolio"
        assert body["data"]["counts"]["account_count"] == case["expected"]["account_count"]
        if inputs.get("duplicate_account_bindings"):
            assert body["data"]["counts"]["strategy_count"] == 3
            assert len(evidence["raw_assignments"]) == 3
        for field, expected in case["expected"].items():
            if field == "account_count":
                continue
            actual = body["data"]["metrics"][field]
            if expected is None:
                assert actual["value"] is None, field
                assert actual["status"] in {"unavailable", "partial"}, field
                assert actual.get("reason_code"), field
            else:
                assert actual["status"] == "complete", f"{field}: {actual}"
                assert Decimal(actual["value"]) == Decimal(expected), field
                if field == "cumulative_return":
                    assert actual["formula_version"] == case["profile"]
        evidence["assertion"] = {"expected_values": case["expected"], "passed": True}
    assert evidence["cleanup_verified_zero"] is True
