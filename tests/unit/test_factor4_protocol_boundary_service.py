"""协议负向断言反例：解析错误/任意内部错误/截断text不能误报通过。"""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPJSONRPCError, MCPProbeResponse, MCPProtocolError, MCPResponse
from service.factor4_protocol_boundary_service import Factor4ProtocolBoundaryService, check_explicit_rejection, check_strict_dual_representation

pytestmark = pytest.mark.unit


def response(body: dict[str, Any], *, error: bool = False, text: str | None = None) -> MCPResponse:
    """构造离线双表示工具响应，无请求；指定text用于截断/差异反例。"""
    return MCPResponse(200, "application/json", {"result": {"isError": error, "structuredContent": body, "content": [{"type": "text", "text": json.dumps(body) if text is None else text}]}}, None)


@pytest.mark.parametrize("code", [-32602, -32603])
def test_only_parameter_jsonrpc_rejection_is_accepted(code: int) -> None:
    def call(*args: Any) -> Any:
        raise MCPJSONRPCError(code, "rejected", request_id=1)
    api = SimpleNamespace(call_tool=call)
    if code == -32602:
        assert not check_explicit_rejection(api, "factor_search", {}).issues
    else:
        with pytest.raises(MCPJSONRPCError):
            check_explicit_rejection(api, "factor_search", {})


@pytest.mark.parametrize("code,valid", [("INVALID_ARGUMENT", True), ("INTERNAL_ERROR", False), ("NOT_FOUND", False)])
def test_tool_rejection_requires_expected_business_code(code: str, valid: bool) -> None:
    api = SimpleNamespace(call_tool=lambda *args: response({"error": {"code": code}}, error=True))
    assert bool(check_explicit_rejection(api, "factor_search", {}).issues) is not valid


@pytest.mark.parametrize("text,issue", [('{"data":', "protocol:text_not_complete_json"), ('{"data":{"value":2}}', "protocol:dual_representation_mismatch")])
def test_structured_content_cannot_mask_incomplete_or_different_text(text: str, issue: str) -> None:
    assert check_strict_dual_representation(response({"data": {"value": 1}}, text=text)).issues == (issue,)


def test_complete_dual_representation_passes() -> None:
    assert not check_strict_dual_representation(response({"data": {"value": 1}})).issues


@pytest.mark.parametrize("status,code,issues", [(400, -32700, False), (500, -32700, True), (400, -32603, True)])
def test_raw_protocol_rejection_checks_status_and_exact_error(status: int, code: int, issues: bool) -> None:
    api = SimpleNamespace(probe_protocol=lambda raw: MCPProbeResponse(status, "application/json", {"jsonrpc": "2.0", "error": {"code": code, "message": "bad input"}}))
    service = Factor4ProtocolBoundaryService(api, None)
    assert bool(service.check_raw_rejection(b'{', -32700).issues) is issues


def test_raw_unparseable_response_propagates_instead_of_passing() -> None:
    def probe(raw: bytes) -> Any:
        raise MCPProtocolError("invalid JSON")
    service = Factor4ProtocolBoundaryService(SimpleNamespace(probe_protocol=probe), None)
    with pytest.raises(MCPProtocolError):
        service.check_raw_rejection(b'{', -32700)
