"""Execute bounded summary discovery against synthetic SQLite rows; no live access."""

from collections.abc import Iterator
from contextlib import contextmanager
import json
import sqlite3
from typing import Any

import pytest

from db.factor4_auxiliary_repository import Factor4AuxiliaryRepository

pytestmark = pytest.mark.unit


class SQLiteReadTransaction:
    """Translate only parameter placeholders while executing the real query predicates."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Store the synthetic connection; no query or return value."""
        self.connection = connection

    def fetch_all(self, query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a read against SQLite and return row dictionaries; SQL errors propagate."""
        return [dict(row) for row in self.connection.execute(query.replace("%s", "?"), parameters)]

    def fetch_one(self, query: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Return the first actual SQLite result or None; SQL errors propagate."""
        rows = self.fetch_all(query, parameters)
        return rows[0] if rows else None


@pytest.fixture
def compact_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Factor4AuxiliaryRepository, sqlite3.Connection]]:
    """Provide isolated synthetic tables and a single shared read transaction; close on exit."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.create_function("JSON_UNQUOTE", 1, lambda value: value)
    connection.executescript("""
        CREATE TABLE factor_ic_runs (id INTEGER PRIMARY KEY, run_id TEXT, status TEXT, completed_at TEXT);
        CREATE TABLE factor_ic_summary_metrics (
            id INTEGER PRIMARY KEY, run_id TEXT, factor_id INTEGER, is_sub_factor_id INTEGER,
            ic_scope TEXT, metrics_json TEXT, updated_at TEXT);
        CREATE INDEX runs_completion ON factor_ic_runs(status, completed_at);
        CREATE INDEX summaries_run ON factor_ic_summary_metrics(run_id, factor_id, is_sub_factor_id, ic_scope);
        CREATE INDEX summaries_factor ON factor_ic_summary_metrics(factor_id, is_sub_factor_id, ic_scope);
    """)
    repository = Factor4AuxiliaryRepository(None)  # type: ignore[arg-type]

    @contextmanager
    def snapshot() -> Iterator[SQLiteReadTransaction]:
        yield SQLiteReadTransaction(connection)

    monkeypatch.setattr(repository, "_snapshot", snapshot)
    try:
        yield repository, connection
    finally:
        connection.close()


def _run(connection: sqlite3.Connection, identity: int, *, status: str = "completed", completed_at: str | None = "2026-09-10") -> None:
    connection.execute("INSERT INTO factor_ic_runs VALUES (?, ?, ?, ?)", (identity, f"run-{identity}", status, completed_at))


def _summary(connection: sqlite3.Connection, run: int, factor: int, *, kind: int = 1,
             scope: str = "time_series", complete_period: bool = True) -> int:
    payload = {"summary": {"period_start": "2026-08-01T00:00:00Z"}}
    if complete_period:
        payload["summary"]["period_end"] = "2026-09-01T00:00:00Z"
    cursor = connection.execute(
        "INSERT INTO factor_ic_summary_metrics(run_id,factor_id,is_sub_factor_id,ic_scope,metrics_json,updated_at) VALUES (?,?,?,?,?,?)",
        (f"run-{run}", factor, kind, scope, json.dumps(payload), "2026-09-10"),
    )
    return int(cursor.lastrowid)


@pytest.mark.parametrize("scope", ["time_series", "cross_sectional"])
def test_compact_qualification_counts_all_history_and_retains_typed_factor_identity(
    compact_database: tuple[Factor4AuxiliaryRepository, sqlite3.Connection], scope: str,
) -> None:
    """21 histories disqualify even if most are other scope/failed Run; 20 qualify separately from same-ID parent."""
    repository, connection = compact_database
    _run(connection, 2)
    _run(connection, 1, status="failed")
    _summary(connection, 2, 1, scope=scope)
    expected = _summary(connection, 2, 2, scope=scope)
    other_scope = "cross_sectional" if scope == "time_series" else "time_series"
    for _ in range(20):
        _summary(connection, 1, 1, scope=other_scope)
    for _ in range(19):
        _summary(connection, 1, 2, scope=other_scope)
    for _ in range(21):
        _summary(connection, 1, 2, kind=0, scope=scope)
    sample = repository.compact_time_summary_sample(scope)
    assert sample is not None
    assert sample["id"] == expected
    assert sample["is_sub_factor_id"] == 1


def test_compact_search_never_uses_wrong_scope_failed_run_or_missing_period(
    compact_database: tuple[Factor4AuxiliaryRepository, sqlite3.Connection],
) -> None:
    """Real but ineligible rows cannot manufacture a usable sample; exhaustive no-match returns None."""
    repository, connection = compact_database
    _run(connection, 2)
    _run(connection, 1, status="failed")
    _summary(connection, 2, 1, complete_period=False)
    _summary(connection, 2, 2, scope="cross_sectional")
    _summary(connection, 1, 1)
    assert repository.compact_time_summary_sample("time_series") is None


def test_compact_search_continues_past_first_run_batch_and_preserves_null_completion(
    compact_database: tuple[Factor4AuxiliaryRepository, sqlite3.Connection],
) -> None:
    """Equal completion timestamps and a final null timestamp must not hide an older eligible completed Run."""
    repository, connection = compact_database
    for identity in range(1, 36):
        _run(connection, identity, completed_at=None if identity == 1 else "2026-09-10")
    expected = _summary(connection, 1, 1)
    sample = repository.compact_time_summary_sample("time_series")
    assert sample is not None and sample["id"] == expected


def test_candidate_truncation_cannot_be_reported_as_no_data(
    compact_database: tuple[Factor4AuxiliaryRepository, sqlite3.Connection],
) -> None:
    """A real valid factor beyond a large Run's inspected prefix yields explicit incomplete discovery."""
    repository, connection = compact_database
    _run(connection, 1)
    for _ in range(129):
        _summary(connection, 1, 1)
    _summary(connection, 1, 2)
    with pytest.raises(RuntimeError, match="BLOCKED_SAMPLE_DISCOVERY.*candidate_rows_truncated=True"):
        repository.compact_time_summary_sample("time_series")


def test_run_budget_exhaustion_cannot_be_reported_as_no_data(
    compact_database: tuple[Factor4AuxiliaryRepository, sqlite3.Connection],
) -> None:
    """A valid older sample beyond the Run budget remains unverified, not absent."""
    repository, connection = compact_database
    for identity in range(1, 258):
        _run(connection, identity)
    _summary(connection, 1, 1)
    with pytest.raises(RuntimeError, match="BLOCKED_SAMPLE_DISCOVERY.*checked_runs=256"):
        repository.compact_time_summary_sample("time_series")


def test_factor_budget_exhaustion_cannot_be_reported_as_no_data(
    compact_database: tuple[Factor4AuxiliaryRepository, sqlite3.Connection],
) -> None:
    """Rejecting 256 large-history factors does not prove the next real compact factor absent."""
    repository, connection = compact_database
    for identity in range(1, 5):
        _run(connection, identity, status="failed" if identity == 1 else "completed")
    for factor in range(1, 257):
        _summary(connection, 4 if factor <= 128 else 3, factor)
        for _ in range(20):
            _summary(connection, 1, factor, scope="cross_sectional")
    _summary(connection, 2, 257)
    with pytest.raises(RuntimeError, match="BLOCKED_SAMPLE_DISCOVERY.*factor_budget_exhausted=True"):
        repository.compact_time_summary_sample("time_series")


@pytest.mark.parametrize("status", ["completed", "running", "failed"])
def test_exact_public_summary_lookup_preserves_row_and_run_status(
    compact_database: tuple[Factor4AuxiliaryRepository, sqlite3.Connection], status: str,
) -> None:
    """The oracle reads only the requested primary key and does not replace an unfinished sample."""
    repository, connection = compact_database
    _run(connection, 1, status=status)
    _run(connection, 2)
    expected = _summary(connection, 1, 1)
    for _ in range(30):
        _summary(connection, 2, 1, scope="cross_sectional")
    sample = repository.summary_by_id(expected)
    assert sample is not None and sample["id"] == expected
    assert sample["ic_scope"] == "time_series" and sample["_sample_run_status"] == status


def test_exact_public_summary_lookup_distinguishes_missing_row_and_missing_run(
    compact_database: tuple[Factor4AuxiliaryRepository, sqlite3.Connection],
) -> None:
    """A dangling Run remains observable while an absent summary stays absent."""
    repository, connection = compact_database
    identity = _summary(connection, 999, 1)
    sample = repository.summary_by_id(identity)
    assert sample is not None and sample["_sample_run_status"] is None
    assert repository.summary_by_id(identity + 1) is None
