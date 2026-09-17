"""精确公式重放取样保持原 Python 选样规则，不读取全库目录。"""

from contextlib import contextmanager
from datetime import datetime
import sqlite3
from typing import Any, Iterator

import pytest

from db.client import DatabaseTransaction, QueryParameters
from db.factor4_formula_repository import Factor4FormulaRepository

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("has_eligible", [True, False])
def test_exact_replay_sample_selects_same_maximum_id_as_original_catalog_filter(has_eligible: bool) -> None:
    """实际执行选样 SQL，排除空表达式/错误模式/未完成 Run，保留原规则允许的空白表达式。"""
    rows = [
        (1, "completed", "direct", "close"),
        (2, "completed", "direct", "  "),
        (3, "completed", "direct", ""),
        (4, "completed", "direct", None),
        (5, "completed", "child_aggregate", "close"),
        (6, "running", "direct", "close"),
        (7, "completed", "DIRECT", "close"),
        (8, "COMPLETED", "direct", "close"),
    ]
    if not has_eligible:
        rows = rows[2:]
    expected = [row for row in rows if row[1] == "completed" and row[2] == "direct" and row[3]]
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.create_function("OCTET_LENGTH", 1, lambda value: len(value.encode()) if value is not None else None)
    connection.execute("CREATE TABLE factor_ic_runs (run_id INTEGER, status TEXT, completed_at TEXT)")
    connection.execute("CREATE TABLE factor_ic_run_formula_evidence (id INTEGER PRIMARY KEY, run_id INTEGER, calculation_mode TEXT, expression TEXT)")
    for identifier, status, mode, expression in rows:
        connection.execute("INSERT INTO factor_ic_runs VALUES (?, ?, ?)", (identifier, status, "2026-09-10"))
        connection.execute("INSERT INTO factor_ic_run_formula_evidence VALUES (?, ?, ?, ?)", (identifier, identifier, mode, expression))

    class Transaction(DatabaseTransaction):
        def fetch_one(self, query: str, parameters: QueryParameters | None = None) -> dict[str, Any] | None:
            """提供固定时钟并以 SQLite 默认精确文本比较模拟 MySQL BINARY，其余查询实际执行。"""
            if "UTC_TIMESTAMP" in query:
                return {"as_of_time": datetime(2026, 9, 10)}
            return super().fetch_one(query.replace("BINARY ", ""), parameters)

    class Repository(Factor4FormulaRepository):
        @contextmanager
        def _snapshot(self) -> Iterator[DatabaseTransaction]:
            yield Transaction(connection, read_only=True)

    try:
        sample = Repository(None).completed_direct_replay_sample()  # type: ignore[arg-type]
        assert len(sample.evidence) == int(bool(expected))
        if expected:
            assert sample.evidence[0]["id"] == max(row[0] for row in expected)
            assert sample.evidence[0]["expression"] == "  "
        assert sample.details == sample.definitions == sample.routes == ()
    finally:
        connection.close()
