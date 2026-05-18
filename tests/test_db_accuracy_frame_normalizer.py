from decimal import Decimal

import pytest

from tests.db_accuracy.cache_models import MarketShard
from tests.db_accuracy.frame_normalizer import (
    DuplicateJoinKeyError,
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

    assert frame.columns == ["symbol", "interval", "timestamp", "open", "close"]
    assert frame.height == 0
