"""真实 portfolio 摘要查询；原始多账户 fixture 不预计算聚合结果。"""

from __future__ import annotations

from typing import Any

from api.client import HTTPClient


class PortfolioFixtureAPI:
    """只封装已部署的 portfolio summary 端点。"""

    def __init__(self, client: HTTPClient) -> None:
        """保存已鉴权测试客户端，不发送请求。"""
        self.client = client

    def summary(self, view_id: str, *, account_ids: list[int] | None = None,
                metric_source: str | None = None, from_at: str | None = None,
                to_at: str | None = None) -> dict[str, Any]:
        """查询确切输入视图；传输错误原样抛出，返回原始状态和 JSON。"""
        params: dict[str, Any] = {
            "subject_type": "portfolio", "snapshot_id": view_id,
            "scope": "lifetime", "metric_basis": "accrual", "population": "all",
            "metrics": "margin_equity,wallet_balance,unrealized_pnl,realized_pnl_gross,net_deposit,"
                       "commission_expense,funding_pnl,cumulative_return,position_ratio",
        }
        if account_ids is not None:
            params["account_ids"] = ",".join(str(value) for value in account_ids)
        if metric_source is not None:
            params["metric_source"] = metric_source
        if from_at is not None:
            params["from"] = from_at
        if to_at is not None:
            params["to"] = to_at
        response = self.client.request(
            "GET", "/quantitative-trading/analytics/summary", params=params,
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
            retryable=False, allow_redirects=False,
        )
        return {"http_status": response.status_code, "body": response.json(), "request_params": params}
