import json

import pytest
import polars as pl

from tests.db_accuracy.cache_models import CacheManifest, MarketShard, TimePartition
from tests.db_accuracy.cache_store import CacheStore
from tests.db_accuracy.cached_source import CachedBinanceSource
from tests.db_accuracy.models import SourceRow, TableSpec, ValidationKey


def _spec():
    return TableSpec(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
    )


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


def _partition():
    return TimePartition(start_ms=1704067200000, end_ms=1704153599999)


class RecordingSource:
    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls = []

    def fetch_rows(self, spec, key, start_ms, end_ms):
        self.calls.append((spec, key, start_ms, end_ms))
        if self.error is not None:
            raise self.error
        return self.rows


def test_ensure_partition_fetches_and_writes_missing_partition(tmp_path):
    source = RecordingSource(
        [
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "1.0", "close": "2.0"},
            )
        ]
    )
    store = CacheStore(tmp_path)
    fetcher = CachedBinanceSource(store=store, source=source)

    frame, manifest = fetcher.ensure_partition(_spec(), _shard(), _partition(), refresh=False)

    assert manifest.status == "complete"
    assert manifest.row_count == 1
    expected_frame = {
        "symbol": ["BTCUSDT"],
        "interval": ["1m"],
        "timestamp": ["1704067200000"],
        "timestamp__compare": ["1704067200000"],
        "open": ["1"],
        "close": ["2"],
    }
    assert frame.to_dict(as_series=False) == expected_frame
    assert source.calls == [
        (
            _spec(),
            ValidationKey({"symbol": "BTCUSDT", "interval": "1m"}),
            1704067200000,
            1704153599999,
        )
    ]

    paths = store.paths_for(_shard(), _partition())
    assert store.read_manifest(paths) == manifest
    assert store.read_frame(paths).to_dict(as_series=False) == expected_frame


def test_ensure_partition_reuses_complete_partition_without_refresh(tmp_path):
    source = RecordingSource(
        [
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "9", "close": "9"},
            )
        ]
    )
    store = CacheStore(tmp_path)
    partition = _partition()
    paths = store.paths_for(_shard(), partition)
    existing = CacheManifest(
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        market_key={"symbol": "BTCUSDT", "interval": "1m"},
        start_ms=partition.start_ms,
        end_ms=partition.end_ms,
        status="complete",
        row_count=1,
        source_error=None,
        created_at_utc="2026-05-18T12:00:00+00:00",
    )
    store.write_frame(
        paths,
        pl.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "interval": ["1m"],
                "timestamp": ["1704067200000"],
                "timestamp__compare": ["1704067200000"],
                "open": ["1"],
                "close": ["2"],
            }
        ),
    )
    store.write_manifest(paths, existing)
    fetcher = CachedBinanceSource(store=store, source=source)

    frame, manifest = fetcher.ensure_partition(_spec(), _shard(), partition, refresh=False)

    assert manifest == existing
    assert frame.to_dict(as_series=False)["open"] == ["1"]
    assert source.calls == []
    assert store.read_frame(paths).to_dict(as_series=False)["open"] == ["1"]


def test_ensure_partition_records_empty_partition_and_removes_stale_data(tmp_path):
    source = RecordingSource([])
    store = CacheStore(tmp_path)
    partition = _partition()
    paths = store.paths_for(_shard(), partition)
    store.write_frame(
        paths,
        pl.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "interval": ["1m"],
                "timestamp": ["1704067200000"],
                "timestamp__compare": ["1704067200000"],
                "open": ["1"],
                "close": ["2"],
            }
        ),
    )
    fetcher = CachedBinanceSource(store=store, source=source)

    frame, manifest = fetcher.ensure_partition(_spec(), _shard(), partition, refresh=True)

    assert manifest.status == "empty"
    assert manifest.row_count == 0
    assert manifest.source_error is None
    assert frame.is_empty()
    assert frame.columns == [
        "symbol",
        "interval",
        "timestamp",
        "timestamp__compare",
        "open",
        "close",
    ]
    assert not paths.data_path.exists()


def test_ensure_partition_reuses_empty_partition_with_stable_schema(tmp_path):
    source = RecordingSource(
        [
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "9", "close": "9"},
            )
        ]
    )
    store = CacheStore(tmp_path)
    partition = _partition()
    paths = store.paths_for(_shard(), partition)
    existing = CacheManifest(
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        market_key={"symbol": "BTCUSDT", "interval": "1m"},
        start_ms=partition.start_ms,
        end_ms=partition.end_ms,
        status="empty",
        row_count=0,
        source_error=None,
        created_at_utc="2026-05-18T12:00:00+00:00",
    )
    store.write_manifest(paths, existing)
    fetcher = CachedBinanceSource(store=store, source=source)

    frame, manifest = fetcher.ensure_partition(_spec(), _shard(), partition, refresh=False)

    assert manifest == existing
    assert source.calls == []
    assert frame.is_empty()
    assert frame.columns == [
        "symbol",
        "interval",
        "timestamp",
        "timestamp__compare",
        "open",
        "close",
    ]


def test_ensure_partition_empty_cache_hit_ignores_stale_parquet(tmp_path):
    source = RecordingSource()
    store = CacheStore(tmp_path)
    partition = _partition()
    paths = store.paths_for(_shard(), partition)
    stale_frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "timestamp__compare": ["1704067200000"],
            "open": ["9"],
            "close": ["9"],
        }
    )
    empty_manifest = CacheManifest(
        table="binance_kline_all_future_raw",
        endpoint="usdm_klines",
        market_key={"symbol": "BTCUSDT", "interval": "1m"},
        start_ms=partition.start_ms,
        end_ms=partition.end_ms,
        status="empty",
        row_count=0,
        source_error=None,
        created_at_utc="2026-05-18T12:00:00+00:00",
    )
    store.write_frame(paths, stale_frame)
    paths.manifest_path.write_text(
        json.dumps(empty_manifest.to_dict()),
        encoding="utf-8",
    )
    fetcher = CachedBinanceSource(store=store, source=source)

    frame, manifest = fetcher.ensure_partition(_spec(), _shard(), partition, refresh=False)

    assert manifest == empty_manifest
    assert source.calls == []
    assert frame.is_empty()
    assert frame.to_dict(as_series=False) == {
        "symbol": [],
        "interval": [],
        "timestamp": [],
        "timestamp__compare": [],
        "open": [],
        "close": [],
    }


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ValueError("Invalid symbol BTCUSDT"), "source_market_unavailable"),
        (RuntimeError("read timed out"), "source_request_failed"),
    ],
)
def test_ensure_partition_records_source_errors(tmp_path, error, status):
    source = RecordingSource(error=error)
    store = CacheStore(tmp_path)
    fetcher = CachedBinanceSource(store=store, source=source)

    frame, manifest = fetcher.ensure_partition(_spec(), _shard(), _partition(), refresh=False)

    assert manifest.status == status
    assert manifest.row_count == 0
    assert manifest.source_error == str(error)
    assert frame.is_empty()
