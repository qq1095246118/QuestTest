from __future__ import annotations

"""Open Interest 数据中台原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from typing import Any

from config.settings import settings
from infrastructure.http.http_client import HTTPClient


class OpenInterestAPI:
    def __init__(self):
        self.base_url = settings.base_url
        self.headers = {"Content-Type": "application/json"}

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=_clean(params))

    def get_history(
        self,
        exchange: Any = None,
        symbol: Any = None,
        interval: Any = None,
        start_time: Any = None,
        end_time: Any = None,
        unit: Any = None,
        force_refresh: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/oi/history",
            {
                "exchange": exchange,
                "symbol": symbol,
                "interval": interval,
                "start_time": start_time,
                "end_time": end_time,
                "unit": unit,
                "force_refresh": force_refresh,
                "limit": limit,
            },
        )

    def get_aggregated_history(
        self,
        symbol: Any = None,
        interval: Any = None,
        start_time: Any = None,
        end_time: Any = None,
        unit: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/oi/aggregated/history",
            {
                "symbol": symbol,
                "interval": interval,
                "start_time": start_time,
                "end_time": end_time,
                "unit": unit,
                "limit": limit,
            },
        )

    def get_exchanges(
        self,
        symbol: Any = None,
        interval: Any = None,
        unit: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/oi/exchanges",
            {"symbol": symbol, "interval": interval, "unit": unit, "limit": limit},
        )

    def get_orderbook_aggregated_history(
        self,
        exchange_list: Any = None,
        symbol: Any = None,
        interval: Any = None,
        range: Any = None,
        start_time: Any = None,
        end_time: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/oi/orderbook/aggregated-history",
            {
                "exchange_list": exchange_list,
                "symbol": symbol,
                "interval": interval,
                "range": range,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
            },
        )

    def get_summary(
        self,
        symbol: Any = None,
        exchange: Any = None,
        interval: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/oi/summary",
            {"symbol": symbol, "exchange": exchange, "interval": interval, "limit": limit},
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None}
