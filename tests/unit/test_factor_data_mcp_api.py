"""Factor Data MCP 协议封装的离线单元测试。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from api.client import HTTPClient
from api.factor_data_mcp_api import (
    FactorDataMCPAPI,
    MCPJSONRPCError,
    MCPProtocolError,
)
from config.settings import ApiSettings


pytestmark = pytest.mark.unit


class StubResponse:
    """提供可控 HTTP 状态、响应头、正文和 JSON 解析行为。"""

    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        json_value: Any = None,
        text: str | None = None,
        json_error: ValueError | None = None,
    ) -> None:
        """保存一次预置响应。

        参数用于控制 HTTP 状态、响应头、JSON 值、原始正文和解析异常；不返回值。
        """

        self.status_code = status_code
        self.headers = dict(headers or {"Content-Type": "application/json"})
        self._json_value = json_value
        self._json_error = json_error
        self.text = text if text is not None else json.dumps(json_value)

    def json(self) -> Any:
        """返回预置 JSON 值，或抛出预置解析异常。"""

        if self._json_error is not None:
            raise self._json_error
        return self._json_value


class RecordingSession:
    """按顺序返回响应并记录 HTTPClient 发出的完整请求。"""

    def __init__(self, *responses: StubResponse) -> None:
        """保存有序响应队列；没有剩余响应时调用 ``request`` 会让测试失败。"""

        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> StubResponse:
        """记录一次请求并返回队列首个响应。

        参数 ``method``、``url`` 和 ``kwargs`` 来自 ``HTTPClient``；返回预置响应，无响应可用时抛出
        ``AssertionError``，且不会进行网络访问。
        """

        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


def rpc_result(request_id: str | int, result: Any) -> dict[str, Any]:
    """构造一个合法 JSON-RPC 成功信封。"""

    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def api_with_session(*responses: StubResponse) -> tuple[FactorDataMCPAPI, RecordingSession]:
    """构造使用假 Token 和响应队列的离线 MCP API。"""

    session = RecordingSession(*responses)
    client = HTTPClient(
        ApiSettings(
            base_url="https://factor.example.test",
            timeout_seconds=7,
            retry_attempts=2,
            retry_backoff_seconds=0,
            auth_token="unit-test-token",
        ),
        session=session,
    )
    return FactorDataMCPAPI(client), session


class TestFactorDataMCPAPI:
    """验证 MCP 握手、传输解析、会话复用和工具语义。"""

    def test_new_connection_does_not_copy_negotiated_session(self) -> None:
        """独立连接无旧协议/Session 状态；关闭清空原状态且不触发网络。"""
        api, session = api_with_session(StubResponse(
            headers={"Content-Type": "application/json", "Mcp-Session-Id": "unit-opaque-session"},
            json_value=rpc_result("init", {"protocolVersion": "2025-06-18"}),
        ))
        api.initialize(request_id="init")
        fresh = api.new_connection()
        try:
            assert fresh is not api
            assert fresh.has_session is False
            assert fresh.protocol_version is None
            assert api.has_session is True
            assert len(session.calls) == 1
        finally:
            fresh.close()
            api.close()
        assert api.has_session is False
        assert api.protocol_version is None

    def test_initialize_sends_contract_and_captures_session_without_exposing_it(self) -> None:
        """初始化应发送标准参数、接受 JSON charset，并只公开会话是否存在。"""

        response = StubResponse(
            headers={
                "content-type": "application/json; charset=utf-8",
                "MCP-SESSION-ID": "opaque-session-secret",
            },
            json_value=rpc_result(
                "init-1",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "factor-data", "version": "1.0"},
                },
            ),
        )
        api, session = api_with_session(response)

        result = api.initialize(
            client_name="QuestTest-AI",
            client_version="2.0",
            capabilities={"sampling": {}},
            request_id="init-1",
        )

        assert result.protocol_version == "2025-06-18"
        assert api.protocol_version == "2025-06-18"
        assert api.has_session is True
        assert "opaque-session-secret" not in repr(result)
        assert session.calls == [
            {
                "method": "POST",
                "url": "https://factor.example.test/mcp/factor-data",
                "params": None,
                "json": {
                    "jsonrpc": "2.0",
                    "id": "init-1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"sampling": {}},
                        "clientInfo": {"name": "QuestTest-AI", "version": "2.0"},
                    },
                },
                "headers": {
                    "Accept": "application/json, text/event-stream",
                    "Authorization": "Bearer unit-test-token",
                    "Content-Type": "application/json",
                },
                "timeout": 7,
            }
        ]

    def test_initialized_notification_has_no_id_and_reuses_protocol_session_headers(self) -> None:
        """初始化通知不带 ID、不解析结果，并携带协商版本和会话头。"""

        initialize = StubResponse(
            headers={"Content-Type": "application/json", "Mcp-Session-Id": "session-123"},
            json_value=rpc_result("init", {"protocolVersion": "2025-06-18"}),
        )
        notification = StubResponse(
            status_code=202,
            headers={"Content-Type": "text/plain"},
            text="",
        )
        api, session = api_with_session(initialize, notification)
        api.initialize(request_id="init")

        result = api.notify_initialized()

        assert result.status_code == 202
        assert result.envelope is None
        assert result.result is None
        request = session.calls[1]
        assert request["json"] == {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        assert request["headers"]["MCP-Protocol-Version"] == "2025-06-18"
        assert request["headers"]["Mcp-Session-Id"] == "session-123"
        assert request["headers"]["Authorization"] == "Bearer unit-test-token"

    def test_list_tools_sends_cursor_and_returns_result(self) -> None:
        """工具分页枚举应按 tools/list 参数语义发送游标。"""

        response = StubResponse(
            json_value=rpc_result(
                7,
                {"tools": [{"name": "factor_get_detail"}], "nextCursor": "next-page"},
            )
        )
        api, session = api_with_session(response)

        result = api.list_tools(cursor="cursor-1", request_id=7)

        assert result.result == {
            "tools": [{"name": "factor_get_detail"}],
            "nextCursor": "next-page",
        }
        assert session.calls[0]["json"]["method"] == "tools/list"
        assert session.calls[0]["json"]["params"] == {"cursor": "cursor-1"}

    def test_call_tool_preserves_structured_content_and_business_error(self) -> None:
        """工具的 structuredContent 和 isError 属于 result，不应被当作 JSON-RPC error。"""

        response = StubResponse(
            json_value=rpc_result(
                "call-1",
                {
                    "content": [{"type": "text", "text": "not found"}],
                    "structuredContent": {
                        "error": {"code": "FACTOR_NOT_FOUND", "message": "not found"}
                    },
                    "isError": True,
                },
            )
        )
        api, session = api_with_session(response)

        result = api.call_tool(
            "factor_search",
            {"query": "momentum"},
            request_id="call-1",
        )

        assert result.is_tool_error is True
        assert result.structured_content == {
            "error": {"code": "FACTOR_NOT_FOUND", "message": "not found"}
        }
        assert session.calls[0]["json"]["params"] == {
            "name": "factor_search",
            "arguments": {"query": "momentum"},
        }

    def test_get_factor_detail_builds_only_the_documented_tool_arguments(self) -> None:
        """因子详情入口应固定工具名并显式表达详情级别和子项分页。"""

        response = StubResponse(json_value=rpc_result("detail", {"isError": False}))
        api, session = api_with_session(response)

        api.get_factor_detail(
            " sub_factor:180 ",
            detail_level="executable",
            children_limit=25,
            children_cursor=" child-page ",
            request_id="detail",
        )

        assert session.calls[0]["json"]["params"] == {
            "name": "factor_get_detail",
            "arguments": {
                "factor_ref": "sub_factor:180",
                "detail_level": "executable",
                "children_limit": 25,
                "children_cursor": "child-page",
            },
        }

    def test_get_formula_builds_exact_metric_run_identity(self) -> None:
        """公式入口必须发送不可变证据查询所需的完整指标 Run 身份。"""

        response = StubResponse(json_value=rpc_result("formula", {"isError": False}))
        api, session = api_with_session(response)

        api.get_formula(
            "sub_factor:180",
            "run-42",
            "1h",
            "24H",
            "1h",
            1,
            as_of="2026-09-04T00:00:00Z",
            request_id="formula",
        )

        assert session.calls[0]["json"]["params"] == {
            "name": "factor_get_formula",
            "arguments": {
                "factor_ref": "sub_factor:180",
                "run_id": "run-42",
                "calculation_mode": "direct",
                "interval": "1h",
                "factor_window_bars": "24H",
                "return_bar_interval": "1h",
                "forward_return_bars": 1,
                "as_of": "2026-09-04T00:00:00Z",
            },
        }

    def test_get_factor_details_batch_builds_bounded_documented_arguments(self) -> None:
        """批量详情入口应标准化引用，并只发送服务端 Schema 声明的字段。"""

        response = StubResponse(json_value=rpc_result("details", {"isError": False}))
        api, session = api_with_session(response)

        api.get_factor_details_batch(
            [" sub_factor:180 ", "sub_factor:181"],
            detail_level="executable",
            request_id="details",
        )

        assert session.calls[0]["json"]["params"] == {
            "name": "factor_get_details_batch",
            "arguments": {
                "factor_refs": ["sub_factor:180", "sub_factor:181"],
                "detail_level": "executable",
            },
        }

    @pytest.mark.parametrize("factor_refs", [[], ["sub_factor:1"] * 51])
    def test_get_factor_details_batch_rejects_out_of_bounds_batches(
        self,
        factor_refs: list[str],
    ) -> None:
        """批量详情入口不应发送服务端明确拒绝的空批次或超大批次。"""

        api, session = api_with_session()

        with pytest.raises(ValueError, match="between 1 and 50"):
            api.get_factor_details_batch(factor_refs)

        assert session.calls == []

    def test_sse_selects_matching_response_among_notifications_and_multiline_data(self) -> None:
        """SSE 解析应忽略心跳/通知，并从多行 data 中选择匹配请求 ID 的信封。"""

        sse_text = (
            ": keepalive\n\n"
            "event: message\n"
            'data: {"jsonrpc":"2.0","method":"notifications/progress",\n'
            'data: "params":{"progress":0.5}}\n\n'
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":"sse-call",\n'
            'data: "result":{"tools":[]}}\n\n'
        )
        response = StubResponse(
            headers={"Content-Type": "text/event-stream; charset=utf-8"},
            text=sse_text,
            json_error=ValueError("SSE is not ordinary JSON"),
        )
        api, _ = api_with_session(response)

        result = api.list_tools(request_id="sse-call")

        assert result.result == {"tools": []}

    def test_json_rpc_error_raises_typed_exception_and_redacts_message(self) -> None:
        """JSON-RPC error 应保留结构化字段，但异常文本不能泄漏 Bearer Token 或 data。"""

        response = StubResponse(
            headers={
                "Content-Type": "application/problem+json",
                "Mcp-Session-Id": "session-must-not-leak",
            },
            json_value={
                "jsonrpc": "2.0",
                "id": "bad-call",
                "error": {
                    "code": -32602,
                    "message": "invalid Authorization: Bearer abc.secret-token_123",
                    "data": {"token": "raw-data-secret"},
                },
            },
        )
        api, _ = api_with_session(response)

        with pytest.raises(MCPJSONRPCError) as captured:
            api.list_tools(request_id="bad-call")

        error = captured.value
        assert error.code == -32602
        assert error.request_id == "bad-call"
        assert error.data == {"token": "raw-data-secret"}
        assert "abc.secret-token_123" not in str(error)
        assert "raw-data-secret" not in str(error)
        assert "session-must-not-leak" not in str(error)
        assert "<redacted>" in str(error)

    @pytest.mark.parametrize(
        ("message", "secret"),
        [
            (
                "upstream rejected naf_mcp_abcdefghijklmnopqrstuvwxyz012345",
                "naf_mcp_abcdefghijklmnopqrstuvwxyz012345",
            ),
            (
                "upstream JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature_value_123",
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature_value_123",
            ),
            (
                "Mcp-Session-Id=session-secret-value-123456",
                "session-secret-value-123456",
            ),
            (
                "rejected session_token_abcdefghijklmnopqrstuvwxyz012345",
                "session_token_abcdefghijklmnopqrstuvwxyz012345",
            ),
        ],
        ids=["mcp-token", "jwt", "session-header", "session-token"],
    )
    def test_json_rpc_error_redacts_unwrapped_token_shapes(
        self,
        message: str,
        secret: str,
    ) -> None:
        """异常字符串应脱敏无 Bearer 包装的 MCP Token、JWT 和会话类密钥。"""

        error = MCPJSONRPCError(-32000, message, request_id="redaction")

        assert secret not in error.message
        assert secret not in str(error)
        assert "<redacted" in str(error)

    @pytest.mark.parametrize(
        ("message", "secret"),
        [
            ("Authorization: Bearer abc+/==", "abc+/=="),
            ("Authorization=Basic abc+/==", "abc+/=="),
            ("Bearer abc+/==", "abc+/=="),
            ("naf_mcp_abc+/==", "naf_mcp_abc+/=="),
            ("session_token=session+/==", "session+/=="),
        ],
    )
    def test_json_rpc_error_redacts_base64_style_authorization_and_session_values(
        self,
        message: str,
        secret: str,
    ) -> None:
        """Bearer/header/session values containing ``+``, ``/`` and ``=`` are hidden."""

        error = MCPJSONRPCError(-32000, message, request_id="redaction-boundary")

        assert secret not in error.message
        assert secret not in str(error)

    def test_json_rpc_error_preserves_raw_data_and_exposes_sanitized_data_view(self) -> None:
        """Structured data remains API-compatible while the logging view is safe."""

        raw_data = {
            "token": "raw-token-secret",
            "nested": [
                {
                    "Authorization": "Bearer nested-secret+/=",
                    "safe": "Bearer ordinary-secret+/=",
                }
            ],
        }
        error = MCPJSONRPCError(-32000, "upstream failure", request_id="data-view", data=raw_data)

        assert error.data == raw_data
        assert error.sanitized_data == {
            "token": "<redacted>",
            "nested": [
                {
                    "Authorization": "<redacted>",
                    "safe": "Bearer <redacted>",
                }
            ],
        }
        assert error.safe_data == error.sanitized_data

    def test_json_rpc_error_is_preserved_when_http_status_is_not_successful(self) -> None:
        """非 2xx 响应中的合法 JSON-RPC error 仍应映射成可检查的 RPC 异常。"""

        response = StubResponse(
            status_code=400,
            json_value={
                "jsonrpc": "2.0",
                "id": "bad-request",
                "error": {"code": -32600, "message": "Invalid Request"},
            },
        )
        api, _ = api_with_session(response)

        with pytest.raises(MCPJSONRPCError) as captured:
            api.list_tools(request_id="bad-request")

        assert captured.value.code == -32600
        assert captured.value.message == "Invalid Request"

    def test_failed_reinitialize_does_not_reuse_previous_session(self) -> None:
        """新握手缺少协议版本时应清除旧协商状态，避免后续请求携带失效 Session。"""

        first_initialize = StubResponse(
            headers={"Content-Type": "application/json", "Mcp-Session-Id": "old-session"},
            json_value=rpc_result("first", {"protocolVersion": "2025-06-18"}),
        )
        invalid_initialize = StubResponse(
            headers={"Content-Type": "application/json", "Mcp-Session-Id": "invalid-session"},
            json_value=rpc_result("second", {"serverInfo": {"name": "factor-data"}}),
        )
        tools = StubResponse(json_value=rpc_result("tools", {"tools": []}))
        api, session = api_with_session(first_initialize, invalid_initialize, tools)
        api.initialize(request_id="first")

        with pytest.raises(MCPProtocolError, match="protocolVersion"):
            api.initialize(request_id="second")
        api.list_tools(request_id="tools")

        assert api.has_session is False
        assert api.protocol_version is None
        assert "Mcp-Session-Id" not in session.calls[2]["headers"]
        assert "MCP-Protocol-Version" not in session.calls[2]["headers"]

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (
                StubResponse(json_value=[], text="[]"),
                "root must be an object",
            ),
            (
                StubResponse(
                    headers={"Content-Type": "application/json"},
                    text='{"token":"body-secret"}',
                    json_error=ValueError("bad json"),
                ),
                "invalid JSON",
            ),
            (
                StubResponse(
                    headers={"Content-Type": "text/event-stream"},
                    text="event: ping\n\n",
                ),
                "without JSON data events",
            ),
            (
                StubResponse(
                    headers={"Content-Type": "text/html"},
                    text="session-must-not-appear",
                ),
                "unsupported Content-Type",
            ),
        ],
    )
    def test_malformed_transport_raises_protocol_error_without_response_body(
        self,
        response: StubResponse,
        message: str,
    ) -> None:
        """非法 JSON/SSE/媒体类型应给出可定位错误，同时不拼接可能含凭据的响应正文。"""

        api, _ = api_with_session(response)

        with pytest.raises(MCPProtocolError) as captured:
            api.list_tools(request_id="request-1")

        assert message in str(captured.value)
        assert "body-secret" not in str(captured.value)
        assert "session-must-not-appear" not in str(captured.value)

    def test_mismatched_response_id_and_ambiguous_envelope_are_rejected(self) -> None:
        """响应 ID 不一致或同时包含 result/error 时必须被判为协议错误。"""

        wrong_id = StubResponse(json_value=rpc_result("other", {}))
        ambiguous = StubResponse(
            json_value={
                "jsonrpc": "2.0",
                "id": "expected",
                "result": {},
                "error": {"code": -32603, "message": "unexpected"},
            }
        )
        api, _ = api_with_session(wrong_id, ambiguous)

        with pytest.raises(MCPProtocolError, match="id does not match"):
            api.list_tools(request_id="expected")
        with pytest.raises(MCPProtocolError, match="exactly one"):
            api.list_tools(request_id="expected")

    def test_invalid_semantic_arguments_fail_before_http_request(self) -> None:
        """Schema 已声明的空值、枚举、范围和类型错误应在发送请求前失败。"""

        api, session = api_with_session()

        with pytest.raises(ValueError, match="factor_ref"):
            api.get_factor_detail(" ")
        with pytest.raises(ValueError, match="detail_level"):
            api.get_factor_detail("factor:1", detail_level="full")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="between 1 and 200"):
            api.get_factor_detail("factor:1", children_limit=0)
        with pytest.raises(TypeError, match="forward_return_bars"):
            api.get_formula("factor:1", "run", "1h", "24H", "1h", True)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="calculation_mode"):
            api.get_formula(
                "factor:1",
                "run",
                "1h",
                "24H",
                "1h",
                1,
                calculation_mode="aggregate",  # type: ignore[arg-type]
            )

        assert session.calls == []

    def test_http_and_client_logs_do_not_contain_token_or_session(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """HTTP 请求日志仅含方法、相对路径、状态和耗时，不应出现 Token 或 Session。"""

        initialize = StubResponse(
            headers={"Content-Type": "application/json", "Mcp-Session-Id": "secret-session"},
            json_value=rpc_result("init", {"protocolVersion": "2025-06-18"}),
        )
        tools = StubResponse(json_value=rpc_result("list", {"tools": []}))
        api, _ = api_with_session(initialize, tools)

        with caplog.at_level("INFO"):
            api.initialize(request_id="init")
            api.list_tools(request_id="list")

        assert "unit-test-token" not in caplog.text
        assert "secret-session" not in caplog.text

    def test_non_success_notification_rejects_status_without_exposing_body(self) -> None:
        """通知的非 2xx 响应应只报告状态，不输出响应正文。"""

        response = StubResponse(
            status_code=401,
            headers={"Content-Type": "text/plain"},
            text="Bearer credential-must-not-leak",
        )
        api, _ = api_with_session(response)

        with pytest.raises(MCPProtocolError) as captured:
            api.notify_initialized()

        assert "401" in str(captured.value)
        assert "credential-must-not-leak" not in str(captured.value)
