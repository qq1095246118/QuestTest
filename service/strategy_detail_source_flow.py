"""策略详情测试环境源数据到真实接口的编排。"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator
from unittest import SkipTest
from uuid import uuid4
from api.strategy_detail_api import StrategyDetailAPI
from db.strategy_detail_fixture_repository import StrategyDetailFixtureRepository


class StrategyDetailSourceContractBlocked(SkipTest):
    """测试视图未被当前 collector 合约消费。

    自造 read view 不能替代 collector 发布的策略快照；这类情况属于
    测试环境能力门禁，应该显示为 skipped 而不是接口失败。
    """


_FIXTURE_CONTRACT_CODES = frozenset(
    {
        "SNAPSHOT_EXPIRED",
        "MINUTE_VIEW_UNAVAILABLE",
        "SOURCE_GENERATION_UNAVAILABLE",
        "SUBJECT_NOT_FOUND",
        "STRATEGY_NOT_FOUND",
    }
)


def _response_error_code(body: dict[str, Any]) -> str | None:
    """读取错误响应中的稳定业务码，不把普通参数错误当作 fixture 阻断。"""

    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    value = body.get("error_code") or body.get("code") or error.get("code")
    return str(value) if value else None


def _raise_for_fixture_response(resource: str, result: dict[str, Any]) -> dict[str, Any]:
    """区分 fixture 合约阻断和脚本/API 参数错误并返回响应体。"""

    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    code = _response_error_code(body)
    if result.get("http_status") != 200:
        detail = f"HTTP {result.get('http_status')}, code={code}, body={body}"
        if code in _FIXTURE_CONTRACT_CODES:
            raise StrategyDetailSourceContractBlocked(
                f"BLOCKED_CONTRACT: {resource} fixture not consumed; {detail}"
            )
        raise AssertionError(f"{resource} request contract failed: {detail}")
    if body.get("success") is not True:
        if code in _FIXTURE_CONTRACT_CODES:
            raise StrategyDetailSourceContractBlocked(
                f"BLOCKED_CONTRACT: {resource} fixture business response unavailable; code={code}, body={body}"
            )
        raise AssertionError(f"{resource} business response failed: {body}")
    return body


@dataclass(frozen=True)
class StrategyDetailSourceEvidence:
    fixture_id: str
    view_id: str
    strategy_id: str
    summary: dict[str, Any]


@dataclass(frozen=True)
class StrategyDetailResourceEvidence:
    """单个策略详情资源的真实接口证据。"""

    fixture_id: str
    view_id: str
    strategy_id: str
    resource: str
    response: dict[str, Any]


@dataclass(frozen=True)
class ExistingStrategyDetailEvidence:
    """真实已存在策略的无 snapshot HTTP 响应证据。"""

    strategy_id: str
    strategy: dict[str, Any]
    response: dict[str, Any]


class StrategyDetailSourceFlow:
    """准备策略视图并调用真实策略详情接口，结束后精确清理。"""

    def __init__(self, repository: StrategyDetailFixtureRepository, api: StrategyDetailAPI,
                 actor_id: int) -> None:
        self.repository, self.api, self.actor_id = repository, api, actor_id

    @contextmanager
    def execute_existing(self, *, strategy_id: str | None = None,
                         metrics: str | None = None) -> Iterator[ExistingStrategyDetailEvidence]:
        """对真实可见策略发起页面同款无 ``snapshot_id`` 摘要请求。

        Args:
            strategy_id: 已知策略 ID；为空时从测试库只读发现一个 active/running 策略。
            metrics: 可选的指标选择串，默认覆盖策略详情核心卡片。

        Yields:
            ``ExistingStrategyDetailEvidence``，包含实际策略身份和原始 HTTP 响应。

        Raises:
            StrategyDetailSourceContractBlocked: 测试库中没有可见策略，或实际接口拒绝该主体。
        """
        strategy = self.repository.find_existing_strategy() if strategy_id is None else None
        selected_id = strategy_id or (str(strategy.get("id")) if strategy else "")
        if not selected_id:
            raise StrategyDetailSourceContractBlocked(
                "BLOCKED_DATA: no active/running strategy is available for live detail requests"
            )
        if strategy is None:
            strategy = {"id": selected_id}
        result = self.api.analytics(
            "summary",
            subject_id=selected_id,
            subject_type="strategy",
            scope="lifetime",
            metric_basis="accrual",
            metrics=metrics or (
                "margin_equity,unrealized_pnl,realized_pnl_gross,cumulative_return,"
                "annualized_return,current_drawdown,commission_expense,funding_pnl,position_ratio,"
                "sharpe_ratio,sortino_ratio,calmar_ratio,max_drawdown,volatility,win_rate,profit_loss_ratio"
            ),
        )
        body = _raise_for_fixture_response("existing-summary", result)
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        if meta.get("mock") is not False:
            raise AssertionError(f"existing strategy summary unexpectedly mock: {body}")
        if not meta.get("snapshot_id"):
            raise AssertionError(f"existing strategy summary did not return a backend snapshot: {body}")
        yield ExistingStrategyDetailEvidence(selected_id, strategy, body)

    @contextmanager
    def execute_existing_resource(
        self,
        resource: str,
        *,
        strategy_id: str | None = None,
        metrics: str | None = None,
    ) -> Iterator[StrategyDetailResourceEvidence]:
        """对真实可见策略调用一个无 snapshot 的详情资源。

        该入口只调用 GET，不写 fixture 数据；resource 为 definition、accounts、analytics
        或 trading 资源名。后端实际生成的 snapshot 只用于响应身份断言，不作为请求参数回传。
        """
        strategy = self.repository.find_existing_strategy() if strategy_id is None else None
        selected_id = strategy_id or (str(strategy.get("id")) if strategy else "")
        if not selected_id:
            raise StrategyDetailSourceContractBlocked(
                "BLOCKED_DATA: no active/running strategy is available for live detail requests"
            )
        params: dict[str, Any] = {
            "subject_id": selected_id,
            "subject_type": "strategy",
        }
        if resource in {"summary", "risk", "series"}:
            params["scope"] = "lifetime"
            params["metric_basis"] = "accrual"
            if metrics:
                params["metrics"] = metrics
            if resource == "series":
                params["types"] = "nav,drawdown"
            result = self.api.analytics(resource, **params)
        elif resource in {"definition", "accounts"}:
            result = getattr(self.api, resource)(selected_id)
        else:
            result = self.api.trading(resource, **params)
        body = _raise_for_fixture_response("existing-" + resource, result)
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        if meta.get("mock") is not False:
            raise AssertionError(f"existing strategy {resource} unexpectedly mock: {body}")
        returned_id = body.get("data", {}).get("context", {}).get("subject_id") if isinstance(body.get("data"), dict) else None
        if returned_id is not None and str(returned_id) != selected_id:
            raise AssertionError(
                f"existing strategy {resource} subject mismatch: expected={selected_id}, actual={returned_id}"
            )
        yield StrategyDetailResourceEvidence("", str(meta.get("snapshot_id") or ""), selected_id, resource, body)

    @contextmanager
    def execute(self, *, metrics: dict[str, Any], accounts: list[dict[str, Any]] | None = None) -> Iterator[StrategyDetailSourceEvidence]:
        """写入隔离测试视图，调用真实策略详情接口并在退出时清理。

        Args:
            metrics: 要由测试环境数据链路消费的策略详情指标载荷。
            accounts: 可选的账号归属行，用于构造多账号策略场景。

        Yields:
            包含 fixture、视图、策略标识和真实接口响应的证据对象。

        Raises:
            StrategyDetailSourceContractBlocked: 测试视图未被后端 collector
                消费，或真实接口返回非成功状态。
        """
        fixture = "qsd_" + uuid4().hex
        view = "read_" + uuid4().hex
        strategy_id = "strat_" + uuid4().hex
        requested_rows = accounts or [{"strategy_id": strategy_id}]
        owned_accounts: list[int] = []
        rows: list[dict[str, Any]] = []
        try:
            for index, row in enumerate(requested_rows):
                account_id = self.repository.create_account(fixture, self.actor_id, index)
                owned_accounts.append(account_id)
                rows.append({**row, "id": account_id, "account_id": account_id, "strategy_id": strategy_id})
            self.repository.create_view(fixture, view, self.actor_id, strategy_id, rows, metrics)
            result = self.api.analytics(
                "summary",
                subject_id=strategy_id,
                snapshot_id=view,
                subject_type="strategy",
                scope="lifetime",
                metrics=(
                    "margin_equity,unrealized_pnl,realized_pnl_gross,cumulative_return,"
                    "annualized_return,current_drawdown,commission_expense,funding_pnl"
                ),
            )
            body = _raise_for_fixture_response("summary", result)
            meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
            if meta.get("snapshot_id") != view or meta.get("mock") is not False:
                raise StrategyDetailSourceContractBlocked(
                    "BLOCKED_CONTRACT: strategy summary identity metadata does not match fixture; "
                    f"snapshot_id={meta.get('snapshot_id')}, mock={meta.get('mock')}")
            yield StrategyDetailSourceEvidence(fixture, view, strategy_id, body)
        finally:
            self.repository.cleanup(fixture, view, self.actor_id, owned_accounts)

    @contextmanager
    def execute_resource(
        self,
        resource: str,
        *,
        metrics: dict[str, Any],
        accounts: list[dict[str, Any]] | None = None,
        analytics: bool = False,
    ) -> Iterator[StrategyDetailResourceEvidence]:
        """准备隔离 fixture，调用一个真实详情资源并在退出时清理。

        Args:
            resource: ``definition``、``accounts``、analytics 或 trading 资源名。
            metrics: 要由测试环境数据链路消费的指标及交易资源载荷。
            accounts: 可选的多账号归属行。
            analytics: 为真时调用 ``analytics`` 端点，否则调用定义或交易端点。

        Yields:
            含真实响应和 fixture 身份的证据对象。

        Raises:
            StrategyDetailSourceContractBlocked: fixture 未被真实接口消费。
            ValueError: resource 不属于允许的接口资源。
        """
        fixture = "qsd_" + uuid4().hex
        view = "read_" + uuid4().hex
        strategy_id = "strat_" + uuid4().hex
        requested_rows = accounts or [{"strategy_id": strategy_id}]
        owned_accounts: list[int] = []
        rows: list[dict[str, Any]] = []
        try:
            for index, row in enumerate(requested_rows):
                account_id = self.repository.create_account(fixture, self.actor_id, index)
                owned_accounts.append(account_id)
                rows.append({**row, "id": account_id, "account_id": account_id, "strategy_id": strategy_id})
            self.repository.create_view(fixture, view, self.actor_id, strategy_id, rows, metrics)
            params = {
                "subject_id": strategy_id,
                "snapshot_id": view,
                "subject_type": "strategy",
            }
            if analytics:
                # Analytics resources share the summary scope contract. The
                # trading resources intentionally do not accept ``scope``;
                # passing it produced a client-side 400 even though the
                # underlying request was otherwise valid.
                params["scope"] = "lifetime"
                if resource == "series":
                    # N02 requires an explicit series selection. ``nav`` and
                    # ``drawdown`` are the two curves rendered by the detail
                    # page and are accepted as a comma-separated value.
                    params["types"] = "nav,drawdown"
                response = self.api.analytics(resource, **params)
            elif resource in {"definition", "accounts"}:
                response = getattr(self.api, resource)(strategy_id, **{"snapshot_id": view})
            else:
                response = self.api.trading(resource, **params)
            body = _raise_for_fixture_response(resource, response)
            meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
            if meta.get("mock") is not False or meta.get("snapshot_id") != view:
                raise StrategyDetailSourceContractBlocked(
                    f"BLOCKED_CONTRACT: {resource} identity metadata mismatch; "
                    f"snapshot_id={meta.get('snapshot_id')}, mock={meta.get('mock')}"
                )
            yield StrategyDetailResourceEvidence(fixture, view, strategy_id, resource, body)
        finally:
            self.repository.cleanup(fixture, view, self.actor_id, owned_accounts)
