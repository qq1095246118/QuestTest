"""Schema migration mutation checks; no real API/database success is claimed here."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from db.factor4_schema_repository import ApprovedSchemaSnapshot
from service.factor4_schema_service import Factor4SchemaService

pytestmark = pytest.mark.unit


def _response(data: dict[str, Any]) -> MCPResponse:
    body = {"data": data, "meta": {}}
    return MCPResponse(200, None, {"result": {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}}, None)


class StubAPI:
    """Return controlled data and count actual replay reads."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls = 0

    def fields(self, **kwargs: Any) -> MCPResponse:
        """Return schema fixture; no I/O or deliberate exception."""
        self.calls += 1
        return _response(self.data)

    def raw(self, **kwargs: Any) -> MCPResponse:
        """Return raw-schema fixture; no I/O or deliberate exception."""
        return self.fields(**kwargs)


def _snapshot() -> ApprovedSchemaSnapshot:
    return ApprovedSchemaSnapshot("v1", ({"field_name": "close", "source_field": "close", "created_at": datetime(2026, 1, 1, 8)},), (),
                                  ({"case_key": "alignment", "fixture_json": '{"event_time":"2026-01-01T00:00:00Z"}'},))


def _raw() -> dict[str, Any]:
    return {"schema_version": "v1", "mappings": [{"field_name": "close", "source_field": "close", "created_at": "2026-01-01T08:00:00+08:00"}],
            "field_resolutions": [], "replay_cases": [{"case_key": "alignment", "fixture_json": {"event_time": "2026-01-01T08:00:00+08:00"}}]}


def test_raw_schema_uses_case_key_and_recursive_instant_normalization() -> None:
    check = Factor4SchemaService(StubAPI(_raw())).check_approved(_snapshot(), raw=True, explicit=True)  # type: ignore[arg-type]
    assert check.checked_count == 2 and not check.issues


def test_schema_effective_instant_is_distinct_from_lifecycle_wall_clock() -> None:
    snapshot = _snapshot()
    snapshot.mappings[0]["effective_from"] = datetime(2026, 1, 1)
    data = _raw()
    data["mappings"][0]["effective_from"] = "2026-01-01T08:00:00+08:00"
    check = Factor4SchemaService(StubAPI(data)).check_approved(snapshot, raw=True, explicit=True)  # type: ignore[arg-type]
    assert not check.issues


@pytest.mark.parametrize("variant", ["version", "missing", "duplicate", "source", "replay", "timestamp", "null", "missing_source"])
def test_raw_schema_detects_independent_persisted_field_corruption(variant: str) -> None:
    data = _raw()
    if variant == "version":
        data["schema_version"] = "v2"
    elif variant == "missing":
        data["mappings"] = []
    elif variant == "duplicate":
        data["mappings"] *= 2
    elif variant == "source":
        data["mappings"][0]["source_field"] = "open"
    elif variant == "replay":
        data["replay_cases"][0]["fixture_json"]["event_time"] = "2026-01-01T09:00:00+08:00"
    elif variant == "timestamp":
        data["mappings"][0]["created_at"] = "2026-01-01T09:00:00+08:00"
    elif variant == "missing_source":
        data["mappings"][0].pop("source_field")
    else:
        data["mappings"][0]["source_field"] = None
    check = Factor4SchemaService(StubAPI(data)).check_approved(_snapshot(), raw=True, explicit=False)  # type: ignore[arg-type]
    assert check.issues


@pytest.mark.parametrize("variant", ["correct", "unrelated", "dependency", "replay"])
def test_single_vwap_selector_checks_closure_and_three_real_reads(variant: str) -> None:
    dependencies = ["high", "low", "close", "volume"]
    snapshot = ApprovedSchemaSnapshot("v1", tuple({"field_name": name} for name in dependencies),
                                      ({"field_name": "vwap", "dependency_fields_json": json.dumps(dependencies)},), ())
    data = {"schema_version": "v1", "fields": [{"field_name": "vwap", "dependency_fields": dependencies}]}
    if variant == "unrelated":
        data["fields"].append({"field_name": "close"})
    elif variant == "dependency":
        data["fields"][0]["dependency_fields"] = ["close"]
    api = StubAPI(data)
    if variant == "replay":
        original = api.fields
        def changed(**kwargs: Any) -> MCPResponse:
            value = original(**kwargs)
            api.data = copy.deepcopy(api.data)
            api.data["schema_version"] = "v2"
            return value
        api.fields = changed  # type: ignore[method-assign]
    check = Factor4SchemaService(api).check_selected(snapshot, "vwap")  # type: ignore[arg-type]
    assert api.calls == 3
    assert bool(check.issues) == (variant != "correct")
