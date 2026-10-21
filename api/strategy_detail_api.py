"""策略详情数据接口的协议层封装。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from api.client import HTTPClient


class StrategyDetailAPI:
    """只封装策略详情数据端点，不在 API 层计算结果或执行写操作。"""

    def __init__(self, client: HTTPClient) -> None:
        """保存已鉴权 HTTP 客户端；构造不产生网络请求。"""

        self.client = client

    def definition(self, strategy_id: str, **params: Any) -> dict[str, Any]:
        """读取策略定义和详情基础数据。"""

        return self._get(
            "/quantitative-trading/strategy-definitions/" + quote(str(strategy_id), safe=""),
            params,
        )

    def accounts(self, strategy_id: str, **params: Any) -> dict[str, Any]:
        """读取策略关联账号和归属区间。"""

        return self._get(
            "/quantitative-trading/strategy-definitions/"
            + quote(str(strategy_id), safe="")
            + "/accounts",
            params,
        )

    def analytics(self, resource: str, **params: Any) -> dict[str, Any]:
        """读取策略摘要、风险或曲线原始响应。"""

        if resource not in {"summary", "risk", "series"}:
            raise ValueError("unsupported strategy analytics resource")
        return self._get("/quantitative-trading/analytics/" + resource, params)

    def trading(self, resource: str, **params: Any) -> dict[str, Any]:
        """读取策略持仓、信号、调仓计划、委托或成交。"""

        allowed = {
            "positions",
            "signals",
            "signal-batches",
            "rebalance-plans",
            "order-intents",
            "orders",
            "fills",
        }
        if resource not in allowed:
            raise ValueError("unsupported strategy trading resource")
        return self._get("/quantitative-trading/trading/" + resource, params)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """发送不可重试、禁止跳转的 GET 并返回原始 JSON。"""

        response = self.client.request(
            "GET",
            path,
            params=dict(params),
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache, no-store"},
            # Strategy detail resources are read-only GETs; retry only the
            # shared client's transient transport/status failures.
            retryable=None,
            allow_redirects=False,
        )
        return {
            "http_status": response.status_code,
            "body": response.json(),
            "path": path,
            "request_params": dict(params),
        }
