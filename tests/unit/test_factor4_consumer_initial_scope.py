"""Real SQL counterexamples for completion-index consumer scope discovery."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3

import pytest

from db.factor4_consumer_repository import Factor4ConsumerRepository
from db.factor4_read_repository import SUMMARY_KEYS
from tests.unit.test_factor4_compact_summary_repository import SQLiteReadTransaction

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def scope_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Factor4ConsumerRepository, sqlite3.Connection]]:
    """Return isolated tables and one actual SQLite read transaction; no live connection."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(f"""
        CREATE TABLE factor_ic_runs(id INTEGER PRIMARY KEY,run_id TEXT,status TEXT,completed_at TEXT);
        CREATE TABLE factor_ic_summary_metrics(id INTEGER PRIMARY KEY,run_id TEXT,is_sub_factor_id INTEGER,
            {','.join(key+' TEXT' for key in SUMMARY_KEYS)});
        CREATE INDEX run_completion ON factor_ic_runs(status,completed_at);
        CREATE INDEX summary_run ON factor_ic_summary_metrics(run_id,is_sub_factor_id,ic_scope);
    """)
    repository = Factor4ConsumerRepository(None)  # type: ignore[arg-type]

    @contextmanager
    def snapshot() -> Iterator[SQLiteReadTransaction]:
        yield SQLiteReadTransaction(connection)

    monkeypatch.setattr(repository, "_snapshot", snapshot)
    monkeypatch.setattr(repository, "_clock", lambda _: NOW)
    try:
        yield repository, connection
    finally:
        connection.close()


def _run(connection: sqlite3.Connection, identity: int, completed: str = "2026-09-10 19:00:00", status: str = "completed") -> None:
    connection.execute("INSERT INTO factor_ic_runs VALUES(?,?,?,?)", (identity, f"run-{identity}", status, completed))


def _summary(connection: sqlite3.Connection, identity: int, run: int, *, scope: str = "time_series", symbol: str | None = "BTCUSDT", sub: int = 1) -> None:
    # A unique window identifies the selected result; no metric values exist.
    values = {key: "1" for key in SUMMARY_KEYS}
    values.update(ic_scope=scope, symbol=symbol, factor_window_bars=str(identity))
    columns = ("id", "run_id", "is_sub_factor_id", *SUMMARY_KEYS)
    connection.execute(f"INSERT INTO factor_ic_summary_metrics({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                       (identity, f"run-{run}", sub, *(values[key] for key in SUMMARY_KEYS)))


def test_latest_scope_preserves_completion_priority_and_summary_id_ties(scope_database: tuple[Factor4ConsumerRepository, sqlite3.Connection]) -> None:
    """A smaller Run id with the greatest summary id wins tied completion times; older huge ids do not."""
    repository, connection = scope_database
    _run(connection, 1)
    _run(connection, 2)
    _run(connection, 3, "2026-09-10 18:00:00")
    _summary(connection, 10, 2)
    _summary(connection, 20, 1)
    _summary(connection, 999, 3)
    sample = repository.initial_scope("ts_symbol")
    assert sample is not None and sample.scope["factor_window_bars"] == "20"
    assert sample.as_of == NOW


@pytest.mark.parametrize("shape,scope,symbol", [
    ("ts_symbol", "time_series", "BTCUSDT"),
    ("ts_aggregate", "time_series", None),
    ("cs_aggregate", "cross_sectional", ""),
])
def test_scope_preserves_pit_shape_and_typed_entity_without_population_gates(
    scope_database: tuple[Factor4ConsumerRepository, sqlite3.Connection], shape: str, scope: str, symbol: str | None,
) -> None:
    """Exactly-at-cutoff completed results qualify without metric values; future/failed/parent do not."""
    repository, connection = scope_database
    _run(connection, 1, "2026-09-10 20:00:00")
    _run(connection, 2, "2026-09-10 20:00:00.000001")
    _run(connection, 3, "2026-09-10 20:00:00", "failed")
    _summary(connection, 1, 1, scope=scope, symbol=symbol)
    _summary(connection, 2, 2, scope=scope, symbol=symbol)
    _summary(connection, 3, 3, scope=scope, symbol=symbol)
    _summary(connection, 4, 1, scope=scope, symbol=symbol, sub=0)
    sample = repository.initial_scope(shape)
    assert sample is not None and sample.scope["factor_window_bars"] == "1"


def test_completion_tie_across_pages_is_not_cut_by_run_order(scope_database: tuple[Factor4ConsumerRepository, sqlite3.Connection]) -> None:
    """The winning summary may be in the 33rd tied Run, outside the first index page."""
    repository, connection = scope_database
    for identity in range(1, 34):
        _run(connection, identity)
    _summary(connection, 1, 33)
    _summary(connection, 99, 1)
    sample = repository.initial_scope("ts_symbol")
    assert sample is not None and sample.scope["factor_window_bars"] == "99"


@pytest.mark.parametrize("matching_in_first_page", [False, True])
def test_unfinished_completion_group_is_not_absent_or_a_premature_winner(
    scope_database: tuple[Factor4ConsumerRepository, sqlite3.Connection], matching_in_first_page: bool,
) -> None:
    """Budget exhaustion cannot prove no data or return a partial tie group's smaller summary id."""
    repository, connection = scope_database
    for identity in range(1, 258):
        _run(connection, identity)
    if matching_in_first_page:
        _summary(connection, 1, 257)
    _summary(connection, 99, 1)
    with pytest.raises(RuntimeError, match="BLOCKED_SAMPLE_DISCOVERY.*checked_runs=256"):
        repository.initial_scope("ts_symbol")


def test_exhausted_scope_search_returns_none_only_when_no_matching_shape(scope_database: tuple[Factor4ConsumerRepository, sqlite3.Connection]) -> None:
    """The existing no-sample return is retained for a fully scanned visible population."""
    repository, connection = scope_database
    _run(connection, 1)
    _summary(connection, 1, 1, scope="cross_sectional", symbol="")
    assert repository.initial_scope("ts_symbol") is None
