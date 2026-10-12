"""Real cumulative endpoints to interval net PnL and net-deposit change."""

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
from api.strategy_dashboard_raw_business_api import RawBusinessFixtureAPI
from db.client import DatabaseClient
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository
from service.strategy_dashboard_capital_source_flow import CapitalSourceFlow
from service.strategy_dashboard_formula_scenarios import load_formula_catalog
from service.strategy_dashboard_raw_business_fixture import RawBusinessFixtureFlow


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]
_INTERVAL_IDS = {f"FM-INTERVAL-{i:03d}" for i in (1, 2, 3, 4, 5, 9)} | {"FXT-INTERVAL-001", "FXT-INTERVAL-003"}
_CASES = tuple(case for case in load_formula_catalog([
    _ROOT / "tests/data/strategy_dashboard/formula_finance.json",
    _ROOT / "tests/data/strategy_dashboard/formula_extended.json",
    _ROOT / "tests/data/strategy_dashboard/formula_deposit_change.json",
]) if case.scenario_id in _INTERVAL_IDS or case.family == "net_deposit_change")


def _client(settings: Any) -> HTTPClient:
    session = requests.Session()
    proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
    if proxy:
        session.trust_env = False
        session.proxies = {"http": proxy, "https": proxy}
    return HTTPClient(settings, session=session)


@pytest.fixture(scope="module")
def capital_source_runtime(settings: Any, pytestconfig: Any):
    """Authenticate only in explicitly selected test/live environment."""
    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: cumulative endpoint injection requires --live --env test")
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
    repo = SourceFixtureRepository(DatabaseClient.from_settings(settings.database), environment="test")
    try:
        yield client, repo, actor_id
    finally:
        client.close()


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.scenario_id)
def test_raw_capital_endpoints_produce_interval_amount(case: Any, capital_source_runtime: Any) -> None:
    """Check the actual output against a fixed independent expected amount and cleanup."""
    client, repo, actor_id = capital_source_runtime
    metric, expected_field = ("net_trading_pnl", "interval_net_profit") if case.family == "interval_profit" else ("net_deposit", "net_deposit_change")
    report = _ROOT / "reports" / f"quant-capital-source-{case.scenario_id}-{uuid4().hex}.json"

    def save(evidence: dict[str, Any]) -> None:
        report.write_text(json.dumps({"case_id": case.scenario_id, "title": case.title,
                                      **evidence}, ensure_ascii=False, indent=2), encoding="utf-8")

    raw = RawBusinessFixtureFlow(repo, RawBusinessFixtureAPI(client), actor_id, evidence_sink=save)
    with CapitalSourceFlow(raw).execute(case.inputs, metric=metric) as evidence:
        response = evidence["summary_response"]
        assert response["http_status"] == 200, response
        body = response["body"]
        assert body["success"] is True and body["meta"]["mock"] is False
        assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
        assert body["data"]["context"]["subject_id"] == str(evidence["account_id"])
        actual = body["data"]["metrics"][metric]
        expected = case.expected[expected_field]
        evidence["assertion"] = {"expected": dict(case.expected), "actual": actual}
        if expected is None:
            assert actual["value"] is None and actual["status"] == "unavailable", actual
            start_currency = case.inputs["start"].get("currency", "USDT")
            end_currency = case.inputs["end"].get("currency", "USDT")
            if start_currency != end_currency:
                reasons = {"CURRENCY_MISMATCH", "UNSUPPORTED_CURRENCY"}
            else:
                reasons = {"SOURCE_FIELD_UNAVAILABLE", "BASELINE_UNAVAILABLE", "DEPENDENCY_INCOMPLETE", "SNAPSHOT_SOURCE_UNAVAILABLE"}
            assert actual.get("reason_code") in reasons, ("Expected dependency guard was masked", actual)
        else:
            assert actual["status"] == "complete", actual
            assert Decimal(actual["value"]) == Decimal(expected), actual
        assert all("net_trading_pnl" not in snapshot["Payload"] for snapshot in evidence["raw_business"]["snapshots"])
        evidence["assertion"]["passed"] = True
    assert evidence["cleanup_verified_zero"] is True
