"""目录业务断言的离线反例，不计真实环境覆盖。"""

import json
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from service.factor4_catalog_extension_service import Factor4CatalogExtensionService
from service.factor4_read_service import ReadContractError

pytestmark = pytest.mark.unit


class StubAPI:
    """返回指定目录结果并记录请求，不执行网络。"""
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.arguments: dict[str, Any] = {}
    def search_catalog(self, **arguments: Any) -> MCPResponse:
        """返回可控双表示，保存筛选参数；无异常。"""
        self.arguments = arguments
        body = {"data": {"items": self.rows}, "meta": {}}
        return MCPResponse(200, None, {"result": {"structuredContent": body, "content": [{"type": "text", "text": json.dumps(body)}]}}, None)


@pytest.fixture
def seed() -> dict[str, Any]:
    """最小可复算目录样本；无 I/O。"""
    return {"factor_ref": "sub_factor:1", "kind": "sub_factor", "library_status": "valid", "name": "value", "themes": ["value"], "tags": ["value"], "data_source": "Kline", "factor_bar_interval": "1h", "library_coin_categories": ["all"]}


def test_matching_catalog_filter_requires_seed_not_vacuous_all(seed: dict[str, Any]) -> None:
    result = Factor4CatalogExtensionService(StubAPI([])).check_filter(seed, "combined")  # type: ignore[arg-type]
    assert result.issues == ("catalog:matching_seed_missing",)


def test_catalog_filter_reports_ignored_theme(seed: dict[str, Any]) -> None:
    result = Factor4CatalogExtensionService(StubAPI([{**seed, "themes": ["other"]}])).check_filter(seed, "theme")  # type: ignore[arg-type]
    assert "catalog:filter=theme" in result.issues


def test_catalog_conflicting_filter_requires_success_empty(seed: dict[str, Any]) -> None:
    api = StubAPI([seed])
    result = Factor4CatalogExtensionService(api).check_filter(seed, "combined", matching=False)  # type: ignore[arg-type]
    assert "catalog:conflicting_filter_not_empty" in result.issues
    assert api.arguments["filters"]["query"] == "value"


def test_matching_all_filters_are_sent_together(seed: dict[str, Any]) -> None:
    api = StubAPI([seed])
    result = Factor4CatalogExtensionService(api).check_filter(seed, "combined")  # type: ignore[arg-type]
    assert not result.issues
    assert set(api.arguments["filters"]) == {"query", "theme", "tags", "data_source", "interval", "library_coin_category", "library_status"}


class PagedCatalogAPI:
    """Return deterministic catalog pages and separate research-count statistics."""

    def __init__(self, pages: tuple[tuple[dict[str, Any], ...], ...], *, repeated_cursor: bool = False) -> None:
        """Store pages and cursor behavior; no I/O or return value."""
        self.pages = pages
        self.repeated_cursor = repeated_cursor
        self.calls: list[dict[str, Any]] = []

    def search_catalog(self, **arguments: Any) -> MCPResponse:
        """Consume the next requested page; exhausted fixtures raise IndexError."""
        self.calls.append(arguments)
        index = len(self.calls) - 1
        rows = self.pages[index]
        cursor = "loop" if self.repeated_cursor else str(index + 1) if index + 1 < len(self.pages) else None
        return self._response({"items": list(rows), "returned_count": len(rows)}, cursor)

    def catalog_stats(self, kind: str, **arguments: Any) -> MCPResponse:
        """Return self-consistent counts with intentionally different research semantics."""
        return self._response({"total": 999, "groups": [{"count": 999}]}, None)

    @staticmethod
    def _response(data: dict[str, Any], cursor: str | None) -> MCPResponse:
        body = {"data": data, "meta": {"next_cursor": cursor, "truncated": bool(cursor)}}
        return MCPResponse(200, None, {"result": {"structuredContent": body,
            "content": [{"type": "text", "text": json.dumps(body)}]}}, None)


def _catalog_rows() -> tuple[dict[str, Any], ...]:
    return tuple({"id": i, "name": f"factor-{i}", "cn_name": None, "coin_category": "all"} for i in range(1, 6))


def _catalog_item(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "factor_ref": f"sub_factor:{row['id']}", "kind": "sub_factor", "name": row["name"],
            "cn_name": row["cn_name"], "library_status": "valid", "library_coin_categories": ["all"]}


def test_catalog_status_full_pagination_compares_complete_db_set_not_stats_total() -> None:
    rows = _catalog_rows()
    items = tuple(_catalog_item(row) for row in rows)
    api = PagedCatalogAPI((items[:2], items[2:4], items[4:]))
    result = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", "all", rows)
    assert not result.issues
    assert result.checked_count == 5
    assert [call["filters"].get("cursor") for call in api.calls] == [None, "1", "2"]
    assert all(call["filters"]["library_status"] == "valid" and call["filters"]["library_coin_category"] == "all" for call in api.calls)
    assert result.evidence["stats_oracle"] == "group_sum_only"


def test_catalog_early_terminal_page_cannot_hide_missing_members_with_self_consistent_stats() -> None:
    rows = _catalog_rows()
    api = PagedCatalogAPI(((_catalog_item(rows[0]),),))
    result = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)
    assert "catalog:status_category_complete_membership" in result.issues


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "wrong_status", "wrong_ref", "wrong_field"])
def test_catalog_later_page_corruption_is_not_hidden_by_valid_first_page(mutation: str) -> None:
    rows = _catalog_rows()
    items = [_catalog_item(row) for row in rows]
    if mutation == "duplicate":
        items[-1] = dict(items[0])
    elif mutation == "missing":
        items.pop()
    elif mutation == "wrong_status":
        items[-1]["library_status"] = "deleted"
    elif mutation == "wrong_ref":
        items[-1]["factor_ref"] = "factor:5"
    else:
        items[-1]["name"] = "different"
    api = PagedCatalogAPI((tuple(items[:2]), tuple(items[2:])))
    assert Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows).issues


def test_catalog_repeated_cursor_cannot_count_as_completed_traversal() -> None:
    rows = _catalog_rows()
    api = PagedCatalogAPI(((_catalog_item(rows[0]),), (_catalog_item(rows[1]),)), repeated_cursor=True)
    with pytest.raises(ReadContractError, match="cursor repeated"):
        Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)


def test_catalog_empty_db_partition_requires_successful_terminal_empty_page() -> None:
    api = PagedCatalogAPI(((),))
    assert not Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", "custom", ()).issues


def test_catalog_multiple_category_rows_do_not_duplicate_expected_entity_count() -> None:
    rows = (_catalog_rows()[0], {**_catalog_rows()[0], "coin_category": "main"})
    item = {**_catalog_item(rows[0]), "library_coin_categories": ["all", "main"]}
    api = PagedCatalogAPI(((item,),))
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)
    assert not check.issues
    assert check.checked_count == 1
