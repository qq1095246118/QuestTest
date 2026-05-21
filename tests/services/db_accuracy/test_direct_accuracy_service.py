from __future__ import annotations

from decimal import Decimal

from services.db_accuracy.source_service import BinanceSourceService
from services.db_accuracy.models import MarketLifecycle, SourceRow, TableSpec, ValidationKey
from services.db_accuracy.direct.accuracy_service import DirectAccuracyService, compare_db_and_source_rows, compare_registry_rows


KLINE_PAYLOAD = [
    [
        1704067200000,
        "1",
        "2",
        "0.5",
        "1.5",
        "10",
        1704070799999,
        "15",
        20,
        "6",
        "9",
        "0",
    ]
]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeUSDM:
    def __init__(self):
        self.calls = []

    def get_klines(self, **kwargs):
        self.calls.append(("get_klines", kwargs))
        return FakeResponse(KLINE_PAYLOAD)

    def get_continuous_klines(self, **kwargs):
        self.calls.append(("get_continuous_klines", kwargs))
        return FakeResponse(KLINE_PAYLOAD)

    def get_funding_rate(self, **kwargs):
        self.calls.append(("get_funding_rate", kwargs))
        return FakeResponse(
            [
                {
                    "symbol": "BTCUSDT",
                    "fundingRate": "0.01",
                    "fundingTime": 1704067200000,
                    "markPrice": "42000",
                }
            ]
        )

    def get_exchange_info(self):
        self.calls.append(("get_exchange_info", {}))
        return FakeResponse(
            {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "TRADING",
                        "contractType": "PERPETUAL",
                        "quoteAsset": "USDT",
                        "marginAsset": "USDT",
                        "onboardDate": 1577836800000,
                    }
                ]
            }
        )


class FakeSpot:
    def __init__(self):
        self.calls = []

    def get_klines(self, **kwargs):
        self.calls.append(("get_klines", kwargs))
        return FakeResponse(KLINE_PAYLOAD)

    def get_exchange_info(self):
        self.calls.append(("get_exchange_info", {}))
        return FakeResponse(
            {
                "symbols": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "TRADING",
                    }
                ]
            }
        )


class FakeCoinM:
    def __init__(self):
        self.calls = []

    def get_klines(self, **kwargs):
        self.calls.append(("get_klines", kwargs))
        return FakeResponse(KLINE_PAYLOAD)

    def get_continuous_klines(self, **kwargs):
        self.calls.append(("get_continuous_klines", kwargs))
        return FakeResponse(KLINE_PAYLOAD)

    def get_funding_rate(self, **kwargs):
        self.calls.append(("get_funding_rate", kwargs))
        return FakeResponse(
            [
                {
                    "symbol": "BTCUSD_PERP",
                    "fundingRate": "0.01",
                    "fundingTime": 1704067200000,
                }
            ]
        )

    def get_exchange_info(self):
        self.calls.append(("get_exchange_info", {}))
        return FakeResponse(
            {
                "symbols": [
                    {
                        "symbol": "BTCUSD_240329",
                        "pair": "BTCUSD",
                        "contractType": "CURRENT_QUARTER",
                        "contractStatus": "TRADING",
                        "onboardDate": 1704067200000,
                        "deliveryDate": 1711670400000,
                    },
                    {
                        "symbol": "BTCUSD_240628",
                        "pair": "BTCUSD",
                        "contractType": "NEXT_QUARTER",
                        "contractStatus": "TRADING",
                        "onboardDate": 1704067200000,
                        "deliveryDate": 1719532800000,
                    },
                ]
            }
        )


def _source():
    usdm = FakeUSDM()
    spot = FakeSpot()
    coinm = FakeCoinM()
    return BinanceSourceService(usdm=usdm, spot=spot, coinm=coinm), usdm, spot, coinm


def test_source_maps_usdm_kline_array_to_named_fields():
    source, usdm, _, _ = _source()
    spec = TableSpec(
        table="kline_data_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp", "open_time"),
        interval_field="interval",
        compare_fields=(
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        ),
        optional_compare_fields=("open_time", "trade_count", "trades"),
        request_limit=1000,
    )

    rows = source.fetch_rows(
        spec,
        ValidationKey({"symbol": "BTCUSDT", "interval": "1h"}),
        1704067200000,
        1704070800000,
    )

    assert rows[0].key == 1704067200000
    assert rows[0].fields == {
        "timestamp": 1704067200000,
        "open_time": 1704067200000,
        "open": "1",
        "high": "2",
        "low": "0.5",
        "close": "1.5",
        "volume": "10",
        "close_time": 1704070799999,
        "quote_volume": "15",
        "trade_count": 20,
        "trades": 20,
        "taker_buy_base_volume": "6",
        "taker_buy_quote_volume": "9",
    }
    assert usdm.calls == [
        (
            "get_klines",
            {
                "symbol": "BTCUSDT",
                "interval": "1h",
                "startTime": 1704067200000,
                "endTime": 1704070800000,
                "limit": 1000,
            },
        )
    ]


def test_source_routes_each_kline_endpoint_to_expected_client_and_key_fields():
    source, usdm, spot, coinm = _source()
    cases = [
        (
            "spot_klines",
            ValidationKey({"symbol": "ETHUSDT", "interval": "15m"}),
            spot,
            "get_klines",
            {"symbol": "ETHUSDT"},
        ),
        (
            "coinm_klines",
            ValidationKey({"symbol": "BTCUSD_PERP", "interval": "1h"}),
            coinm,
            "get_klines",
            {"symbol": "BTCUSD_PERP"},
        ),
        (
            "coinm_continuous_klines",
            ValidationKey({"pair": "BTCUSD", "contract_type": "CURRENT_QUARTER", "interval": "4h"}),
            coinm,
            "get_continuous_klines",
            {"pair": "BTCUSD", "contractType": "CURRENT_QUARTER"},
        ),
        (
            "usdm_continuous_klines",
            ValidationKey({"pair": "BTCUSDT", "contract_type": "PERPETUAL", "interval": "1d"}),
            usdm,
            "get_continuous_klines",
            {"pair": "BTCUSDT", "contractType": "PERPETUAL"},
        ),
    ]

    for endpoint, key, client, method, expected_params in cases:
        spec = TableSpec(
            table=f"{endpoint}_table",
            kind="kline",
            endpoint=endpoint,
            key_fields=tuple(key.values),
            time_fields=("timestamp",),
            interval_field="interval",
            compare_fields=("timestamp", "open", "close"),
            request_limit=500,
        )

        rows = source.fetch_rows(spec, key, 1704067200000, 1704070800000)

        call_method, call_kwargs = client.calls[-1]
        assert rows[0].key == 1704067200000
        assert call_method == method
        assert call_kwargs == {
            **expected_params,
            "interval": key.values["interval"],
            "startTime": 1704067200000,
            "endTime": 1704070800000,
            "limit": 500,
        }


def test_source_maps_usdm_funding_dict_to_named_fields():
    source, usdm, _, _ = _source()
    spec = TableSpec(
        table="binance_funding_rate_all_future_raw",
        kind="funding",
        endpoint="usdm_funding",
        key_fields=("symbol",),
        time_fields=("funding_time",),
        interval_field=None,
        compare_fields=("symbol", "funding_rate", "funding_time", "mark_price"),
        request_limit=1000,
    )

    rows = source.fetch_rows(spec, ValidationKey({"symbol": "BTCUSDT"}), 1704067200000, 1704070800000)

    assert rows[0].key == 1704067200000
    assert rows[0].fields == {
        "symbol": "BTCUSDT",
        "funding_rate": "0.01",
        "funding_time": 1704067200000,
        "mark_price": "42000",
    }
    assert usdm.calls == [
        (
            "get_funding_rate",
            {
                "symbol": "BTCUSDT",
                "startTime": 1704067200000,
                "endTime": 1704070800000,
                "limit": 1000,
            },
        )
    ]


def test_source_maps_coinm_funding_without_mark_price_to_none():
    source, _, _, coinm = _source()
    spec = TableSpec(
        table="binance_funding_rate_coinm_perp_raw",
        kind="funding",
        endpoint="coinm_funding",
        key_fields=("symbol",),
        time_fields=("funding_time",),
        interval_field=None,
        compare_fields=("symbol", "funding_rate", "funding_time"),
        request_limit=1000,
    )

    rows = source.fetch_rows(spec, ValidationKey({"symbol": "BTCUSD_PERP"}), 1704067200000, 1704070800000)

    assert rows[0].key == 1704067200000
    assert rows[0].fields["symbol"] == "BTCUSD_PERP"
    assert rows[0].fields["funding_rate"] == "0.01"
    assert rows[0].fields["funding_time"] == 1704067200000
    assert rows[0].fields["mark_price"] is None
    assert coinm.calls == [
        (
            "get_funding_rate",
            {
                "symbol": "BTCUSD_PERP",
                "startTime": 1704067200000,
                "endTime": 1704070800000,
                "limit": 1000,
            },
        )
    ]


def test_source_maps_registry_rows():
    source, usdm, _, _ = _source()
    spec = TableSpec(
        table="binance_futures_symbols",
        kind="registry",
        endpoint="usdm_exchange_info",
        key_fields=("symbol",),
        time_fields=(),
        interval_field=None,
        compare_fields=("symbol", "status", "contract_type", "quote_asset", "margin_asset", "onboard_date_ms"),
        request_limit=1000,
    )

    rows = source.fetch_registry_rows(spec)

    assert rows[0].key == "BTCUSDT"
    assert rows[0].fields == {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "contract_type": "PERPETUAL",
        "quote_asset": "USDT",
        "margin_asset": "USDT",
        "is_enabled": 1,
        "onboard_date_ms": 1577836800000,
    }
    assert usdm.calls == [("get_exchange_info", {})]


def test_source_maps_registry_enabled_status():
    source, _, _, _ = _source()
    spec = TableSpec(
        table="binance_futures_symbols",
        kind="registry",
        endpoint="usdm_exchange_info",
        key_fields=("symbol",),
        time_fields=(),
        interval_field=None,
        compare_fields=("symbol", "status", "is_enabled"),
        request_limit=1000,
    )

    rows = source.fetch_registry_rows(spec)

    assert rows[0].fields["is_enabled"] == 1


def test_source_returns_usdm_market_lifecycle_from_exchange_info():
    source, usdm, _, _ = _source()
    spec = TableSpec(
        table="binance_1h_usdm_kline_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol",),
        time_fields=("timestamp",),
        interval_field=None,
        compare_fields=("timestamp", "open"),
        request_limit=1000,
        fixed_interval="1h",
    )

    lifecycle = source.market_lifecycle(spec, ValidationKey({"symbol": "BTCUSDT"}))

    assert lifecycle == MarketLifecycle(
        is_known=True,
        status="TRADING",
        onboard_ms=1577836800000,
        delivery_ms=None,
    )
    assert usdm.calls == [("get_exchange_info", {})]


def test_source_returns_spot_market_lifecycle_from_exchange_info():
    source, _, spot, _ = _source()
    spec = TableSpec(
        table="kline_data_spot_raw",
        kind="kline",
        endpoint="spot_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open"),
        request_limit=1000,
    )

    lifecycle = source.market_lifecycle(
        spec,
        ValidationKey({"symbol": "ETHUSDT", "interval": "1h"}),
    )

    assert lifecycle == MarketLifecycle(
        is_known=True,
        status="TRADING",
        onboard_ms=None,
        delivery_ms=None,
    )
    assert spot.calls == [("get_exchange_info", {})]


def test_source_matches_continuous_contract_lifecycle_by_pair_and_contract_type():
    source, _, _, coinm = _source()
    spec = TableSpec(
        table="binance_kline_coinm_delivery_raw",
        kind="kline",
        endpoint="coinm_continuous_klines",
        key_fields=("pair", "contract_type", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open"),
        request_limit=1000,
        pair_field="pair",
        contract_type_field="contract_type",
    )

    lifecycle = source.market_lifecycle(
        spec,
        ValidationKey(
            {
                "pair": "BTCUSD",
                "contract_type": "NEXT_QUARTER",
                "interval": "1h",
            }
        ),
    )

    assert lifecycle == MarketLifecycle(
        is_known=True,
        status="TRADING",
        onboard_ms=1704067200000,
        delivery_ms=1719532800000,
    )
    assert coinm.calls == [("get_exchange_info", {})]


def test_source_uses_coinm_specific_kline_volume_fields():
    source, _, _, _ = _source()
    spec = TableSpec(
        table="binance_kline_coinm_perp_raw",
        kind="kline",
        endpoint="coinm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open", "volume", "base_asset_volume", "taker_buy_base_asset_volume"),
        request_limit=1000,
    )

    rows = source.fetch_rows(
        spec,
        ValidationKey({"symbol": "BTCUSD_PERP", "interval": "1h"}),
        1704067200000,
        1704070800000,
    )

    assert rows[0].fields["base_asset_volume"] == "15"
    assert rows[0].fields["taker_buy_volume"] == "6"
    assert rows[0].fields["taker_buy_base_asset_volume"] == "9"
    assert "quote_volume" not in rows[0].fields
    assert "taker_buy_quote_volume" not in rows[0].fields


def test_compare_db_and_source_rows_reports_missing_source_row_and_value_mismatch():
    differences = compare_db_and_source_rows(
        table="kline_data_future_raw",
        key_label="symbol=BTCUSDT,interval=1h",
        row_key_field="timestamp",
        compare_fields=("timestamp", "open"),
        db_rows=[
            {"timestamp": 1704067200000, "open": "1"},
            {"timestamp": 1704070800000, "open": "2"},
        ],
        source_rows=[
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "3"},
            ),
        ],
    )

    assert [diff.reason for diff in differences] == [
        "value_mismatch",
        "missing_source_row",
    ]


def test_compare_db_and_source_rows_matches_numeric_row_keys_by_canonical_value():
    differences = compare_db_and_source_rows(
        table="kline_data_future_raw",
        key_label="symbol=BTCUSDT,interval=1h",
        row_key_field="timestamp",
        compare_fields=("timestamp", "open"),
        db_rows=[
            {"timestamp": Decimal("1704067200000"), "open": "1"},
        ],
        source_rows=[
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "1"},
            ),
        ],
    )

    assert differences == []


def test_compare_registry_rows_reports_missing_db_row():
    differences = compare_registry_rows(
        table="binance_futures_symbols",
        compare_fields=("symbol", "status"),
        db_rows=[],
        source_rows=[
            SourceRow(
                key="BTCUSDT",
                fields={"symbol": "BTCUSDT", "status": "TRADING"},
            )
        ],
    )

    assert len(differences) == 1
    assert differences[0].reason == "missing_db_row"


def test_compare_db_and_source_rows_reports_duplicate_and_invalid_keys():
    differences = compare_db_and_source_rows(
        table="kline_data_future_raw",
        key_label="symbol=BTCUSDT,interval=1h",
        row_key_field="timestamp",
        compare_fields=("timestamp", "open"),
        db_rows=[
            {"timestamp": 1704067200000, "open": "1"},
            {"timestamp": 1704067200000, "open": "1"},
            {"open": "2"},
            {"timestamp": None, "open": "3"},
        ],
        source_rows=[
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "1"},
            ),
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "1"},
            ),
            SourceRow(key=None, fields={"timestamp": None, "open": "3"}),
        ],
    )

    assert [diff.reason for diff in differences] == [
        "duplicate_db_row_key",
        "missing_db_row_key_field",
        "null_db_row_key",
        "duplicate_source_row_key",
        "null_source_row_key",
    ]


def test_compare_registry_rows_reports_duplicate_symbol_keys():
    differences = compare_registry_rows(
        table="binance_futures_symbols",
        compare_fields=("symbol", "status"),
        db_rows=[
            {"symbol": "BTCUSDT", "status": "TRADING"},
            {"symbol": "BTCUSDT", "status": "TRADING"},
        ],
        source_rows=[
            SourceRow(
                key="BTCUSDT",
                fields={"symbol": "BTCUSDT", "status": "TRADING"},
            ),
            SourceRow(
                key="BTCUSDT",
                fields={"symbol": "BTCUSDT", "status": "TRADING"},
            ),
        ],
    )

    assert [diff.reason for diff in differences] == [
        "duplicate_source_row_key",
        "duplicate_db_row_key",
    ]


def test_accuracy_runner_filters_tables_and_continues_after_table_error(monkeypatch):
    specs = [
        TableSpec(
            table="broken_table",
            kind="registry",
            endpoint="usdm_exchange_info",
            key_fields=("symbol",),
            time_fields=(),
            interval_field=None,
            compare_fields=("symbol", "status"),
            request_limit=1000,
        ),
        TableSpec(
            table="binance_futures_symbols",
            kind="registry",
            endpoint="usdm_exchange_info",
            key_fields=("symbol",),
            time_fields=(),
            interval_field=None,
            compare_fields=("symbol", "status"),
            request_limit=1000,
        ),
    ]

    class FakeDB:
        def query(self, sql, params=()):
            if "broken_table" in sql:
                raise RuntimeError("boom")
            if sql.startswith("SHOW COLUMNS"):
                return [{"Field": "symbol"}, {"Field": "status"}]
            if sql.startswith("SELECT"):
                return [{"symbol": "BTCUSDT", "status": "TRADING"}]
            raise AssertionError(f"unexpected SQL: {sql}")

    class FakeSource:
        def fetch_registry_rows(self, spec):
            return [
                SourceRow(
                    key="BTCUSDT",
                    fields={"symbol": "BTCUSDT", "status": "TRADING"},
                )
            ]

    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: specs)

    result = DirectAccuracyService(db=FakeDB(), source=FakeSource()).run(
        safety_hours=24,
        include_tables=None,
    )
    filtered_result = DirectAccuracyService(db=FakeDB(), source=FakeSource()).run(
        safety_hours=24,
        include_tables=["binance_futures_symbols"],
    )

    assert [table.table for table in result.tables] == [
        "broken_table",
        "binance_futures_symbols",
    ]
    assert result.tables[0].differences[0].reason.startswith("table_error:RuntimeError:boom")
    assert result.tables[1].passed
    assert result.tables[1].windows_checked == 1
    assert result.tables[1].db_rows_checked == 1
    assert result.tables[1].source_rows_checked == 1
    assert [table.table for table in filtered_result.tables] == ["binance_futures_symbols"]


def test_accuracy_runner_reports_unknown_include_table(monkeypatch):
    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: [])

    result = DirectAccuracyService(db=object(), source=object()).run(
        safety_hours=24,
        include_tables=["missing_table"],
    )

    assert not result.passed
    assert result.tables[0].table == "table_selection"
    assert result.tables[0].differences[0].reason == "unknown_table"
    assert result.tables[0].differences[0].row_key == "missing_table"


def test_accuracy_runner_reports_no_stable_rows_for_time_series_table(monkeypatch):
    specs = [
        TableSpec(
            table="empty_kline",
            kind="kline",
            endpoint="usdm_klines",
            key_fields=("symbol",),
            time_fields=("timestamp",),
            interval_field=None,
            compare_fields=("timestamp", "open"),
            request_limit=1000,
            fixed_interval="1h",
        )
    ]

    class FakeDB:
        def query(self, sql, params=()):
            if sql.startswith("SHOW COLUMNS"):
                return [{"Field": "symbol"}, {"Field": "timestamp"}, {"Field": "open"}]
            if "GROUP BY" in sql:
                return []
            raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: specs)

    result = DirectAccuracyService(db=FakeDB(), source=object()).run(safety_hours=24)

    assert not result.passed
    assert result.tables[0].differences[0].reason == "no_stable_db_rows"


def test_accuracy_runner_skips_time_series_window_before_market_onboard(monkeypatch):
    first_hour = 1704067200000
    second_hour = 1704070800000
    third_hour = 1704074400000

    specs = [
        TableSpec(
            table="binance_1h_usdm_kline_raw",
            kind="kline",
            endpoint="usdm_klines",
            key_fields=("symbol",),
            time_fields=("timestamp",),
            interval_field=None,
            compare_fields=("timestamp", "open"),
            request_limit=1000,
            fixed_interval="1h",
        )
    ]

    class FakeDB:
        def query(self, sql, params=()):
            if sql.startswith("SHOW COLUMNS"):
                return [{"Field": "symbol"}, {"Field": "timestamp"}, {"Field": "open"}]
            if "GROUP BY" in sql:
                return [
                    {
                        "symbol": "AIAUSDT",
                        "min_time_ms": first_hour,
                        "max_time_ms": third_hour,
                    }
                ]
            if sql.startswith("SELECT"):
                assert params == (second_hour, third_hour, "AIAUSDT")
                return [
                    {"symbol": "AIAUSDT", "timestamp": second_hour, "open": "2"},
                    {"symbol": "AIAUSDT", "timestamp": third_hour, "open": "3"},
                ]
            raise AssertionError(f"unexpected SQL: {sql}")

    class FakeSource:
        def __init__(self):
            self.calls = []

        def market_lifecycle(self, spec, key):
            return MarketLifecycle(
                is_known=True,
                status="TRADING",
                onboard_ms=second_hour,
                delivery_ms=None,
            )

        def fetch_rows(self, spec, key, start_ms, end_ms):
            self.calls.append((key.values["symbol"], start_ms, end_ms))
            return [
                SourceRow(key=second_hour, fields={"timestamp": second_hour, "open": "2"}),
                SourceRow(key=third_hour, fields={"timestamp": third_hour, "open": "3"}),
            ]

    source = FakeSource()
    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: specs)

    result = DirectAccuracyService(db=FakeDB(), source=source).run(safety_hours=24)

    assert result.passed
    assert result.tables[0].windows_checked == 1
    assert result.tables[0].db_rows_checked == 2
    assert result.tables[0].source_rows_checked == 2
    assert source.calls == [("AIAUSDT", second_hour, third_hour)]


def test_accuracy_runner_reports_mismatch_inside_lifecycle_window(monkeypatch):
    first_hour = 1704067200000
    second_hour = 1704070800000

    specs = [
        TableSpec(
            table="binance_1h_usdm_kline_raw",
            kind="kline",
            endpoint="usdm_klines",
            key_fields=("symbol",),
            time_fields=("timestamp",),
            interval_field=None,
            compare_fields=("timestamp", "open"),
            request_limit=1000,
            fixed_interval="1h",
        )
    ]

    class FakeDB:
        def query(self, sql, params=()):
            if sql.startswith("SHOW COLUMNS"):
                return [{"Field": "symbol"}, {"Field": "timestamp"}, {"Field": "open"}]
            if "GROUP BY" in sql:
                return [
                    {
                        "symbol": "AIAUSDT",
                        "min_time_ms": first_hour,
                        "max_time_ms": second_hour,
                    }
                ]
            if sql.startswith("SELECT"):
                assert params == (second_hour, second_hour, "AIAUSDT")
                return [{"symbol": "AIAUSDT", "timestamp": second_hour, "open": "2"}]
            raise AssertionError(f"unexpected SQL: {sql}")

    class FakeSource:
        def market_lifecycle(self, spec, key):
            return MarketLifecycle(
                is_known=True,
                status="TRADING",
                onboard_ms=second_hour,
                delivery_ms=None,
            )

        def fetch_rows(self, spec, key, start_ms, end_ms):
            return [SourceRow(key=second_hour, fields={"timestamp": second_hour, "open": "9"})]

    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: specs)

    result = DirectAccuracyService(db=FakeDB(), source=FakeSource()).run(safety_hours=24)

    assert not result.passed
    assert len(result.tables[0].differences) == 1
    assert result.tables[0].differences[0].reason == "value_mismatch"
    assert result.tables[0].differences[0].row_key == second_hour


def test_accuracy_runner_skips_non_trading_market_without_source_fetch(monkeypatch):
    specs = [
        TableSpec(
            table="binance_1h_usdm_kline_raw",
            kind="kline",
            endpoint="usdm_klines",
            key_fields=("symbol",),
            time_fields=("timestamp",),
            interval_field=None,
            compare_fields=("timestamp", "open"),
            request_limit=1000,
            fixed_interval="1h",
        )
    ]

    class FakeDB:
        def query(self, sql, params=()):
            if sql.startswith("SHOW COLUMNS"):
                return [{"Field": "symbol"}, {"Field": "timestamp"}, {"Field": "open"}]
            if "GROUP BY" in sql:
                return [
                    {
                        "symbol": "PENDINGUSDT",
                        "min_time_ms": 1704067200000,
                        "max_time_ms": 1704070800000,
                    }
                ]
            raise AssertionError(f"unexpected SQL: {sql}")

    class FakeSource:
        def market_lifecycle(self, spec, key):
            return MarketLifecycle(
                is_known=True,
                status="PENDING_TRADING",
                onboard_ms=1704067200000,
                delivery_ms=None,
            )

        def fetch_rows(self, spec, key, start_ms, end_ms):
            raise AssertionError("non-trading markets should not be fetched")

    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: specs)

    result = DirectAccuracyService(db=FakeDB(), source=FakeSource()).run(safety_hours=24)

    assert result.passed
    assert result.tables[0].windows_checked == 0
    assert result.tables[0].differences == []


def test_accuracy_runner_crops_window_after_market_delivery(monkeypatch):
    first_hour = 1704067200000
    second_hour = 1704070800000
    third_hour = 1704074400000

    specs = [
        TableSpec(
            table="binance_kline_coinm_delivery_raw",
            kind="kline",
            endpoint="coinm_continuous_klines",
            key_fields=("pair", "contract_type", "interval"),
            time_fields=("timestamp",),
            interval_field="interval",
            compare_fields=("timestamp", "open"),
            request_limit=1000,
            pair_field="pair",
            contract_type_field="contract_type",
        )
    ]

    class FakeDB:
        def query(self, sql, params=()):
            if sql.startswith("SHOW COLUMNS"):
                return [
                    {"Field": "pair"},
                    {"Field": "contract_type"},
                    {"Field": "interval"},
                    {"Field": "timestamp"},
                    {"Field": "open"},
                ]
            if "GROUP BY" in sql:
                return [
                    {
                        "pair": "BTCUSD",
                        "contract_type": "CURRENT_QUARTER",
                        "interval": "1h",
                        "min_time_ms": first_hour,
                        "max_time_ms": third_hour,
                    }
                ]
            if sql.startswith("SELECT"):
                assert params == (
                    first_hour,
                    second_hour,
                    "BTCUSD",
                    "CURRENT_QUARTER",
                    "1h",
                )
                return [
                    {
                        "pair": "BTCUSD",
                        "contract_type": "CURRENT_QUARTER",
                        "interval": "1h",
                        "timestamp": first_hour,
                        "open": "1",
                    },
                    {
                        "pair": "BTCUSD",
                        "contract_type": "CURRENT_QUARTER",
                        "interval": "1h",
                        "timestamp": second_hour,
                        "open": "2",
                    },
                ]
            raise AssertionError(f"unexpected SQL: {sql}")

    class FakeSource:
        def __init__(self):
            self.calls = []

        def market_lifecycle(self, spec, key):
            return MarketLifecycle(
                is_known=True,
                status="TRADING",
                onboard_ms=None,
                delivery_ms=second_hour,
            )

        def fetch_rows(self, spec, key, start_ms, end_ms):
            self.calls.append((start_ms, end_ms))
            return [
                SourceRow(key=first_hour, fields={"timestamp": first_hour, "open": "1"}),
                SourceRow(key=second_hour, fields={"timestamp": second_hour, "open": "2"}),
            ]

    source = FakeSource()
    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: specs)

    result = DirectAccuracyService(db=FakeDB(), source=source).run(safety_hours=24)

    assert result.passed
    assert result.tables[0].windows_checked == 1
    assert result.tables[0].db_rows_checked == 2
    assert source.calls == [(first_hour, second_hour)]


def test_accuracy_runner_skips_window_after_market_delivery(monkeypatch):
    specs = [
        TableSpec(
            table="binance_kline_coinm_delivery_raw",
            kind="kline",
            endpoint="coinm_continuous_klines",
            key_fields=("pair", "contract_type", "interval"),
            time_fields=("timestamp",),
            interval_field="interval",
            compare_fields=("timestamp", "open"),
            request_limit=1000,
            pair_field="pair",
            contract_type_field="contract_type",
        )
    ]

    class FakeDB:
        def query(self, sql, params=()):
            if sql.startswith("SHOW COLUMNS"):
                return [
                    {"Field": "pair"},
                    {"Field": "contract_type"},
                    {"Field": "interval"},
                    {"Field": "timestamp"},
                    {"Field": "open"},
                ]
            if "GROUP BY" in sql:
                return [
                    {
                        "pair": "BTCUSD",
                        "contract_type": "CURRENT_QUARTER",
                        "interval": "1h",
                        "min_time_ms": 1704070800000,
                        "max_time_ms": 1704074400000,
                    }
                ]
            raise AssertionError(f"unexpected SQL: {sql}")

    class FakeSource:
        def market_lifecycle(self, spec, key):
            return MarketLifecycle(
                is_known=True,
                status="TRADING",
                onboard_ms=None,
                delivery_ms=1704067200000,
            )

        def fetch_rows(self, spec, key, start_ms, end_ms):
            raise AssertionError("post-delivery windows should not be fetched")

    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: specs)

    result = DirectAccuracyService(db=FakeDB(), source=FakeSource()).run(safety_hours=24)

    assert result.passed
    assert result.tables[0].windows_checked == 0
    assert result.tables[0].differences == []


def test_accuracy_runner_skips_unavailable_market_without_source_fetch(monkeypatch):
    specs = [
        TableSpec(
            table="binance_1h_usdm_kline_raw",
            kind="kline",
            endpoint="usdm_klines",
            key_fields=("symbol",),
            time_fields=("timestamp",),
            interval_field=None,
            compare_fields=("timestamp", "open"),
            request_limit=1000,
            fixed_interval="1h",
        )
    ]

    class FakeDB:
        def query(self, sql, params=()):
            if sql.startswith("SHOW COLUMNS"):
                return [{"Field": "symbol"}, {"Field": "timestamp"}, {"Field": "open"}]
            if "GROUP BY" in sql:
                return [
                    {
                        "symbol": "DELISTEDUSDT",
                        "min_time_ms": 1704067200000,
                        "max_time_ms": 1704070800000,
                    }
                ]
            raise AssertionError(f"unexpected SQL: {sql}")

    class FakeSource:
        def market_lifecycle(self, spec, key):
            return MarketLifecycle(
                is_known=False,
                status=None,
                onboard_ms=None,
                delivery_ms=None,
            )

        def fetch_rows(self, spec, key, start_ms, end_ms):
            raise AssertionError("unavailable markets should not be fetched")

    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: specs)

    result = DirectAccuracyService(db=FakeDB(), source=FakeSource()).run(safety_hours=24)

    assert result.passed
    assert result.tables[0].windows_checked == 0
    assert result.tables[0].db_rows_checked == 0
    assert result.tables[0].source_rows_checked == 0
    assert result.tables[0].differences == []


def test_accuracy_runner_continues_after_window_planning_error(monkeypatch):
    specs = [
        TableSpec(
            table="mixed_kline",
            kind="kline",
            endpoint="usdm_klines",
            key_fields=("symbol", "interval"),
            time_fields=("timestamp",),
            interval_field="interval",
            compare_fields=("timestamp", "open"),
            request_limit=1,
        )
    ]

    class FakeDB:
        def query(self, sql, params=()):
            if sql.startswith("SHOW COLUMNS"):
                return [
                    {"Field": "symbol"},
                    {"Field": "interval"},
                    {"Field": "timestamp"},
                    {"Field": "open"},
                ]
            if "GROUP BY" in sql:
                return [
                    {
                        "symbol": "BTCUSDT",
                        "interval": "0m",
                        "min_time_ms": 1704067200000,
                        "max_time_ms": 1704067200000,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "interval": "1h",
                        "min_time_ms": 1704067200000,
                        "max_time_ms": 1704067200000,
                    },
                ]
            if sql.startswith("SELECT"):
                return [{"symbol": "ETHUSDT", "interval": "1h", "timestamp": 1704067200000, "open": "1"}]
            raise AssertionError(f"unexpected SQL: {sql}")

    class FakeSource:
        def fetch_rows(self, spec, key, start_ms, end_ms):
            return [
                SourceRow(
                    key=1704067200000,
                    fields={"timestamp": 1704067200000, "open": "1"},
                )
            ]

    monkeypatch.setattr("services.db_accuracy.direct.accuracy_service.load_table_specs", lambda: specs)

    result = DirectAccuracyService(db=FakeDB(), source=FakeSource()).run(safety_hours=24)

    assert not result.passed
    assert result.tables[0].windows_checked == 1
    assert result.tables[0].db_rows_checked == 1
    assert result.tables[0].source_rows_checked == 1
    assert result.tables[0].differences[0].reason.startswith("window_planning_error:ValueError")
