from tests.db_accuracy.cache_models import (
    CacheManifest,
    CachedRunResult,
    CachedShardResult,
    MarketShard,
    TimePartition,
)


def test_market_shard_label_and_path_parts_are_stable():
    shard = MarketShard(
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        kind="kline",
        key_values={"symbol": "BTCUSDT", "interval": "1m"},
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
    )

    assert shard.join_columns == ("symbol", "interval", "timestamp")
    assert shard.label == "table=binance_kline_all_future_raw,symbol=BTCUSDT,interval=1m"
    assert shard.path_parts == (
        "table=binance_kline_all_future_raw",
        "symbol=BTCUSDT",
        "interval=1m",
    )


def test_time_partition_builds_date_bucket():
    partition = TimePartition(start_ms=1704067200000, end_ms=1704153599999)

    assert partition.bucket == "date=2024-01-01"
    assert partition.label == "1704067200000-1704153599999"


def test_cached_run_result_summarizes_shards():
    result = CachedRunResult(
        shards=[
            CachedShardResult(
                shard_label="symbol=BTCUSDT,interval=1m",
                partition_label="1704067200000-1704153599999",
                status="passed",
                db_rows=10,
                source_rows=10,
                differences=0,
                report_path=None,
                diff_path=None,
                message=None,
            ),
            CachedShardResult(
                shard_label="symbol=ETHUSDT,interval=1m",
                partition_label="1704067200000-1704153599999",
                status="failed",
                db_rows=10,
                source_rows=9,
                differences=1,
                report_path="reports/sample.report.txt",
                diff_path="reports/sample.diff.json",
                message="rows only in db",
            ),
        ]
    )

    assert result.passed is False
    assert result.summary_text() == (
        "shards=2\n"
        "passed=1\n"
        "failed=1\n"
        "skipped=0\n"
        "db_rows=20\n"
        "source_rows=19\n"
        "differences=1"
    )


def test_cache_manifest_serializes_to_plain_dict():
    manifest = CacheManifest(
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        market_key={"symbol": "BTCUSDT", "interval": "1m"},
        start_ms=1704067200000,
        end_ms=1704153599999,
        status="complete",
        row_count=1440,
        source_error=None,
        created_at_utc="2026-05-18T12:00:00+00:00",
    )

    assert manifest.to_dict()["status"] == "complete"
    assert manifest.to_dict()["market_key"] == {"symbol": "BTCUSDT", "interval": "1m"}
