"""Factor 4.0 测试 Backend 的只读端点语义；不提供计算/发布/内部写入口。"""

from datetime import datetime

import requests

from api.client import HTTPClient


class Factor4BackendAPI:
    """复用现有测试 JWT 客户端读取环境和最终 summary 指标。"""

    def __init__(self, client: HTTPClient) -> None:
        """保存已通过测试环境/用户认证的客户端；无请求和返回值。"""
        self.client = client

    def daily(self, label_kind: str, *, environment_date: str | None = None, as_of: datetime | None = None, limit: int = 10) -> requests.Response:
        """按 kind/日期/PIT 读当前环境；返回原始响应，HTTP 网络异常透传。"""
        params: dict[str, object] = {"label_kind": label_kind, "include_revisions": False, "limit": limit}
        if environment_date is not None:
            params["environment_date"] = environment_date
        if as_of is not None:
            params["as_of"] = as_of.isoformat()
        return self.client.request("GET", "/market-environments/daily", params=params)

    def summary_metrics(self, factor_id: int, is_sub_factor: bool) -> requests.Response:
        """读已由 Repository 确认为至多 20 行的因子 summary；最大查询 5000，无分页伪通过。"""
        return self.client.request("GET", "/factor-ic/summary-metrics", params={"factor_id": factor_id, "is_sub_factor_id": is_sub_factor, "limit": 5000})

    def openapi(self) -> requests.Response:
        """读取同一 Backend 的公开 OpenAPI YAML；返回原始响应，网络异常透传。"""
        return self.client.request("GET", "/openapi.yaml")

    def recommendations(self, market_scope: str, profile: str, *, as_of: datetime, limit: int = 200) -> requests.Response:
        """读指定市场/配置/PIT 的最终推荐；返回 HTTP 响应，不创建或发布 route，网络异常透传。"""
        return self.client.request("GET", "/market-environment/recommendations", params={
            "market_scope": market_scope, "route_profile_key": profile, "as_of": as_of.isoformat(), "limit": limit,
        })
