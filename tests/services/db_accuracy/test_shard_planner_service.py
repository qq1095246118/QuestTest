import pytest

from services.db_accuracy.cached.cache_models import CachedCompareRequest
from services.db_accuracy.cached.shard_planner_service import (
    explicit_market_key,
    split_time_partitions,
    validate_cached_request,
)
from services.db_accuracy.models import TableSpec
from services.db_accuracy.table_specs import resolve_spec


def _kline_spec():
    spec = TableSpec(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp", "open_time"),
        interval_field="interval",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
    )
    return resolve_spec(spec, {"symbol", "interval", "timestamp", "open", "close"})


def _delivery_spec():
    spec = TableSpec(
        table="binance_kline_usdm_delivery_raw",
        kind="kline",
        endpoint="usdm_continuous_klines",
        key_fields=("pair", "contract_type", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        pair_field="pair",
        contract_type_field="contract_type",
    )
    return resolve_spec(spec, {"pair", "contract_type", "interval", "timestamp", "open", "close"})


def test_explicit_market_key_for_symbol_interval_table(tmp_path):
    request = CachedCompareRequest(
        table="binance_kline_all_future_raw",
        symbols=("BTCUSDT",),
        intervals=("1m",),
        start_ms=1704067200000,
        end_ms=1704153599999,
        cache_root=tmp_path,
    )

    assert explicit_market_key(_kline_spec(), request) == {"symbol": "BTCUSDT", "interval": "1m"}


def test_explicit_market_key_for_delivery_table(tmp_path):
    request = CachedCompareRequest(
        table="binance_kline_usdm_delivery_raw",
        pairs=("BTCUSDT",),
        contract_types=("CURRENT_QUARTER",),
        intervals=("1h",),
        start_ms=1704067200000,
        end_ms=1704153599999,
        cache_root=tmp_path,
    )

    assert explicit_market_key(_delivery_spec(), request) == {
        "pair": "BTCUSDT",
        "contract_type": "CURRENT_QUARTER",
        "interval": "1h",
    }


def test_cached_request_requires_time_range_and_table(tmp_path):
    request = CachedCompareRequest(
        table="",
        start_ms=1704067200000,
        end_ms=1704153599999,
        cache_root=tmp_path,
    )

    with pytest.raises(ValueError, match="table is required"):
        validate_cached_request(request)


def test_cached_request_requires_start_and_end_ms(tmp_path):
    request = CachedCompareRequest(
        table="binance_kline_all_future_raw",
        start_ms=None,
        end_ms=None,
        cache_root=tmp_path,
    )

    with pytest.raises(ValueError, match="start_ms and end_ms are required"):
        validate_cached_request(request)


def test_cached_request_rejects_end_before_start(tmp_path):
    request = CachedCompareRequest(
        table="binance_kline_all_future_raw",
        start_ms=1704153599999,
        end_ms=1704067200000,
        cache_root=tmp_path,
    )

    with pytest.raises(ValueError, match="end_ms must be greater than or equal to start_ms"):
        validate_cached_request(request)


def test_cached_request_rejects_non_positive_partition_days(tmp_path):
    request = CachedCompareRequest(
        table="binance_kline_all_future_raw",
        start_ms=1704067200000,
        end_ms=1704153599999,
        cache_root=tmp_path,
        partition_days=0,
    )

    with pytest.raises(ValueError, match="partition_days must be >= 1"):
        validate_cached_request(request)


def test_split_time_partitions_uses_inclusive_end_ms():
    partitions = split_time_partitions(
        start_ms=1704067200000,
        end_ms=1704239999999,
        partition_days=1,
    )

    assert [(part.start_ms, part.end_ms, part.bucket) for part in partitions] == [
        (1704067200000, 1704153599999, "date=2024-01-01"),
        (1704153600000, 1704239999999, "date=2024-01-02"),
    ]


@pytest.mark.parametrize("partition_days", [0, -1])
def test_split_time_partitions_rejects_non_positive_partition_days(partition_days):
    with pytest.raises(ValueError, match="partition_days must be >= 1"):
        split_time_partitions(
            start_ms=1704067200000,
            end_ms=1704153599999,
            partition_days=partition_days,
        )


def test_split_time_partitions_aligns_intraday_start_to_utc_date_buckets():
    partitions = split_time_partitions(
        start_ms=1704110400000,
        end_ms=1704196800000,
        partition_days=1,
    )

    assert [(part.start_ms, part.end_ms, part.bucket) for part in partitions] == [
        (1704110400000, 1704153599999, "date=2024-01-01"),
        (1704153600000, 1704196800000, "date=2024-01-02"),
    ]
