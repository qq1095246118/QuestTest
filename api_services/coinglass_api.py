from __future__ import annotations

from typing import Any

from config.settings import settings
from infrastructure.http.http_client import HTTPClient


class CoinGlassAPI:
    def __init__(self):
        self.base_url = settings.base_url
        self.headers = {"Content-Type": "application/json"}

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=_clean(params))

    def get_funding_rate_ohlc_history(
        self,
        symbol: Any = None,
        interval: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/funding-rate/ohlc-history",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    def get_funding_rate_exchange_list(self, symbol: Any = None, limit: Any = None):
        return self.get("/coinglass/funding-rate/exchange-list", {"symbol": symbol, "limit": limit})

    def get_funding_rate_arbitrage(self, symbol: Any = None, limit: Any = None):
        return self.get("/coinglass/funding-rate/arbitrage", {"symbol": symbol, "limit": limit})

    def get_funding_rate_summary(self, symbol: Any = None, limit: Any = None):
        return self.get("/coinglass/funding-rate/summary", {"symbol": symbol, "limit": limit})

    def get_long_short_ratio_history(
        self,
        exchange: Any = None,
        symbol: Any = None,
        interval: Any = None,
        start_time: Any = None,
        end_time: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/long-short-ratio/history",
            {
                "exchange": exchange,
                "symbol": symbol,
                "interval": interval,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
            },
        )

    def get_controlled_coin_summary(
        self,
        symbol: Any = None,
        exchange: Any = None,
        interval: Any = None,
    ):
        return self.get(
            "/coinglass/controlled_coin_summary",
            {"symbol": symbol, "exchange": exchange, "interval": interval},
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None}
