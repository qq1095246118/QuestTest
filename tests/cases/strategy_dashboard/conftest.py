"""Shared fresh, read-only current-overview runtime; no business data preparation."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from api.strategy_dashboard_current_overview_api import CurrentOverviewAPI
from db.client import DatabaseClient
from db.strategy_dashboard_current_overview_repository import CurrentOverviewRepository
from service.strategy_dashboard_current_overview_audit import CurrentOverviewAudit
from service.strategy_dashboard_current_overview_queries import CurrentOverviewQueries


def _client(settings: Any) -> HTTPClient:
    session = requests.Session()
    proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
    if proxy:
        session.trust_env = False
        session.proxies = {"http": proxy, "https": proxy}
    return HTTPClient(settings, session=session)


@pytest.fixture(scope="session")
def current_overview_runtime(settings: Any, pytestconfig: Any) -> Iterator[dict[str, Any]]:
    """Yield test-only readers, timezone-aware boundaries and a compact evidence sink.

    Explicit live/environment and host checks run before login. Transport/auth/DB
    errors propagate, without changing accounts, bindings or source inputs.
    Evidence is generated per completed collection, never loaded from old files.
    """
    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: requires --live --env test")
    assert pytestconfig.getoption("--env") == settings.environment == "test"
    assert urlparse(settings.api.base_url).hostname in settings.environment_safety.allowed_hosts
    assert settings.database.host in settings.environment_safety.allowed_database_hosts
    now = datetime.now(timezone(timedelta(hours=8)))
    boundaries = {
        "from_at": os.environ.get("QUEST_OVERVIEW_FROM", (now - timedelta(days=29)).replace(
            hour=0, minute=0, second=0, microsecond=0).isoformat()),
        "to_at": os.environ.get("QUEST_OVERVIEW_TO", now.isoformat()),
    }
    parsed = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in boundaries.values()]
    assert all(value.utcoffset() is not None for value in parsed), "QUERY_BOUNDARIES_REQUIRE_TIMEZONE"
    assert parsed[0] < parsed[1], "QUERY_BOUNDARIES_REQUIRE_POSITIVE_RANGE"
    login = _client(replace(settings.api, auth_token=None))
    try:
        credentials = settings.authentication.privileged
        response = AuthAPI(login).login(credentials.email, credentials.password)
        assert response.status_code == 200, "BLOCKED_AUTH"
        token = AuthResponsePayload.token(response.json())
    finally:
        login.close()
    client = _client(replace(settings.api, auth_token=token))

    def save(evidence: dict[str, Any]) -> None:
        report = Path(__file__).resolve().parents[3] / "reports" / ("quant-current-overview-" + uuid4().hex + ".json")
        evidence.update(collected_at=datetime.now(timezone.utc).isoformat(), evidence_report=str(report))
        report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    try:
        repository = CurrentOverviewRepository(DatabaseClient.from_settings(replace(settings.database, read_only=True)))
        audit = CurrentOverviewAudit(repository, CurrentOverviewAPI(client))
        yield {"audit": audit, "queries": CurrentOverviewQueries(audit, save), "save": save, **boundaries}
    finally:
        client.close()
