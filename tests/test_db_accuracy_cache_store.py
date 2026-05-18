from tests.db_accuracy.cache_models import CacheManifest, MarketShard, TimePartition
from tests.db_accuracy.cache_store import CacheStore


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


def test_cache_store_builds_partition_paths(tmp_path):
    store = CacheStore(tmp_path)
    partition = TimePartition(start_ms=1704067200000, end_ms=1704153599999)

    paths = store.paths_for(_shard(), partition)

    assert paths.data_path == (
        tmp_path
        / "source"
        / "table=binance_kline_all_future_raw"
        / "symbol=BTCUSDT"
        / "interval=1m"
        / "date=2024-01-01"
        / "data.parquet"
    )
    assert paths.manifest_path.name == "manifest.json"


def test_cache_store_manifest_roundtrip(tmp_path):
    store = CacheStore(tmp_path)
    partition = TimePartition(start_ms=1704067200000, end_ms=1704153599999)
    paths = store.paths_for(_shard(), partition)
    manifest = CacheManifest(
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        market_key={"symbol": "BTCUSDT", "interval": "1m"},
        start_ms=1704067200000,
        end_ms=1704153599999,
        status="complete",
        row_count=1,
        source_error=None,
        created_at_utc="2026-05-18T12:00:00+00:00",
    )

    store.write_manifest(paths, manifest)

    loaded = store.read_manifest(paths)
    assert loaded is not None
    assert loaded.status == "complete"
    assert loaded.market_key == {"symbol": "BTCUSDT", "interval": "1m"}


def test_cache_store_parquet_roundtrip(tmp_path):
    import polars as pl

    store = CacheStore(tmp_path)
    partition = TimePartition(start_ms=1704067200000, end_ms=1704153599999)
    paths = store.paths_for(_shard(), partition)
    frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": [1704067200000],
            "open": ["1"],
            "close": ["2"],
        }
    )

    store.write_frame(paths, frame)

    loaded = store.read_frame(paths)
    assert loaded.to_dict(as_series=False) == frame.to_dict(as_series=False)


def test_cache_store_empty_manifest_removes_stale_parquet(tmp_path):
    import polars as pl

    store = CacheStore(tmp_path)
    partition = TimePartition(start_ms=1704067200000, end_ms=1704153599999)
    paths = store.paths_for(_shard(), partition)
    frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": [1704067200000],
            "open": ["1"],
            "close": ["2"],
        }
    )
    empty_manifest = CacheManifest(
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        market_key={"symbol": "BTCUSDT", "interval": "1m"},
        start_ms=1704067200000,
        end_ms=1704153599999,
        status="empty",
        row_count=0,
        source_error=None,
        created_at_utc="2026-05-18T12:00:00+00:00",
    )

    store.write_frame(paths, frame)
    store.write_manifest(paths, empty_manifest)

    assert not paths.data_path.exists()
    assert store.read_frame(paths).is_empty()
