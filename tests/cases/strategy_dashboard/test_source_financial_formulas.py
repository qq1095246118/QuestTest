"""M03 source-snapshot injection and real API calculation, with owned-row cleanup."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
import requests

from api.auth_api import AuthAPI, AuthResponsePayload
from api.client import HTTPClient
from api.strategy_dashboard_source_fixture_api import SourceFixtureAPI
from db.client import DatabaseClient
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository
from service.strategy_dashboard_financial_source_flow import FinancialSourceFlow
from tests.strategy_dashboard_formula_routes import SOURCE_CUMULATIVE_CASE_IDS
from service.strategy_dashboard_source_fixture_flow import SourceFixtureFlow


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]
_GOLDENS = json.loads((_ROOT / "tests/data/strategy_dashboard/formula_finance.json").read_text(encoding="utf-8"))
_CASES = [item for item in _GOLDENS if item["id"] in SOURCE_CUMULATIVE_CASE_IDS]


def _client(settings: Any) -> HTTPClient:
    session = requests.Session()
    proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
    if proxy:
        session.trust_env = False
        session.proxies = {"http": proxy, "https": proxy}
    return HTTPClient(settings, session=session)


@pytest.fixture(scope="module")
def financial_source_runtime(settings: Any, pytestconfig: Any):
    """Authenticate only after explicit test/live gates and hostname checks."""
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
        token = AuthResponsePayload.token(body)
        actor_id = int(body["data"]["user"]["id"])
    finally:
        login.close()
    client = _client(replace(settings.api, auth_token=token))
    repository = SourceFixtureRepository(DatabaseClient.from_settings(settings.database), environment=settings.environment)
    try:
        yield client, repository, actor_id
    finally:
        client.close()


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["id"])
def test_cumulative_return_is_calculated_from_owned_source_fields(case: dict[str, Any], financial_source_runtime: Any) -> None:
    """Submit only G/D inputs; compare the real ratio and verify exact cleanup."""
    client, repository, actor_id = financial_source_runtime
    report = _ROOT / "reports" / ("quant-financial-source-" + case["id"] + "-" + uuid4().hex + ".json")

    def save(evidence: dict[str, Any]) -> None:
        report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps({"case_id": case["id"], "title": case["title"], **evidence}, ensure_ascii=False, indent=2), encoding="utf-8")

    source_flow = SourceFixtureFlow(repository, SourceFixtureAPI(client), actor_id, evidence_sink=save)
    with FinancialSourceFlow(source_flow).cumulative_return(case["inputs"]) as evidence:
        result = evidence["summary_response"]
        assert result["http_status"] == 200
        body = result["body"]
        assert body["success"] is True
        assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
        assert body["meta"]["generation"] == evidence["fixture_id"] + "_batch"
        assert body["meta"]["mock"] is False
        assert str(body["data"]["context"]["subject_id"]) == str(evidence["account_id"])
        metric = body["data"]["metrics"]["cumulative_return"]
        expected = case["expected"]["cumulative_return"]
        if expected is None:
            assert metric["value"] is None
            assert metric["status"] == "unavailable"
            assert metric.get("reason_code")
        else:
            assert metric["status"] == "complete"
            assert metric["formula_version"] == "exchange_cumulative_realized_latest_net_deposit_v1"
            assert Decimal(metric["value"]) == Decimal(expected)
        inputs = evidence["source"]["snapshots"][0]["Payload"]
        assert "cumulative_return" not in inputs
        evidence["assertion"] = {"expected": expected, "actual": metric, "passed": True}
    assert evidence["cleanup_verified_zero"] is True
