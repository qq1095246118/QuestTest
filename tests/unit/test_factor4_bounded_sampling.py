"""Execute bounded discovery against independent relational fixtures, without live DB access."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
from typing import Any

import pytest

import db.factor4_read_repository as repository_module
from db.factor4_read_repository import Factor4ReadRepository, SampleDiscoveryExhausted

pytestmark = pytest.mark.unit

_CLOCK = datetime(2026, 9, 11, 0)
_BEFORE = datetime(2026, 9, 10, 12)
_CUTOFF = datetime(2026, 9, 11, 8)
_TIMESTAMP_FIELDS = {"completed_at", "run_completed_at", "updated_at", "period_end", "metric_period_end"}


class _RelationalDB:
    """Run production SELECT statements through SQLite with only dialect substitutions."""

    def __init__(self) -> None:
        """Create the three minimal relational tables; SQLite setup failures propagate."""
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.connection.executescript("""
            CREATE TABLE factor_ic_runs (
                run_id TEXT PRIMARY KEY, status TEXT, completed_at TEXT
            );
            CREATE TABLE factor_ic_summary_metrics (
                id INTEGER PRIMARY KEY, factor_id INTEGER, run_id TEXT,
                is_sub_factor_id INTEGER, ic_scope TEXT, calculation_mode TEXT,
                factor_bar_interval TEXT, factor_window_bars TEXT,
                return_bar_interval TEXT, forward_return_bars INTEGER,
                universe_key TEXT, symbol TEXT, window_scope TEXT, scoring_version TEXT,
                mean_ic REAL, rank_is_icir REAL, updated_at TEXT, period_end TEXT
            );
            CREATE TABLE factor_ic_run_formula_evidence (
                id INTEGER PRIMARY KEY, run_id TEXT, factor_id INTEGER,
                is_sub_factor_id INTEGER, calculation_mode TEXT,
                factor_bar_interval TEXT, factor_window_bars TEXT,
                return_bar_interval TEXT, forward_return_bars INTEGER, formula_hash TEXT
            );
        """)

    @contextmanager
    def transaction(self) -> Iterator[_RelationalDB]:
        """Yield the offline transaction; test fixture rows remain available to assertions."""
        yield self

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        """Record MySQL transaction commands and reject unexpected writes or other SQL."""
        assert sql in {
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ", "SET TRANSACTION READ ONLY",
            "START TRANSACTION WITH CONSISTENT SNAPSHOT", "ROLLBACK",
        }
        self.calls.append((sql, parameters))

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Return the fixed DB clock or one actual SQL row; SQL failures propagate."""
        if sql == "SELECT UTC_TIMESTAMP(6) AS as_of_time":
            self.calls.append((sql, parameters))
            return {"as_of_time": _CLOCK}
        rows = self.fetch_all(sql, parameters)
        return rows[0] if rows else None

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute SELECT with null-safe equality and parameter syntax translated only."""
        self.calls.append((sql, parameters))
        parameters = tuple(_stored(value) for value in parameters)
        rows = self.connection.execute(sql.replace("<=>", "IS").replace("%s", "?"), parameters)
        return [
            {key: datetime.fromisoformat(value) if key in _TIMESTAMP_FIELDS and value is not None else value
             for key, value in dict(row).items()}
            for row in rows
        ]

    def run(self, run_id: str, completed_at: datetime | None = _BEFORE, *, status: str = "completed") -> None:
        """Insert a Run with the supplied lifecycle status and naive Shanghai completion."""
        self.connection.execute("INSERT INTO factor_ic_runs VALUES (?, ?, ?)",
                                (run_id, status, _stored(completed_at)))

    def metric(self, factor_id: int, run_id: str = "base", **overrides: Any) -> int:
        """Insert a summary fixture and return its identity; invalid fields raise SQLite errors."""
        row = {
            "factor_id": factor_id, "run_id": run_id, "is_sub_factor_id": 1,
            "ic_scope": "time_series", "calculation_mode": "direct",
            "factor_bar_interval": "1h", "factor_window_bars": "24",
            "return_bar_interval": "1h", "forward_return_bars": 1,
            "universe_key": "all", "symbol": "BTCUSDT", "window_scope": "1y",
            "scoring_version": "v1", "mean_ic": 0.1, "rank_is_icir": 0.3,
            "updated_at": _BEFORE, "period_end": datetime(2026, 9, 9), **overrides,
        }
        result = self.connection.execute(
            f"INSERT INTO factor_ic_summary_metrics ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
            tuple(_stored(value) for value in row.values()),
        )
        assert result.lastrowid is not None
        return result.lastrowid

    def formula(self, metric_id: int, formula_hash: str) -> None:
        """Insert evidence matching one summary's identity and formula scope."""
        self.connection.execute("""INSERT INTO factor_ic_run_formula_evidence (
            run_id,factor_id,is_sub_factor_id,calculation_mode,factor_bar_interval,
            factor_window_bars,return_bar_interval,forward_return_bars,formula_hash)
            SELECT run_id,factor_id,is_sub_factor_id,calculation_mode,factor_bar_interval,
            factor_window_bars,return_bar_interval,forward_return_bars,?
            FROM factor_ic_summary_metrics WHERE id=?""", (formula_hash, metric_id))


def _stored(value: Any) -> Any:
    """Encode datetimes for SQLite with microseconds; retain all other parameter values."""
    return value.isoformat(" ", timespec="microseconds") if isinstance(value, datetime) else value


@pytest.fixture
def relational_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[_RelationalDB]:
    """Provide tiny discovery budgets and isolated SQL data; always close the connection."""
    monkeypatch.setattr(repository_module, "_DISCOVERY_PAGE_SIZE", 2)
    monkeypatch.setattr(repository_module, "_DISCOVERY_MAX_CANDIDATES", 6)
    monkeypatch.setattr(repository_module, "_SCOPE_BASELINE_ROW_LIMIT", 30)
    database = _RelationalDB()
    database.run("base")
    try:
        yield database
    finally:
        database.connection.close()


@pytest.mark.parametrize("shape,ic_scope,symbol", [
    ("ts_symbol", "time_series", "BTCUSDT"),
    ("ts_aggregate", "time_series", ""),
    ("cs_aggregate", "cross_sectional", ""),
])
@pytest.mark.parametrize("kind,flag", [("sub_factor", 1), ("factor", 0)])
def test_summary_discovery_budget_does_not_truncate_full_historical_membership(
    relational_db: _RelationalDB, shape: str, ic_scope: str, symbol: str, kind: str, flag: int,
) -> None:
    """Every historical factor contributes its own latest row, exceeding candidate page size."""
    db = relational_db
    db.run("recent", datetime(2026, 9, 11, 7))
    scope = {"ic_scope": ic_scope, "symbol": symbol, "is_sub_factor_id": flag, "scoring_version": None}
    old_ids = {factor_id: db.metric(factor_id, **scope) for factor_id in range(1, 20)}
    recent_id = db.metric(1, "recent", **scope)
    db.metric(999, "recent", **{**scope, "is_sub_factor_id": 1 - flag})
    db.formula(old_ids[1], "old-evidence")
    db.formula(recent_id, "selected-evidence")

    sample = Factor4ReadRepository(db).summary_sample(shape, kind)

    assert sample is not None
    assert sample.as_of == _CLOCK.replace(tzinfo=timezone.utc)
    assert sample.scope["scoring_version"] is None
    assert {row["factor_id"]: row["id"] for row in sample.rows} == {**old_ids, 1: recent_id}
    assert {row["formula_hash"] for row in sample.formulas} == {"selected-evidence"}


def test_summary_latest_null_remains_latest_without_backfilling_older_metrics(relational_db: _RelationalDB) -> None:
    """Historical quality permits discovery but never replaces the latest null metric."""
    db = relational_db
    db.run("latest", datetime(2026, 9, 11, 7))
    for factor_id in (1, 2, 3):
        db.metric(factor_id)
    latest_id = db.metric(1, "latest", mean_ic=None, rank_is_icir=None)

    sample = Factor4ReadRepository(db).summary_sample("ts_symbol")

    assert sample is not None
    latest = next(row for row in sample.rows if row["factor_id"] == 1)
    assert (latest["id"], latest["run_id"], latest["mean_ic"], latest["rank_is_icir"]) == (
        latest_id, "latest", None, None,
    )


def test_summary_selection_uses_completion_boundary_and_ignores_unfinished_members(relational_db: _RelationalDB) -> None:
    """Future/running members cannot replace results or inflate the completed scope beyond 20."""
    db = relational_db
    db.run("boundary", _CUTOFF)
    db.run("future", _CUTOFF.replace(microsecond=1))
    db.run("running", _BEFORE, status="running")
    db.run("undated", None)
    expected = {factor_id: db.metric(factor_id) for factor_id in (1, 2, 3)}
    expected[3] = db.metric(3, "boundary")
    for run_id in ("future", "running", "undated"):
        for factor_id in range(1, 30):
            db.metric(factor_id, run_id)

    sample = Factor4ReadRepository(db).summary_sample("ts_symbol")

    assert sample is not None
    assert {row["factor_id"]: row["id"] for row in sample.rows} == expected


@pytest.mark.parametrize("candidate_count", [4, 5])
def test_summary_discovery_reaches_last_candidate_on_full_or_partial_final_page(
    relational_db: _RelationalDB, candidate_count: int,
) -> None:
    """Within one profile, a valid tail symbol remains reachable on each kind of final page."""
    db = relational_db
    for candidate in range(candidate_count):
        for factor_id in (1, 2, 3):
            db.metric(factor_id, symbol=f"S{candidate}USDT",
                      rank_is_icir=0 if candidate < candidate_count - 1 else 0.3)

    sample = Factor4ReadRepository(db).summary_sample("ts_symbol")

    assert sample is not None
    assert sample.scope["symbol"] == f"S{candidate_count - 1}USDT"
    assert {row["factor_id"] for row in sample.rows} == {1, 2, 3}


def test_summary_profiles_share_budget_before_revisiting_large_symbol_population(
    relational_db: _RelationalDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Twenty rejected 15m symbols cannot starve the valid 1d profile under a two-candidate budget."""
    monkeypatch.setattr(repository_module, "_DISCOVERY_MAX_CANDIDATES", 2)
    for candidate in range(20):
        for factor_id in (1, 2, 3):
            relational_db.metric(factor_id, factor_bar_interval="15m", symbol=f"S{candidate:02}USDT", rank_is_icir=0)
    expected = {
        factor_id: relational_db.metric(factor_id, factor_bar_interval="1d", symbol="BTCUSDT")
        for factor_id in (10, 11, 12)
    }

    sample = Factor4ReadRepository(relational_db).summary_sample("ts_symbol")

    assert sample is not None
    assert (sample.scope["factor_bar_interval"], sample.scope["symbol"]) == ("1d", "BTCUSDT")
    assert {row["factor_id"]: row["id"] for row in sample.rows} == expected


@pytest.mark.parametrize("profile_count", [2, 3])
def test_summary_profile_budget_overflow_is_distinct_from_exhausting_all_profiles(
    relational_db: _RelationalDB, monkeypatch: pytest.MonkeyPatch, profile_count: int,
) -> None:
    """Profiles with no requested symbol shape still distinguish complete and truncated discovery."""
    monkeypatch.setattr(repository_module, "_DISCOVERY_MAX_CANDIDATES", 2)
    for candidate in range(profile_count):
        for factor_id in (1, 2, 3):
            relational_db.metric(factor_id, universe_key=f"u-{candidate}", symbol="")

    if profile_count == 2:
        assert Factor4ReadRepository(relational_db).summary_sample("ts_symbol") is None
    else:
        with pytest.raises(SampleDiscoveryExhausted, match="summary candidate budget=2 exhausted"):
            Factor4ReadRepository(relational_db).summary_sample("ts_symbol")


def test_summary_discovery_budget_exhaustion_is_not_reported_as_no_natural_data(
    relational_db: _RelationalDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid scope outside the inspected budget produces an explicit incomplete-search block."""
    monkeypatch.setattr(repository_module, "_DISCOVERY_MAX_CANDIDATES", 2)
    for candidate in range(3):
        for factor_id in (1, 2, 3):
            relational_db.metric(factor_id, symbol=f"S{candidate}USDT", rank_is_icir=0 if candidate < 2 else 0.3)

    with pytest.raises(SampleDiscoveryExhausted, match="summary candidate budget=2 exhausted"):
        Factor4ReadRepository(relational_db).summary_sample("ts_symbol")


@pytest.mark.parametrize("candidate_count", [0, 1, 2])
def test_summary_complete_search_without_qualifying_scope_returns_none(
    relational_db: _RelationalDB, candidate_count: int,
) -> None:
    """Natural exhaustion, including an exactly full last page, is not a budget block."""
    for candidate in range(candidate_count):
        for factor_id in (1, 2, 3):
            relational_db.metric(factor_id, universe_key=f"u-{candidate}", mean_ic=None)

    assert Factor4ReadRepository(relational_db).summary_sample("ts_symbol") is None


def test_scope_baseline_preserves_all_runs_factors_and_maximum_period_end(relational_db: _RelationalDB) -> None:
    """Group raw history only by complete scope/factor/Run; preserve future rows for later PIT filtering."""
    db = relational_db
    db.run("recent", datetime(2026, 9, 11, 7))
    db.run("future", datetime(2026, 9, 12, 7))
    db.run("running", _BEFORE, status="running")
    expected: dict[tuple[str, int, str], datetime | None] = {}
    for window in ("24", "48"):
        for factor_id in (1, 2):
            for run_id in ("base", "recent", "future"):
                periods = (None, datetime(2026, 9, 8)) if run_id == "base" else (datetime(2026, 9, 9),)
                for period in periods:
                    db.metric(factor_id, run_id, factor_window_bars=window, period_end=period)
                expected[(window, factor_id, run_id)] = max(period for period in periods if period is not None)
    db.metric(3, factor_window_bars="48", period_end=None)
    expected[("48", 3, "base")] = None
    db.metric(1, "running")
    db.metric(999, is_sub_factor_id=0)

    snapshot = Factor4ReadRepository(db).metric_scope_snapshot("time_series")

    assert snapshot is not None
    assert snapshot.filters == {"ic_scope": "time_series", "interval": "1h", "universe_key": "all"}
    assert {(row["factor_window_bars"], row["factor_id"], row["run_id"]): row["metric_period_end"]
            for row in snapshot.rows} == expected


def test_scope_baseline_at_exact_budget_returns_every_row(
    relational_db: _RelationalDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The limit itself is complete; only the extra sentinel row signifies truncation."""
    monkeypatch.setattr(repository_module, "_SCOPE_BASELINE_ROW_LIMIT", 4)
    for factor_id in range(1, 5):
        relational_db.metric(factor_id, factor_window_bars="24" if factor_id < 3 else "48")

    snapshot = Factor4ReadRepository(relational_db).metric_scope_snapshot("time_series")

    assert snapshot is not None
    assert {row["factor_id"] for row in snapshot.rows} == {1, 2, 3, 4}


@pytest.mark.parametrize("duplicate_group", [False, True])
def test_scope_budget_plus_one_never_returns_partial_baseline(
    relational_db: _RelationalDB, monkeypatch: pytest.MonkeyPatch, duplicate_group: bool,
) -> None:
    """Oversized raw history is blocked even when a partial read could collapse to one group."""
    monkeypatch.setattr(repository_module, "_SCOPE_BASELINE_ROW_LIMIT", 4)
    for factor_id in range(1, 6):
        relational_db.metric(1 if duplicate_group else factor_id)

    with pytest.raises(SampleDiscoveryExhausted, match="no complete scope baseline within 4 rows"):
        Factor4ReadRepository(relational_db).metric_scope_snapshot("time_series")


def test_scope_oversized_candidate_advances_to_next_complete_partition(
    relational_db: _RelationalDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject the oversized first partition and return all rows of the next viable partition."""
    monkeypatch.setattr(repository_module, "_SCOPE_BASELINE_ROW_LIMIT", 4)
    for factor_id in range(1, 6):
        relational_db.metric(factor_id, universe_key="a-oversized")
    for factor_id in range(10, 14):
        relational_db.metric(factor_id, universe_key="b-complete", factor_window_bars=str(factor_id % 2 + 1))

    snapshot = Factor4ReadRepository(relational_db).metric_scope_snapshot("time_series")

    assert snapshot is not None
    assert snapshot.filters["universe_key"] == "b-complete"
    assert {row["factor_id"] for row in snapshot.rows} == {10, 11, 12, 13}


def test_scope_complete_single_scope_fallback_survives_other_oversized_candidates(
    relational_db: _RelationalDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entirely read single-scope partition is usable after all other candidates are rejected."""
    monkeypatch.setattr(repository_module, "_SCOPE_BASELINE_ROW_LIMIT", 4)
    for universe in ("a-oversized", "c-oversized"):
        for factor_id in range(1, 6):
            relational_db.metric(factor_id, universe_key=universe)
    for factor_id in (10, 11):
        relational_db.metric(factor_id, universe_key="b-fallback")

    snapshot = Factor4ReadRepository(relational_db).metric_scope_snapshot("time_series")

    assert snapshot is not None
    assert snapshot.filters["universe_key"] == "b-fallback"
    assert {row["factor_id"] for row in snapshot.rows} == {10, 11}


@pytest.mark.parametrize("candidate_count", [4, 5])
def test_scope_discovery_reaches_last_candidate_on_full_or_partial_final_page(
    relational_db: _RelationalDB, candidate_count: int,
) -> None:
    """Candidates containing another entity kind cannot prevent a final complete target partition."""
    for candidate in range(candidate_count):
        for window in ("24", "48"):
            relational_db.metric(1, universe_key=f"u-{candidate}", factor_window_bars=window,
                                 is_sub_factor_id=int(candidate == candidate_count - 1))

    snapshot = Factor4ReadRepository(relational_db).metric_scope_snapshot("time_series")

    assert snapshot is not None
    assert snapshot.filters["universe_key"] == f"u-{candidate_count - 1}"
    assert {row["factor_window_bars"] for row in snapshot.rows} == {"24", "48"}


def test_scope_discovery_budget_exhaustion_is_not_reported_as_no_natural_data(
    relational_db: _RelationalDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target-kind partition outside the budget must be distinguished from absent data."""
    monkeypatch.setattr(repository_module, "_DISCOVERY_MAX_CANDIDATES", 2)
    for candidate in range(3):
        relational_db.metric(1, universe_key=f"u-{candidate}", is_sub_factor_id=int(candidate == 2))

    with pytest.raises(SampleDiscoveryExhausted, match="scope candidate budget=2 exhausted"):
        Factor4ReadRepository(relational_db).metric_scope_snapshot("time_series")


@pytest.mark.parametrize("candidate_count", [0, 1, 2])
def test_scope_complete_search_without_target_kind_returns_none(
    relational_db: _RelationalDB, candidate_count: int,
) -> None:
    """Exhausting every candidate without target-kind rows is a real absent-sample result."""
    for candidate in range(candidate_count):
        relational_db.metric(1, universe_key=f"u-{candidate}", is_sub_factor_id=0)

    assert Factor4ReadRepository(relational_db).metric_scope_snapshot("time_series") is None
