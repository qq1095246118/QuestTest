from services.db_accuracy.cached.cache_models import MarketShard, TimePartition
from services.db_accuracy.cached.cached_db_reader_service import CachedDBReaderService


class FakeDB:
    def __init__(self):
        self.calls = []

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        if "GROUP BY" in sql:
            return [{"symbol": "BTCUSDT", "interval": "1m"}, {"symbol": "ETHUSDT", "interval": "1m"}]
        return [{"symbol": "BTCUSDT", "interval": "1m", "timestamp": 1704067200000, "open": "1", "close": "2"}]


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


def test_rows_for_partition_filters_by_time_and_market():
    db = FakeDB()
    reader = CachedDBReaderService(db)
    partition = TimePartition(start_ms=1704067200000, end_ms=1704153599999)

    rows = reader.rows_for_partition(_shard(), partition)

    assert rows == [{"symbol": "BTCUSDT", "interval": "1m", "timestamp": 1704067200000, "open": "1", "close": "2"}]
    sql, params = db.calls[0]
    assert "FROM `binance_kline_all_future_raw`" in sql
    assert "`timestamp` >= %s" in sql
    assert "`timestamp` <= %s" in sql
    assert "`symbol` = %s" in sql
    assert "`interval` = %s" in sql
    assert params == (1704067200000, 1704153599999, "BTCUSDT", "1m")


def test_discover_market_keys_groups_by_key_fields():
    db = FakeDB()
    reader = CachedDBReaderService(db)

    keys = reader.discover_market_keys(
        table="binance_kline_all_future_raw",
        key_fields=("symbol", "interval"),
        time_field="timestamp",
        start_ms=1704067200000,
        end_ms=1704153599999,
        filters={"interval": "1m"},
        limit=10,
    )

    assert keys == [
        {"symbol": "BTCUSDT", "interval": "1m"},
        {"symbol": "ETHUSDT", "interval": "1m"},
    ]
    sql, params = db.calls[0]
    assert "GROUP BY `symbol`, `interval`" in sql
    assert "LIMIT 10" in sql
    assert params == (1704067200000, 1704153599999, "1m")
