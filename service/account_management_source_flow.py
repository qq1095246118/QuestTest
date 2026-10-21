"""账号管理多账号原始数据到 portfolio 摘要的测试编排。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from unittest import SkipTest

from api.account_management_api import AccountManagementAPI
from db.account_management_fixture_repository import AccountManagementFixtureRepository


class AccountManagementSourceContractBlocked(SkipTest):
    """测试 fixture 尚未接入当前部署的 collector/read-view 合约。

    这是测试环境能力门禁，不是产品接口失败。继承 ``SkipTest`` 让未接入
    collector 的 fixture 在 pytest 中显示为 ``skipped``，避免把有效的页面
    请求误报为失败；真实接口响应仍由无 snapshot 的 live smoke case 覆盖。
    """


@dataclass(frozen=True)
class AccountManagementSourceEvidence:
    """保存多账号 fixture 的身份和 portfolio 响应。"""

    fixture_id: str
    view_id: str
    account_ids: tuple[int, ...]
    source_ids: tuple[str, ...]
    directory_body: dict[str, Any]
    response_body: dict[str, Any]


class AccountManagementSourceFlow:
    """兼容旧 source Case 的只读入口；持久化数据由独立 seed 生命周期管理。"""

    def __init__(
        self,
        repository: AccountManagementFixtureRepository,
        api: AccountManagementAPI,
        actor_id: int,
    ) -> None:
        """保存数据库仓储、账号管理 API 和当前测试用户身份。"""

        self.repository = repository
        self.api = api
        self.actor_id = actor_id

    @contextmanager
    def execute(
        self,
        accounts: list[dict[str, Any]],
        *,
        account_ids: list[str] | None = None,
        metric_source: str | None = None,
        directory_page: int = 1,
        directory_limit: int = 100,
        directory_runtime_status: str | None = None,
        directory_exchange: str | None = None,
        evidence_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> Iterator[AccountManagementSourceEvidence]:
        """读取预置数据集的 portfolio 摘要。

        账号管理的派生结果来自策略侧源数据。旧的 ``qam_*`` 临时账号和
        ``quant_read_views`` 只写入本地表，无法触发开发侧全量/增量计算，因而
        不再允许在 Case 中创建或清理。数据集初始化由独立 seed 流程负责；旧
        source Case 在完成持久化场景绑定前明确跳过。
        """

        if False:  # 保持 contextmanager 的生成器契约；不会执行任何数据操作。
            yield AccountManagementSourceEvidence("", "", (), (), {}, {})
        raise AccountManagementSourceContractBlocked(
            "BLOCKED_DATA_PRECONDITION: legacy account-management fixture writes are disabled; "
            "use the pre-seeded strategy dataset and read-only page cases"
        )
