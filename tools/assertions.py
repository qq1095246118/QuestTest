"""低业务耦合的 HTTP 响应断言工具。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from tools.http_response import read_json


class JsonResponse(Protocol):
    """描述通用响应断言所需的最小响应接口。"""

    status_code: int

    def json(self) -> Any:
        """解析响应 JSON。

        不接收参数。
        返回 JSON 解析结果；响应体非 JSON 时抛出底层异常。
        """


class ResponseAssertions:
    """提供可跨业务复用的响应状态和字段断言。"""

    @staticmethod
    def has_status_code(response: JsonResponse, expected_status_code: int) -> None:
        """断言 HTTP 响应状态码。

        参数 ``response`` 是任意具有 ``status_code`` 的响应对象，``expected_status_code`` 是预期状态码。
        不返回值；实际状态不一致时抛出 ``AssertionError``。
        """

        assert response.status_code == expected_status_code, (
            f"Expected HTTP {expected_status_code}, received HTTP {response.status_code}"
        )

    @staticmethod
    def has_json_fields(response: JsonResponse, expected_fields: Mapping[str, Any]) -> dict[str, Any]:
        """断言 JSON 对象包含指定字段和值。

        参数 ``response`` 是可解析 JSON 的响应对象，``expected_fields`` 是字段名到预期值的映射。
        返回已解析的 JSON 字典；响应不是对象或任一字段不匹配时抛出 ``AssertionError``。
        """

        payload = read_json(response, "response assertion")
        assert isinstance(payload, dict), f"Expected JSON object, received {type(payload).__name__}"
        for field, expected_value in expected_fields.items():
            assert payload.get(field) == expected_value, (
                f"Expected JSON field {field!r} to be {expected_value!r}, received {payload.get(field)!r}"
            )
        return payload
