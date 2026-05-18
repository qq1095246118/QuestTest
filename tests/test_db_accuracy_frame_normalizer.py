from decimal import Decimal

import pytest

from tests.db_accuracy.cache_models import MarketShard
from tests.db_accuracy.frame_normalizer import (
    DuplicateJoinKeyError,
    MISSING_FIELD_SENTINEL,
    normalized_compare_columns,
    rows_to_normalized_frame,
    source_rows_to_normalized_frame,
)
from tests.db_accuracy.models import SourceRow


def _shard():
    return MarketShard(
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        kind="kline",
        key_values={"symbol": "BTCUSDT", "interval": "1m"},
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
    )


def _funding_shard():
    return MarketShard(
        table="binance_funding_rate_raw",
        endpoint="funding_rate",
        kind="funding",
        key_values={"symbol": "BTCUSDT"},
        time_field="funding_time",
        source_time_field="fundingTime",
        compare_fields=("symbol", "funding_time", "funding_rate"),
        request_limit=1000,
    )


def test_rows_to_normalized_frame_adds_key_values_and_decimal_strings():
    frame = rows_to_normalized_frame(
        _shard(),
        [
            {
                "timestamp": Decimal("1704067200000"),
                "open": Decimal("1.2300"),
                "close": "2.000",
            }
        ],
    )

    assert frame.to_dict(as_series=False) == {
        "symbol": ["BTCUSDT"],
        "interval": ["1m"],
        "timestamp": ["1704067200000"],
        "timestamp__compare": ["1704067200000"],
        "open": ["1.23"],
        "close": ["2"],
    }


def test_source_rows_to_normalized_frame_uses_source_fields():
    frame = source_rows_to_normalized_frame(
        _shard(),
        [
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "1.2300", "close": "2.0"},
            )
        ],
    )

    assert frame.to_dict(as_series=False)["open"] == ["1.23"]


def test_rows_to_normalized_frame_rejects_duplicate_join_key():
    with pytest.raises(DuplicateJoinKeyError, match="duplicate join key"):
        rows_to_normalized_frame(
            _shard(),
            [
                {"timestamp": 1704067200000, "open": "1", "close": "2"},
                {"timestamp": 1704067200000, "open": "1", "close": "2"},
            ],
        )


def test_rows_to_normalized_frame_preserves_schema_for_empty_rows():
    frame = rows_to_normalized_frame(_shard(), [])

    assert frame.columns == [
        "symbol",
        "interval",
        "timestamp",
        "timestamp__compare",
        "open",
        "close",
    ]
    assert frame.height == 0


def test_overlapping_compare_fields_use_payload_columns_without_overwriting_join_keys():
    frame = rows_to_normalized_frame(
        _funding_shard(),
        [
            {
                "symbol": "ETHUSDT",
                "funding_time": 1704067200000,
                "funding_rate": Decimal("0.0001000"),
            }
        ],
    )

    assert frame.to_dict(as_series=False) == {
        "symbol": ["BTCUSDT"],
        "funding_time": ["1704067200000"],
        "symbol__compare": ["ETHUSDT"],
        "funding_time__compare": ["1704067200000"],
        "funding_rate": ["0.0001"],
    }
    assert normalized_compare_columns(_funding_shard()) == (
        "symbol__compare",
        "funding_time__compare",
        "funding_rate",
    )


def test_missing_field_uses_sentinel_but_explicit_none_remains_null():
    frame = rows_to_normalized_frame(
        _shard(),
        [{"timestamp": 1704067200000, "open": None}],
    )

    assert frame.to_dict(as_series=False)["open"] == [None]
    assert frame.to_dict(as_series=False)["close"] == [MISSING_FIELD_SENTINEL]


def test_non_finite_decimal_cannot_collide_with_numeric_text():
    frame = rows_to_normalized_frame(
        _shard(),
        [
            {"timestamp": 1, "open": Decimal("NaN"), "close": "789778"},
        ],
    )

    values = frame.to_dict(as_series=False)
    assert values["open"] != values["close"]
    assert values["open"][0].startswith("decimal:")
