"""账户详情测试源数据到真实摘要接口的编排。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator
from unittest import SkipTest

from api.account_detail_api import AccountDetailAPI
from db.account_detail_repository import AccountDetailFixtureRepository


class AccountDetailSourceContractBlocked(SkipTest):
    """表示测试自造分钟快照未满足当前后端 collector 合约。

    该异常代表测试数据没有进入后端采集链，而不是接口请求失败；继承
    ``SkipTest`` 可让 pytest 正确显示为环境门禁跳过。
    """


@dataclass(frozen=True)
class AccountDetailSourceEvidence:
    """保存一条源数据流程的身份和摘要响应。"""

    fixture_id: str
    view_id: str
    account_id: int
    response_body: dict[str, Any]


class AccountDetailSourceFlow:
    """为旧 source Case 提供兼容入口；live 运行只允许持久化数据只读访问。"""

    def __init__(self, repository: AccountDetailFixtureRepository, api: AccountDetailAPI, actor_id: int) -> None:
        """保存数据库仓储、账户详情 API 和当前登录用户身份。"""

        self._repository = repository
        self._api = api
        self._actor_id = actor_id

    @contextmanager
    def execute(
        self,
        *,
        realized_profit: Decimal | None = Decimal("0"),
        net_deposit: Decimal | None = Decimal("0"),
        positions: list[dict[str, Any]] | None = None,
        snapshot_overrides: dict[str, Any] | None = None,
    ) -> Iterator[AccountDetailSourceEvidence]:
        """读取预置策略侧数据集的账户摘要。

        账户详情由策略侧源表在首次全量计算和后续增量更新时生成。直接写入
        ``quant_account_snapshots`` 或 ``quant_read_views`` 不会触发该链路，
        因此旧的 ``qad_*`` 临时 fixture 已停用；数据集初始化和清理属于独立
        seed 生命周期，Case 只使用无 snapshot 的只读接口请求。
        """

        if False:  # 保持 contextmanager 的生成器契约；不会执行任何数据操作。
            yield AccountDetailSourceEvidence("", "", 0, {})
        raise AccountDetailSourceContractBlocked(
            "BLOCKED_DATA_PRECONDITION: legacy account-detail fixture writes are disabled; "
            "use the pre-seeded strategy dataset and read-only N01 cases"
        )
