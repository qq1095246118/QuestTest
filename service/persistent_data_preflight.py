"""三个量化页面共用的持久化测试数据前置检查。

测试数据由独立 seed 步骤预先写入策略侧表。本模块只读检查数据集，绝不在
pytest 用例前后插入或删除数据。缺少数据时抛出 ``PersistentDataBlocked``，
由测试 fixture 转换为单条 ``pytest.skip``。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable
from unittest import SkipTest

from db.persistent_dataset_repository import PersistentDatasetRepository, PersistentDatasetSnapshot


DEFAULT_DATASET_TAG = "qta_persist_20261022"


class PersistentDataBlocked(SkipTest):
    """表示持久化测试数据缺失或不满足当前 case 的源数据前置。"""


@dataclass(frozen=True)
class PersistentDataPreflight:
    """持久化数据集前置服务。

    ``snapshot`` 在测试会话中缓存一次，保证同一轮测试使用一致的数据发现结果，
    同时避免每个 case 重复查询数据库。
    """

    snapshot: PersistentDatasetSnapshot
    expected_strategy_ids: tuple[str, ...] = ()
    expected_account_ids: tuple[int, ...] = ()

    @classmethod
    def from_repository(cls, repository: PersistentDatasetRepository, dataset_tag: str) -> PersistentDataPreflight:
        """从只读 Repository 发现指定数据集。"""

        return cls(repository.inspect(dataset_tag))

    @classmethod
    def from_environment(cls, repository: PersistentDatasetRepository) -> PersistentDataPreflight:
        """按持久化数据集环境变量发现当前数据集。

        ``AUTOMATION_PERSISTENT_DATASET_ID`` 是正式变量；保留
        ``AUTOMATION_LIVE_DATASET_TAG`` 作为已有运行脚本的兼容别名。
        """

        tag = (
            os.getenv("AUTOMATION_PERSISTENT_DATASET_ID")
            or os.getenv("AUTOMATION_LIVE_DATASET_TAG")
            or DEFAULT_DATASET_TAG
        ).strip()
        if not tag:
            raise PersistentDataBlocked("BLOCKED_DATA: AUTOMATION_LIVE_DATASET_TAG is empty")
        snapshot = repository.inspect(tag)
        expected_strategies = tuple(
            value.strip()
            for value in os.getenv("AUTOMATION_LIVE_STRATEGY_IDS", "").split(",")
            if value.strip()
        )
        expected_accounts: tuple[int, ...]
        try:
            expected_accounts = tuple(
                int(value.strip())
                for value in os.getenv("AUTOMATION_LIVE_ACCOUNT_IDS", "").split(",")
                if value.strip()
            )
        except ValueError as error:
            raise PersistentDataBlocked(
                "BLOCKED_DATA: AUTOMATION_LIVE_ACCOUNT_IDS contains a non-numeric ID"
            ) from error
        return cls(snapshot, expected_strategies, expected_accounts)

    @property
    def dataset_tag(self) -> str:
        """返回数据集标签。"""

        return self.snapshot.dataset_tag

    @property
    def strategy_ids(self) -> tuple[str, ...]:
        """返回预先发现的策略主体 ID。"""

        return self.snapshot.strategy_ids

    @property
    def account_ids(self) -> tuple[int, ...]:
        """返回通过策略运行关系发现的账号 ID。"""

        return self.snapshot.account_ids

    def require(self, *capabilities: str, case_id: str | None = None) -> PersistentDatasetSnapshot:
        """要求数据集具备全部能力，否则阻断当前单条用例。

        参数 ``capabilities`` 使用 Repository 的能力名，例如 ``account_detail``、
        ``performance_samples``、``positions``、``multi_interval``、``fills``。
        返回快照供 case 读取稳定主体；缺失时异常消息包含标签、主体数量和缺失能力。
        """

        required = tuple(dict.fromkeys(capability.strip() for capability in capabilities if capability.strip()))
        missing = tuple(capability for capability in required if not self.snapshot.supports(capability))
        missing_strategies = tuple(set(self.expected_strategy_ids) - set(self.snapshot.strategy_ids))
        missing_accounts = tuple(set(self.expected_account_ids) - set(self.snapshot.account_ids))
        if missing or missing_strategies or missing_accounts:
            prefix = f"{case_id}: " if case_id else ""
            reason_code = "PERSISTENT_DATASET_IDENTITY_MISMATCH" if (missing_strategies or missing_accounts) else (
                "PERSISTENT_DATASET_NOT_PUBLISHED" if "published_generation" in missing else (
                    "PERSISTENT_DATASET_SOURCE_INCOMPLETE" if self.snapshot.missing_tables else
                    "PERSISTENT_DATASET_MISSING"
                )
            )
            raise PersistentDataBlocked(
                prefix + f"BLOCKED_DATA_PRECONDITION: {reason_code}; "
                f"tag={self.dataset_tag}, missing={','.join(missing)}, "
                f"missing_strategy_ids={','.join(missing_strategies) or 'none'}, "
                f"missing_account_ids={','.join(str(value) for value in missing_accounts) or 'none'}, "
                f"tables_missing={','.join(self.snapshot.missing_tables) or 'none'}, "
                f"strategies={len(self.snapshot.strategy_ids)}, accounts={len(self.snapshot.account_ids)}, "
                f"runs={self.snapshot.run_count}, assignments={self.snapshot.assignment_count}, "
                f"samples={self.snapshot.sample_count}, position_batches={self.snapshot.position_batch_count}, "
                f"positions={self.snapshot.position_count}, trading_events={self.snapshot.trading_event_count}, "
                f"generations={self.snapshot.generation_count}, curves={self.snapshot.curve_point_count}"
            )
        return self.snapshot

    def require_any(self, capabilities: Iterable[str], *, case_id: str | None = None) -> PersistentDatasetSnapshot:
        """要求能力集合至少命中一项，供不同部署事件类型兼容检查。"""

        required = tuple(dict.fromkeys(capability.strip() for capability in capabilities if capability.strip()))
        missing_strategies = tuple(set(self.expected_strategy_ids) - set(self.snapshot.strategy_ids))
        missing_accounts = tuple(set(self.expected_account_ids) - set(self.snapshot.account_ids))
        if (missing_strategies or missing_accounts) or not any(self.snapshot.supports(capability) for capability in required):
            prefix = f"{case_id}: " if case_id else ""
            reason_code = "PERSISTENT_DATASET_IDENTITY_MISMATCH" if (missing_strategies or missing_accounts) else "PERSISTENT_DATASET_MISSING"
            raise PersistentDataBlocked(
                prefix + f"BLOCKED_DATA_PRECONDITION: {reason_code}; "
                f"tag={self.dataset_tag}, required_any={','.join(required)}, "
                f"missing_strategy_ids={','.join(missing_strategies) or 'none'}, "
                f"missing_account_ids={','.join(str(value) for value in missing_accounts) or 'none'}, "
                f"kinds={','.join(self.snapshot.event_kinds)}"
            )
        return self.snapshot

