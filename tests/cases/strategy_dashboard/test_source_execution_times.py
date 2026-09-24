"""Real raw-event selection tests; not local calculator passes or latency claims."""

from __future__ import annotations

from dataclasses import replace
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
from api.strategy_dashboard_execution_fixture_api import ExecutionFixtureAPI
from api.strategy_dashboard_raw_business_api import RawBusinessFixtureAPI
from db.client import DatabaseClient
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository
from service.strategy_dashboard_execution_source_flow import ExecutionSourceFlow
from service.strategy_dashboard_raw_business_fixture import RawBusinessFixtureFlow
from service.strategy_dashboard_formula_scenarios import load_formula_catalog, formula_catalog_paths


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]
_CASES = tuple(case for case in load_formula_catalog(formula_catalog_paths(_ROOT))
               if case.family == "execution_latest_times")


def _client(settings: Any) -> HTTPClient:
    session = requests.Session()
    proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
    if proxy:
        session.trust_env = False
        session.proxies = {"http": proxy, "https": proxy}
    return HTTPClient(settings, session=session)


@pytest.fixture(scope="module")
def execution_source_runtime(settings: Any, pytestconfig: Any):
    """Authenticate only under explicit test/live configuration and retain no token evidence."""
    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: raw execution tests require --live --env test")
    assert pytestconfig.getoption("--env") == settings.environment == "test"
    assert urlparse(settings.api.base_url).hostname in settings.environment_safety.allowed_hosts
    assert settings.database.host in settings.environment_safety.allowed_database_hosts
    login = _client(replace(settings.api, auth_token=None))
    try:
        credentials = settings.authentication.privileged
        response = AuthAPI(login).login(credentials.email, credentials.password)
        assert response.status_code == 200, "BLOCKED_AUTH"
        payload = response.json()
        token = AuthResponsePayload.token(payload)
        actor_id = int(payload["data"]["user"]["id"])
    finally:
        login.close()
    client = _client(replace(settings.api, auth_token=token))
    repository = SourceFixtureRepository(DatabaseClient.from_settings(settings.database), environment="test")
    try:
        yield client, repository, actor_id
    finally:
        client.close()


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.scenario_id)
def test_raw_events_determine_independent_latest_times(case: Any, execution_source_runtime: Any) -> None:
    """Verify latest trigger/completion from raw facts on both API surfaces and clean resources."""
    client, repository, actor_id = execution_source_runtime
    report = _ROOT / "reports" / f"quant-execution-source-{case.scenario_id}-{uuid4().hex}.json"

    def save(evidence: dict[str, Any]) -> None:
        report.write_text(json.dumps({"case_id": case.scenario_id, "title": case.title,
                                      **evidence}, ensure_ascii=False, indent=2), encoding="utf-8")

    raw = RawBusinessFixtureFlow(repository, RawBusinessFixtureAPI(client), actor_id, evidence_sink=save)
    with ExecutionSourceFlow(raw, ExecutionFixtureAPI(client)).execute(case.inputs) as evidence:
        actuals = {}
        for field in ("summary_response", "risk_response"):
            response = evidence[field]
            assert response["http_status"] == 200, response
            body = response["body"]
            assert body["success"] is True and body["meta"]["mock"] is False
            assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
            assert str(body["data"]["context"]["subject_id"]) == str(evidence["account_id"])
            execution = body["data"]["execution"]
            actuals[field] = execution
            assert execution["last_signal_at"] == case.expected["latest_triggered_at"]
            assert execution["last_rebalance_completed_at"] == case.expected["latest_completed_at"]
            if case.expected["latest_triggered_at"] is not None:
                assert execution["last_signal_account_id"] == str(evidence["account_id"])
            if case.expected["latest_completed_at"] is not None:
                assert execution["last_rebalance_account_id"] == str(evidence["account_id"])
        evidence["assertion"] = {"expected": dict(case.expected), "actual": actuals, "passed": True}
        assert len(evidence["raw_business"]["events"]) == len(case.inputs["events"])
        assert all("duration_ms" not in event["Payload"] for event in evidence["raw_business"]["events"])
    assert evidence["cleanup_verified_zero"] is True
