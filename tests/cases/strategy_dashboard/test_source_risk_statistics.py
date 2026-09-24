"""Natural-source risk recomputation; distinct from synthetic formula scenario coverage."""

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
from api.strategy_dashboard_source_statistics_api import SourceStatisticsAPI
from db.client import DatabaseClient
from db.strategy_dashboard_source_statistics_repository import SourceStatisticsRepository
from service.strategy_dashboard_source_statistics import SourceStatisticsAudit


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]


def _client(settings: Any) -> HTTPClient:
    session = requests.Session()
    proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
    if proxy:
        session.trust_env = False
        session.proxies = {"http": proxy, "https": proxy}
    return HTTPClient(settings, session=session)


@pytest.fixture(scope="module")
def source_risk_statistics(settings: Any, pytestconfig: Any) -> list[dict[str, Any]]:
    """Authenticate under explicit live gates and save all natural inputs/results."""
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
        token = AuthResponsePayload.token(response.json())
    finally:
        login.close()
    client = _client(replace(settings.api, auth_token=token))
    try:
        repository = SourceStatisticsRepository(DatabaseClient.from_settings(replace(settings.database, read_only=True)))
        records = SourceStatisticsAudit(repository, SourceStatisticsAPI(client)).collect()
    finally:
        client.close()
    report = _ROOT / "reports" / ("quant-source-risk-statistics-" + uuid4().hex + ".json")
    report.write_text(json.dumps({"scope": "natural_source_current_version_recomputation",
                                  "not_covered": ["synthetic boundary scenarios", "upstream raw event classification",
                                                  "upstream Sharpe and maximum drawdown calculation"],
                                  "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


@pytest.mark.parametrize("metric", ["sortino_ratio", "volatility", "win_rate", "profit_loss_ratio", "calmar_ratio"])
def test_actual_source_inputs_recompute_deployed_statistics(metric: str, source_risk_statistics: list[dict[str, Any]]) -> None:
    """Compare independent expectations and input window/version for every usable account."""
    checked = 0
    gaps = []
    failures = []
    for record in source_risk_statistics:
        expected = record["expected"].get(metric)
        if expected is None:
            gaps.append({"account": record["account_id"], "gaps": record["source_gaps"]})
            continue
        actual = record["response"]["body"]["data"]["metrics"][metric]
        try:
            for identity in ("formula_version", "sample_count", "from", "to", "observed_at"):
                if identity in expected:
                    assert actual.get(identity) == expected[identity], (identity, expected, actual)
            if metric in ("sortino_ratio", "volatility"):
                assert actual["year_days"] == 365
                assert actual["sampling_period_seconds"] == 86400
                assert Decimal(actual["risk_free_rate_per_period"]) == 0
            elif metric in ("win_rate", "profit_loss_ratio"):
                assert actual["sample_unit"] == "nonzero_realized_income_event"
                assert actual["pnl_basis"] == "realized_gross"
            if expected["value"] is None:
                assert actual["value"] is None and actual["status"] == "unavailable"
            else:
                assert actual["status"] == "complete" and actual["value"] is not None
                assert abs(Decimal(actual["value"]) - Decimal(expected["value"])) <= Decimal("0.00000001")
            checked += 1
        except AssertionError as error:
            failures.append({"account": record["account_id"], "metric": metric,
                             "expected": expected, "actual": actual, "detail": str(error)})
    assert not failures, failures
    assert checked > 0, {"reason": "NO_VERIFIABLE_SOURCE_SAMPLE", "gaps": gaps}
