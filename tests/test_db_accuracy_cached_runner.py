from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.db_accuracy.cached.cache_models import CachedCompareRequest
from services.db_accuracy.cached.cached_accuracy_service import CachedAccuracyService, cached_result_to_json
from services.db_accuracy.models import SourceRow, TableSpec


class FakeDB:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        market_keys: list[dict[str, Any]] | None = None,
    ) -> None:
        self.queries: list[tuple[str, tuple[Any, ...]]] = []
        self.rows = rows or [_db_row(1704067200000)]
        self.market_keys = (
            market_keys
            if market_keys is not None
            else [{"symbol": "BTCUSDT", "interval": "1m"}]
        )

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
            return self.market_keys
        return self.rows


class FakeSource:
    def fetch_rows(
        self,
        spec: TableSpec,
        key: Any,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        if not start_ms <= 1704067200000 <= end_ms:
            return []
        return [
            SourceRow(
                key=1704067200000,
                fields={"timestamp": 1704067200000, "open": "1", "close": "2"},
            )
        ]


class RequestFailedSource:
    def fetch_rows(
        self,
        spec: TableSpec,
        key: Any,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        raise RuntimeError("network down")


class MarketUnavailableSource:
    def fetch_rows(
        self,
        spec: TableSpec,
        key: Any,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        raise RuntimeError("invalid symbol")


def test_cached_runner_passes_single_explicit_shard(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.cached.cached_accuracy_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    db = FakeDB()
    runner = CachedAccuracyService(db=db, source=FakeSource())

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
    assert result.shards[0].report_path is not None
    assert result.shards[0].report_path.startswith("reports/run_id=")
    assert result.shards[0].diff_path is not None
    assert result.shards[0].diff_path.startswith("reports/run_id=")
    assert any(
        sql.startswith("SHOW COLUMNS FROM `binance_kline_all_future_raw`")
        for sql, _ in db.queries
    )


def test_cached_runner_discovers_market_shards_from_db(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.cached.cached_accuracy_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    runner = CachedAccuracyService(db=FakeDB(), source=FakeSource())

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
    assert payload["shards"][0]["report_path"].startswith("reports/run_id=")


def test_cached_runner_fails_when_discovery_finds_no_shards(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.cached.cached_accuracy_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    runner = CachedAccuracyService(db=FakeDB(market_keys=[]), source=FakeSource())

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

    assert not result.passed
    assert len(result.shards) == 1
    assert result.shards[0].status == "failed"
    assert result.shards[0].message is not None
    assert "no_shards_discovered" in result.shards[0].message


def test_cached_runner_returns_failed_result_for_setup_errors(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("services.db_accuracy.cached.cached_accuracy_service.load_table_specs", lambda: [])
    runner = CachedAccuracyService(db=FakeDB(), source=FakeSource())

    result = runner.run(
        CachedCompareRequest(
            table="missing_table",
            start_ms=1704067200000,
            end_ms=1704153599999,
            cache_root=tmp_path,
        )
    )

    assert not result.passed
    assert len(result.shards) == 1
    assert result.shards[0].status == "failed"
    assert result.shards[0].differences == 1
    assert result.shards[0].message is not None
    assert result.shards[0].message.startswith("setup_error:ValueError:")


def test_cached_runner_rejects_multi_value_discovery_filters(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.cached.cached_accuracy_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    runner = CachedAccuracyService(db=FakeDB(), source=FakeSource())

    result = runner.run(
        CachedCompareRequest(
            table="binance_kline_all_future_raw",
            start_ms=1704067200000,
            end_ms=1704153599999,
            cache_root=tmp_path,
            intervals=("1m", "5m"),
        )
    )

    assert not result.passed
    assert result.shards[0].differences == 1
    assert result.shards[0].message is not None
    assert "multi-value discovery filters are unsupported" in result.shards[0].message


def test_cached_runner_counts_source_request_failed_as_operational_failure(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.cached.cached_accuracy_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    db = FakeDB(rows=[_db_row(1704067200000), _db_row(1704067260000)])
    runner = CachedAccuracyService(db=db, source=RequestFailedSource())

    result = runner.run(_explicit_request(tmp_path))

    assert not result.passed
    assert result.shards[0].status == "failed"
    assert result.shards[0].db_rows == 2
    assert result.shards[0].source_rows == 0
    assert result.shards[0].differences == 1
    assert result.shards[0].message is not None
    assert result.shards[0].message.startswith("source_request_failed:")


def test_cached_runner_counts_market_unavailable_db_rows_as_differences(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "services.db_accuracy.cached.cached_accuracy_service.load_table_specs",
        lambda: [_kline_spec()],
    )
    db = FakeDB(rows=[_db_row(1704067200000), _db_row(1704067260000)])
    runner = CachedAccuracyService(db=db, source=MarketUnavailableSource())

    result = runner.run(_explicit_request(tmp_path))

    assert not result.passed
    assert result.shards[0].status == "failed"
    assert result.shards[0].db_rows == 2
    assert result.shards[0].source_rows == 0
    assert result.shards[0].differences == 2
    assert result.shards[0].message is not None
    assert result.shards[0].message.startswith("source_market_unavailable:")


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


def _explicit_request(tmp_path: Path) -> CachedCompareRequest:
    return CachedCompareRequest(
        table="binance_kline_all_future_raw",
        symbols=("BTCUSDT",),
        intervals=("1m",),
        start_ms=1704067200000,
        end_ms=1704153599999,
        cache_root=tmp_path,
    )


def _db_row(timestamp: int) -> dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "timestamp": timestamp,
        "open": "1",
        "close": "2",
    }
