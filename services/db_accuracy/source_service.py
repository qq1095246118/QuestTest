"""Binance 源数据获取与字段映射服务。

本模块负责调用 Binance 行情接口、映射源字段，并提供市场生命周期查询。
"""

from __future__ import annotations

from typing import Any

from services.db_accuracy.models import MarketLifecycle, SourceRow, TableSpec, ValidationKey


PERPETUAL_DELIVERY_MS = 4_133_404_800_000


class BinanceSourceService:
    def __init__(self, usdm: Any = None, spot: Any = None, coinm: Any = None):
        self.usdm = usdm if usdm is not None else _default_usdm_client()
        self.spot = spot if spot is not None else _default_spot_client()
        self.coinm = coinm if coinm is not None else _default_coinm_client()
        self._exchange_info_cache: dict[str, dict[str, Any]] = {}

    def fetch_rows(
        self,
        spec: TableSpec,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        if spec.kind == "kline":
            return self._fetch_kline_rows(spec, key, start_ms, end_ms)
        if spec.kind == "funding":
            return self._fetch_funding_rows(spec, key, start_ms, end_ms)
        raise ValueError(f"fetch_rows does not support kind={spec.kind}")

    def fetch_registry_rows(self, spec: TableSpec) -> list[SourceRow]:
        if spec.endpoint != "usdm_exchange_info":
            raise ValueError(f"Unsupported registry endpoint: {spec.endpoint}")

        payload = _response_json(self.usdm.get_exchange_info())
        rows: list[SourceRow] = []
        for item in payload.get("symbols", []):
            fields = {
                "symbol": item.get("symbol"),
                "status": item.get("status"),
                "contract_type": item.get("contractType"),
                "quote_asset": item.get("quoteAsset"),
                "margin_asset": item.get("marginAsset"),
                "is_enabled": 1 if item.get("status") == "TRADING" else 0,
                "onboard_date_ms": item.get("onboardDate"),
            }
            rows.append(SourceRow(key=fields["symbol"], fields=fields))
        return rows

    def market_lifecycle(self, spec: TableSpec, key: ValidationKey) -> MarketLifecycle:
        market = _lifecycle_market_value(spec, key)
        if market is None:
            return MarketLifecycle(
                is_known=True,
                status="TRADING",
                onboard_ms=None,
                delivery_ms=None,
            )

        payload = self._exchange_info(spec.endpoint)
        symbols = payload.get("symbols", []) if isinstance(payload, dict) else []
        for item in symbols:
            if _matches_market(spec, key, item, market):
                return MarketLifecycle(
                    is_known=True,
                    status=_lifecycle_status(item),
                    onboard_ms=_optional_int(item.get("onboardDate")),
                    delivery_ms=_optional_delivery_ms(item.get("deliveryDate")),
                )

        return MarketLifecycle(
            is_known=False,
            status=None,
            onboard_ms=None,
            delivery_ms=None,
        )

    def _fetch_kline_rows(
        self,
        spec: TableSpec,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        interval = _interval_for(spec, key)
        params = {
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": spec.request_limit,
        }

        if spec.endpoint == "usdm_klines":
            response = self.usdm.get_klines(symbol=_key_value(spec, key, spec.symbol_field), **params)
        elif spec.endpoint == "spot_klines":
            response = self.spot.get_klines(symbol=_key_value(spec, key, spec.symbol_field), **params)
        elif spec.endpoint == "coinm_klines":
            response = self.coinm.get_klines(symbol=_key_value(spec, key, spec.symbol_field), **params)
        elif spec.endpoint == "coinm_continuous_klines":
            response = self.coinm.get_continuous_klines(
                pair=_key_value(spec, key, spec.pair_field or "pair"),
                contractType=_key_value(spec, key, spec.contract_type_field or "contract_type"),
                **params,
            )
        elif spec.endpoint == "usdm_continuous_klines":
            response = self.usdm.get_continuous_klines(
                pair=_key_value(spec, key, spec.pair_field or "pair"),
                contractType=_key_value(spec, key, spec.contract_type_field or "contract_type"),
                **params,
            )
        else:
            raise ValueError(f"Unsupported kline endpoint: {spec.endpoint}")

        rows: list[SourceRow] = []
        for raw in _response_json(response):
            fields = _coinm_kline_fields(raw) if spec.endpoint.startswith("coinm_") else _kline_fields(raw)
            row_key = fields.get(spec.source_time_field or "timestamp")
            rows.append(SourceRow(key=row_key, fields=fields))
        return rows

    def _exchange_info(self, endpoint: str) -> dict[str, Any]:
        cache_key = _exchange_info_cache_key(endpoint)
        if cache_key not in self._exchange_info_cache:
            if cache_key == "usdm":
                response = self.usdm.get_exchange_info()
            elif cache_key == "spot":
                response = self.spot.get_exchange_info()
            elif cache_key == "coinm":
                response = self.coinm.get_exchange_info()
            else:
                return {}
            self._exchange_info_cache[cache_key] = _response_json(response)
        return self._exchange_info_cache[cache_key]

    def _fetch_funding_rows(
        self,
        spec: TableSpec,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        params = {
            "symbol": _key_value(spec, key, spec.symbol_field),
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": spec.request_limit,
        }

        if spec.endpoint == "usdm_funding":
            response = self.usdm.get_funding_rate(**params)
        elif spec.endpoint == "coinm_funding":
            response = self.coinm.get_funding_rate(**params)
        else:
            raise ValueError(f"Unsupported funding endpoint: {spec.endpoint}")

        rows: list[SourceRow] = []
        for item in _response_json(response):
            fields = {
                "symbol": item.get("symbol"),
                "funding_rate": item.get("fundingRate"),
                "funding_time": item.get("fundingTime"),
                "mark_price": item.get("markPrice"),
            }
            row_key = fields.get(spec.source_time_field or "funding_time")
            rows.append(SourceRow(key=row_key, fields=fields))
        return rows


def _default_usdm_client() -> Any:
    from api.external.binance.usdm_market_api import USDMMarketAPI

    return USDMMarketAPI()


def _default_spot_client() -> Any:
    from api.external.binance.spot_market_api import SpotMarketAPI

    return SpotMarketAPI()


def _default_coinm_client() -> Any:
    from api.external.binance.coinm_market_api import COINMMarketAPI

    return COINMMarketAPI()


def _response_json(response: Any) -> Any:
    return response.json() if hasattr(response, "json") else response


def _interval_for(spec: TableSpec, key: ValidationKey) -> str:
    if spec.fixed_interval is not None:
        return spec.fixed_interval
    if spec.interval_field is None:
        raise ValueError(f"{spec.table} requires interval_field or fixed_interval")
    return _key_value(spec, key, spec.interval_field)


def _key_value(spec: TableSpec, key: ValidationKey, field: str | None) -> str:
    if field is None:
        raise ValueError(f"{spec.table} requires a configured key field")
    try:
        return str(key.values[field])
    except KeyError as exc:
        raise ValueError(f"{spec.table} validation key missing field: {field}") from exc


def _exchange_info_cache_key(endpoint: str) -> str | None:
    if endpoint.startswith("usdm_"):
        return "usdm"
    if endpoint.startswith("spot_"):
        return "spot"
    if endpoint.startswith("coinm_"):
        return "coinm"
    return None


def _lifecycle_market_value(spec: TableSpec, key: ValidationKey) -> str | None:
    if spec.endpoint in {"usdm_continuous_klines", "coinm_continuous_klines"}:
        return _key_value(spec, key, spec.pair_field or "pair")
    if spec.kind in {"kline", "funding"}:
        return _key_value(spec, key, spec.symbol_field)
    return None


def _matches_market(
    spec: TableSpec,
    key: ValidationKey,
    item: dict[str, Any],
    market: str,
) -> bool:
    if spec.endpoint in {"usdm_continuous_klines", "coinm_continuous_klines"}:
        contract_type = _key_value(
            spec,
            key,
            spec.contract_type_field or "contract_type",
        )
        return (
            _optional_text(item.get("pair")) == market
            and _optional_text(item.get("contractType")) == contract_type
        )
    return _optional_text(item.get("symbol")) == market


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _lifecycle_status(item: dict[str, Any]) -> str | None:
    return _optional_text(item.get("status") or item.get("contractStatus"))


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_delivery_ms(value: Any) -> int | None:
    delivery_ms = _optional_int(value)
    if delivery_ms in {None, 0, PERPETUAL_DELIVERY_MS}:
        return None
    return delivery_ms


def _kline_fields(raw: list[Any] | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "timestamp": raw[0],
        "open_time": raw[0],
        "open": raw[1],
        "high": raw[2],
        "low": raw[3],
        "close": raw[4],
        "volume": raw[5],
        "close_time": raw[6],
        "quote_volume": raw[7],
        "trade_count": raw[8],
        "trades": raw[8],
        "taker_buy_base_volume": raw[9],
        "taker_buy_quote_volume": raw[10],
    }


def _coinm_kline_fields(raw: list[Any] | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "timestamp": raw[0],
        "open_time": raw[0],
        "open": raw[1],
        "high": raw[2],
        "low": raw[3],
        "close": raw[4],
        "volume": raw[5],
        "close_time": raw[6],
        "base_asset_volume": raw[7],
        "trade_count": raw[8],
        "trades": raw[8],
        "taker_buy_volume": raw[9],
        "taker_buy_base_asset_volume": raw[10],
    }
