"""Offline mutation tests for PIT selection and route reconciliation; no live claims."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from db.factor4_publication_repository import PublicationHistory
from db.factor4_read_repository import DailyReadSnapshot
from service.factor4_read_service import LABELS, ReadPrecondition, _DAILY_FIELDS
from service.factor4_recommendation_service import (
    Factor4RecommendationService, check_forecast_probabilities, lifecycle_time, visible_publication,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


class _API:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[dict[str, Any]] = []
        self.daily_calls: list[dict[str, Any]] = []
        self.daily_rows: list[dict[str, Any]] = []

    def daily(self, label_kind: str, **kwargs: Any) -> MCPResponse:
        """Return an independent public daily fixture; record the real as_of selector."""
        self.daily_calls.append({"label_kind": label_kind, **kwargs})
        body = {"data": {"items": self.daily_rows}, "meta": {"next_cursor": None, "truncated": False}}
        return MCPResponse(200, "application/json", {"result": {
            "structuredContent": body, "content": [{"type": "text", "text": json.dumps(body)}],
        }}, "2025-06-18")

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
             "publication_uid": batch["publication_uid"], "publish_version": batch["publish_version"],
             "factor_version": "v1", "score_rule_version": "s1", "time_series_score": 75.5, "cross_sectional_score": None}
    public_route = {k: v for k, v in route.items() if k not in {"id", "eval_batch_id", "metric_id", "is_active", "is_eligible", "label_kind", "label_code"}}
    public_forecast = {k: v for k, v in forecast.items() if k not in {"id", "label_kind"}}
    api = _API({"status": "ready", "reason_code": None, "publication": deepcopy(batch),
                "forecast": public_forecast, "items": [public_route], "returned_count": 1})
    api.daily_rows = [{key: forecast.get(key) for key in _DAILY_FIELDS}]
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


@pytest.mark.parametrize("label", LABELS)
def test_each_historical_label_uses_its_own_visible_revision_and_deactivated_routes(label: str) -> None:
    api, history, daily = _route_fixture()
    other = next(value for value in LABELS if value != label)
    old_forecast = {**daily.rows[0], "label_code": label}
    newer = {**old_forecast, "id": 11, "label_code": other, "environment_date": "2026-09-07",
             "available_at": (NOW + timedelta(days=1)).isoformat()}
    old_batch = {**history.batches[0], "is_active": 0}
    new_batch = _batch(2, published_at=(NOW + timedelta(days=1)).isoformat())
    old_route = {**history.routes[0], "is_active": 0, "label_code": label,
                 "publication_uid": old_batch["publication_uid"], "publish_version": old_batch["publish_version"]}
    history = PublicationHistory(NOW + timedelta(days=1), (old_batch, new_batch), (old_route,))
    daily = DailyReadSnapshot(NOW + timedelta(days=1), (old_forecast, newer))
    api.data["publication"] = deepcopy(old_batch)
    api.data["forecast"]["label_code"] = label
    api.daily_rows[0]["label_code"] = label
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, label)
    assert not result.issues and not result.evidence["blocked"]
    assert result.checked_count == 1
    assert api.calls[0]["as_of"] == api.daily_calls[0]["as_of"] == NOW.isoformat()
    assert result.evidence["samples"][0]["batch_id"] == 1


@pytest.mark.parametrize("label", LABELS)
def test_each_label_accepts_correct_empty_recommendation_without_requiring_routes(label: str) -> None:
    api, history, daily = _route_fixture()
    daily.rows[0]["label_code"] = label
    api.daily_rows[0]["label_code"] = api.data["forecast"]["label_code"] = label
    history = PublicationHistory(NOW, history.batches, ())
    api.data.update(items=[], returned_count=0, status="no_recommendation", reason_code="NO_ELIGIBLE_FACTOR")
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, label)
    assert not result.issues and not result.evidence["blocked"] and result.checked_count == 1
    assert result.evidence["samples"][0]["returned_count"] == 0


def test_historical_empty_environment_rejects_fallback_to_other_labels_routes() -> None:
    api, history, daily = _route_fixture()
    history.routes[0]["label_code"] = "WIDE_RANGE"
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert any("factor_membership" in issue for issue in result.issues)
    assert any("no_eligible_reason" in issue for issue in result.issues)


def test_historical_label_that_never_was_selected_is_a_sample_gap_not_a_fake_request() -> None:
    api, history, daily = _route_fixture()
    higher = {**daily.rows[0], "id": 11, "revision": 2, "label_code": "WIDE_RANGE"}
    daily = DailyReadSnapshot(NOW, (*daily.rows, higher))
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert not result.issues and result.evidence["blocked"] and result.checked_count == 0
    assert not api.calls and not api.daily_calls


def test_forecast_and_publication_interval_intersection_can_start_at_publication() -> None:
    api, history, daily = _route_fixture()
    published = NOW + timedelta(hours=1)
    batch = {**history.batches[0], "published_at": published.isoformat()}
    next_forecast = {**daily.rows[0], "id": 11, "revision": 2, "label_code": "WIDE_RANGE",
                     "available_at": (NOW + timedelta(hours=2)).isoformat()}
    history = PublicationHistory(NOW + timedelta(hours=3), (batch,), history.routes)
    daily = DailyReadSnapshot(history.as_of, (*daily.rows, next_forecast))
    api.data["publication"] = deepcopy(batch)
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert not result.issues and not result.evidence["blocked"]
    assert api.calls[0]["as_of"] == published.isoformat()


def test_historical_other_partition_sample_gap_does_not_mask_a_confirmed_route_failure() -> None:
    api, history, daily = _route_fixture()
    inaccessible = _batch(2, market_scope="spot", published_at=(NOW + timedelta(days=1)).isoformat())
    history = PublicationHistory(NOW, (*history.batches, inaccessible), history.routes)
    api.data["items"][0]["routing_score"] = 0
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert result.issues and result.evidence["blocked"] and result.checked_count == 1
    assert len(api.calls) == 1 and api.calls[0]["market"] == "all"


def test_historical_selection_respects_the_earlier_database_snapshot_clock() -> None:
    api, history, daily = _route_fixture()
    history = PublicationHistory(NOW - timedelta(microseconds=1), history.batches, history.routes)
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert not result.issues and result.evidence["blocked"] and not api.calls


def test_historical_daily_projection_failure_is_not_hidden_by_correct_recommendation() -> None:
    api, history, daily = _route_fixture()
    api.daily_rows[0]["revision"] = 99
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert any("public_forecast:" in issue for issue in result.issues)


def test_historical_label_does_not_accept_routes_from_another_publication_of_same_batch() -> None:
    api, history, daily = _route_fixture()
    history.routes[0]["publication_uid"] = "different-publication"
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert any("factor_membership" in issue for issue in result.issues)


def test_historical_label_requires_a_declared_code() -> None:
    api, history, daily = _route_fixture()
    with pytest.raises(ValueError):
        Factor4RecommendationService(api).check_forecast_label_history(history, daily, "not-a-label")
    assert not api.calls and not api.daily_calls


@pytest.mark.parametrize("field", ["batch_uid", "publish_version", "market_scope", "route_profile_key"])
def test_historical_forecast_requires_complete_publication_identity(field: str) -> None:
    api, history, daily = _route_fixture()
    api.data["publication"][field] = "wrong"
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert any("publication_field=" + field in issue for issue in result.issues)


def test_historical_recommendation_cannot_project_wrong_label_on_the_same_factor() -> None:
    api, history, daily = _route_fixture()
    api.data["items"][0]["label_code"] = "WIDE_RANGE"
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert any("route_forecast_label" in issue for issue in result.issues)


def test_late_revision_of_older_day_cannot_replace_the_latest_ready_forecast() -> None:
    api, history, daily = _route_fixture()
    old_day_late = {**daily.rows[0], "id": 11, "revision": 99, "label_code": "WIDE_RANGE",
                    "environment_date": "2026-09-05", "available_at": (NOW + timedelta(hours=1)).isoformat()}
    cutoff = NOW + timedelta(hours=2)
    history = PublicationHistory(cutoff, history.batches, history.routes)
    daily = DailyReadSnapshot(cutoff, (*daily.rows, old_day_late))
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "WIDE_RANGE")
    assert not result.issues and result.evidence["blocked"] and not api.calls


@pytest.mark.parametrize("field", ["publication_uid", "publish_version"])
@pytest.mark.parametrize("absence", ["omitted", "null", "empty"])
def test_missing_historical_route_publication_identity_is_evidence_gap(field: str, absence: str) -> None:
    api, history, daily = _route_fixture()
    if absence == "omitted":
        history.routes[0].pop(field)
    else:
        history.routes[0][field] = None if absence == "null" else ""
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert not result.issues and result.checked_count == 0
    assert any("historical_route_publication_identity_missing" in reason for reason in result.evidence["blocked"])


def test_unknown_historical_membership_does_not_invent_an_empty_route_bug() -> None:
    api, history, daily = _route_fixture()
    history.routes[0].pop("publication_uid")
    api.data.update(items=[], returned_count=0, status="no_recommendation", reason_code="NO_ELIGIBLE_FACTOR")
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert not result.issues and result.evidence["blocked"]


def test_missing_historical_identity_preserves_independent_forecast_failure() -> None:
    api, history, daily = _route_fixture()
    history.routes[0].pop("publication_uid")
    api.data["forecast"]["revision"] = 99
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert any("forecast_selection" in issue for issue in result.issues)
    assert result.evidence["blocked"]


def test_missing_other_routes_identity_preserves_exact_bound_factor_value_failure() -> None:
    api, history, daily = _route_fixture()
    unbound = {**history.routes[0], "id": 21, "factor_id": 4, "factor_ref": "sub_factor:4",
               "rank_no": 2, "publication_uid": None}
    history = PublicationHistory(NOW, history.batches, (*history.routes, unbound))
    api.data["items"][0]["routing_score"] = 0
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert any("routing_score" in issue for issue in result.issues)
    assert result.evidence["blocked"]
    assert not any("factor_membership" in issue for issue in result.issues)


def test_unbound_route_cannot_allow_a_definitely_foreign_factor() -> None:
    api, history, daily = _route_fixture()
    history.routes[0].pop("publication_uid")
    api.data["items"][0]["factor_ref"] = "sub_factor:999"
    result = Factor4RecommendationService(api).check_forecast_label_history(history, daily, "CHOPPY_UP")
    assert any("factor_membership" in issue for issue in result.issues) and result.evidence["blocked"]


def test_same_historical_asof_reuses_public_daily_for_multiple_partitions() -> None:
    api, history, daily = _route_fixture()
    batches = (history.batches[0], _batch(2, market_scope="spot"))

    class PartitionAPI(_API):
        def recommendations(self, market: str, profile: str, **kwargs: Any) -> MCPResponse:
            """Return the requested independent publication with a legitimate empty route set."""
            self.data["publication"] = deepcopy(next(batch for batch in batches if batch["market_scope"] == market))
            return super().recommendations(market, profile, **kwargs)

    public = PartitionAPI(deepcopy(api.data))
    public.daily_rows = api.daily_rows
    public.data.update(items=[], returned_count=0, status="no_recommendation", reason_code="NO_ELIGIBLE_FACTOR")
    result = Factor4RecommendationService(public).check_forecast_label_history(
        PublicationHistory(NOW, batches, ()), daily, "CHOPPY_UP")
    assert not result.issues and not result.evidence["blocked"]
    assert result.checked_count == 2 and len(public.calls) == 2 and len(public.daily_calls) == 1
