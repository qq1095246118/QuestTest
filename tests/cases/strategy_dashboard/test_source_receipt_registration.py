"""Real receipt-event aggregation, separate from confirmed financial ledger reads."""

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
from api.strategy_dashboard_receipt_fixture_api import ReceiptFixtureAPI
from db.client import DatabaseClient
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository
from service.strategy_dashboard_formula_scenarios import load_formula_scenarios
from service.strategy_dashboard_raw_business_fixture import RawBusinessFixtureFlow
from service.strategy_dashboard_receipt_source_flow import ReceiptSourceFlow


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]
_CASES = load_formula_scenarios(_ROOT / "tests/data/strategy_dashboard/formula_receipt_registration.json")


def _client(settings: Any) -> HTTPClient:
    session = requests.Session()
    proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
    if proxy:
        session.trust_env = False
        session.proxies = {"http": proxy, "https": proxy}
    return HTTPClient(settings, session=session)


@pytest.fixture(scope="module")
def receipt_source_runtime(settings: Any, pytestconfig: Any):
    """Authenticate under explicit live/test gates without saving tokens."""
    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: receipt source tests require --live --env test")
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
def test_raw_receipts_calculate_registration_totals(case: Any, receipt_source_runtime: Any) -> None:
    """Assert fixed event-derived totals on every page and distinguish ledger inputs."""
    client, repository, actor_id = receipt_source_runtime
    report = _ROOT / "reports" / f"quant-receipt-source-{case.scenario_id}-{uuid4().hex}.json"

    def save(evidence: dict[str, Any]) -> None:
        report.write_text(json.dumps({"case_id": case.scenario_id, "title": case.title,
                                      **evidence}, ensure_ascii=False, indent=2), encoding="utf-8")

    flow = RawBusinessFixtureFlow(repository, RawBusinessFixtureAPI(client), actor_id, evidence_sink=save)
    with ReceiptSourceFlow(flow, ReceiptFixtureAPI(client)).execute(case.inputs) as evidence:
        items = []
        pending_observations = []
        for page in evidence["detail_pages"]:
            assert page["http_status"] == 200, page
            body = page["body"]
            assert body["success"] is True and body["meta"]["mock"] is False
            assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
            data = body["data"]
            assert data["bill_id"] == evidence["fixture_id"] + "_bill"
            assert data["account_id"] == str(evidence["account_id"])
            for metric in ("registered_received_amount", "pending_received_amount"):
                assert Decimal(data[metric]) == Decimal(case.expected[metric]), (metric, data, case.expected)
            if "last_registered_at" in case.expected:
                assert data["last_registered_at"] == case.expected["last_registered_at"]
            else:
                assert "last_registered_at" in case.inputs["pending_fields"]
                pending_observations.append({"field": "last_registered_at", "actual": data["last_registered_at"],
                                             "contract": dict(case.inputs["time_contract"])})
            assert data["receipts"]["total"] == case.expected["receipt_count"]
            assert data["receipts"]["total_status"] == "exact"
            # These are deliberately separate ledger inputs, not confirmed-event
            # aggregates under test. Keeping them unequal detects a readback fake.
            assert Decimal(data["received_amount"]) == Decimal(case.inputs["bill"]["ledger_received"])
            assert data["last_received_at"] == case.inputs["bill"]["ledger_last_received_at"]
            items.extend(data["receipts"]["items"])
        expected_ids = {evidence["fixture_id"] + "_" + row["id"] for row in case.inputs["receipts"]
                        if row.get("bill_id", "selected") == "selected"}
        assert {row["receipt_id"] for row in items} == expected_ids
        assert len(items) == len(expected_ids)
        assert evidence["detail_pages"][-1]["body"]["data"]["receipts"]["has_more"] is False
        evidence["assertion"] = {"expected": dict(case.expected), "passed": True,
                                  "profile": case.inputs["implementation_profile"],
                                  "pending_fields": list(case.inputs.get("pending_fields", [])),
                                  "pending_observations": pending_observations,
                                  "not_claimed": ["confirmed ledger recomputation", "receipt confirmation", "billing generation"]}
    assert evidence["cleanup_verified_zero"] is True
