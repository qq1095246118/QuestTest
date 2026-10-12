"""Fixed historical scenarios evaluated by the deployed raw-business formula engine."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
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
from service.strategy_dashboard_history_formula_inputs import HISTORY_METRIC_NAMES, history_input_plan, bind_history_payload, bind_realized_events
from service.strategy_dashboard_raw_business_fixture import RawBusinessFixtureFlow
from tests.strategy_dashboard_history_routes import HISTORY_SOURCE_IDS


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]
_CATALOG = [case for name in ("formula_finance.json", "formula_extended.json")
            for case in json.loads((_ROOT / "tests/data/strategy_dashboard" / name).read_text(encoding="utf-8"))]
_CASES = [case for case in _CATALOG if case["id"] in HISTORY_SOURCE_IDS]
_EVENT_PROFILES = [case for case in json.loads((_ROOT / "tests/data/strategy_dashboard/source_statistics_profiles.json").read_text())
                   if case["kind"] == "trades"]


def _client(settings: Any) -> HTTPClient:
    session = requests.Session()
    proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
    if proxy:
        session.trust_env = False
        session.proxies = {"http": proxy, "https": proxy}
    return HTTPClient(settings, session=session)


@pytest.fixture(scope="module")
def history_runtime(settings: Any, pytestconfig: Any):
    """Authenticate only after explicit test/live host gates."""
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
    repository = SourceFixtureRepository(DatabaseClient.from_settings(settings.database), environment="test")
    try:
        yield client, repository, actor_id
    finally:
        client.close()


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["id"])
def test_raw_history_computes_fixed_formula_scenario(case: dict[str, Any], history_runtime: Any) -> None:
    """Compare the actual raw-history calculation with the existing fixed scenario expectation."""
    client, repository, actor_id = history_runtime
    plan = history_input_plan(case["inputs"])
    report = _ROOT / "reports" / ("quant-raw-history-" + case["id"] + "-" + uuid4().hex + ".json")

    def save(evidence: dict[str, Any]) -> None:
        if "Gbase" in case["inputs"]:
            evidence["cycle_prerequisite"] = "first_cycle_no_historical_clearance_Gclear_0"
        report.write_text(json.dumps({"case_id": case["id"], "title": case["title"], **evidence}, ensure_ascii=False, indent=2), encoding="utf-8")

    flow = RawBusinessFixtureFlow(repository, RawBusinessFixtureAPI(client), actor_id, evidence_sink=save)
    with flow.execute(lambda base: bind_history_payload(base, plan), metrics=HISTORY_METRIC_NAMES[case["family"]],
                      context={"from": plan["from"], "to": plan["to"]}) as evidence:
        response = evidence["summary_response"]
        assert response["http_status"] == 200
        body = response["body"]
        assert body["success"] is True and body["meta"]["mock"] is False
        assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
        assert str(body["data"]["context"]["subject_id"]) == str(evidence["account_id"])
        if "Gbase" in case["inputs"]:
            # Prove the competing cycle baseline is actually consumable by the
            # same deployed engine; merely placing it in JSON is insufficient.
            cycle_response = RawBusinessFixtureAPI(client).summary(
                evidence["account_id"], evidence["fixture_id"], metrics="realized_pnl_gross,cumulative_return",
                scope="cycle", metric_basis="display")
            evidence["cycle_binding_response"] = cycle_response
            assert cycle_response["http_status"] == 200
            cycle_body = cycle_response["body"]
            assert cycle_body["meta"]["snapshot_id"] == evidence["fixture_id"]
            resolved = cycle_body["data"]["context"]["resolved_cycles"]
            assert len(resolved) == 1 and resolved[0]["cycle_id"] == evidence["fixture_id"] + "_cycle"
            cycle_metric = cycle_body["data"]["profit_sharing"]["return_rate"]
            assert cycle_metric["status"] == "complete"
            assert Decimal(cycle_metric["eligible_profit"]) == Decimal(case["inputs"]["G"]) - Decimal(case["inputs"]["Gbase"])
        for bound in ("from", "to"):
            actual_time = datetime.fromisoformat(body["data"]["context"][bound].replace("Z", "+00:00"))
            expected_time = datetime.fromisoformat(plan[bound].replace("Z", "+00:00"))
            assert actual_time.astimezone(timezone.utc) == expected_time.astimezone(timezone.utc), f"{bound}: formula window changed"
        expected = case["expected"]
        mappings = {"annualized_volatility": "volatility", "sharpe": "sharpe_ratio", "calmar": "calmar_ratio"}
        evidence["assertions"] = []
        for field, value in expected.items():
            if field in ("status", "sample_count"):
                continue
            actual = body["data"]["metrics"][mappings.get(field, field)]
            evidence["assertions"].append({"field": field, "expected": value, "actual": actual})
            if value is None:
                assert actual["value"] is None and actual["status"] == "unavailable", evidence["assertions"]
                if case["family"] == "annualized_volatility":
                    assert actual.get("reason_code") != "INSUFFICIENT_DURATION", (
                        "Volatility sample/missing-data rejection was masked by an unrelated seven-day gate", evidence["assertions"])
            else:
                assert actual["status"] == "complete" and actual["value"] is not None, evidence["assertions"]
                # Backend emits 8 decimals. Keep the original high-precision golden,
                # compare at the observed API output precision without changing it.
                assert abs(Decimal(actual["value"]) - Decimal(value)) <= Decimal("0.00000001"), evidence["assertions"]
        evidence["assertion_passed"] = True
    assert evidence["cleanup_verified_zero"] is True


@pytest.mark.parametrize("case", _EVENT_PROFILES, ids=lambda case: case["id"])
def test_raw_realized_events_compute_current_statistics_profile(case: dict[str, Any], history_runtime: Any) -> None:
    """Test actual event classification/ratios; profile acceptance stays separate from pending business rules."""
    client, repository, actor_id = history_runtime
    start, end = "2026-09-01T00:00:00+00:00", "2026-09-09T00:00:00+00:00"
    report = _ROOT / "reports" / ("quant-raw-events-" + case["id"] + "-" + uuid4().hex + ".json")

    def save(evidence: dict[str, Any]) -> None:
        report.write_text(json.dumps({"case_id": case["id"], "title": case["title"],
                                      "rule_basis": "observed_nonzero_realized_gross_event_profile",
                                      "business_rule_approved": False, **evidence}, ensure_ascii=False, indent=2), encoding="utf-8")

    flow = RawBusinessFixtureFlow(repository, RawBusinessFixtureAPI(client), actor_id, evidence_sink=save)
    with flow.execute(lambda base: bind_realized_events(base, case, start, end), metrics="win_rate,profit_loss_ratio",
                      context={"from": start, "to": end}) as evidence:
        response = evidence["summary_response"]
        assert response["http_status"] == 200
        body = response["body"]
        assert body["success"] is True and body["meta"]["mock"] is False
        assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
        assert str(body["data"]["context"]["subject_id"]) == str(evidence["account_id"])
        evidence["assertions"] = []
        for field, expected in case["expected"].items():
            actual = body["data"]["metrics"][field]
            evidence["assertions"].append({"field": field, "expected": expected, "actual": actual})
            if expected is None:
                assert actual["value"] is None and actual["status"] == "unavailable", evidence["assertions"]
            else:
                assert actual["status"] == "complete" and actual["value"] is not None, evidence["assertions"]
                assert abs(Decimal(actual["value"]) - Decimal(expected)) <= Decimal("0.00000001"), evidence["assertions"]
        evidence["assertion_passed"] = True
    assert evidence["cleanup_verified_zero"] is True


def test_cross_metric_results_share_one_frozen_window_and_source_generation(history_runtime: Any) -> None:
    """CALC-064: risk and return cards must not silently mix snapshots or windows."""
    client, repository, actor_id = history_runtime
    inputs = {"equity_path": ["100", "110", "99", "108.9"], "days": 8, "G": "8.9", "D": "100"}
    plan = history_input_plan(inputs)
    metrics = "cumulative_return,annualized_return,volatility,sharpe_ratio,calmar_ratio,current_drawdown,max_drawdown"
    flow = RawBusinessFixtureFlow(repository, RawBusinessFixtureAPI(client), actor_id)
    with flow.execute(lambda base: bind_history_payload(base, plan), metrics=metrics,
                      context={"from": plan["from"], "to": plan["to"]},
                      from_at=plan["from"], to_at=plan["to"]) as evidence:
        response = evidence["summary_response"]
        assert response["http_status"] == 200
        body = response["body"]
        assert body["success"] is True and body["meta"]["mock"] is False
        assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
        data = body["data"]
        for boundary in ("from", "to"):
            actual = datetime.fromisoformat(data["context"][boundary].replace("Z", "+00:00"))
            expected = datetime.fromisoformat(plan[boundary].replace("Z", "+00:00"))
            assert actual == expected, {"boundary": boundary, "actual": actual, "expected": expected}
        results = data["metrics"]
        assert set(results) >= {"cumulative_return", "annualized_return", "volatility",
                                "sharpe_ratio", "calmar_ratio", "current_drawdown", "max_drawdown"}
        observed = {metric.get("observed_at") for metric in results.values()
                    if metric.get("observed_at") is not None}
        assert len(observed) <= 1, {"observed_at": sorted(observed), "metrics": results}
        assert all(metric.get("snapshot_id", evidence["fixture_id"]) == evidence["fixture_id"]
                   for metric in results.values())
    assert evidence["cleanup_verified_zero"] is True


@pytest.mark.parametrize("missing_field", ["D", "G", "E"])
def test_missing_raw_field_degrades_only_dependent_metrics(missing_field: str, history_runtime: Any) -> None:
    """CALC-065: a missing source field must not be replaced by zero or another card."""
    client, repository, actor_id = history_runtime
    inputs = {"equity_path": ["100", "105", "110"], "days": 8, "G": "10", "D": "100"}
    plan = history_input_plan(inputs)

    def build(base: dict[str, Any]) -> dict[str, Any]:
        payload = bind_history_payload(base, plan)
        for snapshot in payload["snapshots"]:
            raw = snapshot["Payload"]
            field = {"D": "net_deposit", "G": "realized_pnl_gross", "E": "margin_equity"}[missing_field]
            raw[field] = None
            raw["field_status"][field] = {"status": "unavailable", "reason_code": "SOURCE_FIELD_UNAVAILABLE",
                                            "observed_at": snapshot["ObservedAt"]}
        return payload

    metrics = "margin_equity,wallet_balance,cumulative_return,annualized_return,volatility,current_drawdown,max_drawdown"
    flow = RawBusinessFixtureFlow(repository, RawBusinessFixtureAPI(client), actor_id)
    with flow.execute(build, metrics=metrics, context={"from": plan["from"], "to": plan["to"]},
                      from_at=plan["from"], to_at=plan["to"]) as evidence:
        response = evidence["summary_response"]
        assert response["http_status"] == 200
        data = response["body"]["data"]
        results = data["metrics"]
        if missing_field in {"D", "G"}:
            for metric in ("cumulative_return", "annualized_return"):
                assert results[metric]["value"] is None
                assert results[metric]["status"] in {"unavailable", "partial"}
        else:
            for metric in ("volatility", "current_drawdown", "max_drawdown"):
                assert results[metric]["value"] is None
                assert results[metric]["status"] in {"unavailable", "partial"}
        # The fixture removes only one raw field. Wallet balance remains an
        # explicit independent source fact, rather than a required D+G derivation.
        assert Decimal(results["wallet_balance"]["value"]) == Decimal("110")
        if missing_field == "E":
            assert results["margin_equity"]["value"] is None
        else:
            assert Decimal(results["margin_equity"]["value"]) == Decimal("110")
    assert evidence["cleanup_verified_zero"] is True
