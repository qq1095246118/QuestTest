"""投研 Agent 查询接口的协议封装。"""

from __future__ import annotations

import requests

from api.client import HTTPClient


class AgentAPI:
    """封装 Agent API 的当前用户可见 Agent 查询端点。"""

    def __init__(self, client: HTTPClient) -> None:
        """初始化 Agent API。

        参数 ``client`` 必须使用 Agent API 的基础地址和当前 Factor 账号 JWT。
        不返回值；用户归属头由 ``list_agents`` 在发送请求时补充。
        """

        self._client = client

    def list_agents(self, user_id: int) -> requests.Response:
        """查询当前账号可见的投研 Agent 列表。

        参数 ``user_id`` 是与 Authorization JWT 对应的正整数用户 ID。
        返回 Agent 服务的原始 HTTP 响应；该端点通常返回 JSON 数组，具体结构由 Service 校验。
        """

        if isinstance(user_id, bool) or int(user_id) <= 0:
            raise ValueError("user_id must be a positive integer")
        return self._client.request(
            "GET",
            "/agents",
            headers={"X-User-Id": str(int(user_id))},
        )
