from __future__ import annotations

from typing import Any

from tests.db_accuracy.models import SourceRow, TableSpec, ValidationKey


class BinanceSource:
    def __init__(self, usdm: Any = None, spot: Any = None, coinm: Any = None):
        self.usdm = usdm if usdm is not None else _default_usdm_client()
        self.spot = spot if spot is not None else _default_spot_client()
        self.coinm = coinm if coinm is not None else _default_coinm_client()

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
    from api_services.binance.usdm_market_api import USDMMarketAPI

    return USDMMarketAPI()


def _default_spot_client() -> Any:
    from api_services.binance.spot_market_api import SpotMarketAPI

    return SpotMarketAPI()


def _default_coinm_client() -> Any:
    from api_services.binance.coinm_market_api import COINMMarketAPI

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
