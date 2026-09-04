"""HTTP 响应解析和脱敏诊断工具。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


class ResponseBodyDecodeError(ValueError):
    """表示 HTTP 响应不是测试期望的合法 JSON。"""


_SENSITIVE_FIELD_PATTERN = re.compile(
    r'(?i)("(?:password|token|access_token|refresh_token|authorization|secret)"\s*:\s*")([^"\\]*(?:\\.[^"\\]*)*)(")'
)
_BEARER_PATTERN = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~-]+")
_JWT_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def read_json(response: Any, action: str = "HTTP response") -> Any:
    """读取响应 JSON，并在解析失败时保留状态和脱敏正文诊断。

    参数 ``response`` 是具有 ``json``、``status_code``、``headers`` 和 ``text`` 属性的 requests 兼容响应，``action``
    是错误信息中的操作名称。返回服务端解析后的任意 JSON 值；响应不是合法 JSON 时抛出
    ``ResponseBodyDecodeError``，异常中包含 HTTP 状态、Content-Type 和截断后的脱敏正文。
    """

    try:
        return response.json()
    except (TypeError, ValueError) as error:
        diagnostic = response_diagnostic(response)
        raise ResponseBodyDecodeError(
            f"{action} returned a non-JSON response: {diagnostic}"
        ) from error


def read_json_object(response: Any, action: str = "HTTP response") -> dict[str, Any]:
    """读取并校验响应根节点为 JSON 对象。

    参数 ``response`` 和 ``action`` 与 ``read_json`` 相同。返回响应 JSON 对象；解析失败或根节点不是对象时抛出
    ``ResponseBodyDecodeError``，异常保留可定位 HTTP 响应的脱敏诊断。
    """

    body = read_json(response, action)
    if not isinstance(body, dict):
        diagnostic = response_diagnostic(response)
        raise ResponseBodyDecodeError(
            f"{action} returned a JSON root that is not an object: {diagnostic}"
        )
    return body


def read_json_or_diagnostic(response: Any) -> Any:
    """尽力读取 JSON，失败时返回可安全写入异常和日志的诊断对象。

    参数 ``response`` 是 requests 兼容响应。返回原始 JSON 值；非 JSON 响应返回包含状态码、Content-Type 和截断脱敏
    正文的字典，不会抛出解析异常，也不会输出密码、Token 或完整密钥。
    """

    try:
        return response.json()
    except (TypeError, ValueError):
        return response_diagnostic(response)


def response_diagnostic(response: Any, *, max_text_length: int = 2000) -> dict[str, Any]:
    """生成 HTTP 响应的有限脱敏诊断信息。

    参数 ``response`` 是 requests 兼容响应，``max_text_length`` 是正文最大保留字符数。返回状态码、Content-Type 和
    脱敏截断正文；不返回请求头中的 Authorization，也不保留完整 Token 或密码。
    """

    headers = getattr(response, "headers", {})
    content_type = None
    if isinstance(headers, Mapping):
        content_type = headers.get("Content-Type") or headers.get("content-type")
    text = str(getattr(response, "text", ""))
    sanitized = _sanitize_text(text)
    if len(sanitized) > max_text_length:
        sanitized = f"{sanitized[:max_text_length]}...<truncated>"
    return {
        "status_code": getattr(response, "status_code", None),
        "content_type": content_type,
        "text": sanitized,
    }


def _sanitize_text(value: str) -> str:
    """脱敏响应正文中的常见凭据字段和 JWT。"""

    sanitized = _SENSITIVE_FIELD_PATTERN.sub(r"\1<redacted>\3", value)
    sanitized = _BEARER_PATTERN.sub(r"\1<redacted>", sanitized)
    return _JWT_PATTERN.sub("<redacted-jwt>", sanitized)
