"""账户详情测试源数据到真实摘要接口的编排。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator
from unittest import SkipTest
from uuid import uuid4

from api.account_detail_api import AccountDetailAPI
from db.account_detail_repository import AccountDetailFixtureRepository
from tools.http_response import read_json_object


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
    """创建自有测试输入、读取真实 N01 并负责清理。"""

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
        """准备 G/D、持仓事实并读取真实摘要；异常时仍清理全部自有资源。"""

        fixture_id = "qad_" + uuid4().hex
        view_id = "read_" + uuid4().hex
        account_id: int | None = None
        try:
            account_id = self._repository.create_account(fixture_id, self._actor_id)
            snapshot_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            payload: dict[str, Any] = {
                "currency": "USDT",
                "snapshot_time": snapshot_time,
                "margin_equity": "1000",
                "wallet_balance": "1000",
                "unrealized_pnl": "0",
                "margin_used": "0",
                "realized_pnl_gross": str(realized_profit) if realized_profit is not None else None,
                "net_deposit": str(net_deposit) if net_deposit is not None else None,
                "commission_expense": "0",
                "funding_pnl": "0",
                "performance_source": "exchange_account_performance_summaries",
            }
            if snapshot_overrides:
                payload.update(snapshot_overrides)
            prepared_positions = positions or []
            self._repository.insert_sources(fixture_id, account_id, payload, prepared_positions)
            self._repository.insert_view(fixture_id, self._actor_id, account_id, view_id)
            response = self._api.summary(
                account_id,
                snapshot_id=view_id,
                scope="lifetime",
                metric_basis="accrual",
                metrics=(
                    "realized_pnl_gross,net_deposit,cumulative_return,margin_equity,"
                    "unrealized_pnl,commission_expense,funding_pnl"
                ),
            )
            body = read_json_object(response, "account detail source summary")
            if response.status_code != 200:
                error = body.get("error") if isinstance(body.get("error"), dict) else {}
                diagnostics = {
                    key: body[key]
                    for key in ("code", "error_code", "message", "request_id")
                    if key in body
                }
                diagnostics.update({
                    f"error_{key}": error[key]
                    for key in ("code", "message", "details")
                    if key in error
                })
                if diagnostics.get("error_code") in {
                    "SNAPSHOT_EXPIRED",
                    "MINUTE_VIEW_UNAVAILABLE",
                }:
                    raise AccountDetailSourceContractBlocked(
                        "BLOCKED_CONTRACT: custom minute snapshot requires a collector-issued manifest; "
                        f"backend={diagnostics.get('error_code')}"
                    )
                raise RuntimeError(
                    f"account detail summary HTTP {response.status_code}; diagnostics={diagnostics}"
                )
            if body.get("success") is not True:
                raise RuntimeError(f"account detail summary business response failed: {body}")
            meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            context = data.get("context") if isinstance(data.get("context"), dict) else {}
            if context.get("subject_type") != "account" or str(context.get("subject_id")) != str(account_id):
                raise AssertionError(f"account detail summary identity mismatch: {context}")
            if meta.get("mock") is not False:
                raise AccountDetailSourceContractBlocked(
                    f"BLOCKED_CONTRACT: account-detail response is mock={meta.get('mock')}"
                )
            issues = meta.get("issues") if isinstance(meta.get("issues"), list) else []
            issue_codes = {
                issue.get("code")
                for issue in issues
                if isinstance(issue, dict) and issue.get("code")
            }
            if meta.get("snapshot_id") != view_id or "MINUTE_VIEW_UNAVAILABLE" in issue_codes:
                raise AccountDetailSourceContractBlocked(
                    "BLOCKED_CONTRACT: account-detail fixture was not consumed by the minute view; "
                    f"snapshot_id={meta.get('snapshot_id')}, code={next(iter(issue_codes), None)}"
                )
            yield AccountDetailSourceEvidence(fixture_id, view_id, account_id, body)
        finally:
            if account_id is not None:
                self._repository.cleanup(fixture_id, self._actor_id, account_id, view_id)
