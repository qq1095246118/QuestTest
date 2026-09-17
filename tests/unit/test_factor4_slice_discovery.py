"""Execute slice-discovery SQL on isolated SQLite rows and preserve original DB errors."""

from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
import traceback
from typing import Any

import pytest

from db.client import DatabaseClient, QueryParameters
import db.factor4_read_repository as read_repository
from db.factor4_read_repository import Factor4ReadRepository, SampleDiscoveryExhausted

pytestmark = pytest.mark.unit

_SCOPE = {
    "run_id": "completed-run", "factor_id": 7, "is_sub_factor_id": 1,
    "ic_scope": "time_series", "calculation_mode": "direct",
    "factor_bar_interval": "1h", "factor_window_bars": "24",
    "return_bar_interval": "1h", "forward_return_bars": 1,
    "universe_key": "all", "window_scope": "rolling", "symbol": "BTCUSDT",
}


class _SQLiteTransaction:
    """Run repository SQL; translate only MySQL binding and null-safe equality syntax."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        """Record transaction controls; execute other SQL and propagate SQLite errors."""
        self.calls.append((sql, parameters))
        if sql.startswith(("SET TRANSACTION", "START TRANSACTION")) or sql == "ROLLBACK":
            return
        self.connection.execute(sql.replace("<=>", "IS").replace("%s", "?"), parameters)

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Return real SQL result rows; no canned candidate or predicate responses."""
        self.calls.append((sql, parameters))
        return [dict(row) for row in self.connection.execute(
            sql.replace("<=>", "IS").replace("%s", "?"), parameters)]

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Return the first real SQL result or None; SQLite failures propagate."""
        rows = self.fetch_all(sql, parameters)
        return rows[0] if rows else None


class _SQLiteDatabase:
    """Keep one isolated SQLite database alive through the repository transaction."""

    def __init__(self, transaction: _SQLiteTransaction) -> None:
        self.reader = transaction

    @contextmanager
    def transaction(self) -> Iterator[_SQLiteTransaction]:
        """Provide the real SQL adapter and propagate errors without replacement."""
        yield self.reader


@pytest.fixture
def slice_database() -> Iterator[tuple[Factor4ReadRepository, sqlite3.Connection, _SQLiteTransaction]]:
    """Create private in-memory summary/run/slice tables, then close them after each test."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    fields = ",".join(f"{name} {'INTEGER' if isinstance(value, int) else 'TEXT'}"
                      for name, value in _SCOPE.items())
    connection.executescript(f"""
        CREATE TABLE factor_ic_runs(run_id TEXT PRIMARY KEY,status TEXT);
        CREATE TABLE factor_ic_summary_metrics(id INTEGER PRIMARY KEY,{fields},slice_count INTEGER);
        CREATE TABLE factor_ic_slice_metrics(id INTEGER PRIMARY KEY,{fields},as_of_time INTEGER,
            period_start TEXT,period_end TEXT,metric_value REAL);
        INSERT INTO factor_ic_runs VALUES('completed-run','completed');
    """)
    transaction = _SQLiteTransaction(connection)
    repository = Factor4ReadRepository(_SQLiteDatabase(transaction))  # type: ignore[arg-type]
    try:
        yield repository, connection, transaction
    finally:
        connection.close()


def _insert(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    """Insert a local fixture row using bound values; only test-owned table names are used."""
    connection.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                       tuple(row.values()))


def _summary(connection: sqlite3.Connection, identifier: int, *, count: int = 8, **scope: Any) -> dict[str, Any]:
    """Persist one typed summary and return its exact scope for fixture slice rows."""
    row = {"id": identifier, **_SCOPE, **scope, "slice_count": count}
    _insert(connection, "factor_ic_summary_metrics", row)
    return row


def _slices(connection: sqlite3.Connection, summary: dict[str, Any], first_id: int, count: int) -> list[int]:
    """Persist all requested same-scope slices across periods; return their expected time order."""
    scope = {key: summary[key] for key in _SCOPE}
    for offset in range(count):
        _insert(connection, "factor_ic_slice_metrics", {
            "id": first_id + offset, **scope, "as_of_time": count - offset,
            "period_start": f"period-{count - offset}", "period_end": f"end-{count - offset}",
            "metric_value": offset / 100,
        })
    return list(reversed(range(first_id, first_id + count)))


def test_full_candidate_pages_continue_inside_same_id_window_without_skipping_later_match(
    slice_database: tuple[Factor4ReadRepository, sqlite3.Connection, _SQLiteTransaction],
) -> None:
    """A match behind two full 64-row pages remains discoverable inside the first PK window."""
    repository, connection, transaction = slice_database
    connection.execute("INSERT INTO factor_ic_runs VALUES('unfinished','running')")
    for identifier in range(9001, 9129):
        _summary(connection, identifier, run_id="unfinished")
    expected = _summary(connection, 9000)
    ids = _slices(connection, expected, 1, 8)

    selected = repository.slice_sample(symbol_mode="symbol", min_rows=8)

    assert selected is not None and selected.summary["id"] == 9000
    assert [row["id"] for row in selected.rows] == ids
    pages = [parameters for sql, parameters in transaction.calls if "SELECT m.id," in sql]
    assert len(pages) == 3
    assert [parameters[1] for parameters in pages] == [9128, 9064, 9000]


@pytest.mark.parametrize("status", ["running", "failed", "cancelled", None])
def test_latest_candidate_requires_a_real_completed_run_even_with_enough_slices(
    slice_database: tuple[Factor4ReadRepository, sqlite3.Connection, _SQLiteTransaction], status: str | None,
) -> None:
    """Pending/failed/cancelled/missing Runs never beat an older completed same-shape sample."""
    repository, connection, _ = slice_database
    if status is not None:
        connection.execute("INSERT INTO factor_ic_runs VALUES('not-completed',?)", (status,))
    newer = _summary(connection, 2, run_id="not-completed")
    _slices(connection, newer, 100, 8)
    older = _summary(connection, 1)
    expected = _slices(connection, older, 1, 8)

    selected = repository.slice_sample(min_rows=8)

    assert selected is not None and selected.summary["id"] == 1
    assert [row["id"] for row in selected.rows] == expected


def test_inflated_summary_count_does_not_replace_real_slice_count_or_stop_discovery(
    slice_database: tuple[Factor4ReadRepository, sqlite3.Connection, _SQLiteTransaction],
) -> None:
    """The newer summary claims 100 slices but has only one; the next genuine scope is returned whole."""
    repository, connection, _ = slice_database
    inflated = _summary(connection, 2, count=100, factor_id=99)
    _slices(connection, inflated, 100, 1)
    qualifying = _summary(connection, 1, count=12)
    expected = _slices(connection, qualifying, 1, 12)

    selected = repository.slice_sample(min_rows=8)

    assert selected is not None and selected.summary["id"] == 1
    assert [row["id"] for row in selected.rows] == expected
    assert len(selected.rows) == 12


def test_selected_scope_returns_every_slice_and_excludes_decoys_for_each_scope_field(
    slice_database: tuple[Factor4ReadRepository, sqlite3.Connection, _SQLiteTransaction],
) -> None:
    """All 80 matching periods survive; mismatched Run/factor/type/dimensions/window/symbol do not leak."""
    repository, connection, _ = slice_database
    selected_summary = _summary(connection, 1, count=80)
    expected = _slices(connection, selected_summary, 1, 80)
    for index, (name, value) in enumerate(_SCOPE.items()):
        changed = value + 1 if isinstance(value, int) else f"other-{value}"
        _slices(connection, {**selected_summary, name: changed}, 1000 + index, 1)

    selected = repository.slice_sample(symbol_mode="symbol", min_rows=8)

    assert selected is not None
    assert [row["id"] for row in selected.rows] == expected
    assert {row["period_start"] for row in selected.rows} == {f"period-{i}" for i in range(1, 81)}
    assert all(all(row[name] == value for name, value in _SCOPE.items()) for row in selected.rows)


def test_unvisited_id_windows_raise_budget_exhaustion_instead_of_claiming_no_sample(
    slice_database: tuple[Factor4ReadRepository, sqlite3.Connection, _SQLiteTransaction],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real match exists in an older window; a one-page budget cannot report absence."""
    repository, connection, _ = slice_database
    monkeypatch.setattr(read_repository, "_SLICE_DISCOVERY_PAGES", 1)
    monkeypatch.setattr(read_repository, "_SLICE_ID_WINDOW", 20)
    _summary(connection, 100, ic_scope="cross_sectional", symbol="")
    older = _summary(connection, 1)
    _slices(connection, older, 1, 8)

    with pytest.raises(SampleDiscoveryExhausted, match="BLOCKED_SAMPLE_DISCOVERY.*budget=1"):
        repository.slice_sample(min_rows=8)


@pytest.mark.parametrize("fixture_kind", ["empty", "other_scope", "insufficient_actual_slices"])
def test_none_requires_exhausted_natural_population_without_a_matching_sample(
    slice_database: tuple[Factor4ReadRepository, sqlite3.Connection, _SQLiteTransaction], fixture_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty table or fully scanned nonmatching population retains the no-sample return."""
    repository, connection, _ = slice_database
    monkeypatch.setattr(read_repository, "_SLICE_DISCOVERY_PAGES", 1)
    if fixture_kind == "other_scope":
        _summary(connection, 1, ic_scope="cross_sectional", symbol="")
    elif fixture_kind == "insufficient_actual_slices":
        summary = _summary(connection, 1, count=8)
        _slices(connection, summary, 1, 7)

    assert repository.slice_sample(min_rows=8) is None


class _SelectFailure(RuntimeError):
    errno = 3024


class _RollbackFailure(RuntimeError):
    errno = 2013


class _FailingConnection:
    """Fail SELECT and every cleanup layer to exercise the actual client and repository wrappers."""

    description = (("id",),)
    rowcount = 0
    lastrowid = None

    def __init__(self) -> None:
        self.calls: list[str] = []

    def cursor(self) -> "_FailingConnection":
        """Use this object as the cursor; creation itself does not fail."""
        return self

    def execute(self, sql: str, parameters: QueryParameters | None = None) -> None:
        """Raise a secret-bearing original SELECT error and distinct secondary ROLLBACK error."""
        self.calls.append(sql)
        if sql.startswith("SELECT"):
            raise _SelectFailure("select-secret-must-not-leak")
        if sql == "ROLLBACK":
            raise _RollbackFailure("rollback-secret-must-not-leak")

    def fetchone(self) -> None:
        """Unreachable after the SELECT failure; return None for the DB-API shape."""
        return None

    def fetchall(self) -> list[Any]:
        """Unreachable after the SELECT failure; return no rows for the DB-API shape."""
        return []

    def commit(self) -> None:
        """Record any accidental commit so the test can reject it."""
        self.calls.append("driver-commit")

    def rollback(self) -> None:
        """Fail the client's rollback after repository SQL rollback already failed."""
        self.calls.append("driver-rollback")
        raise _RollbackFailure("driver-rollback-secret-must-not-leak")

    def close(self) -> None:
        """Allow cursor closes during setup; fail closes once the SELECT has failed."""
        self.calls.append("close")
        if any(sql.startswith("SELECT") for sql in self.calls):
            raise RuntimeError("close-secret-must-not-leak")


def test_original_select_errno_survives_every_rollback_close_failure_without_raw_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repository reports original SELECT type/errno after rollback/close failures and hides messages."""
    connection = _FailingConnection()
    repository = Factor4ReadRepository(DatabaseClient(lambda: connection, read_only=True))

    with pytest.raises(RuntimeError) as caught:
        repository.slice_sample()

    assert str(caught.value) == "Factor4 read-only database access failed: _SelectFailure errno=3024"
    assert "ROLLBACK" in connection.calls and "driver-rollback" in connection.calls
    assert "driver-commit" not in connection.calls
    assert sum(sql.startswith("SELECT") for sql in connection.calls) == 1
    assert "secret-must-not-leak" not in "".join(traceback.format_exception(caught.value))
    assert "secret-must-not-leak" not in caplog.text
