"""组合因子刷新计算证据 Repository 的只读查询单元测试。"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from db.client import ExecutionResult
from db.factor_combo_repository import FactorComboRepository


class StubDatabaseClient:
    """记录参数化只读 SQL 并按顺序返回预置结果。"""

    def __init__(self, responses: list[Any]) -> None:
        """保存查询响应序列；每次查询消费一项响应。"""

        self.responses = list(responses)
        self.queries: list[tuple[str, tuple[Any, ...] | None]] = []

    def fetch_all(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """记录 SQL 和绑定参数，并返回下一组查询结果。"""

        self.queries.append((query, parameters))
        if not self.responses:
            raise AssertionError("no repository response remains")
        return self.responses.pop(0)


class StubCleanupTransaction:
    """记录清理事务中的查询和写操作。"""

    def __init__(self, responses: list[Any]) -> None:
        """保存清理前置查询响应并初始化 SQL 记录。"""

        self.responses = list(responses)
        self.queries: list[tuple[str, tuple[Any, ...] | None]] = []
        self.executions: list[tuple[str, tuple[Any, ...] | None]] = []

    def fetch_all(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """记录事务查询并返回下一组预置数据。"""

        self.queries.append((query, parameters))
        if not self.responses:
            raise AssertionError("no cleanup response remains")
        return self.responses.pop(0)

    def fetch_one(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> dict[str, Any] | None:
        """记录事务单行查询并返回下一条预置数据。"""

        self.queries.append((query, parameters))
        if not self.responses:
            raise AssertionError("no cleanup response remains")
        return self.responses.pop(0)

    def execute(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> ExecutionResult:
        """记录事务写 SQL 并返回一行受影响的替身结果。"""

        self.executions.append((query, parameters))
        return ExecutionResult(rowcount=1, lastrowid=None)


class StubCleanupDatabaseClient:
    """提供单个可控清理事务的数据库客户端替身。"""

    def __init__(self, transaction: StubCleanupTransaction) -> None:
        """保存要交给仓储的事务对象。"""

        self.transaction_stub = transaction

    @contextmanager
    def transaction(self) -> Iterator[StubCleanupTransaction]:
        """在测试中返回清理事务并模拟提交。"""

        yield self.transaction_stub


class TestFactorComboRefreshEvidenceRepository:
    """验证刷新证据只读查询的表、关联和参数边界。"""

    def test_calculation_query_uses_new_authoritative_summary_and_run_tables(self) -> None:
        """查询子因子计算证据时必须使用新版汇总表和 Run 主表，不得依赖废弃旧指标表。"""

        client = StubDatabaseClient([[{"run_id": "ic-refresh-801"}]])
        repository = FactorComboRepository(client, "test")  # type: ignore[arg-type]

        rows = repository.get_factor_refresh_calculation_runs(801)

        assert rows == [{"run_id": "ic-refresh-801"}], rows
        query, parameters = client.queries[0]
        assert "factor_ic_summary_metrics" in query, query
        assert "factor_ic_runs" in query, query
        assert "factor_mining_symbol_window_metric" not in query, query
        for field_name in (
            "median_ic",
            "positive_ic_rate",
            "is_icir",
            "oos_icir",
            "monotonicity_score",
            "mean_stratification",
        ):
            assert f"summary.{field_name}" in query, query
        assert parameters == (801,), parameters

    def test_validity_query_excludes_registration_snapshot_and_joins_summary_identity(self) -> None:
        """查询刷新有效性时只排除没有 summary 外键的初始快照，并读取时序/截面汇总身份。"""

        client = StubDatabaseClient([[{"id": 904, "run_id": "ic-refresh-801"}]])
        repository = FactorComboRepository(client, "test")  # type: ignore[arg-type]

        rows = repository.get_factor_refresh_validity_snapshots(801, 903)

        assert rows == [{"id": 904, "run_id": "ic-refresh-801"}], rows
        query, parameters = client.queries[0]
        assert "factor_validity_status" in query, query
        assert "factor_ic_summary_metrics" in query, query
        assert "time_series_summary_id" in query, query
        assert "cross_sectional_summary_id" in query, query
        assert "time_series_summary_id IS NOT NULL" in query, query
        assert "cross_sectional_summary_id IS NOT NULL" in query, query
        for field_name in ("universe_key", "factor_bar_interval", "factor_window_bars", "window_scope", "period_start"):
            assert f"validity.{field_name}" in query, query
        assert "factor_combo_register:%%" not in query, query
        assert parameters == (903, 801), parameters

    def test_calculation_detail_query_returns_summary_rows_and_run_status(self) -> None:
        """计算明细查询必须返回新版 summary 全字段、summary 身份和 factor_ic_runs 状态。"""

        client = StubDatabaseClient([[{"summary_id": 1001, "run_id": "ic-refresh-801"}]])
        repository = FactorComboRepository(client, "test")  # type: ignore[arg-type]

        rows = repository.get_factor_refresh_calculation_metrics(801)

        assert rows == [{"summary_id": 1001, "run_id": "ic-refresh-801"}], rows
        query, parameters = client.queries[0]
        assert "summary.*" in query, query
        assert "summary.id AS summary_id" in query, query
        assert "factor_ic_runs" in query, query
        assert "factor_mining_symbol_window_metric" not in query, query
        assert parameters == (801,), parameters

    def test_cleanup_removes_owned_refresh_rows_but_keeps_shared_run_master(self) -> None:
        """清理测试子因子的刷新明细和任务记录，但不删除没有归属字段的共享 Run 主表。"""

        transaction = StubCleanupTransaction(
            [
                [{"id": 701, "combo_id": 801, "experiment_id": 901}],
                [{"id": 41, "factor_combo_experiment_info_id": 902}],
                [{"combo_id": 801, "sub_factor_id": 1001}],
                None,
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph([41], [7])

        executed_sql = "\n".join(query for query, _ in transaction.executions)
        assert "DELETE FROM factor_ic_slice_metrics" in executed_sql, executed_sql
        assert "DELETE FROM factor_value_slice_metrics" in executed_sql, executed_sql
        assert "DELETE FROM factor_ic_summary_metrics" in executed_sql, executed_sql
        assert "DELETE FROM sub_factor_refreshes" in executed_sql, executed_sql
        assert "factor_ic_runs" not in executed_sql, executed_sql

    def test_cleanup_leaves_graph_when_refresh_is_still_active(self) -> None:
        """刷新任务仍在运行或状态未知时，清理必须保留整组业务数据。"""

        transaction = StubCleanupTransaction(
            [
                [{"id": 701, "combo_id": 801, "experiment_id": 901}],
                [{"id": 41, "factor_combo_experiment_info_id": 902}],
                [{"combo_id": 801, "sub_factor_id": 1001}],
                {"active_refresh": 1},
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph([41], [7])

        assert transaction.executions == [], transaction.executions
