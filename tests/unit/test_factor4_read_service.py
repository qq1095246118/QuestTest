"""迁移只读 Case 的离线 Oracle 与协议 envelope 单元测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from db.factor4_read_repository import CatalogSubset, DailyReadSnapshot
from service.factor4_read_service import (
    Factor4ReadService,
    ReadCheck,
    ReadContractError,
    compare_rows,
    read_tool_page,
    visible_daily_rows,
)


def _response(body: dict, *, error: bool = False) -> MCPResponse:
    """Construct a protocol-valid response without network access."""
    return MCPResponse(
        status_code=200,
        content_type="application/json",
        envelope={
            "jsonrpc": "2.0", "id": "unit", "result": {
                "content": [{"type": "text", "text": json.dumps(body)}],
                "structuredContent": body, "isError": error,
            }
        },
        protocol_version="2025-06-18",
    )


def test_read_tool_page_requires_equal_representations() -> None:
    body = {"data": {"items": [], "returned_count": 0}, "meta": {"truncated": False}}
    page = read_tool_page(_response(body))
    assert page.items == ()

    response = _response(body)
    response.envelope["result"]["structuredContent"] = {"data": {}, "meta": {}}
    with pytest.raises(ReadContractError, match="structuredContent differ"):
        read_tool_page(response)


def test_read_tool_page_rejects_business_error_without_leaking_payload() -> None:
    body = {"error": {"code": "BAD_ARGUMENT", "message": "opaque"}, "data": {}, "meta": {}}
    with pytest.raises(ReadContractError, match="business error"):
        read_tool_page(_response(body, error=True))


def test_compare_rows_preserves_decimal_null_and_detects_field_drift() -> None:
    actual = [{"id": 1, "score": 1.2, "optional": None}]
    expected = [{"id": 1, "score": Decimal("1.2"), "optional": None}]
    assert compare_rows(actual, expected, ("score", "optional")).issues == ()
    drift = compare_rows(actual, [{"id": 1, "score": 1.3, "optional": None}], ("score", "optional"))
    assert drift.issues == ("id=1:field=score",)


def test_visible_daily_rows_selects_latest_revision_at_boundary() -> None:
    rows = (
        {"id": 1, "environment_date": "2026-09-01", "label_kind": "fact", "revision": 1,
         "is_current": 0, "available_at": datetime(2026, 9, 1, 0, tzinfo=timezone.utc)},
        {"id": 2, "environment_date": "2026-09-01", "label_kind": "fact", "revision": 2,
         "is_current": 1, "available_at": datetime(2026, 9, 1, 1, tzinfo=timezone.utc)},
    )
    snapshot = DailyReadSnapshot(datetime(2026, 9, 2, tzinfo=timezone.utc), rows)
    assert visible_daily_rows(snapshot, "fact", as_of=datetime(2026, 9, 1, 0, 0, 1, tzinfo=timezone.utc))[0]["id"] == 1
    assert visible_daily_rows(snapshot, "fact", as_of=datetime(2026, 9, 1, 1, tzinfo=timezone.utc))[0]["id"] == 2


def test_daily_traversal_allows_server_pages_smaller_than_requested_limit() -> None:
    from types import SimpleNamespace

    rows = tuple({"id": i, "environment_date": f"2026-09-{i:02}", "label_kind": "fact",
                  "revision": 1, "is_current": 1,
                  "available_at": datetime(2026, 9, 1, tzinfo=timezone.utc)} for i in range(1, 8))
    snapshot = DailyReadSnapshot(datetime(2026, 9, 9, tzinfo=timezone.utc), rows)

    def daily(*args: object, cursor: str | None = None, **kwargs: object) -> MCPResponse:
        index = int(cursor or 0)
        next_cursor = str(index + 1) if index + 1 < len(rows) else None
        return _response({"data": {"items": [{"id": index + 1}]},
                          "meta": {"next_cursor": next_cursor, "truncated": bool(next_cursor)}})

    result = Factor4ReadService(SimpleNamespace(daily=daily)).daily_pages(snapshot, "fact")
    assert len(result.rows) == 7
    assert not result.issues


@pytest.mark.parametrize("horizon,visible", [("before-history", False), ("future", True)])
def test_daily_horizon_is_relative_to_availability_not_current_flag(horizon: str, visible: bool) -> None:
    from types import SimpleNamespace
    from unittest.mock import patch
    from service.factor4_read_service import PageTraversal, ReadCheck

    available = datetime(2026, 9, 1, tzinfo=timezone.utc)
    row = {"id": 1, "environment_date": "2026-08-31", "label_kind": "fact", "revision": 2,
           "available_at": available, "is_current": 0}
    snapshot = DailyReadSnapshot(available, (row,))
    service = Factor4ReadService(SimpleNamespace())
    with patch.object(service, "daily_pages", return_value=PageTraversal((), ())) as traversal, \
         patch.object(service, "check_daily", return_value=ReadCheck(1)):
        service.check_daily_visibility_horizon(snapshot, "fact", horizon)
    kwargs = traversal.call_args.kwargs
    assert kwargs["environment_date"] == "2026-08-31"
    assert (kwargs["as_of"] >= available) is visible
    assert bool(visible_daily_rows(snapshot, "fact", as_of=kwargs["as_of"])) is visible


def _detail_payload(level: str) -> dict[str, Any]:
    data = {"id": 1, "factor_ref": "sub_factor:1", "kind": "sub_factor", "name": "value",
            "cn_name": None, "serial_number": "F1", "factor_version": "version-1"}
    if level != "summary":
        data.update(calc_logic="close", formula_summary="close", metadata=None, params={},
                    data_source_metadata={"required_fields": ["close"]})
    if level == "executable":
        data.update(calc_function="return close", formula_available=True)
    return data


def _detail_check(single: dict[str, Any], batch: dict[str, Any], level: str, *, executable_entry: bool = False) -> ReadCheck:
    mcp = SimpleNamespace(
        get_factor_details_batch=lambda *args, **kwargs: _response({"data": {"items": [
            {"factor_ref": "sub_factor:1", "success": True, "data": batch}]}, "meta": {}}),
        get_factor_detail=lambda *args, **kwargs: _response({"data": single, "meta": {}}),
    )
    subset = CatalogSubset("sub_factor", "valid", "all", ({"id": 1, "name": "value", "cn_name": None, "serial_number": "F1"},))
    service = Factor4ReadService(SimpleNamespace(mcp=mcp))
    return service.check_details_executable_common_fields(subset) if executable_entry else service.check_details_batch(subset, level)


@pytest.mark.parametrize("level", ["summary", "definition", "executable"])
def test_successful_empty_batch_detail_is_not_equal_to_complete_single(level: str) -> None:
    result = _detail_check(_detail_payload(level), {}, level)
    assert f"detail:{level}_missing=factor_ref" in result.issues


@pytest.mark.parametrize("level,field", [("summary", "id"), ("summary", "name"), ("definition", "calc_logic"),
                                       ("definition", "params"), ("definition", "metadata"), ("executable", "calc_function")])
def test_detail_public_common_field_omissions_fail_without_inventing_optional_requirements(level: str, field: str) -> None:
    single = _detail_payload(level)
    batch = dict(single)
    del batch[field]
    assert f"detail:{level}_missing={field}" in _detail_check(single, batch, level).issues
    del single[field]
    result = _detail_check(single, batch, level)
    if level in {"summary", "executable"}:
        assert f"detail:{level}_missing={field}" in result.issues
    else:
        assert not result.issues


@pytest.mark.parametrize("level", ["summary", "definition", "executable"])
def test_detail_single_only_relationships_and_unspecified_derived_fields_are_not_required(level: str) -> None:
    batch = _detail_payload(level)
    single = {**batch, "children": [{"factor_ref": "sub_factor:2"}], "relations": [],
              "children_next_cursor": None, "children_truncated": False, "endpoint_specific_hint": "single"}
    assert not _detail_check(single, batch, level).issues


@pytest.mark.parametrize("field", ["factor_version", "cn_name", "data_source_metadata"])
def test_detail_common_fields_detect_null_omission_and_value_drift(field: str) -> None:
    single = _detail_payload("definition")
    batch = dict(single)
    del batch[field]
    assert _detail_check(single, batch, "definition").issues
    batch[field] = "different"
    assert f"detail:definition_field={field}" in _detail_check(single, batch, "definition").issues


def test_executable_entry_reuses_complete_detail_comparison() -> None:
    single = _detail_payload("executable")
    batch = {**single, "data_source_metadata": {"required_fields": ["open"]}}
    assert _detail_check(single, batch, "executable", executable_entry=True).issues == _detail_check(single, batch, "executable").issues
