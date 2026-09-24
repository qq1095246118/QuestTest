"""已部署的持仓查询接口，用于原始输入聚合验证。"""

from __future__ import annotations

from typing import Any

from api.client import HTTPClient


class SourceFixtureAPI:
    """调用真实持仓/摘要接口，不声明未部署的 fixture 或重算端点。"""

    def __init__(self, client: HTTPClient) -> None:
        """保存已鉴权客户端，不发送请求。"""
        self.client = client

    def positions(self, account_id: int, view_id: str) -> dict[str, Any]:
        """按自有输入快照查询账户全部持仓；返回 HTTP 状态与原始 JSON。"""
        response = self.client.request(
            "GET", "/quantitative-trading/trading/positions",
            params={"subject_type": "account", "subject_id": str(account_id),
                    "snapshot_id": view_id, "limit": 100},
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
            retryable=False, allow_redirects=False,
        )
        return {"http_status": response.status_code, "body": response.json()}

    def summary(self, account_id: int, view_id: str, *, metrics: str | None = None) -> dict[str, Any]:
        """查询同一原始输入快照的账户指标，供身份和来源追踪。"""
        response = self.client.request(
            "GET", "/quantitative-trading/analytics/summary",
            params={"subject_type": "account", "subject_id": str(account_id), "snapshot_id": view_id,
                    "scope": "lifetime", "metric_basis": "accrual",
                    "metrics": metrics or "margin_equity,wallet_balance,unrealized_pnl,position_ratio"},
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"}, retryable=False, allow_redirects=False,
        )
        return {"http_status": response.status_code, "body": response.json()}
