"""API 层 Mock 示例，验证端点语义而不访问真实服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api.client import HTTPClient
from api.example_api import ExampleResourceAPI
from config.settings import ApiSettings
from tools.assertions import ResponseAssertions
from tools.file_utils import FileUtils


class FakeResponse:
    """为 API 层示例提供可控的响应替身。"""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        """初始化固定响应。

        参数 ``status_code`` 是要返回的 HTTP 状态码，``payload`` 是 JSON 响应对象。
        不返回值；实例由 ``FakeSession`` 返回给 HTTP 客户端。
        """

        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        """返回预设 JSON 响应对象。

        不接收参数。
        返回构造时传入的响应字典。
        """

        return self._payload


class FakeSession:
    """记录请求参数并返回固定响应的 Session 替身。"""

    def __init__(self, response: FakeResponse) -> None:
        """初始化 Session 替身。

        参数 ``response`` 是每次请求返回的固定响应。
        不返回值；``requests`` 属性用于 Case 对协议参数断言。
        """

        self._response = response
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        """记录一次 HTTP 请求并返回预设响应。

        参数 ``method``、``url`` 和关键字参数来自 ``HTTPClient``。
        返回构造时注入的 ``FakeResponse``，不执行任何网络访问。
        """

        self.requests.append({"method": method, "url": url, **kwargs})
        return self._response


class TestExampleResourceAPI:
    """验证资源 API 对端点、请求体和响应的基础封装。"""

    @pytest.mark.smoke
    def test_create_resource_sends_semantic_request(self) -> None:
        """验证创建资源时的端点、JSON 请求体和响应字段。

        不接收业务输入；读取 ``tests/data/example_resource.json``，通过 Mock Session 调用 ``ExampleResourceAPI``。
        不返回值；请求参数或响应断言不符合预期时抛出 ``AssertionError``。
        """

        data_path = Path(__file__).resolve().parents[1] / "data" / "example_resource.json"
        payload = FileUtils.load_json_object(data_path)
        response = FakeResponse(201, {"id": "resource-1", "name": payload["name"]})
        session = FakeSession(response)
        client = HTTPClient(
            ApiSettings(
                base_url="https://example.test",
                timeout_seconds=5,
                retry_attempts=0,
                retry_backoff_seconds=0,
                auth_token="test-token",
            ),
            session=session,
        )
        resource_api = ExampleResourceAPI(client)

        actual_response = resource_api.create_resource(payload["name"], payload["metadata"])

        ResponseAssertions.has_status_code(actual_response, 201)
        ResponseAssertions.has_json_fields(actual_response, {"id": "resource-1", "name": payload["name"]})
        assert session.requests == [
            {
                "method": "POST",
                "url": "https://example.test/resources",
                "params": None,
                "json": payload,
                "headers": {
                    "Accept": "application/json",
                    "Authorization": "Bearer test-token",
                    "Content-Type": "application/json",
                },
                "timeout": 5,
            }
        ]
