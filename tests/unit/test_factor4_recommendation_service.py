"""Offline mutation tests for PIT selection and route reconciliation; no live claims."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from db.factor4_publication_repository import PublicationHistory
from db.factor4_read_repository import DailyReadSnapshot
from service.factor4_read_service import LABELS, ReadPrecondition
from service.factor4_recommendation_service import (
    Factor4RecommendationService, check_forecast_probabilities, lifecycle_time, visible_publication,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


class _API:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[dict[str, Any]] = []

    def recommendations(self, market: str, profile: str, **kwargs: Any) -> MCPResponse:
        """Return queued business data, record selectors; perform no I/O."""
        self.calls.append({"market": market, "profile": profile, **kwargs})
        body = {"data": self.data, "meta": {}}
        return MCPResponse(200, "application/json", {"result": {
            "structuredContent": body, "content": [{"type": "text", "text": json.dumps(body)}],
        }}, "2025-06-18")


def _batch(identifier: int = 1, **kwargs: Any) -> dict[str, Any]:
    return {"id": identifier, "batch_uid": f"b{identifier}", "publication_uid": f"p{identifier}",
            "market_scope": "all", "route_profile_key": "default", "publish_version": identifier,
            "published_at": "2026-09-05T00:00:00Z", "is_active": 1, **kwargs}


def test_visible_publication_does_not_use_current_active_flag_or_foreign_profile() -> None:
    old = _batch(1, is_active=0)
    new = _batch(2, published_at=NOW.isoformat())
    foreign = _batch(3, route_profile_key="other")
    history = PublicationHistory(NOW, (old, new, foreign), ())
    assert visible_publication(history, "all", "default", NOW - timedelta(microseconds=1)) == old
    assert visible_publication(history, "all", "default", NOW) == new
    assert visible_publication(history, "missing", "default", NOW) is None


def test_lifecycle_time_handles_database_timezone_without_accepting_naive_api() -> None:
    assert lifecycle_time(datetime(2026, 9, 6, 8), database=True) == NOW
    with pytest.raises(ValueError):
        lifecycle_time(datetime(2026, 9, 6, 8))


def test_publication_boundary_detects_future_identity_and_missing_reason() -> None:
    batch = _batch(1, published_at=NOW.isoformat())
    api = _API({"publication": batch, "forecast": None, "items": [], "returned_count": 0, "status": "ready"})
    history = PublicationHistory(NOW, (batch,), ())
    check = Factor4RecommendationService(api).check_publication_boundary(history, -1)
    assert "recommendation:future_or_foreign_publication" in check.issues
    assert api.calls[0]["as_of"] == (NOW - timedelta(microseconds=1)).isoformat()
    assert not Factor4RecommendationService(api).check_publication_boundary(history, 0).issues


def test_no_publication_requires_no_recommendation_and_explicit_reason() -> None:
    api = _API({"publication": None, "forecast": None, "items": [], "returned_count": 0,
                "status": "no_recommendation", "reason_code": "ACTIVE_PUBLICATION_NOT_FOUND"})
    service = Factor4RecommendationService(api)
    history = PublicationHistory(NOW, (), ())
    assert not service.check_publication_at(history, "all", "default", NOW).issues
    api.data["reason_code"] = None
    assert service.check_publication_at(history, "all", "default", NOW).issues


def _route_fixture() -> tuple[_API, PublicationHistory, DailyReadSnapshot]:
    batch = _batch()
    forecast = {"id": 10, "label_kind": "forecast", "label_status": "ready", "label_code": "CHOPPY_UP",
                "revision": 1, "available_at": NOW.isoformat(), "environment_date": "2026-09-06"}
    route = {"id": 20, "eval_batch_id": 1, "metric_id": 2, "factor_ref": "sub_factor:3", "rank_no": 1,
             "routing_score": 75.5, "confidence": 0.8, "is_active": 1, "is_eligible": 1,
             "label_kind": "fact", "label_code": "CHOPPY_UP", "factor_type": "sub_factor", "factor_id": 3,
             "factor_version": "v1", "score_rule_version": "s1", "time_series_score": 75.5, "cross_sectional_score": None}
    public_route = {k: v for k, v in route.items() if k not in {"id", "eval_batch_id", "metric_id", "is_active", "is_eligible", "label_kind", "label_code"}}
    public_forecast = {k: v for k, v in forecast.items() if k not in {"id", "label_kind"}}
    api = _API({"status": "ready", "reason_code": None, "publication": deepcopy(batch),
                "forecast": public_forecast, "items": [public_route], "returned_count": 1})
    return api, PublicationHistory(NOW, (batch,), (route,)), DailyReadSnapshot(NOW, (forecast,))


def test_current_recommendation_reconciles_exact_route_and_replays_fixed_instant() -> None:
    api, history, daily = _route_fixture()
    assert not Factor4RecommendationService(api).check_current_routes(history, daily, limit=1).issues
    assert len(api.calls) == 2 and api.calls[0] == api.calls[1]


@pytest.mark.parametrize("field,value", [("routing_score", 0), ("factor_id", 99), ("rank_no", 2),
                                          ("factor_version", "v2"), ("factor_ref", "sub_factor:99")])
def test_current_recommendation_catches_route_mutations(field: str, value: Any) -> None:
    api, history, daily = _route_fixture()
    api.data["items"][0][field] = value
    assert Factor4RecommendationService(api).check_current_routes(history, daily, limit=1).issues


def _probabilities() -> dict[str, Any]:
    return {"id": 1, "label_kind": "forecast", "label_status": "ready", "label_code": "CHOPPY_UP",
            "probabilities": dict(zip(LABELS, [0.1, 0.5, 0.1, 0.1, 0.1, 0.1], strict=True))}


def test_probability_contract_accepts_only_normalized_six_label_distribution() -> None:
    assert not check_forecast_probabilities((_probabilities(),)).issues
    with pytest.raises(ReadPrecondition):
        check_forecast_probabilities(())


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", -0.1, 1.1, 0.4])
def test_probability_domain_and_sum_catch_mutations(value: Any) -> None:
    row = _probabilities()
    row["probabilities"]["UNILATERAL_UP"] = value
    assert check_forecast_probabilities((row,)).issues
