from __future__ import annotations

from typing import Any

from config.settings import settings
from core.http_client import HTTPClient


class KlineDataAPI:
    """Kline routes from docs/x.json."""

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

    def fetch_kline(
        self,
        symbol: Any = None,
        interval: Any = "1m",
        source: Any = "binance",
    ):
        return self.get("/kline/fetch", {"symbol": symbol, "interval": interval, "source": source})

    def get_usdm_time_range(self, symbol: Any = None, interval: Any = "1m"):
        return self.get("/kline/usdm/meta/time-range", {"symbol": symbol, "interval": interval})

    def get_usdm_kline_raw(
        self,
        symbol: Any = None,
        interval: Any = "1m",
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        limit: Any = 500,
        offset: Any = 0,
        include_total: Any = False,
    ):
        return self.get(
            "/kline/usdm/kline-raw",
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

    def get_usdm_kline(
        self,
        symbol: Any = None,
        interval: Any = "1m",
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        quality_flag: Any = None,
        limit: Any = 500,
        offset: Any = 0,
        include_total: Any = False,
    ):
        return self.get(
            "/kline/usdm/kline",
            {
                "symbol": symbol,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "quality_flag": quality_flag,
                "limit": limit,
                "offset": offset,
                "include_total": include_total,
            },
        )

    def get_spot_time_range(self, symbol: Any = None, interval: Any = "1m"):
        return self.get("/kline/spot/meta/time-range", {"symbol": symbol, "interval": interval})

    def get_spot_kline_raw(
        self,
        symbol: Any = None,
        interval: Any = "1m",
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        limit: Any = 500,
        offset: Any = 0,
        include_total: Any = False,
    ):
        return self.get(
            "/kline/spot/kline-raw",
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

    def get_spot_kline(
        self,
        symbol: Any = None,
        interval: Any = "1m",
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        quality_flag: Any = None,
        limit: Any = 500,
        offset: Any = 0,
        include_total: Any = False,
    ):
        return self.get(
            "/kline/spot/kline",
            {
                "symbol": symbol,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "quality_flag": quality_flag,
                "limit": limit,
                "offset": offset,
                "include_total": include_total,
            },
        )

    def get_binance_full_usdm_kline(
        self,
        symbol: Any = None,
        interval: Any = "1m",
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        contract_type: Any = None,
        quote_asset: Any = None,
        margin_asset: Any = None,
        include_legacy_coinm_in_usdm_aggregate: Any = False,
        limit: Any = 500,
        offset: Any = 0,
        include_total: Any = False,
    ):
        return self.get(
            "/api/binance-full/usdm/kline",
            {
                "symbol": symbol,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "contract_type": contract_type,
                "quote_asset": quote_asset,
                "margin_asset": margin_asset,
                "include_legacy_coinm_in_usdm_aggregate": include_legacy_coinm_in_usdm_aggregate,
                "limit": limit,
                "offset": offset,
                "include_total": include_total,
            },
        )

    def get_binance_full_usdm_kline_1h_all_symbols(
        self,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        symbol: Any = None,
        contract_type: Any = None,
        quote_asset: Any = None,
        margin_asset: Any = None,
        include_legacy_coinm_in_usdm_aggregate: Any = False,
        order: Any = "time_asc",
    ):
        return self.get(
            "/api/binance-full/usdm/kline-1h/all-symbols",
            {
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "symbol": symbol,
                "contract_type": contract_type,
                "quote_asset": quote_asset,
                "margin_asset": margin_asset,
                "include_legacy_coinm_in_usdm_aggregate": include_legacy_coinm_in_usdm_aggregate,
                "order": order,
            },
        )

    def get_coinm_perp_kline(
        self,
        pair: Any = None,
        contract_type: Any = "PERPETUAL",
        interval: Any = "1m",
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        quote_asset: Any = None,
        margin_asset: Any = None,
        limit: Any = 500,
        offset: Any = 0,
        include_total: Any = False,
    ):
        return self.get_coinm_like_kline(
            "/api/binance-full/coinm-perp/kline",
            pair,
            contract_type,
            interval,
            start_time_ms,
            end_time_ms,
            quote_asset,
            margin_asset,
            limit,
            offset,
            include_total,
        )

    def get_coinm_delivery_kline(
        self,
        pair: Any = None,
        contract_type: Any = "CURRENT_QUARTER",
        interval: Any = "1m",
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        quote_asset: Any = None,
        margin_asset: Any = None,
        limit: Any = 500,
        offset: Any = 0,
        include_total: Any = False,
    ):
        return self.get_coinm_like_kline(
            "/api/binance-full/coinm-delivery/kline",
            pair,
            contract_type,
            interval,
            start_time_ms,
            end_time_ms,
            quote_asset,
            margin_asset,
            limit,
            offset,
            include_total,
        )

    def get_usdm_delivery_kline(
        self,
        pair: Any = None,
        contract_type: Any = "CURRENT_QUARTER",
        interval: Any = "1m",
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        quote_asset: Any = None,
        margin_asset: Any = None,
        limit: Any = 500,
        offset: Any = 0,
        include_total: Any = False,
    ):
        return self.get_coinm_like_kline(
            "/api/binance-full/usdm-delivery/kline",
            pair,
            contract_type,
            interval,
            start_time_ms,
            end_time_ms,
            quote_asset,
            margin_asset,
            limit,
            offset,
            include_total,
        )

    def batch_usdm_kline_time_bounds(
        self,
        symbols: Any = None,
        interval: Any = "1m",
        contract_type: Any = None,
        quote_asset: Any = None,
        margin_asset: Any = None,
    ):
        return self.post_batch_time_bounds(
            "/api/binance-full/usdm/meta/kline-time-bounds",
            symbols,
            interval,
            contract_type,
            quote_asset,
            margin_asset,
        )

    def batch_coinm_perp_kline_time_bounds(
        self,
        symbols: Any = None,
        interval: Any = "1m",
        contract_type: Any = "PERPETUAL",
        quote_asset: Any = None,
        margin_asset: Any = None,
    ):
        return self.post_batch_time_bounds(
            "/api/binance-full/coinm-perp/meta/kline-time-bounds",
            symbols,
            interval,
            contract_type,
            quote_asset,
            margin_asset,
        )

    def batch_coinm_delivery_kline_time_bounds(
        self,
        symbols: Any = None,
        interval: Any = "1m",
        contract_type: Any = "CURRENT_QUARTER",
        quote_asset: Any = None,
        margin_asset: Any = None,
    ):
        return self.post_batch_time_bounds(
            "/api/binance-full/coinm-delivery/meta/kline-time-bounds",
            symbols,
            interval,
            contract_type,
            quote_asset,
            margin_asset,
        )

    def batch_usdm_delivery_kline_time_bounds(
        self,
        symbols: Any = None,
        interval: Any = "1m",
        contract_type: Any = "CURRENT_QUARTER",
        quote_asset: Any = None,
        margin_asset: Any = None,
    ):
        return self.post_batch_time_bounds(
            "/api/binance-full/usdm-delivery/meta/kline-time-bounds",
            symbols,
            interval,
            contract_type,
            quote_asset,
            margin_asset,
        )

    def get_coinm_like_kline(
        self,
        endpoint: str,
        pair: Any,
        contract_type: Any,
        interval: Any,
        start_time_ms: Any,
        end_time_ms: Any,
        quote_asset: Any,
        margin_asset: Any,
        limit: Any,
        offset: Any,
        include_total: Any,
    ):
        return self.get(
            endpoint,
            {
                "pair": pair,
                "contract_type": contract_type,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "quote_asset": quote_asset,
                "margin_asset": margin_asset,
                "limit": limit,
                "offset": offset,
                "include_total": include_total,
            },
        )

    def post_batch_time_bounds(
        self,
        endpoint: str,
        symbols: Any,
        interval: Any,
        contract_type: Any,
        quote_asset: Any,
        margin_asset: Any,
    ):
        body: dict[str, Any] = {}
        if symbols is not None:
            body["symbols"] = symbols
        if interval is not None:
            body["interval"] = interval
        return self.post(
            endpoint,
            json=body,
            data=None,
            params={
                "contract_type": contract_type,
                "quote_asset": quote_asset,
                "margin_asset": margin_asset,
            },
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None}
