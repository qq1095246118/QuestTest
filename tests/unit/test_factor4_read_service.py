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


def _detail_check(single: dict[str, Any], batch: dict[str, Any], level: str) -> ReadCheck:
    mcp = SimpleNamespace(
        get_factor_details_batch=lambda *args, **kwargs: _response({"data": {"items": [
            {"factor_ref": "sub_factor:1", "success": True, "data": batch}]}, "meta": {}}),
        get_factor_detail=lambda *args, **kwargs: _response({"data": single, "meta": {}}),
    )
    subset = CatalogSubset("sub_factor", "valid", "all", ({"id": 1, "name": "value", "cn_name": None, "serial_number": "F1"},))
    service = Factor4ReadService(SimpleNamespace(mcp=mcp))
    return service.check_details_batch(subset, level)


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


def _catalog_subset() -> CatalogSubset:
    return CatalogSubset("sub_factor", "valid", "all", tuple(
        {"id": i, "name": "value", "cn_name": None, "serial_number": f"F{i}", "data_source": "Kline",
         "updated_at": datetime(2026, 9, i, tzinfo=timezone.utc)} for i in range(1, 4)))


def _catalog_page(rows: tuple[dict[str, Any], ...], *, terminal: str = "bounded") -> MCPResponse:
    items = [{**{key: value for key, value in row.items() if key != "updated_at"},
              "factor_ref": f"sub_factor:{row['id']}", "kind": "sub_factor", "library_status": "valid",
              "library_coin_categories": ["all"]} for row in rows]
    return _response({"data": {"items": items, "returned_count": len(items)}, "meta": {
        "next_cursor": "next" if terminal == "continuation" else None,
        "truncated": terminal != "complete",
        "warnings": ["CATALOG_CURSOR_BUDGET_REACHED"] if terminal == "bounded" else [],
    }})


def _catalog_reader(*responses: MCPResponse) -> tuple[Factor4ReadService, list[dict[str, Any]]]:
    pending = iter(responses)
    calls: list[dict[str, Any]] = []

    def search_catalog(*args: object, **kwargs: Any) -> MCPResponse:
        calls.append(kwargs)
        return next(pending)

    return Factor4ReadService(SimpleNamespace(search_catalog=search_catalog)), calls


@pytest.mark.parametrize("entry", ["members", "query"])
def test_legacy_catalog_reads_accept_budget_without_claiming_all_members(entry: str) -> None:
    """Both live entry paths retain correct partial data and JUnit scope without another request."""
    from tests.cases.factor4.test_migrated_readonly_scripts import _assert_check

    subset = _catalog_subset()
    service, calls = _catalog_reader(_catalog_page(subset.rows[:1]))
    traversal = service.catalog_query(subset, "value") if entry == "query" else service.catalog_pages(subset)
    check = service.check_catalog_query(subset, "value", traversal) if entry == "query" else service.check_catalog_members(subset, traversal)
    properties: dict[str, object] = {}
    _assert_check(check, properties.__setitem__)
    assert len(calls) == 1
    assert properties["catalog_acceptance"] == "BOUNDED_READ_ACCEPTED_NOT_FULL_EXPORT"
    assert properties["catalog_returned_unique_count"] == 1
    assert properties["catalog_database_unique_count"] == 3
    assert properties["catalog_full_membership_verified"] is False


@pytest.mark.parametrize("mutation", ["wrong_field", "duplicate", "unexpected"])
def test_legacy_catalog_budget_does_not_hide_bad_returned_data(mutation: str) -> None:
    """Budgeted membership still rejects corrupt fields, repeated IDs and unrequested entities."""
    subset = _catalog_subset()
    row = dict(subset.rows[0])
    if mutation == "wrong_field":
        row["name"] = "wrong"
    elif mutation == "unexpected":
        row["id"] = 99
    selected = (row, row) if mutation == "duplicate" else (row,)
    service, _ = _catalog_reader(_catalog_page(selected))
    check = service.check_catalog_members(subset, service.catalog_pages(subset))
    assert check.issues


def test_legacy_catalog_natural_end_still_requires_complete_database_selection() -> None:
    """Natural completion cannot excuse missing members using the new budget handling."""
    subset = _catalog_subset()
    service, _ = _catalog_reader(_catalog_page(subset.rows[:1], terminal="complete"))
    check = service.check_catalog_members(subset, service.catalog_pages(subset))
    assert "missing_id=2" in check.issues
    assert check.evidence["catalog_full_membership_verified"] is False


def test_catalog_budget_warning_never_relaxes_daily_pagination() -> None:
    """Identical metadata is accepted only for catalog calls, not for environment pages."""
    response = _catalog_page(_catalog_subset().rows[:1])
    service = Factor4ReadService(SimpleNamespace())
    daily = service._traverse(lambda _: response, page_size=3, max_pages=1)
    assert daily.issues == ("pagination:truncated_cursor_mismatch",)
    assert daily.termination == "invalid"


def test_legacy_catalog_checks_invalid_budget_terminal_metadata_without_following_cursor() -> None:
    """A budget warning with a continuation cursor remains a contract failure."""
    subset = _catalog_subset()
    response = _catalog_page(subset.rows[:1])
    body = response.structured_content
    body["meta"]["next_cursor"] = "must-not-follow"
    service, calls = _catalog_reader(_response(body))
    check = service.check_catalog_members(subset, service.catalog_pages(subset))
    assert "pagination:budget_terminal_contract" in check.issues
    assert len(calls) == 1


@pytest.mark.parametrize("corrupt", [False, True])
def test_later_catalog_dependency_block_keeps_earlier_field_failures(corrupt: bool) -> None:
    """A later dependency failure skips only when already-read pages contain no differences."""
    from tests.cases.factor4.test_migrated_readonly_scripts import _assert_check

    subset = _catalog_subset()
    row = {**subset.rows[0], "name": "wrong"} if corrupt else subset.rows[0]
    error = _response({"data": {}, "meta": {}, "error": {"code": "DEPENDENCY_UNAVAILABLE"}}, error=True)
    service, _ = _catalog_reader(_catalog_page((row,), terminal="continuation"), error)
    check = service.check_catalog_members(subset, service.catalog_pages(subset))
    properties: dict[str, object] = {}
    with pytest.raises(AssertionError if corrupt else pytest.skip.Exception):
        _assert_check(check, properties.__setitem__)
    assert properties["catalog_acceptance"] == ("FAILED" if corrupt else "BLOCKED")
    assert check.evidence["blocked"]


@pytest.mark.parametrize("first_terminal,second_terminal", [
    ("bounded", "bounded"), ("complete", "bounded"), ("bounded", "complete"), ("complete", "complete"),
])
def test_catalog_replay_completeness_matches_both_traversals(first_terminal: str, second_terminal: str) -> None:
    """Only two complete traversals certify full replay; bounded lengths may differ."""
    subset = _catalog_subset()
    first = subset.rows if first_terminal == "complete" else subset.rows[:2]
    second = subset.rows if second_terminal == "complete" else subset.rows[:1]
    service, _ = _catalog_reader(_catalog_page(first, terminal=first_terminal), _catalog_page(second, terminal=second_terminal))
    check = service.check_catalog_replay(subset, service.catalog_pages(subset))
    assert not check.issues
    full = first_terminal == second_terminal == "complete"
    assert check.evidence["catalog_repeat_full_verified"] is full
    assert check.evidence["catalog_full_membership_verified"] is full


def test_bounded_catalog_replay_rejects_changed_common_prefix() -> None:
    """Individually valid returned members still fail when their replay order changes."""
    from tests.cases.factor4.test_migrated_readonly_scripts import _assert_check

    subset = _catalog_subset()
    service, _ = _catalog_reader(_catalog_page(subset.rows[:2]), _catalog_page(tuple(reversed(subset.rows[:2]))))
    check = service.check_catalog_replay(subset, service.catalog_pages(subset))
    assert "catalog:repeat_read_changed" in check.issues
    with pytest.raises(AssertionError, match="catalog:repeat_read_changed"):
        _assert_check(check)


@pytest.mark.parametrize("first_terminal", ["complete", "bounded", "blocked"])
def test_catalog_replay_block_retains_initial_failures_and_both_block_reasons(first_terminal: str) -> None:
    """A blocked reread never converts an already-observed data failure into a skip."""
    from tests.cases.factor4.test_migrated_readonly_scripts import _assert_check

    subset = _catalog_subset()
    rows = ({**subset.rows[0], "name": "wrong"}, *subset.rows[1:])
    error = _response({"data": {}, "meta": {}, "error": {"code": "DEPENDENCY_UNAVAILABLE"}}, error=True)
    first = _catalog_page(rows, terminal="continuation" if first_terminal == "blocked" else first_terminal)
    responses = (first, error, error) if first_terminal == "blocked" else (first, error)
    service, _ = _catalog_reader(*responses)
    check = service.check_catalog_replay(subset, service.catalog_pages(subset))
    properties: dict[str, object] = {}
    with pytest.raises(AssertionError, match="field=name"):
        _assert_check(check, properties.__setitem__)
    assert properties["catalog_acceptance"] == "FAILED"
    assert len(check.evidence["blocked"]) == (2 if first_terminal == "blocked" else 1)
    assert check.evidence["catalog_repeat_full_verified"] is False


def test_catalog_updated_filter_budget_preserves_time_selection_assertion() -> None:
    """Budget termination cannot hide a returned row below the requested updated_after boundary."""
    subset = _catalog_subset()
    service, calls = _catalog_reader(_catalog_page(subset.rows[:1]))
    check = service.check_catalog_updated_boundary(subset, 0)
    assert "unexpected_id=1" in check.issues
    assert calls[0]["updated_after"] == subset.rows[1]["updated_at"].isoformat()
