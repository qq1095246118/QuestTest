"""Offline negative controls for temporal and immutable cross-tool assertions."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from db.factor4_formula_repository import FormulaCatalogSnapshot
from db.factor4_publication_repository import PublicationHistory
from db.factor4_read_repository import DailyReadSnapshot
from service.factor4_cross_read_service import Factor4CrossReadService, check_completed_formula_replay
from service.factor4_read_service import Factor4ReadService, ReadCheck

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _response(data: dict[str, Any]) -> MCPResponse:
    body = {"data": data, "meta": {}}
    return MCPResponse(200, None, {"result": {"structuredContent": body, "content": [{"type": "text", "text": json.dumps(body)}]}}, None)


class _API:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data, self.calls = data, 0

    def formula(self, row: dict[str, Any]) -> MCPResponse:
        """Return formula fixture and count actual calls; no network or exception."""
        self.calls += 1
        return _response(copy.deepcopy(self.data))

    def recommendations(self, *args: Any, **kwargs: Any) -> MCPResponse:
        """Return recommendation fixture; no network or exception."""
        return _response(self.data)


@pytest.mark.parametrize("variant", ["correct", "wrong_run", "wrong_ref", "wrong_hash", "wrong_fields", "replay"])
def test_formula_replay_checks_database_identity_and_does_not_use_cached_results(variant: str) -> None:
    row = {"id": 1, "factor_id": 3, "is_sub_factor_id": 1, "run_status": "completed", "calculation_mode": "direct",
           "run_id": "r", "expression": "close", "formula_hash": "h", "formula_version": "v", "required_fields": '["close"]'}
    data = {key: value for key, value in row.items() if key in {"run_id", "expression", "formula_hash", "formula_version"}}
    data.update(factor_ref="sub_factor:3", required_fields=["close"])
    if variant == "wrong_run":
        data["run_id"] = "other"
    elif variant == "wrong_ref":
        data["factor_ref"] = "factor:3"
    elif variant == "wrong_hash":
        data["formula_hash"] = "other"
    elif variant == "wrong_fields":
        data["required_fields"] = ["open"]
    api = _API(data)
    if variant == "replay":
        original = api.formula
        def changed(row: dict[str, Any]) -> MCPResponse:
            response = original(row)
            api.data["formula_hash"] = "changed"
            return response
        api.formula = changed  # type: ignore[method-assign]
    check = check_completed_formula_replay(api, FormulaCatalogSnapshot(NOW, (), (), (row,), ()))  # type: ignore[arg-type]
    assert api.calls == 3
    assert bool(check.issues) == (variant != "correct")


@pytest.mark.parametrize("label", ["WIDE_RANGE", "CHOPPY_UP"])
def test_recommendation_factor_projection_is_reconciled_through_actual_route_label(label: str) -> None:
    batch = {"id": 1, "batch_uid": "b", "publication_uid": "p", "market_scope": "all", "route_profile_key": "default",
             "published_at": "2026-09-01T00:00:00Z", "publish_version": 1}
    forecast = {"id": 2, "revision": 1, "environment_date": "2026-09-06", "label_kind": "forecast", "label_status": "ready", "label_code": "WIDE_RANGE", "available_at": NOW.isoformat()}
    history = PublicationHistory(NOW, (batch,), ({"eval_batch_id": 1, "label_code": label, "factor_ref": "sub_factor:3", "is_eligible": 1},))
    api = _API({"publication": batch, "forecast": forecast, "items": [{"factor_ref": "sub_factor:3"}], "returned_count": 1})
    service = Factor4CrossReadService(Factor4ReadService(api))  # type: ignore[arg-type]
    check = service.check_forecast_recommendation_boundary(DailyReadSnapshot(NOW, (forecast,)), history, 0)
    assert bool(check.issues) == (label != "WIDE_RANGE")


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("returned", ["correct", "old_revision", "future_revision"])
def test_forecast_boundary_uses_database_revision_before_label_route_validation(offset: int, returned: str) -> None:
    """A self-consistent wrong forecast/route pair cannot establish the PIT expectation."""
    batch = {"id": 1, "batch_uid": "b", "publication_uid": "p", "market_scope": "all", "route_profile_key": "default",
             "published_at": "2026-09-01T00:00:00Z", "publish_version": 1}
    old = {"id": 1, "revision": 1, "environment_date": "2026-09-06", "label_kind": "forecast", "label_status": "ready",
           "label_code": "WIDE_RANGE", "available_at": (NOW - timedelta(hours=1)).isoformat()}
    new = {**old, "id": 2, "revision": 2, "label_code": "CHOPPY_UP", "available_at": NOW.isoformat()}
    future = {**new, "id": 3, "revision": 3, "label_code": "CHOPPY_DOWN", "available_at": (NOW + timedelta(hours=1)).isoformat()}
    rows = (old, new, future)
    routes = tuple({"eval_batch_id": 1, "label_code": row["label_code"], "factor_ref": f"sub_factor:{row['id']}", "is_eligible": 1}
                   for row in rows)

    class BoundaryAPI(_API):
        def recommendations(self, *args: Any, **kwargs: Any) -> MCPResponse:
            """Return the independently prepared PIT answer or deliberate stale/future answer."""
            as_of = datetime.fromisoformat(kwargs["as_of"])
            available = [row for row in rows if datetime.fromisoformat(row["available_at"]) <= as_of]
            chosen = max(available, key=lambda row: row["revision"], default=None)
            if returned == "old_revision" and chosen is not None and chosen["revision"] >= 2:
                chosen = old
            elif returned == "future_revision" and as_of < datetime.fromisoformat(future["available_at"]):
                chosen = future
            data = {"publication": batch, "forecast": chosen,
                    "items": [{"factor_ref": f"sub_factor:{chosen['id']}"}] if chosen else [],
                    "returned_count": 1 if chosen else 0}
            if chosen is None:
                data.update(status="no_recommendation", reason_code="ACTIVE_FORECAST_NOT_FOUND")
            return _response(data)

    history = PublicationHistory(NOW + timedelta(days=1), (batch,), routes)
    service = Factor4CrossReadService(Factor4ReadService(BoundaryAPI({})))
    result = service.check_forecast_recommendation_boundary(DailyReadSnapshot(history.as_of, rows), history, offset)
    assert bool(result.issues) == (returned != "correct"), result.issues
    if returned == "old_revision":
        assert "recommendation:forecast_selection" in result.issues
        assert "recommendation:route_forecast_label" in result.issues


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_first_daily_boundary_uses_actual_available_time_not_revision_count(offset: int) -> None:
    class Read:
        def daily_pages(self, snapshot: Any, kind: str, **kwargs: Any) -> None:
            """Record exact query inputs without remote I/O."""
            self.kwargs = kwargs
        def check_daily(self, *args: Any, **kwargs: Any) -> ReadCheck:
            """Return known comparator outcome without replacing expected row selection."""
            return ReadCheck(1, ())
    read = Read()
    snapshot = DailyReadSnapshot(NOW, ({"label_kind": "fact", "environment_date": "2026-09-06", "available_at": NOW},))
    check = Factor4CrossReadService(read).check_daily_availability(snapshot, "fact", offset)  # type: ignore[arg-type]
    assert check.checked_count == 1
    assert (read.kwargs["as_of"] - NOW).total_seconds() == offset / 1_000_000
    assert read.kwargs["environment_date"] == "2026-09-06"
