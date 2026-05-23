import pytest

from services.db_accuracy.db_reader_service import DBAccuracyReaderService, interval_to_ms, quote_identifier
from services.db_accuracy.models import KeyTimeRange, ResolvedTableSpec, TableSpec, ValidationKey
from services.db_accuracy.table_specs import load_table_specs, resolve_spec


class FakeDB:
    def __init__(self):
        self.queries = []

    def query(self, sql, params=None):
        self.queries.append((sql, params))
        if sql.startswith("SHOW COLUMNS FROM"):
            return [
                {"Field": "symbol"},
                {"Field": "interval"},
                {"Field": "timestamp"},
                {"Field": "open"},
                {"Field": "close"},
            ]
        if "GROUP BY" in sql:
            return [
                {
                    "symbol": "BTCUSDT",
                    "interval": "1h",
                    "min_time_ms": 1704067200000,
                    "max_time_ms": 1704070800000,
                }
            ]
        return []


def test_load_table_specs_includes_expected_binance_tables():
    specs = load_table_specs()
    names = {spec.table for spec in specs}

    assert "kline_data_future_raw" in names
    assert "kline_data_spot_raw" in names
    assert "binance_kline_all_future_raw" in names
    assert "binance_funding_rate_all_future_raw" in names
    assert "binance_kline_coinm_perp_raw" in names
    assert "binance_kline_coinm_delivery_raw" in names
    assert "binance_kline_usdm_delivery_raw" in names
    assert "binance_futures_symbols" in names


def test_loaded_specs_have_key_and_compare_fields():
    specs = load_table_specs()

    for spec in specs:
        assert spec.table
        assert spec.kind in {"kline", "funding", "registry"}
        assert spec.endpoint
        if spec.kind != "registry":
            assert spec.key_fields
            assert spec.time_fields
            assert spec.compare_fields
        if spec.kind == "registry":
            assert spec.key_fields == ("symbol",)
            assert "symbol" in spec.compare_fields


def test_loaded_one_hour_usdm_funding_spec_declares_fixed_interval():
    specs = {spec.table: spec for spec in load_table_specs()}

    assert specs["binance_1h_usdm_funding_rate_raw"].fixed_interval == "1h"


def test_loaded_usdm_funding_raw_spec_uses_two_hour_safe_window_and_source_fields_only():
    specs = {spec.table: spec for spec in load_table_specs()}
    spec = specs["binance_usdm_funding_rate_raw"]

    assert spec.kind == "funding"
    assert spec.endpoint == "usdm_funding"
    assert spec.key_fields == ("symbol",)
    assert spec.time_fields == ("funding_time", "timestamp")
    assert spec.fixed_interval == "2h"
    assert spec.compare_fields == (
        "symbol",
        "funding_rate",
        "funding_time",
        "mark_price",
    )
    assert "funding_rate_interval" not in spec.key_fields
    assert "funding_rate_interval" not in spec.compare_fields


def test_load_table_specs_rejects_scalar_list_fields(tmp_path):
    config_path = tmp_path / "tables.yaml"
    config_path.write_text(
        """
tables:
  - table: bad_table
    kind: kline
    endpoint: usdm_klines
    key_fields: symbol
    time_fields: [timestamp]
    interval_field: interval
    compare_fields: [timestamp]
    request_limit: 1000
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bad_table.*key_fields"):
        load_table_specs(config_path)


def test_resolve_spec_requires_mandatory_compare_fields_but_allows_optional():
    spec = TableSpec(
        table="sample_kline",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol",),
        time_fields=("timestamp",),
        interval_field=None,
        compare_fields=("timestamp", "open", "close"),
        optional_compare_fields=("open_time", "trades"),
        request_limit=1000,
    )

    with pytest.raises(ValueError, match="sample_kline.*missing compare fields.*close"):
        resolve_spec(spec, {"symbol", "timestamp", "open", "open_time"})

    resolved = resolve_spec(spec, {"symbol", "timestamp", "open", "close", "trades"})

    assert resolved.compare_fields == ("timestamp", "open", "close", "trades")


def test_interval_to_ms_supports_binance_intervals():
    assert interval_to_ms("1s") == 1_000
    assert interval_to_ms("1m") == 60_000
    assert interval_to_ms("15m") == 900_000
    assert interval_to_ms("1h") == 3_600_000
    assert interval_to_ms("1d") == 86_400_000


def test_interval_to_ms_rejects_zero_interval():
    with pytest.raises(ValueError, match="Unsupported Binance interval"):
        interval_to_ms("0m")


def test_quote_identifier_rejects_trailing_newline():
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        quote_identifier("sample_kline\n")


def test_reader_builds_key_ranges_from_configured_fields():
    spec = TableSpec(
        table="sample_kline",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
    )
    db = FakeDB()
    reader = DBAccuracyReaderService(db)
    resolved = resolve_spec(spec, reader.table_columns("sample_kline"))
    ranges = reader.key_ranges(resolved, stable_before_ms=1704074400000)

    assert len(ranges) == 1
    assert ranges[0].key.values == {"symbol": "BTCUSDT", "interval": "1h"}
    assert ranges[0].start_ms == 1704067200000
    assert ranges[0].end_ms == 1704070800000


def test_reader_returns_empty_for_specs_without_time_field():
    table_spec = TableSpec(
        table="sample_registry",
        kind="registry",
        endpoint="exchange_info",
        key_fields=("symbol",),
        time_fields=(),
        interval_field=None,
        compare_fields=("symbol", "status"),
        request_limit=1000,
    )
    resolved = ResolvedTableSpec(
        spec=table_spec,
        columns=("status", "symbol"),
        time_field=None,
        interval_field=None,
        compare_fields=("symbol", "status"),
        key_fields=("symbol",),
    )
    key = ValidationKey({"symbol": "BTCUSDT"})
    reader = DBAccuracyReaderService(FakeDB())

    assert reader.key_ranges(resolved, stable_before_ms=1704074400000) == []
    assert reader.rows_for_window(resolved, key, 1704067200000, 1704070800000) == []


def test_reader_builds_windows_from_fixed_interval():
    spec = TableSpec(
        table="sample_kline",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol",),
        time_fields=("timestamp",),
        interval_field=None,
        compare_fields=("timestamp", "open", "close"),
        request_limit=3,
        fixed_interval="1h",
    )
    resolved = resolve_spec(spec, {"symbol", "timestamp", "open", "close"})
    time_range = KeyTimeRange(
        table="sample_kline",
        key=ValidationKey({"symbol": "BTCUSDT"}),
        start_ms=1704067200000,
        end_ms=1704078000000,
    )

    windows = DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (1704067200000, 1704077999999),
        (1704078000000, 1704078000000),
    ]


def test_reader_builds_single_row_windows_when_request_limit_is_one():
    spec = TableSpec(
        table="sample_kline",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol",),
        time_fields=("timestamp",),
        interval_field=None,
        compare_fields=("timestamp", "open", "close"),
        request_limit=1,
        fixed_interval="1h",
    )
    resolved = resolve_spec(spec, {"symbol", "timestamp", "open", "close"})
    time_range = KeyTimeRange(
        table="sample_kline",
        key=ValidationKey({"symbol": "BTCUSDT"}),
        start_ms=1704067200000,
        end_ms=1704070800000,
    )

    windows = DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (1704067200000, 1704070799999),
        (1704070800000, 1704070800000),
    ]


def test_reader_rejects_non_positive_request_limit():
    spec = TableSpec(
        table="sample_kline",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol",),
        time_fields=("timestamp",),
        interval_field=None,
        compare_fields=("timestamp", "open", "close"),
        request_limit=0,
        fixed_interval="1h",
    )
    resolved = resolve_spec(spec, {"symbol", "timestamp", "open", "close"})
    time_range = KeyTimeRange(
        table="sample_kline",
        key=ValidationKey({"symbol": "BTCUSDT"}),
        start_ms=1704067200000,
        end_ms=1704070800000,
    )

    with pytest.raises(ValueError, match="request_limit must be >= 1"):
        DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)


def test_reader_rejects_zero_key_interval_without_hanging():
    spec = TableSpec(
        table="sample_kline",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1,
    )
    resolved = resolve_spec(spec, {"symbol", "interval", "timestamp", "open", "close"})
    time_range = KeyTimeRange(
        table="sample_kline",
        key=ValidationKey({"symbol": "BTCUSDT", "interval": "0m"}),
        start_ms=1704067200000,
        end_ms=1704070800000,
    )

    with pytest.raises(ValueError, match="Unsupported Binance interval"):
        DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)


def test_reader_slices_funding_windows_with_default_eight_hour_cadence():
    hour_ms = 3_600_000
    spec = TableSpec(
        table="sample_funding",
        kind="funding",
        endpoint="funding_rate",
        key_fields=("symbol",),
        time_fields=("fundingTime",),
        interval_field=None,
        compare_fields=("fundingTime", "fundingRate"),
        request_limit=3,
    )
    resolved = resolve_spec(spec, {"symbol", "fundingTime", "fundingRate"})
    time_range = KeyTimeRange(
        table="sample_funding",
        key=ValidationKey({"symbol": "BTCUSDT"}),
        start_ms=0,
        end_ms=24 * hour_ms,
    )

    windows = DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (0, 24 * hour_ms - 1),
        (24 * hour_ms, 24 * hour_ms),
    ]


def test_reader_slices_funding_windows_from_explicit_fixed_interval():
    hour_ms = 3_600_000
    spec = TableSpec(
        table="sample_one_hour_funding",
        kind="funding",
        endpoint="usdm_funding",
        key_fields=("symbol",),
        time_fields=("funding_time",),
        interval_field=None,
        compare_fields=("funding_time", "funding_rate", "mark_price"),
        request_limit=3,
        fixed_interval="1h",
    )
    resolved = resolve_spec(spec, {"symbol", "funding_time", "funding_rate", "mark_price"})
    time_range = KeyTimeRange(
        table="sample_one_hour_funding",
        key=ValidationKey({"symbol": "0GUSDT"}),
        start_ms=0,
        end_ms=5 * hour_ms,
    )

    windows = DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (0, 3 * hour_ms - 1),
        (3 * hour_ms, 5 * hour_ms),
    ]


def test_reader_slices_loaded_usdm_funding_raw_windows_with_two_hour_cadence():
    hour_ms = 3_600_000
    specs = {spec.table: spec for spec in load_table_specs()}
    spec = specs["binance_usdm_funding_rate_raw"]
    resolved = resolve_spec(
        spec,
        {"symbol", "funding_time", "funding_rate", "mark_price", "funding_rate_interval"},
    )
    time_range = KeyTimeRange(
        table="binance_usdm_funding_rate_raw",
        key=ValidationKey({"symbol": "BTCUSDT"}),
        start_ms=0,
        end_ms=5 * hour_ms,
    )

    windows = DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (0, 5 * hour_ms),
    ]


def test_reader_slices_loaded_usdm_funding_raw_windows_at_two_hour_limit_boundary():
    hour_ms = 3_600_000
    specs = {spec.table: spec for spec in load_table_specs()}
    spec = specs["binance_usdm_funding_rate_raw"]
    resolved = resolve_spec(
        spec,
        {"symbol", "funding_time", "funding_rate", "mark_price", "funding_rate_interval"},
    )
    time_range = KeyTimeRange(
        table="binance_usdm_funding_rate_raw",
        key=ValidationKey({"symbol": "BTCUSDT"}),
        start_ms=0,
        end_ms=2001 * hour_ms,
    )

    windows = DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (0, 2000 * hour_ms - 1),
        (2000 * hour_ms, 2001 * hour_ms),
    ]


def test_reader_slices_funding_windows_with_request_limit_one():
    hour_ms = 3_600_000
    spec = TableSpec(
        table="sample_one_hour_funding",
        kind="funding",
        endpoint="usdm_funding",
        key_fields=("symbol",),
        time_fields=("funding_time",),
        interval_field=None,
        compare_fields=("funding_time", "funding_rate", "mark_price"),
        request_limit=1,
        fixed_interval="1h",
    )
    resolved = resolve_spec(spec, {"symbol", "funding_time", "funding_rate", "mark_price"})
    time_range = KeyTimeRange(
        table="sample_one_hour_funding",
        key=ValidationKey({"symbol": "0GUSDT"}),
        start_ms=0,
        end_ms=2 * hour_ms,
    )

    windows = DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (0, hour_ms - 1),
        (hour_ms, 2 * hour_ms - 1),
        (2 * hour_ms, 2 * hour_ms),
    ]


def test_reader_caps_coinm_kline_windows_at_two_hundred_days():
    spec = TableSpec(
        table="sample_coinm_kline",
        kind="kline",
        endpoint="coinm_klines",
        key_fields=("symbol",),
        time_fields=("timestamp",),
        interval_field=None,
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        fixed_interval="1d",
    )
    resolved = resolve_spec(spec, {"symbol", "timestamp", "open", "close"})
    time_range = KeyTimeRange(
        table="sample_coinm_kline",
        key=ValidationKey({"symbol": "BTCUSD_PERP"}),
        start_ms=0,
        end_ms=250 * 86_400_000,
    )

    windows = DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (0, 200 * 86_400_000 - 1),
        (200 * 86_400_000, 250 * 86_400_000),
    ]


def test_reader_builds_calendar_month_windows():
    spec = TableSpec(
        table="sample_monthly_kline",
        kind="kline",
        endpoint="spot_klines",
        key_fields=("symbol",),
        time_fields=("timestamp",),
        interval_field=None,
        compare_fields=("timestamp", "open", "close"),
        request_limit=2,
        fixed_interval="1M",
    )
    resolved = resolve_spec(spec, {"symbol", "timestamp", "open", "close"})
    time_range = KeyTimeRange(
        table="sample_monthly_kline",
        key=ValidationKey({"symbol": "BTCUSDT"}),
        start_ms=1704067200000,
        end_ms=1709251200000,
    )

    windows = DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (1704067200000, 1709251199999),
        (1709251200000, 1709251200000),
    ]


def test_reader_rejects_windows_without_interval_source():
    spec = TableSpec(
        table="sample_kline",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol",),
        time_fields=("timestamp",),
        interval_field=None,
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
    )
    resolved = resolve_spec(spec, {"symbol", "timestamp", "open", "close"})
    time_range = KeyTimeRange(
        table="sample_kline",
        key=ValidationKey({"symbol": "BTCUSDT"}),
        start_ms=1704067200000,
        end_ms=1704070800000,
    )

    with pytest.raises(ValueError, match="fixed_interval or interval_field"):
        DBAccuracyReaderService(FakeDB()).build_windows(resolved, time_range)
