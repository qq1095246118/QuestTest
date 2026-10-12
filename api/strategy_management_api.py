"""策略管理页面的真实只读接口；保留进入详情时的交易所参数。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from api.client import HTTPClient


STRATEGY_PAGE_METRICS = (
    "margin_equity,wallet_balance,unrealized_pnl,realized_pnl_gross,cumulative_return,"
    "annualized_return,current_drawdown,commission_expense,funding_pnl,position_utilization,"
    "sharpe_ratio,sortino_ratio,calmar_ratio,max_drawdown,volatility,win_rate,profit_loss_ratio"
)


class StrategyManagementAPI:
    """只发送新请求，不重试、不回放本地结果、不写入业务数据。"""

    def __init__(self, client: HTTPClient) -> None:
        """接收已登录客户端；构造不产生请求。"""
        self.client = client

    def catalog(self, **params: Any) -> dict[str, Any]:
        """读取一页策略目录，筛选和快照参数原样发送。"""
        return self._get("/quantitative-trading/strategies", params)

    def detail(self, definition_id: str, **params: Any) -> dict[str, Any]:
        """读取入口策略详情，不在测试侧删除 strategy_exchange。"""
        return self._get("/quantitative-trading/strategy-definitions/" + quote(definition_id, safe=""), params)

    def accounts(self, definition_id: str, **params: Any) -> dict[str, Any]:
        """读取关联账号；交易所入口不能改变同名策略总览契约。"""
        return self._get("/quantitative-trading/strategy-definitions/" + quote(definition_id, safe="") + "/accounts", params)

    def analytics(self, endpoint: str, **params: Any) -> dict[str, Any]:
        """读取摘要、风险或曲线，包含独立原始输入视图的 snapshot_id。"""
        if endpoint not in {"summary", "risk", "series"}:
            raise ValueError("Unsupported strategy analytics endpoint")
        return self._get("/quantitative-trading/analytics/" + endpoint, params)

    def trading(self, endpoint: str, **params: Any) -> dict[str, Any]:
        """读取持仓、信号、调仓、委托或成交，保留分页契约。"""
        if endpoint not in {"positions", "signals", "signal-batches", "rebalance-plans", "order-intents", "orders", "fills"}:
            raise ValueError("Unsupported strategy trading endpoint")
        return self._get("/quantitative-trading/trading/" + endpoint, params)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """返回原始状态、响应及非敏感请求参数；错误原样抛出。"""
        response = self.client.request("GET", path, params=dict(params), retryable=False,
            allow_redirects=False, headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache, no-store"})
        return {"http_status": response.status_code, "body": response.json(), "path": path, "request_params": dict(params)}
