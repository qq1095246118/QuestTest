"""因子库 Approval 模块原始 API 调用封装。

本模块只负责拼接审批接口请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from service.common.http.http_client import HTTPClient


class ApprovalAPI:
    """Approval 审批接口原始请求封装。

    请求参数:
        实例化时可传入 token，用于访问需要审批权限的接口。
    返回值:
        提供 Approval HTTP 请求方法的 API 客户端实例。
    """

    def __init__(self, token: str | None = None):
        """初始化 Approval API 客户端。

        请求参数:
            token: 可选 JWT token；传入后自动写入 Authorization header。
        返回值:
            无，实例化后保存 base_url 和默认请求头。
        """
        self.base_url = settings.base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        """发送 GET 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            params: 查询参数字典，值为 None 的字段会被过滤。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=self.clean_params(params))

    def post(self, endpoint: str, json: dict[str, Any] | None = None):
        """发送 POST 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            json: 请求 JSON body。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("POST", url, headers=self.headers, json=json)

    def patch(self, endpoint: str, json: dict[str, Any] | None = None):
        """发送 PATCH 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            json: 请求 JSON body。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("PATCH", url, headers=self.headers, json=json)

    def delete(self, endpoint: str):
        """发送 DELETE 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("DELETE", url, headers=self.headers)

    def list_approvals(self, **params: Any):
        """调用审批列表接口。

        请求参数:
            **params: status、target_type、request_type、entity_type、page、limit 等查询参数。
        返回值:
            审批列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/approvals", params)

    def create_approval(self, payload: dict[str, Any]):
        """调用创建审批接口。

        请求参数:
            payload: 创建审批请求的 JSON body。
        返回值:
            创建审批接口 requests.Response 对象。
        """
        return self.post("/api/v1/approvals", json=payload)

    def get_approval(self, approval_id: Any):
        """调用审批详情接口。

        请求参数:
            approval_id: 审批 ID。
        返回值:
            审批详情接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/approvals/{approval_id}")

    def process_approval(self, approval_id: Any, action: str, comment: str | None = None):
        """调用处理审批接口。

        请求参数:
            approval_id: 审批 ID。
            action: approve 或 reject。
            comment: 可选审批意见。
        返回值:
            处理审批接口 requests.Response 对象。
        """
        return self.patch(f"/api/v1/approvals/{approval_id}", json={"action": action, "comment": comment})

    def cancel_approval(self, approval_id: Any):
        """调用取消审批接口。

        请求参数:
            approval_id: 审批 ID。
        返回值:
            取消审批接口 requests.Response 对象。
        """
        return self.delete(f"/api/v1/approvals/{approval_id}")

    def batch_approve(self, approval_ids: list[Any], comment: str | None = None):
        """调用批量通过审批接口。

        请求参数:
            approval_ids: 待批量通过的审批 ID 列表。
            comment: 可选审批意见。
        返回值:
            批量通过审批接口 requests.Response 对象。
        """
        return self.post(
            "/api/v1/approvals/batch/approve",
            json={"approval_ids": approval_ids, "comment": comment},
        )

    @staticmethod
    def clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
        """过滤查询参数中的 None 值。

        请求参数:
            params: 原始查询参数字典。
        返回值:
            去掉 None 值后的查询参数字典；输入为空时返回空字典。
        """
        if not params:
            return {}
        return {key: value for key, value in params.items() if value is not None}
