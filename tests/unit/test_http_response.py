"""HTTP 响应解析工具的离线单元测试。"""

from __future__ import annotations

from typing import Any

import pytest

from tools.http_response import (
    ResponseBodyDecodeError,
    read_json,
    read_json_object,
    read_json_or_diagnostic,
    response_diagnostic,
)


pytestmark = pytest.mark.unit


class StubResponse:
    """提供可控状态码、响应头、正文和 JSON 解析行为的响应替身。"""

    def __init__(
        self,
        *,
        status_code: int,
        headers: dict[str, str],
        text: str,
        json_value: Any = None,
        json_error: ValueError | None = None,
    ) -> None:
        """保存响应字段和解析结果。

        参数 ``status_code`` 是 HTTP 状态码，``headers`` 是响应头，``text`` 是原始正文，``json_value`` 是成功解析的
        JSON 值，``json_error`` 是需要由 ``json`` 方法抛出的解析异常。不返回值。
        """

        self.status_code = status_code
        self.headers = headers
        self.text = text
        self._json_value = json_value
        self._json_error = json_error

    def json(self) -> Any:
        """返回预置 JSON 值，或抛出预置解析异常。"""

        if self._json_error is not None:
            raise self._json_error
        return self._json_value


class TestHTTPResponseTools:
    """验证 HTTP 响应解析、根节点检查和安全诊断行为。"""

    def test_non_json_response_error_keeps_status_and_content_type_without_secrets(self) -> None:
        """解析 HTML 错误响应时保留状态和 Content-Type，并从诊断正文中移除密码、Bearer Token 和 JWT。"""

        response = StubResponse(
            status_code=502,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text='{"password":"plain-password","authorization":"Bearer header.payload.signature"}',
            json_error=ValueError("not json"),
        )

        with pytest.raises(ResponseBodyDecodeError) as error:
            read_json(response, "submit form")

        message = str(error.value)
        assert "submit form" in message
        assert "502" in message
        assert "text/html" in message
        assert "plain-password" not in message
        assert "header.payload.signature" not in message
        assert "<redacted>" in message

    def test_json_root_must_be_object_when_object_is_required(self) -> None:
        """当调用方要求响应信封时，JSON 数组根节点应转换为带诊断信息的解析错误。"""

        response = StubResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            text="[]",
            json_value=[],
        )

        with pytest.raises(ResponseBodyDecodeError) as error:
            read_json_object(response, "login")

        assert "not an object" in str(error.value)
        assert "200" in str(error.value)
        assert "application/json" in str(error.value)

    def test_read_json_or_diagnostic_returns_safe_structured_error(self) -> None:
        """尽力解析非 JSON 响应时返回结构化诊断，而不是抛出 JSONDecodeError。"""

        response = StubResponse(
            status_code=503,
            headers={"Content-Type": "text/plain"},
            text='{"token":"Bearer abc.def.ghi","password":"secret-value"}',
            json_error=ValueError("not json"),
        )

        result = read_json_or_diagnostic(response)

        assert result["status_code"] == 503
        assert result["content_type"] == "text/plain"
        assert "secret-value" not in result["text"]
        assert "abc.def.ghi" not in result["text"]

    def test_response_diagnostic_truncates_large_body(self) -> None:
        """生成诊断时限制正文长度，避免错误页面污染控制台和报告。"""

        response = StubResponse(
            status_code=500,
            headers={},
            text="x" * 20,
            json_error=ValueError("not json"),
        )

        result = response_diagnostic(response, max_text_length=8)

        assert result == {
            "status_code": 500,
            "content_type": None,
            "text": "xxxxxxxx...<truncated>",
        }

    def test_read_json_returns_scalar_or_array_for_generic_callers(self) -> None:
        """通用读取函数保留服务端合法的标量或数组 JSON，不错误收窄为对象。"""

        response = StubResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            text='[1, 2]',
            json_value=[1, 2],
        )

        assert read_json(response) == [1, 2]
