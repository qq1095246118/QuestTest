from decimal import Decimal

from tests.db_accuracy.compare import compare_rows, normalize_value


def test_normalize_value_treats_numeric_strings_and_decimals_as_equal():
    assert normalize_value("1.2300") == normalize_value(Decimal("1.23"))
    assert normalize_value(1) == normalize_value("1.0")


def test_normalize_value_preserves_high_precision_differences():
    assert normalize_value(
        "1.1234567890123456789012345678901"
    ) != normalize_value("1.1234567890123456789012345678902")


def test_normalize_value_treats_zero_scale_as_equal():
    assert normalize_value("0.0000") == normalize_value(0)


def test_normalize_value_treats_positive_exponent_forms_as_equal():
    assert normalize_value("1000") == normalize_value(Decimal("1E+3"))
    assert normalize_value("1200") == normalize_value(Decimal("1.2E+3"))
    assert normalize_value("0") == normalize_value(Decimal("0E+3"))


def test_compare_rows_reports_field_difference():
    differences = compare_rows(
        table="kline_data_future_raw",
        key_label="symbol=BTCUSDT,interval=1h",
        row_key=1704067200000,
        db_row={"open": "100.00", "close": "101.00"},
        source_row={"open": "100.00", "close": "102.00"},
        fields=("open", "close"),
    )

    assert len(differences) == 1
    assert differences[0].field == "close"
    assert differences[0].reason == "value_mismatch"


def test_compare_rows_accepts_exact_normalized_match():
    differences = compare_rows(
        table="binance_funding_rate_all_future_raw",
        key_label="symbol=BTCUSDT",
        row_key=1704067200000,
        db_row={"funding_rate": "0.0100"},
        source_row={"funding_rate": Decimal("0.01")},
        fields=("funding_rate",),
    )

    assert differences == []


def test_compare_rows_reports_missing_source_field_even_when_db_value_is_none():
    differences = compare_rows(
        table="binance_funding_rate_all_future_raw",
        key_label="symbol=BTCUSDT",
        row_key=1704067200000,
        db_row={"mark_price": None},
        source_row={},
        fields=("mark_price",),
    )

    assert len(differences) == 1
    assert differences[0].field == "mark_price"
    assert differences[0].reason == "missing_source_field"
