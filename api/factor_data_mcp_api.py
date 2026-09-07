"""Factor Data MCP 的 JSON-RPC 和 Streamable HTTP 协议封装。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias
from uuid import uuid4

import requests

from api.client import HTTPClient


JSONRPCId: TypeAlias = str | int
FactorDetailLevel: TypeAlias = Literal["summary", "definition", "executable"]

_JSON_RPC_VERSION = "2.0"
_DEFAULT_PROTOCOL_VERSION = "2025-06-18"
_ACCEPT_HEADER = "application/json, text/event-stream"
_SENSITIVE_FIELD_PATTERN = re.compile(
    r'(?i)("(?:password|token|access_token|refresh_token|authorization|secret|session_id)"\s*:\s*")'
    r'([^"\\]*(?:\\.[^"\\]*)*)(")'
)
_AUTHORIZATION_VALUE_PATTERN = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)\b\s*[:=]\s*)"
    r"(?:(?:[A-Za-z][A-Za-z0-9_-]*\s+)?[A-Za-z0-9._~+/=-]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_NAF_MCP_TOKEN_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9_-])naf_mcp_[A-Za-z0-9_+./=-]+")
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)
_SESSION_VALUE_PATTERN = re.compile(
    r"(?i)(\b(?:mcp[-_ ]?)?session(?:[-_ ]?(?:id|token|secret))?\b\s*[:=]\s*[\"']?)"
    r"([A-Za-z0-9._~+/=-]{8,})([\"']?)"
)
_SESSION_TOKEN_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:mcp[_-]?session|session[_-](?:id|token|secret))"
    r"[_-][A-Za-z0-9._~+/=-]{8,}"
)
_SENSITIVE_DATA_KEYS = frozenset(
    {
        "authorization",
        "access_token",
        "api_key",
        "password",
        "refresh_token",
        "secret",
        "session_id",
        "session_token",
        "token",
    }
)
_SENSITIVE_DATA_COMPACT_KEYS = frozenset(
    key.replace("_", "") for key in _SENSITIVE_DATA_KEYS
)


class MCPProtocolError(ValueError):
    """表示 HTTP 响应无法按 MCP JSON-RPC 协议解释。"""


class MCPJSONRPCError(RuntimeError):
    """表示服务端返回了合法的 JSON-RPC ``error`` 信封。"""

    def __init__(
        self,
        code: int,
        message: str,
        *,
        request_id: JSONRPCId | None,
        data: Any = None,
    ) -> None:
        """保存服务端错误字段，同时避免异常文本输出凭据或错误数据。

        参数 ``code``、``message``、``request_id`` 和 ``data`` 分别来自 JSON-RPC 错误信封。
        不返回值；异常字符串只包含脱敏后的 code/message，不包含 ``data``、Token 或 MCP Session。
        """

        self.code = code
        self.message = _sanitize_text(message)
        self.request_id = request_id
        self.data = data
        super().__init__(f"MCP JSON-RPC error {code}: {self.message}")

    @property
    def sanitized_data(self) -> Any:
        """Return a recursively sanitized copy of JSON-RPC ``data``.

        ``data`` intentionally retains the original structured object for
        callers that need to inspect protocol-specific fields.  Callers that
        log or expose the payload must use this view so nested credential,
        authorization, and session values cannot be emitted accidentally.
        """

        return _sanitize_data(self.data)

    @property
    def safe_data(self) -> Any:
        """Alias for :attr:`sanitized_data` for logging-oriented callers."""

        return self.sanitized_data


@dataclass(frozen=True)
class MCPResponse:
    """保存一次已验证的 MCP 请求结果，不暴露认证或会话请求头。"""

    status_code: int
    content_type: str | None
    envelope: dict[str, Any] | None
    protocol_version: str | None

    @property
    def result(self) -> Any:
        """返回 JSON-RPC ``result``；通知响应没有信封时返回 ``None``。"""

        if self.envelope is None:
            return None
        return self.envelope.get("result")

    @property
    def structured_content(self) -> dict[str, Any] | None:
        """返回工具结果中的 ``structuredContent`` 对象；字段不存在或类型错误时返回 ``None``。"""

        result = self.result
        if not isinstance(result, Mapping):
            return None
        value = result.get("structuredContent")
        return dict(value) if isinstance(value, Mapping) else None

    @property
    def is_tool_error(self) -> bool:
        """返回工具结果是否声明 ``isError=true``；该业务错误不会被误判为 JSON-RPC 错误。"""

        result = self.result
        return isinstance(result, Mapping) and result.get("isError") is True


@dataclass(frozen=True)
class MCPProbeResponse:
    """未经成功/错误裁决的协议探测；正文不进入默认 repr，HTTP/JSONRPC 状态由 Service 判断。"""
    status_code: int
    content_type: str | None
    envelope: dict[str, Any] = field(repr=False)


class FactorDataMCPAPI:
    """封装 Factor Data MCP 的 JSON-RPC 方法和因子定义/公式读取工具。"""

    def __init__(
        self,
        client: HTTPClient,
        endpoint_path: str = "/mcp/factor-data",
    ) -> None:
        """初始化一个 MCP 会话客户端。

        参数 ``client`` 提供基础 URL、Bearer 鉴权、超时和 HTTP 发送能力；``endpoint_path`` 是 MCP
        Streamable HTTP 相对路径。实例会在初始化成功后保存协议版本和不透明 Session ID，并只在后续请求头中复用。
        不返回值；路径为空时抛出 ``ValueError``。
        """

        normalized_path = str(endpoint_path).strip()
        if not normalized_path:
            raise ValueError("endpoint_path must not be blank")
        self._client = client
        self._endpoint_path = normalized_path
        self._session_id: str | None = None
        self._protocol_version: str | None = None

    @property
    def protocol_version(self) -> str | None:
        """返回初始化协商出的 MCP 协议版本；尚未成功初始化时返回 ``None``。"""

        return self._protocol_version

    def new_connection(self) -> FactorDataMCPAPI:
        """返回同一端点/鉴权配置的新独立未握手连接；不复制 MCP 会话，不发送请求。"""
        return FactorDataMCPAPI(self._client.new_session_client(), self._endpoint_path)

    def close(self) -> None:
        """释放 HTTP Session 并清空协商状态；底层关闭异常透传，无业务请求。"""
        try:
            self._client.close()
        finally:
            self._session_id = None
            self._protocol_version = None

    def probe_protocol(self, raw_body: bytes, *, accept: str = _ACCEPT_HEADER, session_mode: str = "current", authentication: str = "current", origin: str | None = None) -> MCPProbeResponse:
        """发送只读协议负向/SSE探测原始字节；保留错误码，不自动将非2xx视为通过，非法JSON回包抛协议异常。

        ``session_mode`` 为 current/omit/invalid，仅测试 Session 关联；``authentication`` 为 current/missing/invalid/malformed，
        不接受任意外部凭据。``origin`` 可覆盖请求来源头。调用者不得携带业务写方法；网络异常透传。
        """
        if session_mode not in {"current", "omit", "invalid"}:
            raise ValueError("unsupported probe session mode")
        if authentication not in {"current", "missing", "invalid", "malformed"}:
            raise ValueError("unsupported probe authentication")
        headers = self._headers(include_session=session_mode == "current")
        headers["Accept"] = accept
        if session_mode == "invalid":
            headers["Mcp-Session-Id"] = "questtest-invalid-session"
        if authentication in {"invalid", "malformed"}:
            headers["Authorization"] = {"invalid": "Bearer questtest-invalid-token", "malformed": "Bearer"}[authentication]
        if origin is not None:
            headers["Origin"] = origin
        response = self._client.request("POST", self._endpoint_path, raw_body=raw_body, headers=headers, retryable=False, include_authentication=authentication != "missing")
        content_type = _content_type(response)
        if content_type and "text/event-stream" in content_type:
            events = _parse_sse(response.text, "protocol probe")
            if len(events) != 1:
                raise MCPProtocolError("protocol probe requires exactly one response event")
            envelope = events[0]
        else:
            try:
                envelope = response.json()
            except ValueError:
                raise MCPProtocolError(f"protocol probe response is not JSON: HTTP {response.status_code}") from None
            if not isinstance(envelope, dict):
                raise MCPProtocolError("protocol probe response must be an object")
        return MCPProbeResponse(response.status_code, content_type, envelope)

    @property
    def has_session(self) -> bool:
        """返回服务端是否已分配非空 Session ID，但不暴露该值。"""

        return self._session_id is not None

    def initialize(
        self,
        *,
        protocol_version: str = _DEFAULT_PROTOCOL_VERSION,
        client_name: str = "QuestTest",
        client_version: str = "1.0",
        capabilities: Mapping[str, Any] | None = None,
        request_id: JSONRPCId | None = None,
    ) -> MCPResponse:
        """协商 MCP 协议并捕获服务端会话。

        参数 ``protocol_version`` 是客户端请求的 MCP 版本，``client_name``/``client_version`` 标识客户端，
        ``capabilities`` 是客户端能力对象，``request_id`` 可为测试证据指定非空字符串或整数 ID。
        返回验证过的 ``MCPResponse``；网络错误由 ``HTTPClient`` 原样抛出，非法 HTTP/MCP 响应抛出
        ``MCPProtocolError``，JSON-RPC 错误信封抛出 ``MCPJSONRPCError``。
        """

        requested_protocol = _non_blank(protocol_version, "protocol_version")
        normalized_name = _non_blank(client_name, "client_name")
        normalized_client_version = _non_blank(client_version, "client_version")
        if capabilities is not None and not isinstance(capabilities, Mapping):
            raise TypeError("capabilities must be a mapping or None")

        self._session_id = None
        self._protocol_version = None
        response = self._send_request(
            method="initialize",
            params={
                "protocolVersion": requested_protocol,
                "capabilities": dict(capabilities or {}),
                "clientInfo": {
                    "name": normalized_name,
                    "version": normalized_client_version,
                },
            },
            request_id=_request_id(request_id),
            include_session=False,
        )
        result = response.result
        if not isinstance(result, Mapping):
            self._session_id = None
            raise MCPProtocolError("initialize result must be a JSON object")
        negotiated_protocol = result.get("protocolVersion")
        if not isinstance(negotiated_protocol, str) or not negotiated_protocol.strip():
            self._session_id = None
            raise MCPProtocolError("initialize result does not contain a non-empty protocolVersion")
        self._protocol_version = negotiated_protocol.strip()
        return MCPResponse(
            status_code=response.status_code,
            content_type=response.content_type,
            envelope=response.envelope,
            protocol_version=self._protocol_version,
        )

    def notify_initialized(self) -> MCPResponse:
        """发送不带 JSON-RPC ID 的 ``notifications/initialized`` 通知。

        不接收参数；已协商的协议版本和 Session ID 会自动加入请求头。
        返回仅包含 HTTP 元数据且 ``envelope`` 为 ``None`` 的 ``MCPResponse``；网络错误由 ``HTTPClient``
        原样抛出，非 2xx HTTP 状态抛出 ``MCPProtocolError``。通知不等待或解析业务 ``result``。
        """

        payload = {
            "jsonrpc": _JSON_RPC_VERSION,
            "method": "notifications/initialized",
            "params": {},
        }
        response = self._client.request(
            "POST",
            self._endpoint_path,
            json_body=payload,
            headers=self._headers(include_session=True),
            retryable=False,
        )
        self._require_success_status(response, "notifications/initialized")
        self._capture_session(response)
        return MCPResponse(
            status_code=response.status_code,
            content_type=_content_type(response),
            envelope=None,
            protocol_version=self._protocol_version,
        )

    def list_tools(
        self,
        *,
        cursor: str | None = None,
        request_id: JSONRPCId | None = None,
    ) -> MCPResponse:
        """读取一页服务端工具声明。

        参数 ``cursor`` 是服务端上一页返回的非空游标，``request_id`` 可指定 JSON-RPC ID；不传游标时发送空
        ``params``。返回验证过的 ``MCPResponse``；网络、HTTP、响应解析和 JSON-RPC 错误行为与 ``initialize`` 相同。
        """

        params: dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = _non_blank(cursor, "cursor")
        return self._send_request(
            method="tools/list",
            params=params,
            request_id=_request_id(request_id),
            include_session=True,
        )

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: JSONRPCId | None = None,
    ) -> MCPResponse:
        """调用一个具名 MCP 工具。

        参数 ``name`` 是非空工具名，``arguments`` 是工具 JSON 参数对象，``request_id`` 可指定 JSON-RPC ID。
        返回验证过的 ``MCPResponse``；服务端 ``result.isError=true`` 作为合法工具结果保留，JSON-RPC ``error``
        则抛出 ``MCPJSONRPCError``，网络或协议错误按 ``initialize`` 的约定抛出。
        """

        normalized_name = _non_blank(name, "name")
        if not isinstance(arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        return self._send_request(
            method="tools/call",
            params={"name": normalized_name, "arguments": dict(arguments)},
            request_id=_request_id(request_id),
            include_session=True,
        )

    def get_factor_detail(
        self,
        factor_ref: str,
        *,
        detail_level: FactorDetailLevel = "summary",
        children_limit: int = 10,
        children_cursor: str | None = None,
        request_id: JSONRPCId | None = None,
    ) -> MCPResponse:
        """调用 ``factor_get_detail`` 读取一个有界因子定义。

        参数 ``factor_ref`` 使用 ``factor:<id>`` 或 ``sub_factor:<id>``，``detail_level`` 是 summary、definition
        或 executable，``children_limit`` 必须在 1..200，``children_cursor`` 是可选非空分页游标，``request_id``
        可指定 JSON-RPC ID。返回 ``call_tool`` 的 ``MCPResponse``；参数非法时抛出 ``ValueError``/``TypeError``，
        网络、协议及 JSON-RPC 异常按 ``call_tool`` 传播。
        """

        normalized_ref = _non_blank(factor_ref, "factor_ref")
        if detail_level not in {"summary", "definition", "executable"}:
            raise ValueError("detail_level must be summary, definition, or executable")
        if isinstance(children_limit, bool) or not isinstance(children_limit, int):
            raise TypeError("children_limit must be an integer")
        if not 1 <= children_limit <= 200:
            raise ValueError("children_limit must be between 1 and 200")
        arguments: dict[str, Any] = {
            "factor_ref": normalized_ref,
            "detail_level": detail_level,
            "children_limit": children_limit,
        }
        if children_cursor is not None:
            arguments["children_cursor"] = _non_blank(children_cursor, "children_cursor")
        return self.call_tool("factor_get_detail", arguments, request_id=request_id)

    def get_formula(
        self,
        factor_ref: str,
        run_id: str,
        interval: str,
        factor_window_bars: str,
        return_bar_interval: str,
        forward_return_bars: int,
        *,
        calculation_mode: Literal["direct"] = "direct",
        as_of: str | None = None,
        request_id: JSONRPCId | None = None,
    ) -> MCPResponse:
        """调用 ``factor_get_formula`` 读取一个精确指标 Run 的不可变公式证据。

        参数依次标识因子、Run、因子 K 线周期、因子窗口、收益周期和 forward-return bars；
        ``calculation_mode`` 按当前工具契约固定为 direct，``as_of`` 是可选非空 ISO 时间，``request_id`` 可指定
        JSON-RPC ID。返回 ``call_tool`` 的 ``MCPResponse``；本地只校验工具 Schema 可确定的类型/枚举/非空约束，
        参数非法时抛出 ``ValueError``/``TypeError``，网络、协议及 JSON-RPC 异常按 ``call_tool`` 传播。
        """

        if calculation_mode != "direct":
            raise ValueError("calculation_mode must be direct")
        if isinstance(forward_return_bars, bool) or not isinstance(forward_return_bars, int):
            raise TypeError("forward_return_bars must be an integer")
        arguments: dict[str, Any] = {
            "factor_ref": _non_blank(factor_ref, "factor_ref"),
            "run_id": _non_blank(run_id, "run_id"),
            "calculation_mode": calculation_mode,
            "interval": _non_blank(interval, "interval"),
            "factor_window_bars": _non_blank(factor_window_bars, "factor_window_bars"),
            "return_bar_interval": _non_blank(return_bar_interval, "return_bar_interval"),
            "forward_return_bars": forward_return_bars,
        }
        if as_of is not None:
            arguments["as_of"] = _non_blank(as_of, "as_of")
        return self.call_tool("factor_get_formula", arguments, request_id=request_id)

    def get_factor_details_batch(
        self,
        factor_refs: Sequence[str],
        *,
        detail_level: FactorDetailLevel = "summary",
        request_id: JSONRPCId | None = None,
    ) -> MCPResponse:
        """调用 ``factor_get_details_batch`` 批量读取有界因子定义。

        ``factor_refs`` 必须包含 1..50 个非空因子引用，``detail_level`` 是 summary、definition 或
        executable，``request_id`` 可指定 JSON-RPC ID。返回 ``call_tool`` 的 ``MCPResponse``；本地参数
        不符合工具 Schema 时抛出 ``ValueError``/``TypeError``，网络、协议及 JSON-RPC 异常按
        ``call_tool`` 传播。每个因子的业务错误由服务端保留在批量结果中。
        """

        if isinstance(factor_refs, (str, bytes)) or not isinstance(factor_refs, Sequence):
            raise TypeError("factor_refs must be a sequence of strings")
        if not 1 <= len(factor_refs) <= 50:
            raise ValueError("factor_refs must contain between 1 and 50 items")
        normalized_refs = [
            _non_blank(factor_ref, f"factor_refs[{index}]")
            for index, factor_ref in enumerate(factor_refs)
        ]
        if detail_level not in {"summary", "definition", "executable"}:
            raise ValueError("detail_level must be summary, definition, or executable")
        return self.call_tool(
            "factor_get_details_batch",
            {
                "factor_refs": normalized_refs,
                "detail_level": detail_level,
            },
            request_id=request_id,
        )

    def _send_request(
        self,
        *,
        method: str,
        params: Mapping[str, Any],
        request_id: JSONRPCId,
        include_session: bool,
    ) -> MCPResponse:
        """发送一个有 ID 的 JSON-RPC 请求并验证响应信封。"""

        payload = {
            "jsonrpc": _JSON_RPC_VERSION,
            "id": request_id,
            "method": method,
            "params": dict(params),
        }
        response = self._client.request(
            "POST",
            self._endpoint_path,
            json_body=payload,
            headers=self._headers(include_session=include_session),
            retryable=False,
        )
        envelope = self._parse_response(response, request_id, method)
        self._raise_json_rpc_error(envelope)
        self._require_success_status(response, method)
        self._capture_session(response)
        return MCPResponse(
            status_code=response.status_code,
            content_type=_content_type(response),
            envelope=envelope,
            protocol_version=self._protocol_version,
        )

    def _headers(self, *, include_session: bool) -> dict[str, str]:
        """构造不记录敏感值的 MCP 传输头。"""

        headers = {
            "Accept": _ACCEPT_HEADER,
            "Content-Type": "application/json",
        }
        if include_session and self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        if include_session and self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _capture_session(self, response: requests.Response) -> None:
        """从响应头捕获或轮换不透明 MCP Session ID。"""

        session_id = _header_value(response, "mcp-session-id")
        if session_id is not None and session_id.strip():
            self._session_id = session_id.strip()

    def _parse_response(
        self,
        response: requests.Response,
        request_id: JSONRPCId,
        method: str,
    ) -> dict[str, Any]:
        """按 Content-Type 解析 JSON 或 SSE，并选择匹配请求 ID 的信封。"""

        content_type = _content_type(response)
        media_type = content_type.split(";", 1)[0].strip().lower() if content_type else ""
        if media_type == "text/event-stream":
            envelopes = _parse_sse(response.text, method)
            matches = [item for item in envelopes if item.get("id") == request_id]
            if len(matches) != 1:
                raise MCPProtocolError(
                    f"{method} SSE response must contain exactly one envelope for the request id"
                )
            envelope = matches[0]
        elif media_type == "application/json" or media_type.endswith("+json"):
            try:
                value = response.json()
            except (TypeError, ValueError) as error:
                raise MCPProtocolError(
                    f"{method} returned invalid JSON (HTTP {getattr(response, 'status_code', 'unknown')})"
                ) from error
            if not isinstance(value, dict):
                raise MCPProtocolError(f"{method} JSON-RPC response root must be an object")
            envelope = dict(value)
        else:
            raise MCPProtocolError(
                f"{method} returned unsupported Content-Type {content_type!r} "
                f"(HTTP {getattr(response, 'status_code', 'unknown')})"
            )

        _validate_envelope(envelope, request_id, method)
        return envelope

    @staticmethod
    def _require_success_status(response: requests.Response, method: str) -> None:
        """拒绝无 JSON-RPC 解释的非 2xx HTTP 结果，且不输出响应正文。"""

        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int) or not 200 <= status_code < 300:
            raise MCPProtocolError(f"{method} returned HTTP status {status_code}")

    @staticmethod
    def _raise_json_rpc_error(envelope: Mapping[str, Any]) -> None:
        """把合法 JSON-RPC error 对象转换为带结构化字段的异常。"""

        if "error" not in envelope:
            return
        value = envelope["error"]
        if not isinstance(value, Mapping):
            raise MCPProtocolError("JSON-RPC error must be an object")
        code = value.get("code")
        message = value.get("message")
        if isinstance(code, bool) or not isinstance(code, int) or not isinstance(message, str):
            raise MCPProtocolError("JSON-RPC error must contain an integer code and string message")
        raise MCPJSONRPCError(
            code,
            message,
            request_id=envelope.get("id"),
            data=value.get("data"),
        )


def _request_id(value: JSONRPCId | None) -> JSONRPCId:
    """创建或校验一个 JSON-RPC 请求 ID。"""

    if value is None:
        return str(uuid4())
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError("request_id must be a non-empty string, integer, or None")
    if isinstance(value, str) and not value.strip():
        raise ValueError("request_id must not be blank")
    return value


def _non_blank(value: str, field_name: str) -> str:
    """规范化协议字符串字段并拒绝空值。"""

    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _content_type(response: requests.Response) -> str | None:
    """以大小写不敏感方式读取响应 Content-Type。"""

    return _header_value(response, "content-type")


def _header_value(response: requests.Response, name: str) -> str | None:
    """从 requests 兼容响应中大小写不敏感地读取一个头。"""

    headers = getattr(response, "headers", {})
    if not isinstance(headers, Mapping):
        return None
    expected = name.lower()
    for key, value in headers.items():
        if str(key).lower() == expected:
            return str(value)
    return None


def _parse_sse(text: str, method: str) -> list[dict[str, Any]]:
    """按 SSE 事件边界解析全部 ``data:`` JSON 对象。"""

    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush_event() -> None:
        if not data_lines:
            return
        raw_data = "\n".join(data_lines)
        data_lines.clear()
        try:
            value = json.loads(raw_data)
        except (TypeError, ValueError) as error:
            raise MCPProtocolError(f"{method} returned an SSE event with invalid JSON data") from error
        if not isinstance(value, dict):
            raise MCPProtocolError(f"{method} returned an SSE data root that is not an object")
        events.append(dict(value))

    for raw_line in normalized.split("\n"):
        if raw_line == "":
            flush_event()
            continue
        if raw_line.startswith(":"):
            continue
        field, separator, value = raw_line.partition(":")
        if field != "data" or not separator:
            continue
        data_lines.append(value[1:] if value.startswith(" ") else value)
    flush_event()
    if not events:
        raise MCPProtocolError(f"{method} returned an SSE stream without JSON data events")
    return events


def _validate_envelope(
    envelope: Mapping[str, Any],
    request_id: JSONRPCId,
    method: str,
) -> None:
    """校验 JSON-RPC 版本、响应 ID 和 result/error 互斥规则。"""

    if envelope.get("jsonrpc") != _JSON_RPC_VERSION:
        raise MCPProtocolError(f"{method} response does not declare jsonrpc 2.0")
    if "id" not in envelope or envelope.get("id") != request_id:
        raise MCPProtocolError(f"{method} response id does not match the request id")
    has_result = "result" in envelope
    has_error = "error" in envelope
    if has_result == has_error:
        raise MCPProtocolError(f"{method} response must contain exactly one of result or error")


def _sanitize_text(value: str) -> str:
    """从 JSON-RPC 错误消息中移除常见凭据和会话标识文本。"""

    sanitized = _SENSITIVE_FIELD_PATTERN.sub(r"\1<redacted>\3", value)
    sanitized = _AUTHORIZATION_VALUE_PATTERN.sub(r"\1<redacted>", sanitized)
    sanitized = _BEARER_PATTERN.sub(r"\1<redacted>", sanitized)
    sanitized = _NAF_MCP_TOKEN_PATTERN.sub("<redacted-token>", sanitized)
    sanitized = _JWT_PATTERN.sub("<redacted-jwt>", sanitized)
    sanitized = _SESSION_VALUE_PATTERN.sub(r"\1<redacted-session>\3", sanitized)
    return _SESSION_TOKEN_PATTERN.sub("<redacted-session>", sanitized)


def _sanitize_data(value: Any) -> Any:
    """Return a recursively sanitized copy of JSON-compatible error data.

    The protocol payload is normally composed only of mappings, lists and
    scalar values.  Unknown objects are returned unchanged so this helper does
    not unexpectedly alter a caller's typed extension value; strings nested in
    those objects are still sanitized at their containing mapping/list level.
    """

    if isinstance(value, Mapping):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).casefold().replace("-", "_").replace(" ", "_")
            sanitized[key] = (
                "<redacted>"
                if _is_sensitive_data_key(normalized_key)
                else _sanitize_data(item)
            )
        return sanitized
    if isinstance(value, list):
        return [_sanitize_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_data(item) for item in value)
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _is_sensitive_data_key(normalized_key: str) -> bool:
    """Return whether a normalized mapping key should hide its value."""

    compact = normalized_key.replace("_", "")
    return (
        normalized_key in _SENSITIVE_DATA_KEYS
        or compact in _SENSITIVE_DATA_COMPACT_KEYS
        or normalized_key.startswith("authorization_")
        or normalized_key.endswith(
            ("_token", "_secret", "_password", "_api_key", "_session_id", "_session_token")
        )
    )
