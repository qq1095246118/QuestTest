"""API 层示例：按资源语义封装 HTTP 调用。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import requests

from api.client import HTTPClient


class ExampleResourceAPI:
    """演示一个资源 API 如何只表达端点语义而不包含业务断言。"""

    def __init__(self, client: HTTPClient) -> None:
        """初始化资源 API。

        参数 ``client`` 是已经配置好基础地址、鉴权、超时和重试的 ``HTTPClient``。
        不返回值；后续资源请求都通过该客户端发送。
        """

        self._client = client

    def create_resource(self, name: str, metadata: Mapping[str, Any] | None = None) -> requests.Response:
        """创建一个示例资源。

        参数 ``name`` 是资源名称，``metadata`` 是可选的资源扩展字段。
        返回原始 HTTP 响应；响应状态码和业务数据由调用方断言或转换。
        """

        payload: dict[str, Any] = {"name": name}
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        return self._client.request(
            "POST",
            "/resources",
            json_body=payload,
            headers={"Content-Type": "application/json"},
        )

    def get_resource(self, resource_id: str) -> requests.Response:
        """读取一个示例资源。

        参数 ``resource_id`` 是资源的唯一标识。
        返回原始 HTTP 响应；资源不存在和权限等业务结果由调用方处理。
        """

        return self._client.request("GET", f"/resources/{resource_id}")
