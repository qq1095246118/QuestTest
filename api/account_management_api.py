"""账号管理总览的协议层封装。"""

from __future__ import annotations

from typing import Any

from api.client import HTTPClient


ACCOUNT_MANAGEMENT_METRICS = (
    "margin_equity,unrealized_pnl,realized_pnl_gross,cumulative_return,"
    "annualized_return,current_drawdown,commission_expense,funding_pnl,position_ratio,"
    "sharpe_ratio,sortino_ratio,calmar_ratio,max_drawdown,volatility,win_rate,profit_loss_ratio"
)


class AccountManagementAPI:
    """只封装账号目录和全账号摘要 GET 请求。"""

    def __init__(self, client: HTTPClient) -> None:
        """保存已鉴权 HTTP 客户端；构造过程不发送请求。"""

        self.client = client

    def accounts_page(
        self,
        *,
        page: int = 1,
        snapshot_id: str | None = None,
        limit: int = 100,
        runtime_status: str | None = None,
        account_ids: list[str] | None = None,
        exchange: str | None = None,
    ) -> dict[str, Any]:
        """读取一页账号目录并保留请求参数。"""

        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("page must be a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        params: dict[str, Any] = {
            "population": "all",
            "scope": "lifetime",
            "metric_basis": "accrual",
            "metrics": ACCOUNT_MANAGEMENT_METRICS,
            "pagination": "page",
            "page": page,
            "limit": limit,
        }
        if snapshot_id is not None:
            params["snapshot_id"] = snapshot_id
        if runtime_status is not None:
            params["runtime_status"] = runtime_status
        if account_ids is not None:
            params["account_ids"] = ",".join(account_ids)
        if exchange is not None:
            params["exchange"] = exchange
        return self._get("/admin/quant-accounts", params)

    def summary(
        self,
        *,
        from_at: str | None = None,
        to_at: str | None = None,
        account_ids: list[str] | None = None,
        snapshot_id: str | None = None,
        metrics: list[str] | None = None,
        metric_source: str | None = None,
    ) -> dict[str, Any]:
        """读取账号集合 portfolio 摘要；不在 API 层计算或断言。"""

        params: dict[str, Any] = {
            "subject_type": "portfolio",
            "scope": "lifetime",
            "metric_basis": "accrual",
            "population": "all",
            "metrics": ACCOUNT_MANAGEMENT_METRICS if metrics is None else ",".join(metrics),
        }
        if from_at is not None:
            params["from"] = from_at
        if to_at is not None:
            params["to"] = to_at
        if account_ids is not None:
            params["account_ids"] = ",".join(str(value) for value in account_ids)
        if snapshot_id is not None:
            params["snapshot_id"] = snapshot_id
        if metric_source is not None:
            params["metric_source"] = metric_source
        return self._get("/quantitative-trading/analytics/summary", params)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """发送不可重试、禁止跳转的 GET，并返回原始 JSON。"""

        response = self.client.request(
            "GET",
            path,
            params=dict(params),
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache, no-store"},
            # GET is idempotent; allow the shared client to retry transient TLS,
            # gateway and 5xx failures without changing the request semantics.
            retryable=None,
            allow_redirects=False,
        )
        return {
            "http_status": response.status_code,
            "body": response.json(),
            "request_params": dict(params),
            "path": path,
        }
