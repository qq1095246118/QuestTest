"""持久化量化测试数据集的只读发现。

该 Repository 只负责查询策略侧源表，不创建、更新或删除测试数据。账号管理、
账号详情和策略详情的 live case 共用同一份数据集；数据集由独立 seed 流程预先
写入，并通过 ``dataset_tag`` 在定义、运行关系和源事实中关联起来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from db.client import DatabaseClient


@dataclass(frozen=True)
class PersistentDatasetSnapshot:
    """持久化数据集的可执行能力摘要。

    ``strategy_ids`` 和 ``account_ids`` 是经过策略运行关系筛选出的可见主体；
    其余数量来自策略侧源表，供测试前置判断和 skip 诊断使用。
    """

    dataset_tag: str
    strategy_ids: tuple[str, ...]
    account_ids: tuple[int, ...]
    run_count: int
    assignment_count: int
    sample_count: int
    sample_start: datetime | None
    sample_end: datetime | None
    position_batch_count: int
    position_count: int
    trading_event_count: int
    event_kinds: tuple[str, ...]
    strategy_account_pairs: int
    account_strategy_pairs: int
    generation_count: int
    curve_point_count: int
    missing_tables: tuple[str, ...]
    capabilities: frozenset[str]

    def supports(self, capability: str) -> bool:
        """返回数据集是否满足一个命名能力。"""

        return capability in self.capabilities


class PersistentDatasetRepository:
    """发现带稳定标签的策略源数据集；所有方法均为只读。"""

    def __init__(self, client: DatabaseClient) -> None:
        """保存只读数据库客户端。"""

        self._client = client

    def inspect(self, dataset_tag: str) -> PersistentDatasetSnapshot:
        """读取指定标签的数据集并生成能力摘要。

        参数 ``dataset_tag`` 必须是非空的 seed 标签。返回只读快照；若标签下没有
        策略定义，仍返回空快照，交由测试前置层生成精确的阻断原因。
        """

        tag = dataset_tag.strip()
        if not tag:
            raise ValueError("persistent dataset tag must not be empty")
        table_rows = self._client.fetch_all(
            "SELECT TABLE_NAME AS table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        )
        available_tables = {
            str(row.get("table_name") or row.get("TABLE_NAME"))
            for row in table_rows
        }
        # These are the MySQL identity/projection tables that must exist for the
        # dashboard read path.  Position and trading fact tables are optional in
        # some deployments (their source lives in NT/PG); absence of one of
        # those tables must disable only the affected resource cases instead of
        # making every core-metric check look like a schema failure.
        required_tables = {
            "quant_strategy_definitions",
            "quant_strategy_account_runs",
            "quant_nt_account_strategy_assignments",
            "quant_strategy_performance_samples",
            "quant_segmented_performance_generations",
        }
        missing_tables = tuple(sorted(required_tables - available_tables))
        pattern = tag if tag.endswith("%") else tag + "%"
        if "quant_strategy_definitions" not in available_tables:
            return PersistentDatasetSnapshot(
                tag, (), (), 0, 0, 0, None, None, 0, 0, 0, (), 0, 0,
                0, 0, missing_tables, frozenset()
            )
        definitions = self._client.fetch_all(
            """SELECT d.id, d.status
               FROM quant_strategy_definitions AS d
               WHERE (d.id LIKE %s OR d.code LIKE %s)
               ORDER BY d.id""",
            (pattern, pattern),
        )
        strategy_ids = tuple(str(row["id"]) for row in definitions if row.get("id") is not None)
        if not strategy_ids:
            return PersistentDatasetSnapshot(
                tag, (), (), 0, 0, 0, None, None, 0, 0, 0, (), 0, 0,
                0, 0, missing_tables, frozenset()
            )

        placeholders = ",".join(["%s"] * len(strategy_ids))
        runs = self._client.fetch_all(
            f"""SELECT r.id, r.account_id, r.definition_id, r.nt_account_id,
                         r.status, r.started_at, r.ended_at
                  FROM quant_strategy_account_runs AS r
                  WHERE r.definition_id IN ({placeholders})
                  ORDER BY r.started_at, r.id""",
            strategy_ids,
        ) if "quant_strategy_account_runs" in available_tables else []
        account_ids = tuple(sorted({int(row["account_id"]) for row in runs if row.get("account_id") is not None}))
        assignment_rows = self._client.fetch_all(
            f"""SELECT a.nt_assignment_id, a.nt_strategy_id, a.nt_account_id,
                         a.effective_from, a.effective_to, a.status
                  FROM quant_nt_account_strategy_assignments AS a
                  WHERE a.nt_strategy_id IN ({placeholders})""",
            strategy_ids,
        ) if "quant_nt_account_strategy_assignments" in available_tables else []
        samples = self._client.fetch_one(
            f"""SELECT COUNT(*) AS n, MIN(s.observed_at) AS sample_start,
                         MAX(s.observed_at) AS sample_end
                  FROM quant_strategy_performance_samples AS s
                  WHERE s.definition_id IN ({placeholders})
                    AND s.data_status IN ('complete', 'partial', 'available')""",
            strategy_ids,
        ) if "quant_strategy_performance_samples" in available_tables else {}
        position_batches = self._client.fetch_one(
            f"""SELECT COUNT(*) AS n
                  FROM quant_position_snapshot_batches AS b
                  WHERE b.definition_id IN ({placeholders})""",
            strategy_ids,
        ) if "quant_position_snapshot_batches" in available_tables else {}
        position_rows = self._client.fetch_one(
            f"""SELECT COUNT(*) AS n
                  FROM quant_position_snapshots AS p
                  JOIN quant_position_snapshot_batches AS b ON b.id = p.batch_id
                  WHERE b.definition_id IN ({placeholders})""",
            strategy_ids,
        ) if {"quant_position_snapshots", "quant_position_snapshot_batches"}.issubset(available_tables) else {}
        trading_rows = self._client.fetch_all(
            f"""SELECT e.kind, COUNT(*) AS n
                  FROM quant_trading_events AS e
                  WHERE e.definition_id IN ({placeholders})
                  GROUP BY e.kind""",
            strategy_ids,
        ) if "quant_trading_events" in available_tables else []
        generations = self._client.fetch_one(
            "SELECT COUNT(*) AS n FROM quant_segmented_performance_generations "
            "WHERE source_generation LIKE %s OR CAST(payload AS CHAR) LIKE %s",
            (pattern, pattern),
        ) if "quant_segmented_performance_generations" in available_tables else {}
        curves = self._client.fetch_one(
            f"""SELECT COUNT(*) AS n FROM quant_curve_points_minute
                WHERE subject_id IN ({placeholders})""",
            strategy_ids,
        ) if "quant_curve_points_minute" in available_tables else {}
        event_kinds = tuple(sorted(str(row["kind"]) for row in trading_rows if row.get("kind")))
        trading_event_count = sum(int(row.get("n") or 0) for row in trading_rows)
        generation_count = int(generations.get("n") or 0)
        curve_point_count = int(curves.get("n") or 0)
        pair_rows = {(int(row["account_id"]), str(row["definition_id"])) for row in runs
                     if row.get("account_id") is not None and row.get("definition_id") is not None}
        by_account: dict[int, set[str]] = {}
        for account_id, strategy_id in pair_rows:
            by_account.setdefault(account_id, set()).add(strategy_id)
        by_strategy: dict[str, set[int]] = {}
        for account_id, strategy_id in pair_rows:
            by_strategy.setdefault(strategy_id, set()).add(account_id)
        strategy_account_pairs = len(pair_rows)
        account_strategy_pairs = len(pair_rows)
        run_count = len(runs)
        assignment_count = len(assignment_rows)
        sample_count = int(samples.get("n") or 0)
        position_batch_count = int(position_batches.get("n") or 0)
        position_count = int(position_rows.get("n") or 0)
        capabilities: set[str] = set()
        if account_ids:
            capabilities.add("account_detail")
        if len(account_ids) >= 2:
            capabilities.update({"account_management", "multi_account"})
        if strategy_ids:
            capabilities.add("strategy_detail")
        if sample_count:
            capabilities.add("performance_samples")
        if position_batch_count:
            capabilities.add("positions")
        if trading_event_count:
            capabilities.add("trading_events")
        if generation_count:
            capabilities.add("published_generation")
        if curve_point_count:
            capabilities.add("curves")
        if assignment_count:
            capabilities.add("assignments")
        if any(len(accounts) >= 2 for accounts in by_strategy.values()):
            capabilities.add("same_strategy_multi_account")
        if any(len(strategies) >= 2 for strategies in by_account.values()):
            capabilities.add("account_strategy_switch")
        if any(sum(1 for row in runs if row.get("account_id") == account_id) >= 2 for account_id in by_account):
            capabilities.add("multi_interval")
        if len(strategy_ids) >= 2:
            capabilities.add("multi_strategy")
        if sample_count and samples.get("sample_start") and samples.get("sample_end"):
            capabilities.add("historical_samples")
        for kind in event_kinds:
            normalized = kind.lower().replace("-", "_")
            capabilities.add(normalized)
            if any(token in normalized for token in ("signal", "rebalance", "plan")):
                capabilities.add("signals")
            if "order" in normalized or "intent" in normalized:
                capabilities.add("orders")
            if "fill" in normalized or "trade" in normalized or "execution" in normalized:
                capabilities.add("fills")
        return PersistentDatasetSnapshot(
            tag,
            strategy_ids,
            account_ids,
            run_count,
            assignment_count,
            sample_count,
            samples.get("sample_start"),
            samples.get("sample_end"),
            position_batch_count,
            position_count,
            trading_event_count,
            event_kinds,
            strategy_account_pairs,
            account_strategy_pairs,
            generation_count,
            curve_point_count,
            missing_tables,
            frozenset(capabilities),
        )
