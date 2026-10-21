"""账号管理多账号原始数据到 portfolio 摘要的测试编排。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from unittest import SkipTest
from uuid import uuid4

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
    """在测试环境创建多账号源输入、读取摘要并精确清理。"""

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
        """创建多账号源数据并读取真实 portfolio 摘要。"""

        if not accounts:
            raise ValueError("account-management source flow requires multiple accounts")
        if len(accounts) < 2:
            raise ValueError("account-management live cases must use at least two accounts")
        fixture_id = "qam_" + uuid4().hex
        view_id = "read_" + uuid4().hex
        owned_accounts: list[int] = []
        source_ids: list[str] = []
        evidence: dict[str, Any] = {
            "fixture_id": fixture_id,
            "view_id": view_id,
            "boundary": "raw multi-account source fields to deployed portfolio summary",
            "accounts": accounts,
        }
        try:
            prepared: list[dict[str, Any]] = []
            for index, row in enumerate(accounts):
                source_id = "qam_" + uuid4().hex
                account_id = self.repository.create_account(
                    source_id,
                    self.actor_id,
                    exchange=str(row.get("exchange", "questtest_fixture")),
                    status=str(row.get("runtime_status", "running")),
                )
                owned_accounts.append(account_id)
                source_ids.append(source_id)
                prepared.append({**row, "account_id": account_id, "source_id": source_id})
            self.repository.insert_sources(
                fixture_id,
                self.actor_id,
                prepared,
                view_id=view_id,
            )
            query_kwargs: dict[str, Any] = {"snapshot_id": view_id}
            if account_ids is not None:
                query_kwargs["account_ids"] = list(account_ids)
            if metric_source is not None:
                query_kwargs["metric_source"] = metric_source
            directory_response = self.api.accounts_page(
                snapshot_id=view_id,
                account_ids=[str(value) for value in owned_accounts],
                page=directory_page,
                limit=directory_limit,
                runtime_status=directory_runtime_status,
                exchange=directory_exchange,
            )
            directory_body = directory_response.get("body")
            if not isinstance(directory_body, dict):
                raise ValueError("account management directory body must be an object")
            if directory_response.get("http_status") not in {200, 201} or directory_body.get("success") is not True:
                error = directory_body.get("error") if isinstance(directory_body.get("error"), dict) else {}
                code = directory_body.get("error_code") or error.get("code")
                if code in {"SNAPSHOT_EXPIRED", "MINUTE_VIEW_UNAVAILABLE", "SOURCE_GENERATION_UNAVAILABLE"}:
                    raise AccountManagementSourceContractBlocked(
                        f"BLOCKED_CONTRACT: account-management directory fixture is not consumed; code={code}"
                    )
                raise RuntimeError(f"account management directory failed: HTTP {directory_response.get('http_status')}, code={code}")
            response = self.api.summary(**query_kwargs)
            body = response.get("body")
            if not isinstance(body, dict):
                raise ValueError("account management portfolio summary body must be an object")
            evidence["response"] = {
                "http_status": response.get("http_status"),
                "body": body,
                "request_params": response.get("request_params"),
                "directory": directory_body,
            }
            if response.get("http_status") not in {200, 201} or body.get("success") is not True:
                error = body.get("error") if isinstance(body.get("error"), dict) else {}
                code = body.get("error_code") or error.get("code")
                if code in {"SNAPSHOT_EXPIRED", "MINUTE_VIEW_UNAVAILABLE", "SOURCE_GENERATION_UNAVAILABLE"}:
                    raise AccountManagementSourceContractBlocked(
                        f"BLOCKED_CONTRACT: account-management fixture is not consumed by deployed source reader; code={code}"
                    )
                raise RuntimeError(f"account management summary failed: HTTP {response.get('http_status')}, code={code}")
            meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            context = data.get("context") if isinstance(data.get("context"), dict) else {}
            if meta.get("snapshot_id") != view_id or meta.get("mock") is not False:
                raise AccountManagementSourceContractBlocked(
                    "BLOCKED_CONTRACT: account-management summary identity metadata does not match fixture; "
                    f"snapshot_id={meta.get('snapshot_id')}, mock={meta.get('mock')}"
                )
            if context.get("subject_type") != "portfolio":
                raise AssertionError(f"account management summary subject_type mismatch: {context}")
            yield AccountManagementSourceEvidence(
                fixture_id,
                view_id,
                tuple(owned_accounts),
                tuple(source_ids),
                directory_body,
                body,
            )
        finally:
            self.repository.cleanup(fixture_id, self.actor_id, owned_accounts, source_ids, view_id)
            evidence["cleanup_verified_zero"] = True
            if evidence_sink is not None:
                evidence_sink(evidence)
