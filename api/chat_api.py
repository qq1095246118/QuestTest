"""组合因子测试所需的会话接口封装。"""

from __future__ import annotations

from typing import Any

import requests

from api.client import HTTPClient


class ChatAPI:
    """封装创建组合因子表单所需的 Chat Session 接口。"""

    def __init__(self, client: HTTPClient) -> None:
        """初始化 Chat API。

        参数 ``client`` 是已经配置基础地址、鉴权、超时和重试的 ``HTTPClient``。
        不返回值；会话请求均通过该客户端发送。
        """

        self._client = client

    def create_session(self, title: str) -> requests.Response:
        """创建一个供组合因子表单关联的研究会话。

        参数 ``title`` 是会话标题。
        返回原始 HTTP 响应；会话 ID 和状态由调用方读取和断言。
        """

        return self._client.request(
            "POST",
            "/chat/sessions",
            json_body={"title": title},
            headers={"Content-Type": "application/json"},
        )

    def get_session(self, session_id: int) -> requests.Response:
        """查询一个研究会话。

        参数 ``session_id`` 是会话主键。
        返回原始 HTTP 响应；会话归属和状态由调用方处理。
        """

        return self._client.request("GET", f"/chat/sessions/{session_id}")


class ChatSessionPayload:
    """从 Chat Session 响应中提取稳定字段的轻量工具。"""

    @staticmethod
    def session_id(response_json: dict[str, Any]) -> int:
        """从统一响应 JSON 中读取会话 ID。

        参数 ``response_json`` 是 Chat Session 接口解析后的 JSON 对象。
        返回整数会话 ID；响应结构缺失或类型不正确时抛出 ``ValueError``。
        """

        try:
            value = response_json["data"]["id"]
            return int(value)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Chat Session response does not contain a valid id: {response_json}") from error
