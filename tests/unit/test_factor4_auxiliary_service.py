"""候选与集合对账反例，离线通过不等于真实接口验收通过。"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from service.factor4_auxiliary_service import Factor4AuxiliaryService, visible_universe_rows

pytestmark = pytest.mark.unit


def response(items: list[dict[str, Any]]) -> MCPResponse:
    """构造双表示成功回包，不执行 I/O。"""
    body = {"data": {"items": items}, "meta": {}}
    return MCPResponse(200, None, {"result": {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}}, None)


class StubAPI:
    """保存可控候选结果。"""
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
    def candidate(self, extraction_id: int, **kwargs: Any) -> MCPResponse:
        """返回候选 fixture，无网络/异常。"""
        return response(self.items)


def test_matching_filter_cannot_pass_vacuously_with_empty_candidates() -> None:
    result = Factor4AuxiliaryService(StubAPI([])).check_candidate({"id": 1, "validation_status": "ready"}, filter_name="validation_status")  # type: ignore[arg-type]
    assert "kb:exact_filter_membership" in result.issues


def test_candidate_detects_one_wrong_database_field() -> None:
    row = {"id": 1, "factor_name": "a", "validation_status": "ready", "mapping_status": "mapped", "target_asset_class": '["crypto"]', "confidence_score": "0.8"}
    item = {**row, "extraction_id": 1, "factor_name": "changed", "target_asset_class": ["crypto"], "confidence_score": .8}
    result = Factor4AuxiliaryService(StubAPI([item])).check_candidate(row)  # type: ignore[arg-type]
    assert result.issues == ("kb:field=factor_name",)


def test_candidate_does_not_expose_nested_claim_token() -> None:
    result = Factor4AuxiliaryService(StubAPI([{"extraction_id": 1, "task": {"claim_token": "unit-only-secret"}}])).check_candidate({"id": 1}, matching=False)  # type: ignore[arg-type]
    assert "kb:private_task_field_exposed" in result.issues
    assert "unit-only-secret" not in repr(result)


def test_universe_visibility_is_half_open_with_null_bounds_and_active_filter() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    base = {"universe_key": "all", "is_active": 1, "sort_order": 1, "valid_from": None, "valid_to": None}
    rows = tuple({**base, **extra} for extra in [
        {"symbol": "unbounded"}, {"symbol": "starts", "valid_from": now},
        {"symbol": "ends", "valid_to": now}, {"symbol": "future", "valid_from": now + timedelta(seconds=1)},
        {"symbol": "inactive", "is_active": 0}, {"symbol": "other", "universe_key": "main"},
    ])
    assert [row["symbol"] for row in visible_universe_rows(rows, "all", now)] == ["starts", "unbounded"]
