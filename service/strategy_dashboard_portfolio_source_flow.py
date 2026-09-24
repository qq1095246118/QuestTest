"""多账户原始金额和持仓 → 部署端组合汇总 → 精确清理。"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Callable, Iterator
from uuid import uuid4

from api.strategy_dashboard_portfolio_fixture_api import PortfolioFixtureAPI
from db.strategy_dashboard_portfolio_fixture_repository import PortfolioFixtureRepository
from service.strategy_dashboard_source_fixture_flow import position_source_payload


class PortfolioSourceFlow:
    """编排隔离的多账户输入，不读取真实账户、不注入聚合输出。"""

    def __init__(self, repository: PortfolioFixtureRepository, api: PortfolioFixtureAPI,
                 actor_id: int, evidence_sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        """接收已验证环境的仓储、API 与真实登录人身份。"""
        self.repository, self.api, self.actor_id = repository, api, actor_id
        self.evidence_sink = evidence_sink

    @contextmanager
    def execute(self, accounts: list[dict[str, Any]], *, selected_indexes: list[int] | None = None,
                duplicate_members: bool = False, duplicate_account_bindings: bool = False,
                metric_source: str | None = None) -> Iterator[dict[str, Any]]:
        """提交原始账户字段；任何请求/断言错误仍清理所有自有身份和输入。

        边界从采集后账户源字段开始，不证明 NT/交易所采集器。duplicate_members
        只重复视图账户引用，不重复快照或预先消除重复；其结果由后端决定。
        """
        fixture_id = "qfi_" + uuid4().hex
        owned: list[tuple[str, int]] = []
        extra_bindings: list[tuple[str, int]] = []
        evidence: dict[str, Any] = {"fixture_id": fixture_id, "source_accounts": accounts,
                                    "boundary": "raw account source fields to deployed portfolio aggregation",
                                    "metric_source": metric_source or "default"}
        try:
            payloads = []
            for index, inputs in enumerate(accounts):
                source_id = fixture_id if index == 0 else "qfi_" + uuid4().hex
                account_id = self.repository.create_account(source_id, self.actor_id)
                owned.append((source_id, account_id))
                payload = position_source_payload(account_id, self.actor_id, source_id)
                assignment = payload["assignments"][0]
                assignment.update(Status="ended", EndedAt=payload["snapshots"][0]["ObservedAt"], Version=1,
                                  NTStrategyID="", NTAccountID="", ActiveKey=None,
                                  ResearcherID=self.actor_id, BusinessOwnerID=self.actor_id,
                                  StrategyName=source_id, CreatedAt=assignment["StartedAt"],
                                  UpdatedAt=payload["snapshots"][0]["ObservedAt"])
                payload["definitions"][0].update(ResearcherID=self.actor_id, CreatedBy=self.actor_id,
                                                 Version=1, CreatedAt=assignment["StartedAt"],
                                                 UpdatedAt=assignment["UpdatedAt"])
                self.repository.insert_ended_binding(source_id, account_id, self.actor_id,
                                                     assignment, payload["definitions"][0])
                if duplicate_account_bindings and index == 0:
                    binding_id = "qfi_" + uuid4().hex
                    extra_assignment, extra_definition = deepcopy(assignment), deepcopy(payload["definitions"][0])
                    extra_assignment.update(ID=binding_id + "_run", DefinitionID=binding_id + "_definition",
                                            StrategyName=binding_id)
                    extra_definition.update(ID=binding_id + "_definition", Code=binding_id, Name=binding_id)
                    extra_bindings.append((binding_id, account_id))
                    self.repository.insert_ended_binding(binding_id, account_id, self.actor_id,
                                                         extra_assignment, extra_definition)
                    payload["assignments"].append(extra_assignment)
                    payload["definitions"].append(extra_definition)
                raw = payload["snapshots"][0]["Payload"]
                for field in ("margin_equity", "wallet_balance", "unrealized_pnl", "margin_used",
                              "realized_pnl_gross", "net_deposit", "commission_expense", "funding_pnl"):
                    raw[field] = inputs.get(field)
                    raw["field_status"][field] = {
                        "status": "available" if raw[field] is not None else "unavailable",
                        "reason_code": None if raw[field] is not None else "SOURCE_FIELD_UNAVAILABLE",
                        "observed_at": raw["snapshot_time"],
                    }
                raw["performance_source"] = "exchange_account_performance_summaries"
                raw["performance_synced_until"] = raw["snapshot_time"]
                payloads.append(payload)
            if not owned:
                raise ValueError("An isolated portfolio fixture requires at least one owned account")
            account_ids = [value for _, value in owned]
            view_members = account_ids + account_ids[:1] if duplicate_members else account_ids
            business = {key: [] for key in payloads[0]}
            for payload in payloads:
                for key in ("accounts", "profiles", "snapshots", "assignments", "definitions"):
                    business[key].extend(payload[key])
            evidence["raw_business"] = business
            window_from = payloads[0]["assignments"][0]["StartedAt"]
            window_to = payloads[-1]["snapshots"][0]["ObservedAt"]
            self.repository.insert_portfolio_view(
                fixture_id, self.actor_id, view_members, business,
                {"minute_mode": False, "from": window_from, "to": window_to})
            evidence["owned_accounts"] = [{"fixture_id": tag, "account_id": value} for tag, value in owned]
            evidence["raw_snapshots"] = [payload["snapshots"][0] for payload in payloads]
            evidence["raw_assignments"] = business["assignments"]
            evidence["view_account_ids"] = view_members
            selected = account_ids if selected_indexes is None else [account_ids[index] for index in selected_indexes]
            evidence["summary_response"] = self.api.summary(
                fixture_id, account_ids=selected, metric_source=metric_source,
                from_at=window_from, to_at=window_to)
            yield evidence
        finally:
            cleanup_errors = []
            evidence["cleanup"] = []
            try:
                evidence["view_cleanup"] = self.repository.delete(fixture_id, self.actor_id)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
            for binding_id, account_id in extra_bindings:
                try:
                    self.repository.delete_ended_binding(binding_id, account_id, self.actor_id)
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
            for source_id, account_id in reversed(owned):
                try:
                    binding = self.repository.delete_ended_binding(source_id, account_id, self.actor_id)
                    identity = self.repository.delete_account(source_id, self.actor_id, account_id)
                    self.repository.assert_clean(source_id, account_id)
                    evidence["cleanup"].append({"fixture_id": source_id, "account_id": account_id,
                                                "binding": binding,
                                                "identity": identity, "verified_zero": True})
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
            for binding_id, account_id in extra_bindings:
                try:
                    self.repository.assert_clean(binding_id, account_id)
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
            evidence["cleanup_verified_zero"] = not cleanup_errors
            if self.evidence_sink:
                self.evidence_sink(evidence)
            if cleanup_errors:
                raise RuntimeError("Owned portfolio cleanup failed: " + ",".join(cleanup_errors))
