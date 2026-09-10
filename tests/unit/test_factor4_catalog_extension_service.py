"""目录业务断言的离线反例，不计真实环境覆盖。"""

import json
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from service.factor4_catalog_extension_service import Factor4CatalogExtensionService
from service.factor4_read_service import ReadContractError, ReadPrecondition

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

    def __init__(self, pages: tuple[tuple[dict[str, Any], ...], ...], *, repeated_cursor: bool = False,
                 terminal_meta: dict[str, Any] | None = None) -> None:
        """Store pages/cursor/terminal metadata; no I/O, return value or exceptions."""
        self.pages = pages
        self.repeated_cursor = repeated_cursor
        self.terminal_meta = terminal_meta
        self.calls: list[dict[str, Any]] = []

    def search_catalog(self, **arguments: Any) -> MCPResponse:
        """Consume the next requested page; exhausted fixtures raise IndexError."""
        self.calls.append(arguments)
        index = len(self.calls) - 1
        rows = self.pages[index]
        cursor = "loop" if self.repeated_cursor else str(index + 1) if index + 1 < len(self.pages) else None
        meta = self.terminal_meta if index + 1 == len(self.pages) else None
        return self._response({"items": list(rows), "returned_count": len(rows)}, cursor, meta)

    def catalog_stats(self, kind: str, **arguments: Any) -> MCPResponse:
        """Return self-consistent counts with intentionally different research semantics."""
        return self._response({"total": 999, "groups": [{"count": 999}]}, None)

    @staticmethod
    def _response(data: dict[str, Any], cursor: str | None, meta: dict[str, Any] | None = None) -> MCPResponse:
        body = {"data": data, "meta": {"next_cursor": cursor, "truncated": bool(cursor)} if meta is None else meta}
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
    assert result.evidence["catalog_traversal"] == "complete"
    assert result.evidence["catalog_full_membership_verified"] is True


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


def _budget_meta() -> dict[str, Any]:
    return {"next_cursor": None, "truncated": True, "warnings": [{"code": "CATALOG_CURSOR_BUDGET_REACHED"}]}


@pytest.mark.parametrize("delivered", [1, 3, 5])
@pytest.mark.parametrize("warning_object", [False, True])
def test_declared_catalog_budget_stops_without_exporting_or_claiming_full_membership(
    delivered: int, warning_object: bool,
) -> None:
    rows = _catalog_rows()
    meta = _budget_meta()
    if not warning_object:
        meta["warnings"] = ["CATALOG_CURSOR_BUDGET_REACHED"]
    # Request quota is a separate dimension; remaining quota must not restart
    # the delivery-bound cursor, regardless of its configured numeric limit.
    meta["quota"] = {"limit": 1200, "used": 1, "remaining": 1199}
    api = PagedCatalogAPI((tuple(_catalog_item(row) for row in rows[:delivered]),), terminal_meta=meta)
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)
    assert not check.issues
    assert len(api.calls) == 1 and "cursor" not in api.calls[0]["filters"]
    assert check.checked_count == delivered
    assert check.evidence["catalog_traversal"] == "bounded"
    assert check.evidence["catalog_returned_count"] == delivered
    assert check.evidence["catalog_returned_unique_count"] == delivered
    assert check.evidence["catalog_database_unique_count"] == 5
    assert check.evidence["catalog_completeness"] == "not_verified"
    assert check.evidence["catalog_full_membership_verified"] is False


@pytest.mark.parametrize("meta", [
    {"next_cursor": None, "truncated": True},
    {"next_cursor": None, "truncated": True, "warnings": ["RATE_LIMITED"]},
    {"next_cursor": None, "truncated": True, "warnings": [{"code": "CATALOG_CURSOR_BUDGET_REACHED_OTHER"}]},
    {"next_cursor": None, "truncated": True, "warnings": {"code": "CATALOG_CURSOR_BUDGET_REACHED"}},
])
def test_undeclared_catalog_truncation_is_not_mistaken_for_a_budget_boundary(meta: dict[str, Any]) -> None:
    rows = _catalog_rows()
    api = PagedCatalogAPI(((_catalog_item(rows[0]),),), terminal_meta=meta)
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)
    assert "catalog:status_category_cursor_truncation" in check.issues
    assert check.evidence["catalog_traversal"] == "invalid"
    assert check.evidence["catalog_full_membership_verified"] is False


@pytest.mark.parametrize("meta", [
    {"truncated": True, "warnings": ["CATALOG_CURSOR_BUDGET_REACHED"]},
    {"next_cursor": None, "truncated": False, "warnings": ["CATALOG_CURSOR_BUDGET_REACHED"]},
    {"next_cursor": "must-not-follow", "truncated": True, "warnings": ["CATALOG_CURSOR_BUDGET_REACHED"]},
    {"next_cursor": "", "truncated": True, "warnings": ["CATALOG_CURSOR_BUDGET_REACHED"]},
])
def test_catalog_budget_warning_with_inconsistent_terminal_fields_fails_without_continuing(meta: dict[str, Any]) -> None:
    rows = _catalog_rows()
    api = PagedCatalogAPI(((_catalog_item(rows[0]),),), terminal_meta=meta)
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)
    assert "catalog:status_category_budget_terminal_contract" in check.issues
    assert len(api.calls) == 1
    assert check.evidence["catalog_traversal"] == "invalid"


@pytest.mark.parametrize("mutation,expected_issue", [
    ("duplicate", "catalog:status_category_duplicate_identity"),
    ("unknown_id", "catalog:status_category_identity"),
    ("invalid_id", "catalog:status_category_invalid_id"),
    ("bool_id", "catalog:status_category_invalid_id"),
    ("wrong_status", "catalog:status_category_identity"),
    ("wrong_ref", "catalog:status_category_identity"),
    ("wrong_field", "catalog:status_category_fields"),
    ("wrong_category", "catalog:status_category_memberships"),
])
def test_catalog_budget_does_not_hide_errors_on_an_earlier_page(mutation: str, expected_issue: str) -> None:
    rows = _catalog_rows()
    first = _catalog_item(rows[0])
    if mutation == "duplicate":
        first = _catalog_item(rows[1])
    elif mutation == "unknown_id":
        first["id"] = 99
    elif mutation == "invalid_id":
        first["id"] = [1]
    elif mutation == "bool_id":
        first["id"] = True
    elif mutation == "wrong_status":
        first["library_status"] = "deleted"
    elif mutation == "wrong_ref":
        first["factor_ref"] = "factor:1"
    elif mutation == "wrong_field":
        first["name"] = "wrong"
    else:
        first["library_coin_categories"] = []
    api = PagedCatalogAPI(((first,), (_catalog_item(rows[1]),)), terminal_meta=_budget_meta())
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)
    assert expected_issue in check.issues
    assert check.evidence["catalog_traversal"] == "bounded"
    assert check.evidence["catalog_full_membership_verified"] is False
    assert [call["filters"].get("cursor") for call in api.calls] == [None, "1"]


@pytest.mark.parametrize("entry", ["status", "category"])
@pytest.mark.parametrize("bounded", [False, True])
def test_catalog_cases_record_acceptance_scope_and_completeness_in_junit(entry: str, bounded: bool) -> None:
    from types import SimpleNamespace
    from tests.cases.factor4 import test_catalog_extension_business as cases

    rows = _catalog_rows()
    selected = rows[:2] if bounded else rows
    api = PagedCatalogAPI((tuple(_catalog_item(row) for row in selected),), terminal_meta=_budget_meta() if bounded else None)
    service = Factor4CatalogExtensionService(api)
    repository = SimpleNamespace(catalog_status_members=lambda *args: rows)
    properties: dict[str, object] = {}
    if entry == "status":
        cases.test_catalog_every_kind_and_status_returns_database_members(
            service, repository, "sub_factor", "valid", properties.__setitem__)
    else:
        cases.test_catalog_valid_subfactor_each_coin_category_matches_database(
            service, repository, "all", properties.__setitem__)
    assert properties["catalog_acceptance"] == (
        "BOUNDED_READ_ACCEPTED_NOT_FULL_EXPORT" if bounded else "COMPLETE_CATALOG_ACCEPTED")
    assert properties["catalog_full_membership_verified"] is (not bounded)
    assert properties["catalog_returned_unique_count"] == len(selected)
    assert properties["catalog_database_unique_count"] == 5


def test_catalog_case_records_failed_bounded_evidence_before_raising() -> None:
    from tests.cases.factor4 import test_catalog_extension_business as cases

    rows = _catalog_rows()
    api = PagedCatalogAPI((({**_catalog_item(rows[0]), "name": "wrong"},),), terminal_meta=_budget_meta())
    service = Factor4CatalogExtensionService(api)
    properties: dict[str, object] = {}
    with pytest.raises(AssertionError, match="status_category_fields"):
        cases._verify(lambda: service.check_status_category("sub_factor", "valid", None, rows), properties.__setitem__)
    assert properties["catalog_acceptance"] == "FAILED"
    assert properties["catalog_traversal"] == "bounded"


class InterruptedCatalogAPI(PagedCatalogAPI):
    """Expose controlled paging/statistics dependencies without network requests."""

    def __init__(self, pages: tuple[tuple[dict[str, Any], ...], ...], *,
                 block_page: int | None = None, block_stats: bool = False,
                 terminal_meta: dict[str, Any] | None = None) -> None:
        """Store pages and interruption points; no I/O, return value or exceptions."""
        super().__init__(pages, terminal_meta=terminal_meta)
        self.block_page = block_page
        self.block_stats = block_stats
        self.stats_calls = 0

    def search_catalog(self, **arguments: Any) -> MCPResponse:
        """Return a page or raise ReadPrecondition at the configured request index."""
        if len(self.calls) == self.block_page:
            self.calls.append(arguments)
            raise ReadPrecondition("BLOCKED_DEPENDENCY: SERVICE_UNAVAILABLE")
        return super().search_catalog(**arguments)

    def catalog_stats(self, kind: str, **arguments: Any) -> MCPResponse:
        """Count a stats read; raise a controlled precondition or return valid groups."""
        self.stats_calls += 1
        if self.block_stats:
            raise ReadPrecondition("BLOCKED_DEPENDENCY: RATE_LIMITED")
        return super().catalog_stats(kind, **arguments)


@pytest.mark.parametrize("mutation,expected_issue", [
    ("wrong_name", "catalog:status_category_fields"),
    ("duplicate", "catalog:status_category_duplicate_identity"),
    ("wrong_status", "catalog:status_category_identity"),
])
def test_later_page_precondition_cannot_hide_earlier_catalog_data_errors(mutation: str, expected_issue: str) -> None:
    """Check read pages before classifying an incomplete continuation as blocked."""
    from tests.cases.factor4 import test_catalog_extension_business as cases

    rows = _catalog_rows()
    first_page = [_catalog_item(row) for row in rows[:2]]
    if mutation == "duplicate":
        first_page[1] = dict(first_page[0])
    elif mutation == "wrong_name":
        first_page[0]["name"] = "wrong"
    else:
        first_page[0]["library_status"] = "deleted"
    api = InterruptedCatalogAPI((tuple(first_page), ()), block_page=1)
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)
    assert expected_issue in check.issues
    assert "catalog:status_category_complete_membership" not in check.issues
    assert check.checked_count == 2
    assert check.evidence["catalog_traversal"] == "blocked"
    assert check.evidence["catalog_page_count"] == 1
    assert check.evidence["catalog_statistics"] == "verified"
    assert check.evidence["blocked"] == ("catalog:pagination:BLOCKED_DEPENDENCY: SERVICE_UNAVAILABLE",)
    properties: dict[str, object] = {}
    with pytest.raises(AssertionError, match=expected_issue):
        cases._verify(lambda: check, properties.__setitem__)
    assert properties["catalog_acceptance"] == "FAILED"
    assert properties["catalog_blocked"] == check.evidence["blocked"][0]
    assert len(api.calls) == 2 and api.stats_calls == 1


@pytest.mark.parametrize("block_stats", [False, True])
def test_clean_pages_with_interrupted_continuation_skip_without_inventing_missing_members(block_stats: bool) -> None:
    """Retain both independent dependencies while distinguishing unread rows from loss."""
    from tests.cases.factor4 import test_catalog_extension_business as cases

    rows = _catalog_rows()
    api = InterruptedCatalogAPI((tuple(_catalog_item(row) for row in rows[:2]), ()),
                                block_page=1, block_stats=block_stats)
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)
    assert not check.issues
    assert check.checked_count == 2
    assert len(check.evidence["blocked"]) == (2 if block_stats else 1)
    assert check.evidence["catalog_full_membership_verified"] is False
    properties: dict[str, object] = {}
    with pytest.raises(pytest.skip.Exception, match="catalog checks incomplete"):
        cases._verify(lambda: check, properties.__setitem__)
    assert properties["catalog_acceptance"] == "BLOCKED"
    assert properties["catalog_statistics"] == ("blocked" if block_stats else "verified")
    assert properties["catalog_returned_count"] == 2
    assert len(api.calls) == 2 and api.stats_calls == 1


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("corrupt", [False, True])
def test_stats_precondition_preserves_completed_catalog_scope_and_prior_failures(bounded: bool, corrupt: bool) -> None:
    """A later stats dependency cannot reclassify checked data errors into skips."""
    from tests.cases.factor4 import test_catalog_extension_business as cases

    rows = _catalog_rows()
    items = [_catalog_item(row) for row in (rows[:2] if bounded else rows)]
    if corrupt:
        items[0]["name"] = "wrong"
    api = InterruptedCatalogAPI((tuple(items),), block_stats=True,
                                terminal_meta=_budget_meta() if bounded else None)
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, rows)
    assert bool(check.issues) is corrupt
    assert check.evidence["catalog_traversal"] == ("bounded" if bounded else "complete")
    assert check.evidence["catalog_full_membership_verified"] is (not bounded)
    assert check.evidence["catalog_statistics"] == "blocked"
    assert check.evidence["blocked"] == ("catalog:statistics:BLOCKED_DEPENDENCY: RATE_LIMITED",)
    properties: dict[str, object] = {}
    with pytest.raises(AssertionError if corrupt else pytest.skip.Exception):
        cases._verify(lambda: check, properties.__setitem__)
    assert properties["catalog_acceptance"] == ("FAILED" if corrupt else "BLOCKED")
    assert properties["catalog_full_membership_verified"] is (not bounded)
    assert len(api.calls) == 1 and api.stats_calls == 1


def test_first_page_and_stats_preconditions_do_not_claim_any_checked_rows() -> None:
    """Record zero completed reads and skip only after saving dependency evidence."""
    from tests.cases.factor4 import test_catalog_extension_business as cases

    api = InterruptedCatalogAPI(((),), block_page=0, block_stats=True)
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, _catalog_rows())
    assert check.checked_count == 0 and not check.issues
    assert len(check.evidence["blocked"]) == 2
    properties: dict[str, object] = {}
    with pytest.raises(pytest.skip.Exception):
        cases._verify(lambda: check, properties.__setitem__)
    assert properties["catalog_page_count"] == 0
    assert properties["catalog_returned_unique_count"] == 0
    assert properties["catalog_acceptance"] == "BLOCKED"


def test_statistics_data_failure_still_fails_when_catalog_pagination_is_blocked() -> None:
    """Independent stats assertions remain executable and take priority over paging blocks."""
    from tests.cases.factor4 import test_catalog_extension_business as cases

    api = InterruptedCatalogAPI(((),), block_page=0)
    api.catalog_stats = lambda *args, **kwargs: api._response({"total": 2, "groups": [{"count": 1}]}, None)
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, _catalog_rows())
    assert check.issues == ("catalog:stats_group_sum",)
    assert check.evidence["catalog_statistics"] == "failed"
    assert check.evidence["blocked"]
    with pytest.raises(AssertionError, match="catalog:stats_group_sum"):
        cases._verify(lambda: check)


def test_catalog_precondition_evidence_does_not_copy_untrusted_exception_text() -> None:
    """Only stable dependency codes, never arbitrary transport details, enter reports."""
    api = InterruptedCatalogAPI(((),))

    def unavailable(**arguments: Any) -> MCPResponse:
        raise ReadPrecondition("BLOCKED_DEPENDENCY: SERVICE_UNAVAILABLE secret_response_marker")

    api.search_catalog = unavailable
    check = Factor4CatalogExtensionService(api).check_status_category("sub_factor", "valid", None, _catalog_rows())
    assert check.evidence["blocked"] == ("catalog:pagination:ReadPrecondition",)
    assert "secret_response_marker" not in repr(check)
    assert "secret_response_marker" not in str(check.evidence)
