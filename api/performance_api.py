"""Performance Refresh 查询接口的协议封装。"""

from __future__ import annotations

from urllib.parse import quote

import requests

from api.client import HTTPClient


class PerformanceAPI:
    """封装登记接口自动创建的 Performance Refresh 任务查询端点。"""

    def __init__(self, client: HTTPClient) -> None:
        """初始化 Performance API。

        参数 ``client`` 必须使用 Factor Backend 基础地址和具备查询权限的 JWT。
        不返回值；该类只发送查询请求，不会创建或重提交刷新任务。
        """

        self._client = client

    def get_refresh_run(self, task_id: str) -> requests.Response:
        """查询一个 Performance Refresh 任务。

        参数 ``task_id`` 是登记响应返回的非空刷新任务 ID。
        返回原始 HTTP 响应；任务状态、进度、汇总和错误详情由调用方解析。
        """

        normalized_task_id = str(task_id).strip()
        if not normalized_task_id:
            raise ValueError("task_id must not be blank")
        encoded_task_id = quote(normalized_task_id, safe="")
        return self._client.request("GET", f"/factor/performance/runs/{encoded_task_id}")
