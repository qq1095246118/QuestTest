"""Binance full 数据中台原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from infrastructure.http.http_client import HTTPClient


class BinanceFullAPI:
    def __init__(self):
        self.base_url = settings.base_url
        self.headers = {"Content-Type": "application/json"}

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=_clean(params))

    def post(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        data=None,
        params: dict[str, Any] | None = None,
    ):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request(
            "POST",
            url,
            headers=self.headers,
            json=json,
            data=data,
            params=_clean(params),
        )

    def get_meta_tables(self):
        return self.get("/api/binance-full/meta/tables")

    def get_usdm_registry_symbols(
        self,
        contract_type: Any = None,
        quote_asset: Any = None,
        status: Any = None,
    ):
        return self.get(
            "/api/binance-full/usdm/registry/symbols",
            {"contract_type": contract_type, "quote_asset": quote_asset, "status": status},
        )

    def get_usdm_complete_symbols(
        self,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        limit: Any = None,
        include_legacy_coinm_in_usdm_aggregate: Any = None,
    ):
        return self.get(
            "/api/binance-full/usdm/meta/complete-symbols",
            {
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
                "include_legacy_coinm_in_usdm_aggregate": include_legacy_coinm_in_usdm_aggregate,
            },
        )

    def get_usdm_delisted_symbols(
        self,
        status: Any = None,
        limit: Any = None,
        include_disabled_only: Any = None,
    ):
        return self.get(
            "/api/binance-full/usdm/meta/delisted-symbols",
            {
                "status": status,
                "limit": limit,
                "include_disabled_only": include_disabled_only,
            },
        )

    def get_usdm_time_range(self, symbol: Any = None, interval: Any = None):
        return self.get("/api/binance-full/usdm/meta/time-range", {"symbol": symbol, "interval": interval})

    def get_coinm_perp_time_range(
        self,
        pair: Any = None,
        contract_type: Any = None,
        interval: Any = None,
    ):
        return self.get(
            "/api/binance-full/coinm-perp/meta/time-range",
            {"pair": pair, "contract_type": contract_type, "interval": interval},
        )

    def get_coinm_delivery_time_range(
        self,
        pair: Any = None,
        contract_type: Any = None,
        interval: Any = None,
    ):
        return self.get(
            "/api/binance-full/coinm-delivery/meta/time-range",
            {"pair": pair, "contract_type": contract_type, "interval": interval},
        )

    def get_usdm_delivery_time_range(
        self,
        pair: Any = None,
        contract_type: Any = None,
        interval: Any = None,
    ):
        return self.get(
            "/api/binance-full/usdm-delivery/meta/time-range",
            {"pair": pair, "contract_type": contract_type, "interval": interval},
        )

    def get_usdm_kline(
        self,
        symbol: Any = None,
        interval: Any = None,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        limit: Any = None,
        offset: Any = None,
        include_total: Any = None,
    ):
        return self.get(
            "/api/binance-full/usdm/kline",
            {
                "symbol": symbol,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
                "offset": offset,
                "include_total": include_total,
            },
        )

    def get_usdm_kline_1h_all_symbols(
        self,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        symbol: Any = None,
        order: Any = None,
    ):
        return self.get(
            "/api/binance-full/usdm/kline-1h/all-symbols",
            {
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "symbol": symbol,
                "order": order,
            },
        )

    def get_coinm_perp_kline(
        self,
        pair: Any = None,
        contract_type: Any = None,
        interval: Any = None,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        limit: Any = None,
        offset: Any = None,
        include_total: Any = None,
    ):
        return self.get(
            "/api/binance-full/coinm-perp/kline",
            {
                "pair": pair,
                "contract_type": contract_type,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
                "offset": offset,
                "include_total": include_total,
            },
        )

    def get_coinm_delivery_kline(
        self,
        pair: Any = None,
        contract_type: Any = None,
        interval: Any = None,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        limit: Any = None,
        offset: Any = None,
        include_total: Any = None,
    ):
        return self.get(
            "/api/binance-full/coinm-delivery/kline",
            {
                "pair": pair,
                "contract_type": contract_type,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
                "offset": offset,
                "include_total": include_total,
            },
        )

    def get_usdm_delivery_kline(
        self,
        pair: Any = None,
        contract_type: Any = None,
        interval: Any = None,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        limit: Any = None,
        offset: Any = None,
        include_total: Any = None,
    ):
        return self.get(
            "/api/binance-full/usdm-delivery/kline",
            {
                "pair": pair,
                "contract_type": contract_type,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
                "offset": offset,
                "include_total": include_total,
            },
        )

    def get_usdm_funding(
        self,
        symbol: Any = None,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        limit: Any = None,
        include_legacy_coinm_in_usdm_aggregate: Any = None,
    ):
        return self.get(
            "/api/binance-full/usdm/funding",
            {
                "symbol": symbol,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
                "include_legacy_coinm_in_usdm_aggregate": include_legacy_coinm_in_usdm_aggregate,
            },
        )

    def get_coinm_perp_funding(
        self,
        pair: Any = None,
        contract_type: Any = None,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/api/binance-full/coinm-perp/funding",
            {
                "pair": pair,
                "contract_type": contract_type,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
            },
        )

    def post_time_bounds(
        self,
        endpoint: str,
        symbols: Any = None,
        interval: Any = None,
        contract_type: Any = None,
    ):
        return self.post(
            endpoint,
            json=_clean({"symbols": symbols, "interval": interval}),
            params={"contract_type": contract_type},
        )

    def batch_usdm_kline_time_bounds(
        self,
        symbols: Any = None,
        interval: Any = None,
        contract_type: Any = None,
    ):
        return self.post_time_bounds(
            "/api/binance-full/usdm/meta/kline-time-bounds",
            symbols=symbols,
            interval=interval,
            contract_type=contract_type,
        )

    def batch_coinm_perp_kline_time_bounds(
        self,
        symbols: Any = None,
        interval: Any = None,
        contract_type: Any = None,
    ):
        return self.post_time_bounds(
            "/api/binance-full/coinm-perp/meta/kline-time-bounds",
            symbols=symbols,
            interval=interval,
            contract_type=contract_type,
        )

    def batch_coinm_delivery_kline_time_bounds(
        self,
        symbols: Any = None,
        interval: Any = None,
        contract_type: Any = None,
    ):
        return self.post_time_bounds(
            "/api/binance-full/coinm-delivery/meta/kline-time-bounds",
            symbols=symbols,
            interval=interval,
            contract_type=contract_type,
        )

    def batch_usdm_delivery_kline_time_bounds(
        self,
        symbols: Any = None,
        interval: Any = None,
        contract_type: Any = None,
    ):
        return self.post_time_bounds(
            "/api/binance-full/usdm-delivery/meta/kline-time-bounds",
            symbols=symbols,
            interval=interval,
            contract_type=contract_type,
        )

    def batch_usdm_funding_time_bounds(
        self,
        symbols: Any = None,
        contract_type: Any = None,
    ):
        return self.post_time_bounds(
            "/api/binance-full/usdm/meta/funding-time-bounds",
            symbols=symbols,
            interval=None,
            contract_type=contract_type,
        )

    def batch_coinm_perp_funding_time_bounds(
        self,
        symbols: Any = None,
        contract_type: Any = None,
    ):
        return self.post_time_bounds(
            "/api/binance-full/coinm-perp/meta/funding-time-bounds",
            symbols=symbols,
            interval=None,
            contract_type=contract_type,
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None}
