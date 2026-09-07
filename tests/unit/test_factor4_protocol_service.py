"""正常协议流程的离线反例；不计入真实接口通过数。"""

from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from service.factor4_protocol_service import Factor4ProtocolService
from service.factor4_read_service import ReadContractError

pytestmark = pytest.mark.unit

_READ_TOOLS = (
    "factor_search", "factor_catalog_stats", "schema_get_factor_fields", "schema_get_raw_data",
    "universe_list_symbols", "factor_get_detail", "factor_get_details_batch", "environment_get_daily",
    "factor_get_environment_metrics", "factor_get_environment_tags", "environment_get_recommendations",
    "factor_list_metric_scopes",
)


class InventoryAPI:
    """Return deterministic tools/list pages without network access."""

    def __init__(self, pages: tuple[tuple[str, ...], ...]) -> None:
        """Store page names and an empty cursor log; no return value or I/O."""
        self.pages = pages
        self.cursors: list[str | None] = []

    def list_tools(self, *, cursor: str | None = None) -> MCPResponse:
        """Return the selected page and next cursor; invalid indices raise ValueError/IndexError."""
        self.cursors.append(cursor)
        index = 0 if cursor is None else int(cursor)
        result: dict[str, Any] = {"tools": [
            {"name": name, "inputSchema": {"type": "object", "properties": {}}}
            for name in self.pages[index]
        ]}
        if index + 1 < len(self.pages):
            result["nextCursor"] = str(index + 1)
        return MCPResponse(200, None, {"result": result}, None)


def test_inventory_required_environment_tools_on_second_page_are_discovered() -> None:
    """All required tools may span pages; complete discovery must not report a missing tool."""
    api = InventoryAPI((_READ_TOOLS[:5], _READ_TOOLS[5:]))
    result = Factor4ProtocolService(api).check_descriptors()  # type: ignore[arg-type]
    assert result.checked_count == len(_READ_TOOLS)
    assert not result.issues
    assert api.cursors == [None, "1"]


@pytest.mark.parametrize("missing_tool", _READ_TOOLS)
def test_inventory_reports_each_required_tool_missing_after_complete_traversal(missing_tool: str) -> None:
    """Omitting any required tool fails the merged inventory contract after all pages are read."""
    names = tuple(name for name in _READ_TOOLS if name != missing_tool)
    api = InventoryAPI((names[:5], names[5:]))
    result = Factor4ProtocolService(api).check_descriptors()  # type: ignore[arg-type]
    assert result.checked_count == len(_READ_TOOLS) - 1
    assert "protocol:missing_read_tool" in result.issues
    assert api.cursors == [None, "1"]


class StubAPI:
    """保存可控 MCP 结果并记录读取。"""

    def __init__(self, data: tuple[dict[str, Any], ...] = ({"fields": ["close"]}, {"fields": ["close"]})) -> None:
        self.data = list(data)
        self.calls: list[tuple[str, str | int]] = []

    def call_tool(self, tool: str, arguments: dict[str, Any], *, request_id: str | int) -> MCPResponse:
        """返回一条双表示 schema；没有预置结果时抛 IndexError。"""
        import json
        self.calls.append((tool, request_id))
        body = {"data": self.data.pop(0), "meta": {"request_id": len(self.calls)}}
        return MCPResponse(200, "application/json", {"id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}}, None)


def test_schema_replay_compares_data_not_volatile_meta() -> None:
    api = StubAPI()
    result = Factor4ProtocolService(api).check_schema_replay("schema_get_factor_fields", 41)  # type: ignore[arg-type]
    assert result.checked_count == 2
    assert not result.issues
    assert api.calls == [("schema_get_factor_fields", 41)] * 2


def test_schema_replay_reports_changed_business_fields() -> None:
    api = StubAPI(({"fields": ["close"]}, {"fields": ["open"]}))
    result = Factor4ProtocolService(api).check_schema_replay("schema_get_raw_data", "same")  # type: ignore[arg-type]
    assert "protocol:schema_replay_difference" in result.issues


def test_schema_replay_cannot_call_write_tool() -> None:
    api = StubAPI()
    with pytest.raises(ValueError, match="readonly schema"):
        Factor4ProtocolService(api).check_schema_replay("submit_backtest_factor_feedback", "same")  # type: ignore[arg-type]
    assert not api.calls


def test_empty_schema_is_not_a_successful_replay() -> None:
    result = Factor4ProtocolService(StubAPI(({}, {}))).check_schema_replay("schema_get_raw_data", "same")  # type: ignore[arg-type]
    assert "protocol:empty_schema_data" in result.issues


def test_repeated_inventory_cursor_is_a_contract_failure() -> None:
    class LoopAPI:
        def list_tools(self, *, cursor: str | None = None) -> MCPResponse:
            """返回重复下一页游标；无网络与异常。"""
            return MCPResponse(200, None, {"result": {"tools": [{"name": "a"}], "nextCursor": "loop"}}, None)
    with pytest.raises(ReadContractError, match="repeated cursor"):
        Factor4ProtocolService(LoopAPI()).tool_descriptors()  # type: ignore[arg-type]
