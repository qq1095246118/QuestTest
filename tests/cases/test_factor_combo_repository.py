"""组合因子刷新计算证据 Repository 的只读查询单元测试。"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

import pytest

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


class StubPreparationTransaction:
    """提供测试数据准备和恢复所需的可控事务替身。"""

    def __init__(
        self,
        *,
        fetch_one_responses: list[dict[str, Any] | None],
        fetch_all_responses: list[list[dict[str, Any]]],
        execution_result: ExecutionResult | None = None,
    ) -> None:
        """保存单行查询、多行查询和写操作响应序列。"""

        self.fetch_one_responses = list(fetch_one_responses)
        self.fetch_all_responses = list(fetch_all_responses)
        self.execution_result = execution_result or ExecutionResult(rowcount=1, lastrowid=None)
        self.queries: list[tuple[str, tuple[Any, ...] | None]] = []
        self.executions: list[tuple[str, tuple[Any, ...] | None]] = []

    def fetch_one(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> dict[str, Any] | None:
        """记录查询并返回下一条预置单行结果。"""

        self.queries.append((query, parameters))
        if not self.fetch_one_responses:
            raise AssertionError("no preparation fetch_one response remains")
        return self.fetch_one_responses.pop(0)

    def fetch_all(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """记录查询并返回下一组预置多行结果。"""

        self.queries.append((query, parameters))
        if not self.fetch_all_responses:
            raise AssertionError("no preparation fetch_all response remains")
        return self.fetch_all_responses.pop(0)

    def execute(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> ExecutionResult:
        """记录写操作并返回预置执行结果。"""

        self.executions.append((query, parameters))
        return self.execution_result


class StubPreparationDatabaseClient:
    """提供查询和单事务能力的测试数据准备客户端替身。"""

    def __init__(
        self,
        *,
        fetch_one_responses: list[dict[str, Any] | None],
        transaction: StubPreparationTransaction,
    ) -> None:
        """保存 Repository 直连查询和事务替身。"""

        self.fetch_one_responses = list(fetch_one_responses)
        self.transaction_stub = transaction
        self.queries: list[tuple[str, tuple[Any, ...] | None]] = []

    def fetch_one(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> dict[str, Any] | None:
        """记录 Repository 直连查询并返回下一条预置结果。"""

        self.queries.append((query, parameters))
        if not self.fetch_one_responses:
            raise AssertionError("no preparation client response remains")
        return self.fetch_one_responses.pop(0)

    @contextmanager
    def transaction(self) -> Iterator[StubPreparationTransaction]:
        """在测试中返回准备事务并模拟提交。"""

        yield self.transaction_stub


def _cleanup_responses(
    *,
    form_rows: list[dict[str, Any]] | None = None,
    version_rows: list[dict[str, Any]] | None = None,
    component_rows: list[dict[str, Any]] | None = None,
    experiment_rows: list[dict[str, Any]] | None = None,
    metric_rows: list[dict[str, Any]] | None = None,
    registration_rows: list[dict[str, Any]] | None = None,
    external_reference: dict[str, Any] | None = None,
    active_refresh: dict[str, Any] | None = None,
) -> list[Any]:
    """构造清理事务按 Repository 查询顺序消费的替身响应。

    参数分别对应表单、组合版本、成分、实验、指标、登记、外部引用和刷新任务查询结果；未提供的集合按空列表处理。
    返回八项响应序列，供 ``StubCleanupTransaction`` 按顺序消费；该辅助函数不执行数据库操作。
    """

    return [
        form_rows or [],
        version_rows or [],
        component_rows or [],
        experiment_rows or [],
        metric_rows or [],
        registration_rows or [],
        external_reference,
        active_refresh,
    ]


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
                [{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None, "factor_combo_experiment_info_id": 902}],
                [{"id": 701, "combo_id": 801, "experiment_id": 901, "best_experiment_result_id": 902, "combo_version_hash": "hash-1"}],
                [{"id": 601, "combo_id": 701, "component_factor_id": 11, "component_sub_factor_id": 100}],
                [{"id": 901, "combo_id": 701, "metrics_id": 10001}, {"id": 902, "combo_id": 701, "metrics_id": None}],
                [{"id": 10001, "experiment_info_id": 901, "combo_id": 701}],
                [{"id": 501, "combo_id": 701, "combo_version_hash": "hash-1", "factor_id": None, "sub_factor_id": 1001, "version_id": 701}],
                None,
                None,
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        executed_sql = "\n".join(query for query, _ in transaction.executions)
        assert "DELETE FROM factor_ic_slice_metrics" in executed_sql, executed_sql
        assert "DELETE FROM factor_value_slice_metrics" in executed_sql, executed_sql
        assert "DELETE FROM factor_ic_summary_metrics" in executed_sql, executed_sql
        assert "DELETE FROM sub_factor_refreshes" in executed_sql, executed_sql
        assert "DELETE FROM factor_combo_metrics" in executed_sql, executed_sql
        assert "best_experiment_result_id = NULL" in executed_sql, executed_sql
        assert "SET metrics_id = NULL" in executed_sql, executed_sql
        assert "factor_ic_runs" not in executed_sql, executed_sql
        execution_queries = [query for query, _ in transaction.executions]
        component_delete_index = next(
            index for index, query in enumerate(execution_queries) if "DELETE FROM factor_combo_component" in query
        )
        pool_member_delete_index = next(
            index for index, query in enumerate(execution_queries) if "DELETE FROM factor_combo_pool_member" in query
        )
        sub_factor_delete_index = next(
            index for index, query in enumerate(execution_queries) if "DELETE FROM sub_factors" in query
        )
        assert component_delete_index < sub_factor_delete_index, execution_queries
        assert pool_member_delete_index < sub_factor_delete_index, execution_queries
        external_query = transaction.queries[-2][0]
        assert "factor_sub_factor_relations" in external_query, external_query
        assert "sub_factor_parent_relations" in external_query, external_query

    def test_cleanup_accepts_legacy_family_combo_identity_with_exact_version_hash(self) -> None:
        """历史记录使用组合族 ID 时，只要版本哈希和直接指针一致仍可安全清理。"""

        transaction = StubCleanupTransaction(
            _cleanup_responses(
                form_rows=[
                    {
                        "id": 41,
                        "session_id": 7,
                        "status": "completed",
                        "pipeline_run_id": None,
                        "factor_combo_experiment_info_id": 901,
                    }
                ],
                version_rows=[
                    {
                        "id": 701,
                        "combo_id": 801,
                        "experiment_id": 901,
                        "best_experiment_result_id": None,
                        "combo_version_hash": "hash-1",
                    }
                ],
                experiment_rows=[{"id": 901, "combo_id": 801, "metrics_id": 10001}],
                metric_rows=[{"id": 10001, "experiment_info_id": 901, "combo_id": 801}],
                registration_rows=[
                    {
                        "id": 501,
                        "combo_id": 801,
                        "combo_version_hash": "hash-1",
                        "factor_id": None,
                        "sub_factor_id": 1001,
                        "version_id": 701,
                    }
                ],
            )
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        executed_sql = "\n".join(query for query, _ in transaction.executions)
        assert "DELETE FROM factor_combo_registered_factor" in executed_sql, executed_sql
        assert "DELETE FROM factor_combo_metrics" in executed_sql, executed_sql

    def test_cleanup_leaves_graph_when_refresh_is_still_active(self) -> None:
        """刷新任务仍在运行或状态未知时，清理必须保留整组业务数据。"""

        transaction = StubCleanupTransaction(
            [
                [{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None, "factor_combo_experiment_info_id": 902}],
                [{"id": 701, "combo_id": 801, "experiment_id": 901, "best_experiment_result_id": 902, "combo_version_hash": "hash-1"}],
                [{"id": 601, "combo_id": 701, "component_factor_id": 11, "component_sub_factor_id": 100}],
                [{"id": 901, "combo_id": 701, "metrics_id": 10001}, {"id": 902, "combo_id": 701, "metrics_id": None}],
                [{"id": 10001, "experiment_info_id": 901, "combo_id": 701}],
                [{"id": 501, "combo_id": 701, "combo_version_hash": "hash-1", "factor_id": None, "sub_factor_id": 1001, "version_id": 701}],
                None,
                {"active_refresh": 1},
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions

    def test_cleanup_refuses_form_assigned_to_another_session(self) -> None:
        """数据库中的表单会话归属与 Scope 不一致时，清理必须拒绝并且不执行写操作。"""

        transaction = StubCleanupTransaction(
            [
                [{"id": 41, "session_id": 99, "status": "completed", "pipeline_run_id": None}],
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="session does not match"):
            repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions

    def test_cleanup_keeps_session_when_an_untracked_form_remains(self) -> None:
        """目标表单删除后同一会话仍有未登记表单时，只清理目标图，不删除会话和消息。"""

        transaction = StubCleanupTransaction(
            [
                [
                    {"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None, "factor_combo_experiment_info_id": 902},
                    {"id": 42, "session_id": 7, "status": "completed", "pipeline_run_id": None, "factor_combo_experiment_info_id": None},
                ],
                [{"id": 701, "combo_id": 801, "experiment_id": 901, "best_experiment_result_id": 902, "combo_version_hash": "hash-1"}],
                [{"id": 601, "combo_id": 701, "component_factor_id": 11, "component_sub_factor_id": 100}],
                [{"id": 901, "combo_id": 701, "metrics_id": 10001}, {"id": 902, "combo_id": 701, "metrics_id": None}],
                [{"id": 10001, "experiment_info_id": 901, "combo_id": 701}],
                [{"id": 501, "combo_id": 701, "combo_version_hash": "hash-1", "factor_id": None, "sub_factor_id": 1001, "version_id": 701}],
                None,
                None,
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        executed_sql = "\n".join(query for query, _ in transaction.executions)
        assert "DELETE FROM factor_combo_form" in executed_sql, executed_sql
        assert "DELETE FROM chat_messages" not in executed_sql, executed_sql
        assert "DELETE FROM chat_sessions" not in executed_sql, executed_sql

    def test_cleanup_keeps_graph_when_pipeline_run_is_not_terminal(self) -> None:
        """表单仍携带运行中的 Pipeline 时，即使没有刷新记录也必须保留整组数据。"""

        transaction = StubCleanupTransaction(
            [
                [
                    {
                        "id": 41,
                        "session_id": 7,
                        "status": "processing",
                        "pipeline_run_id": "combo-41-abcdef0123456789",
                        "factor_combo_experiment_info_id": None,
                    }
                ],
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions

    def test_cleanup_keeps_graph_when_sub_factor_has_external_registration(self) -> None:
        """生成子因子被其他登记记录引用时，清理不得删除共享实体。"""

        transaction = StubCleanupTransaction(
            [
                [{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                [{"id": 701, "combo_id": 801, "experiment_id": 901, "best_experiment_result_id": None, "combo_version_hash": "hash-1"}],
                [{"id": 601, "combo_id": 701, "component_factor_id": 11, "component_sub_factor_id": 100}],
                [{"id": 901, "combo_id": 701, "metrics_id": None}],
                [],
                [
                    {"id": 501, "combo_id": 701, "combo_version_hash": "hash-1", "factor_id": None, "sub_factor_id": 1001, "version_id": 701},
                ],
                {"external_reference": 1},
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions

    def test_cleanup_keeps_graph_when_experiment_has_external_version_reference(self) -> None:
        """组合实验被当前 Scope 外的版本引用时，清理不得删除共享实验记录。"""

        transaction = StubCleanupTransaction(
            [
                [{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                [{"id": 701, "combo_id": 801, "experiment_id": 901, "best_experiment_result_id": None, "combo_version_hash": "hash-1"}],
                [{"id": 601, "combo_id": 701, "component_factor_id": 11, "component_sub_factor_id": 100}],
                [{"id": 901, "combo_id": 701, "metrics_id": None}],
                [],
                [],
                {"external_reference": 1},
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions

    def test_cleanup_keeps_graph_when_version_identity_is_incomplete(self) -> None:
        """组合版本缺少版本哈希时，清理不得猜测登记归属。"""

        transaction = StubCleanupTransaction(
            [
                [{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                [{"id": 701, "combo_id": 801, "experiment_id": 901, "combo_version_hash": None}],
            ]
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions

    def test_cleanup_keeps_graph_when_experiment_record_is_missing(self) -> None:
        """组合版本指针指向不存在的实验时，清理必须保留整组资源。"""

        transaction = StubCleanupTransaction(
            _cleanup_responses(
                form_rows=[{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                version_rows=[
                    {
                        "id": 701,
                        "combo_id": 801,
                        "experiment_id": 901,
                        "best_experiment_result_id": None,
                        "combo_version_hash": "hash-1",
                    }
                ],
                component_rows=[],
                experiment_rows=[],
            )
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions
        assert len(transaction.queries) == 4

    def test_cleanup_keeps_graph_when_experiment_metric_record_is_missing(self) -> None:
        """实验声明的指标记录不存在时，清理必须保留整组资源。"""

        transaction = StubCleanupTransaction(
            _cleanup_responses(
                form_rows=[{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                version_rows=[
                    {
                        "id": 701,
                        "combo_id": 801,
                        "experiment_id": 901,
                        "best_experiment_result_id": None,
                        "combo_version_hash": "hash-1",
                    }
                ],
                component_rows=[],
                experiment_rows=[{"id": 901, "combo_id": 701, "metrics_id": 10001}],
                metric_rows=[],
            )
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions
        assert len(transaction.queries) == 5

    def test_cleanup_keeps_graph_when_best_experiment_has_external_reference(self) -> None:
        """仅由 best_experiment_result_id 产生的外部引用也必须阻止清理。"""

        transaction = StubCleanupTransaction(
            _cleanup_responses(
                form_rows=[{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                version_rows=[
                    {
                        "id": 701,
                        "combo_id": 801,
                        "experiment_id": 901,
                        "best_experiment_result_id": 902,
                        "combo_version_hash": "hash-1",
                    }
                ],
                component_rows=[],
                experiment_rows=[
                    {"id": 901, "combo_id": 701, "metrics_id": None},
                    {"id": 902, "combo_id": 701, "metrics_id": None},
                ],
                external_reference={"external_reference": 1},
            )
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions
        external_query = transaction.queries[-1][0]
        assert "best_experiment_result_id" in external_query, external_query

    def test_cleanup_keeps_graph_when_generated_sub_factor_is_used_as_parent(self) -> None:
        """生成子因子被其他子因子作为父级使用时，清理必须保留整组资源。"""

        transaction = StubCleanupTransaction(
            _cleanup_responses(
                form_rows=[{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                version_rows=[
                    {
                        "id": 701,
                        "combo_id": 801,
                        "experiment_id": 901,
                        "best_experiment_result_id": None,
                        "combo_version_hash": "hash-1",
                    }
                ],
                component_rows=[{"id": 601, "combo_id": 701, "component_factor_id": 11, "component_sub_factor_id": 100}],
                experiment_rows=[{"id": 901, "combo_id": 701, "metrics_id": None}],
                registration_rows=[
                    {
                        "id": 501,
                        "combo_id": 701,
                        "combo_version_hash": "hash-1",
                        "factor_id": None,
                        "sub_factor_id": 1001,
                        "version_id": 701,
                    }
                ],
                external_reference={"external_reference": 1},
            )
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions
        external_query = transaction.queries[-1][0]
        assert "sub_factor_parent_relations" in external_query, external_query

    def test_cleanup_keeps_graph_when_feedback_without_form_references_version(self) -> None:
        """form_id 为 NULL 的反馈引用组合版本时，不能把它当作当前表单的内部记录删除。"""

        transaction = StubCleanupTransaction(
            _cleanup_responses(
                form_rows=[{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                version_rows=[
                    {
                        "id": 701,
                        "combo_id": 801,
                        "experiment_id": None,
                        "best_experiment_result_id": None,
                        "combo_version_hash": "hash-1",
                    }
                ],
                external_reference={"external_reference": 1},
            )
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions
        external_query = transaction.queries[-1][0]
        assert "form_id IS NULL" in external_query, external_query

    def test_cleanup_keeps_graph_when_metric_is_referenced_by_external_experiment(self) -> None:
        """目标指标被其他实验引用时，清理必须保留实验和指标。"""

        transaction = StubCleanupTransaction(
            _cleanup_responses(
                form_rows=[{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                version_rows=[
                    {
                        "id": 701,
                        "combo_id": 801,
                        "experiment_id": 901,
                        "best_experiment_result_id": None,
                        "combo_version_hash": "hash-1",
                    }
                ],
                experiment_rows=[{"id": 901, "combo_id": 701, "metrics_id": 10001}],
                metric_rows=[{"id": 10001, "experiment_info_id": 901, "combo_id": 701}],
                external_reference={"external_reference": 1},
            )
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions
        external_query = transaction.queries[-1][0]
        assert "metrics_id IN" in external_query, external_query

    def test_cleanup_keeps_graph_when_registration_identity_does_not_match_version(self) -> None:
        """登记记录的版本主键或哈希不匹配时，清理不得猜测生成子因子归属。"""

        transaction = StubCleanupTransaction(
            _cleanup_responses(
                form_rows=[{"id": 41, "session_id": 7, "status": "completed", "pipeline_run_id": None}],
                version_rows=[
                    {
                        "id": 701,
                        "combo_id": 801,
                        "experiment_id": 901,
                        "best_experiment_result_id": None,
                        "combo_version_hash": "hash-1",
                    }
                ],
                experiment_rows=[{"id": 901, "combo_id": 701, "metrics_id": None}],
                registration_rows=[
                    {
                        "id": 501,
                        "combo_id": 701,
                        "combo_version_hash": "wrong-hash",
                        "factor_id": None,
                        "sub_factor_id": 1001,
                        "version_id": None,
                    }
                ],
            )
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions

    def test_cleanup_keeps_graph_when_factor_pool_has_external_references(self) -> None:
        """因子池被当前 Scope 外的组合版本、池成员或表单引用时，清理必须保留整组资源。"""

        transaction = StubCleanupTransaction(
            _cleanup_responses(
                form_rows=[
                    {
                        "id": 41,
                        "session_id": 7,
                        "status": "completed",
                        "pipeline_run_id": None,
                        "factor_combo_pool_id": 88,
                        "factor_combo_experiment_info_id": None,
                    }
                ],
                version_rows=[
                    {
                        "id": 701,
                        "combo_id": 801,
                        "pool_id": 88,
                        "experiment_id": None,
                        "best_experiment_result_id": None,
                        "combo_version_hash": "hash-1",
                    }
                ],
                metric_rows=[],
                registration_rows=[],
                external_reference={"external_reference": 1},
            )
        )
        repository = FactorComboRepository(StubCleanupDatabaseClient(transaction), "test")  # type: ignore[arg-type]

        repository.clean_test_graph({7: {41}})

        assert transaction.executions == [], transaction.executions
        external_query, external_parameters = next(
            (query, parameters)
            for query, parameters in transaction.queries
            if "factor_combo_pool_member" in query
        )
        assert "FROM factor_combo" in external_query, external_query
        assert "pool_id IN" in external_query, external_query
        assert "FROM factor_combo_pool_member" in external_query, external_query
        assert "factor_combo_form_id NOT IN" in external_query, external_query
        assert "factor_combo_pool_id IN" in external_query, external_query
        assert external_parameters is not None, external_query
        assert external_query.count("%s") == len(external_parameters), (
            external_query,
            external_parameters,
        )
        assert 88 in external_parameters, external_parameters
        assert 701 in external_parameters, external_parameters
        assert 41 in external_parameters, external_parameters


class TestFactorComboTestDataPreparationRepository:
    """验证计划内负向场景的数据准备不会依赖可选数据库记录。"""

    def test_temporarily_detach_pool_member_restores_the_same_snapshot(self) -> None:
        """临时移出池成员后，无论接口调用块如何结束都恢复原成员字段。"""

        member = {
            "id": 501,
            "factor_combo_form_id": 41,
            "pool_id": 88,
            "form_pool_id": 88,
            "sub_factor_id": 1001,
            "factor_detail_id": 2001,
            "metrics_snapshot_json": {"score": 0.5},
            "validity_snapshot_json": {"valid": True},
            "created_by": 7,
            "created_at": "2026-08-20 10:00:00",
            "updated_by": 7,
            "updated_at": "2026-08-20 10:00:00",
            "definition_snapshot_json": {"name": "sub-factor"},
            "sort_order": 1,
        }
        transaction = StubPreparationTransaction(
            fetch_one_responses=[member, None],
            fetch_all_responses=[],
        )
        repository = FactorComboRepository(
            StubPreparationDatabaseClient(fetch_one_responses=[], transaction=transaction),
            "test",
        )  # type: ignore[arg-type]

        with repository.temporarily_detach_pool_member(41, 1001) as snapshot:
            assert snapshot.row == member, snapshot.row
            assert transaction.executions[0][0].startswith("DELETE FROM factor_combo_pool_member"), (
                transaction.executions
            )

        assert len(transaction.executions) == 2, transaction.executions
        restore_sql, restore_parameters = transaction.executions[1]
        assert restore_sql.strip().startswith("INSERT INTO factor_combo_pool_member"), restore_sql
        assert restore_parameters is not None, restore_sql
        assert '{"score":0.5}' in restore_parameters, restore_parameters
        assert '{"valid":true}' in restore_parameters, restore_parameters

    def test_temporarily_detach_does_not_duplicate_a_member_recreated_by_api(self) -> None:
        """接口已经用相同表单恢复成员时，退出上下文不得插入重复行且要还原快照。"""

        member = {
            "id": 501,
            "factor_combo_form_id": 41,
            "pool_id": 88,
            "form_pool_id": 88,
            "sub_factor_id": 1001,
            "factor_detail_id": 2001,
            "created_by": 7,
            "updated_by": 7,
        }
        transaction = StubPreparationTransaction(
            fetch_one_responses=[member, {"id": 777, "factor_combo_form_id": 41}],
            fetch_all_responses=[],
        )
        repository = FactorComboRepository(
            StubPreparationDatabaseClient(fetch_one_responses=[], transaction=transaction),
            "test",
        )  # type: ignore[arg-type]

        with repository.temporarily_detach_pool_member(41, 1001):
            pass

        assert len(transaction.executions) == 2, transaction.executions
        assert transaction.executions[1][0].strip().startswith("UPDATE factor_combo_pool_member"), (
            transaction.executions
        )

    def test_temporarily_detach_restores_when_interface_block_raises(self) -> None:
        """接口调用块抛出异常时，临时移出的成员仍必须恢复。"""

        member = {
            "id": 501,
            "factor_combo_form_id": 41,
            "pool_id": 88,
            "form_pool_id": 88,
            "sub_factor_id": 1001,
            "factor_detail_id": 2001,
            "created_by": 7,
            "updated_by": 7,
        }
        transaction = StubPreparationTransaction(
            fetch_one_responses=[member, None],
            fetch_all_responses=[],
        )
        repository = FactorComboRepository(
            StubPreparationDatabaseClient(fetch_one_responses=[], transaction=transaction),
            "test",
        )  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="simulated API failure"):
            with repository.temporarily_detach_pool_member(41, 1001):
                raise RuntimeError("simulated API failure")

        assert len(transaction.executions) == 2, transaction.executions
        assert transaction.executions[1][0].strip().startswith("INSERT INTO factor_combo_pool_member"), (
            transaction.executions
        )

    def test_unrelated_parent_is_cloned_with_unique_identity_and_tracked(self) -> None:
        """找不到真实不相关母因子时，临时母因子应克隆当前 schema 并登记归属。"""

        source = {
            "id": 11,
            "factor_theme": "existing-theme",
            "parent_factor_id": None,
            "parent_factor": None,
            "parent_factor_name": None,
            "factor_name": "existing-factor",
            "factor_tags": "tag",
            "level": 2,
            "child_factor_count": 9,
            "created_by": "real-user",
            "created_by_uid": 7,
            "operator_by": "real-user",
            "operator_by_uid": 7,
            "metadata": {"source": True},
            "created_at": "2026-08-20 10:00:00",
            "updated_at": "2026-08-20 10:00:00",
            "serial_number": "old-serial",
            "serial_prefix": "old",
            "cn_name": "existing-cn-name",
            "max_level": 2,
            "latest_status_updated_at": "2026-08-20 10:00:00",
            "mining_method": "manual",
            "strategy": "old-strategy",
            "data_source": "old-source",
            "evaluation_method": "old-evaluation",
        }
        schema = [
            {"Field": column, "Extra": ""}
            for column in source
        ]
        transaction = StubPreparationTransaction(
            fetch_one_responses=[source],
            fetch_all_responses=[schema],
            execution_result=ExecutionResult(rowcount=1, lastrowid=701),
        )
        client = StubPreparationDatabaseClient(
            fetch_one_responses=[None],
            transaction=transaction,
        )
        repository = FactorComboRepository(client, "test")  # type: ignore[arg-type]

        factor_id = repository.ensure_unrelated_parent_factor_for_test(41, 1001, 11)

        assert factor_id == 701
        assert repository._test_parent_factor_ids_by_form == {41: {701}}
        insert_sql, insert_parameters = transaction.executions[0]
        assert "INSERT INTO factors" in insert_sql, insert_sql
        assert insert_parameters is not None, insert_sql
        assert any(
            isinstance(value, str) and value.startswith("__questtest_unrelated_parent__")
            for value in insert_parameters
        ), insert_parameters
        assert "existing-factor" not in insert_parameters, insert_parameters
        assert "old-serial" not in insert_parameters, insert_parameters

    def test_unrelated_parent_cleanup_uses_literal_prefix_and_deletes_unreferenced_row(self) -> None:
        """清理临时母因子时按字面前缀匹配，并只删除没有外部引用的记录。"""

        transaction = StubPreparationTransaction(
            fetch_one_responses=[{"id": 701, "factor_name": "__questtest_unrelated_parent__abc"}, None],
            fetch_all_responses=[],
        )
        repository = FactorComboRepository(
            StubPreparationDatabaseClient(fetch_one_responses=[], transaction=transaction),
            "test",
        )  # type: ignore[arg-type]
        repository._test_parent_factor_ids_by_form[41] = {701}

        cleaned = repository._clean_test_parent_factors(transaction, [41])

        assert cleaned == {701}
        assert len(transaction.executions) == 1, transaction.executions
        cleanup_query, cleanup_parameters = transaction.queries[0]
        assert "LEFT(factor_name" in cleanup_query, cleanup_query
        assert cleanup_parameters == (701, "__questtest_unrelated_parent__", "__questtest_unrelated_parent__")
