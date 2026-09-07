"""只读数据发现的 SQL 参数、时区与回滚契约；不访问真实数据库。"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest

from db.factor4_read_repository import Factor4ReadRepository

pytestmark = pytest.mark.unit


class _DB:
    def __init__(self, ones: list[Any], alls: list[Any]) -> None:
        self.ones, self.alls = ones, alls
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @contextmanager
    def transaction(self) -> Iterator["_DB"]:
        """返回离线事务替身；无实际连接、提交或异常处理。"""
        yield self

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        """记录 SQL 与绑定参数；无返回、无 I/O。"""
        self.calls.append((sql, parameters))

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        """返回排队记录；队列用尽 IndexError，无数据库副作用。"""
        self.execute(sql, parameters)
        value = self.ones.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        """返回排队记录集；队列用尽 IndexError，无数据库副作用。"""
        self.execute(sql, parameters)
        return self.alls.pop(0)


def test_summary_discovery_converts_utc_clock_to_db_lifecycle_zone_and_rolls_back() -> None:
    clock = datetime(2026, 9, 6, 0)
    scope = {"ic_scope": "time_series", "calculation_mode": "direct", "factor_bar_interval": "1h",
             "factor_window_bars": "24", "return_bar_interval": "1h", "forward_return_bars": 1,
             "universe_key": "all", "symbol": "BTCUSDT", "window_scope": "1y", "scoring_version": "v1"}
    row = {**scope, "id": 11, "factor_id": 1, "run_id": "run"}
    db = _DB([{"as_of_time": clock}, scope], [[row], []])
    sample = Factor4ReadRepository(db).summary_sample("ts_symbol")
    assert sample.as_of == clock.replace(tzinfo=timezone.utc)
    selection = next((sql, args) for sql, args in db.calls if "ROW_NUMBER()" in sql)
    assert selection[1][-1] == datetime(2026, 9, 6, 8)
    assert "r.completed_at DESC, m.updated_at DESC, m.id DESC" in selection[0]
    assert "mean_ic IS NOT NULL" not in selection[0]  # Latest first, never backfill nulls from old runs.
    evidence = next((sql, args) for sql, args in db.calls if "FROM factor_ic_run_formula_evidence" in sql)
    assert evidence[1] == ("run", 1, 1, "direct", "1h", "24", "1h", 1)
    assert [sql for sql, _ in db.calls[:3]] == ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
                                               "SET TRANSACTION READ ONLY", "START TRANSACTION WITH CONSISTENT SNAPSHOT"]
    assert db.calls[-1] == ("ROLLBACK", ())


def test_scope_discovery_keeps_run_factor_rows_without_group_concat_truncation() -> None:
    db = _DB([{"as_of_time": datetime(2026, 9, 6)}, {"factor_bar_interval": "1h", "universe_key": "all"}], [[]])
    snapshot = Factor4ReadRepository(db).metric_scope_snapshot("cross_sectional")
    assert snapshot.rows == ()
    query, args = next((sql, args) for sql, args in db.calls if "MAX(m.period_end)" in sql)
    assert "m.factor_id, m.run_id" in query
    assert args == (1, "cross_sectional", "1h", "all")
    assert all("GROUP_CONCAT" not in sql for sql, _ in db.calls)
    assert db.calls[-1][0] == "ROLLBACK"


def test_repository_error_rolls_back_and_does_not_leak_driver_text() -> None:
    db = _DB([RuntimeError("password=must-not-appear")], [])
    with pytest.raises(RuntimeError) as raised:
        Factor4ReadRepository(db).summary_sample("ts_symbol")
    assert "must-not-appear" not in str(raised.value)
    assert db.calls[-1][0] == "ROLLBACK"


@pytest.mark.parametrize("shape", ["unknown", "ts_symbol' OR 1=1", ""])
def test_invalid_shape_is_rejected_before_opening_database(shape: str) -> None:
    db = _DB([], [])
    with pytest.raises(ValueError):
        Factor4ReadRepository(db).summary_sample(shape)
    assert not db.calls


def test_validity_history_uses_one_exact_partition_and_distinct_runs() -> None:
    group = {key: "scope" for key in ("universe_key", "factor_bar_interval", "factor_window_bars",
             "return_bar_interval", "window_scope", "time_series_scoring_version", "cross_sectional_scoring_version")}
    group.update(factor_id=7, is_sub_factor_id=1, forward_return_bars=1)
    rows = [{**group, "id": 10 + i, "run_id": f"run-{i}", "time_series_summary_id": 100 + i,
             "cross_sectional_summary_id": 200 + i} for i in range(2)]
    db = _DB([{"as_of_time": datetime(2026, 9, 7)}, group, {}, {}, {}, {}], [rows])
    samples = Factor4ReadRepository(db).validity_history()
    assert len(samples) == 2
    sql, args = next((sql, args) for sql, args in db.calls if "ROW_NUMBER()" in sql)
    assert "PARTITION BY v.run_id" in sql
    assert "v.factor_id <=> %s" in sql and "v.cross_sectional_scoring_version <=> %s" in sql
    assert "ts.run_id=v.run_id" in sql and "cs.run_id=v.run_id" in sql
    assert "ORDER BY run_completed_at DESC" in sql
    assert "LIMIT 2" not in sql
    assert args[0:3] == (1, 7, 1)
    assert db.calls[-1][0] == "ROLLBACK"


def test_default_validity_candidates_do_not_filter_invalid_or_incomplete_history() -> None:
    from db.factor4_read_repository import ValiditySample
    sample = ValiditySample({"factor_id": 7, "is_sub_factor_id": 1, "universe_key": "all",
        "factor_bar_interval": "1h", "factor_window_bars": "24H", "return_bar_interval": "1h",
        "forward_return_bars": 1, "window_scope": "1y", "time_series_scoring_version": "v1"}, {})
    invalid = {**sample.validity, "id": 9, "run_id": "new-invalid", "overall_is_valid": 0,
               "time_series_summary_id": None, "cross_sectional_summary_id": None}
    db = _DB([None, None], [[invalid]])
    rows = Factor4ReadRepository(db).validity_candidates(sample, "ts", datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert rows[0].validity["overall_is_valid"] == 0
    sql, args = next((sql, args) for sql, args in db.calls if "SELECT v.*" in sql)
    assert "overall_is_valid" not in sql and "is_valid=1" not in sql
    assert "JOIN factor_ic_summary_metrics" not in sql and "LIMIT" not in sql
    assert "r.completed_at<=%s" in sql and "v.updated_at<=%s" in sql
    assert args[-2:] == (datetime(2026, 9, 7, 8), datetime(2026, 9, 7, 8))


@pytest.mark.parametrize("mode,clause", [("symbol", "COALESCE(m.symbol,'')<>''"),
                                         ("aggregate", "COALESCE(m.symbol,'')=''")])
def test_slice_discovery_does_not_merge_symbol_and_aggregate(mode: str, clause: str) -> None:
    row = {"id": 1, "symbol": "BTCUSDT" if mode == "symbol" else ""}
    db = _DB([row], [[]])
    Factor4ReadRepository(db).slice_sample(symbol_mode=mode)
    sql = next(sql for sql, _ in db.calls if "SELECT m.*" in sql)
    assert clause in sql and "COALESCE(s.symbol,'')=COALESCE(m.symbol,'')" in sql
    query, args = next((sql, args) for sql, args in db.calls if "SELECT * FROM factor_ic_slice_metrics" in sql)
    assert "COALESCE(symbol,'')=COALESCE(%s,'')" in query
    assert "ORDER BY as_of_time, id" in query
    assert args[-1] == row["symbol"]
