from __future__ import annotations

"""Binance USDM 数据中台原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from typing import Any

from config.settings import settings
from infrastructure.http.http_client import HTTPClient


class BinanceUSDMAPI:
    def __init__(self):
        self.base_url = settings.base_url
        self.headers = {"Content-Type": "application/json"}

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=_clean(params))

    def get_volume_rank(
        self,
        range_unit: Any = "hours",
        n: Any = 24,
        top_k: Any = 10,
        use_quote_volume: Any = True,
        m_days: Any = 7,
        include_ticker_24h: Any = True,
    ):
        return self.get(
            "/api/usdm/volume-rank",
            {
                "range_unit": range_unit,
                "n": n,
                "top_k": top_k,
                "use_quote_volume": use_quote_volume,
                "m_days": m_days,
                "include_ticker_24h": include_ticker_24h,
            },
        )

    def get_top_gainers(
        self,
        change_threshold: Any = 5,
        days_history: Any = 10,
        limit: Any = 10,
    ):
        return self.get(
            "/api/usdm/top-gainers",
            {
                "change_threshold": change_threshold,
                "days_history": days_history,
                "limit": limit,
            },
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None}
