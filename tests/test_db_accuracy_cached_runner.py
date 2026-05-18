from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.db_accuracy.cache_models import CachedCompareRequest
from tests.db_accuracy.cached_runner import CachedAccuracyRunner, cached_result_to_json
from tests.db_accuracy.models import SourceRow, TableSpec


class FakeDB:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        if sql.startswith("SHOW COLUMNS"):
            return [
                {"Field": "symbol"},
                {"Field": "interval"},
                {"Field": "timestamp"},
                {"Field": "open"},
                {"Field": "close"},
            ]
        if "GROUP BY" in sql:
            return [{"symbol": "BTCUSDT", "interval": "1m"}]
        return [
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "timestamp": 1704067200000,
                "open": "1",
                "close": "2",
            }
        ]


class FakeSource:
    def fetch_rows(
        self,
        spec: TableSpec,
        key: Any,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        return [
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "1", "close": "2"},
            )
        ]


def test_cached_runner_passes_single_explicit_shard(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "tests.db_accuracy.cached_runner.load_table_specs",
        lambda: [_kline_spec()],
    )
    db = FakeDB()
    runner = CachedAccuracyRunner(db=db, source=FakeSource())

    result = runner.run(
        CachedCompareRequest(
            table="binance_kline_all_future_raw",
            symbols=("BTCUSDT",),
            intervals=("1m",),
            start_ms=1704067200000,
            end_ms=1704153599999,
            cache_root=tmp_path,
        )
    )

    assert result.passed
    assert len(result.shards) == 1
    assert result.shards[0].status == "passed"
    assert result.shards[0].db_rows == 1
    assert result.shards[0].source_rows == 1
    assert result.shards[0].differences == 0
    assert any(
        sql.startswith("SHOW COLUMNS FROM `binance_kline_all_future_raw`")
        for sql, _ in db.queries
    )


def test_cached_runner_discovers_market_shards_from_db(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "tests.db_accuracy.cached_runner.load_table_specs",
        lambda: [_kline_spec()],
    )
    runner = CachedAccuracyRunner(db=FakeDB(), source=FakeSource())

    result = runner.run(
        CachedCompareRequest(
            table="binance_kline_all_future_raw",
            start_ms=1704067200000,
            end_ms=1704153599999,
            cache_root=tmp_path,
            intervals=("1m",),
            max_shards=10,
        )
    )

    payload = json.loads(cached_result_to_json(result))

    assert result.passed
    assert len(result.shards) == 1
    assert result.shards[0].status == "passed"
    assert payload["passed"] is True
    assert payload["shards"][0]["db_rows"] == 1


def _kline_spec() -> TableSpec:
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
