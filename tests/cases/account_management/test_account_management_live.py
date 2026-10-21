"""账号管理真实测试环境的多账号聚合用例。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from service.account_management_service import AccountAggregationInput, AccountManagementService
from service.account_management_source_flow import AccountManagementSourceContractBlocked


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.account_management]


def _metric(body: dict[str, Any], name: str) -> dict[str, Any]:
    """读取摘要中的结构化指标对象。"""

    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
        raise AssertionError({"error": "portfolio summary metrics object missing", "body": body})
    value = data["metrics"].get(name)
    if not isinstance(value, dict):
        raise AssertionError({"error": "portfolio metric missing", "metric": name, "body": body})
    return value


def _decimal(value: Any) -> Decimal | None:
    """把 live fixture 的数值转换为 Decimal。"""

    return AccountManagementService.from_strings(value)


def _close(actual: Any, expected: Decimal) -> bool:
    """按金额和比例的高精度容差比较。"""

    value = _decimal(actual)
    return value is not None and abs(value - expected) <= Decimal("0.00000001")


def _accounts() -> list[dict[str, Any]]:
    """创建主多账号汇总 fixture；不使用已有账号。"""

    return [
        {
            "margin_equity": "8000",
            "unrealized_pnl": "100",
            "realized_pnl": "500",
            "commission_expense": "-20",
            "funding_pnl": "-5",
            "initial_capital": "10000",
            "exchange": "binance",
            "strategy_id": "S-A",
            "runtime_status": "running",
        },
        {
            "margin_equity": "15000",
            "unrealized_pnl": "-200",
            "realized_pnl": "-1000",
            "commission_expense": "-50",
            "funding_pnl": "10",
            "initial_capital": "20000",
            "exchange": "binance",
            "strategy_id": "S-B",
            "runtime_status": "running",
        },
        {
            "margin_equity": "0",
            "unrealized_pnl": "0",
            "realized_pnl": "250",
            "commission_expense": "0",
            "funding_pnl": "0",
            "initial_capital": "5000",
            "exchange": "okx",
            "strategy_id": "S-A",
            "runtime_status": "stopped",
        },
        {
            "margin_equity": "40000",
            "unrealized_pnl": "500",
            "realized_pnl": "0",
            "commission_expense": "-100",
            "funding_pnl": "-2",
            "initial_capital": "50000",
            "exchange": "okx",
            "strategy_id": "S-C",
            "runtime_status": "running",
        },
    ]


def _oracle_inputs(rows: list[dict[str, Any]]) -> list[AccountAggregationInput]:
    """给 live 断言构造同一组独立 Oracle 输入。"""

    return [
        AccountAggregationInput(
            account_id=str(index),
            margin_equity=_decimal(row.get("margin_equity")),
            unrealized_pnl=_decimal(row.get("unrealized_pnl")),
            realized_pnl=_decimal(row.get("realized_pnl")),
            commission_expense=_decimal(row.get("commission_expense")),
            funding_pnl=_decimal(row.get("funding_pnl")),
            initial_capital=_decimal(row.get("initial_capital")),
        )
        for index, row in enumerate(rows, start=1)
    ]


def test_portfolio_summary_uses_all_fixture_accounts(account_management_source_flow) -> None:
    """AM-LIVE-001：总览必须使用全部多账号 fixture，而非单个账号。"""

    rows = _accounts()
    try:
        with account_management_source_flow.execute(rows) as evidence:
            body = evidence.response_body
            data = body["data"]
            assert data["context"]["subject_type"] == "portfolio", body
            assert body["meta"]["mock"] is False, body
            assert body["meta"]["snapshot_id"] == evidence.view_id, body
            assert data["counts"]["account_count"] == 4, body
            directory_data = evidence.directory_body["data"]
            assert evidence.directory_body["meta"]["snapshot_id"] == evidence.view_id, evidence.directory_body
            assert directory_data["total"] == 4, evidence.directory_body
            assert len(directory_data["items"]) == 4, evidence.directory_body
            assert _close(_metric(body, "margin_equity").get("value"), Decimal("63000")), body
            assert _close(_metric(body, "unrealized_pnl").get("value"), Decimal("400")), body
            assert _close(_metric(body, "realized_pnl_gross").get("value"), Decimal("-250")), body
            assert _close(_metric(body, "commission_expense").get("value"), Decimal("-170")), body
            assert _close(_metric(body, "funding_pnl").get("value"), Decimal("3")), body
    except AccountManagementSourceContractBlocked as error:
        pytest.skip(str(error))


def test_portfolio_total_return_recomputes_from_all_account_g_and_d(account_management_source_flow) -> None:
    """AM-LIVE-002：总收益率按汇总G/D计算，不能平均各账号收益率。"""

    rows = _accounts()
    expected = AccountManagementService.total_return(_oracle_inputs(rows))
    try:
        with account_management_source_flow.execute(rows) as evidence:
            actual = _metric(evidence.response_body, "cumulative_return")
            assert actual.get("value") is not None, actual
            assert _close(actual["value"], expected.value), {
                "expected": expected,
                "actual": actual,
                "account_ids": evidence.account_ids,
            }
    except AccountManagementSourceContractBlocked as error:
        pytest.skip(str(error))


def test_portfolio_missing_realized_source_is_not_claimed_complete(account_management_source_flow) -> None:
    """AM-LIVE-003：一个账号收益源缺失时不能返回完整总收益率。"""

    rows = _accounts()
    rows[1] = {**rows[1], "realized_pnl": None}
    try:
        with account_management_source_flow.execute(rows) as evidence:
            metric = _metric(evidence.response_body, "cumulative_return")
            assert metric.get("status") in {"partial", "unavailable"}, metric
            assert metric.get("reason_code"), metric
    except AccountManagementSourceContractBlocked as error:
        pytest.skip(str(error))


def test_portfolio_amount_metrics_reconcile_every_account_source(account_management_source_flow) -> None:
    """AM-LIVE-004：总览的金额卡片逐项按所有账号源数据汇总。"""

    rows = _accounts()
    expected = {
        "margin_equity": Decimal("63000"),
        "unrealized_pnl": Decimal("400"),
        "realized_pnl_gross": Decimal("-250"),
        "commission_expense": Decimal("-170"),
        "funding_pnl": Decimal("3"),
    }
    try:
        with account_management_source_flow.execute(rows) as evidence:
            for name, value in expected.items():
                metric = _metric(evidence.response_body, name)
                assert metric.get("status") == "complete", {"metric": name, "metric_value": metric}
                assert _close(metric.get("value"), value), {"metric": name, "expected": value, "actual": metric}
    except AccountManagementSourceContractBlocked as error:
        pytest.skip(str(error))


def test_account_directory_live_pagination_preserves_total_and_page(account_management_source_flow) -> None:
    """AM-LIVE-005：真实账号目录分页只改变当前页，不改变总账号数。"""

    try:
        with account_management_source_flow.execute(_accounts(), directory_limit=2) as evidence:
            directory = evidence.directory_body
            data = directory["data"]
            assert data["total"] == 4, directory
            assert len(data["items"]) == 2, directory
            assert data.get("page", 1) == 1, directory
            assert data.get("limit", 2) == 2, directory
    except AccountManagementSourceContractBlocked as error:
        pytest.skip(str(error))


def test_account_directory_live_exchange_filter_excludes_other_exchange(account_management_source_flow) -> None:
    """AM-LIVE-006：交易所筛选只返回目标交易所账号，且总数与当前页一致。"""

    try:
        with account_management_source_flow.execute(_accounts(), directory_exchange="binance") as evidence:
            directory = evidence.directory_body
            data = directory["data"]
            assert data["total"] == 2, directory
            assert len(data["items"]) == 2, directory
            assert all(item.get("exchange") == "binance" for item in data["items"]), directory
    except AccountManagementSourceContractBlocked as error:
        pytest.skip(str(error))


def test_account_directory_live_without_snapshot_reports_business_status(account_management_source_flow) -> None:
    """AM-LIVE-007：页面默认请求不带 snapshot 时，成功响应中的数据状态可解释。"""

    response = account_management_source_flow.api.accounts_page()
    assert response["http_status"] == 200, response
    body = response["body"]
    assert body.get("success") is True, body
    assert body.get("meta", {}).get("mock") is False, body
    if body.get("meta", {}).get("data_status") == "unavailable":
        codes = {
            issue.get("code")
            for issue in body.get("meta", {}).get("issues", [])
            if isinstance(issue, dict)
        }
        assert "READ_LIMIT_EXCEEDED" in codes, body


def test_portfolio_summary_live_without_snapshot_returns_real_response(account_management_source_flow) -> None:
    """AM-LIVE-008：页面实际 portfolio 请求不携带 snapshot_id 时应返回真实响应。"""

    response = account_management_source_flow.api.summary()
    assert response["http_status"] == 200, response
    body = response["body"]
    assert body.get("success") is True, body
    assert body.get("meta", {}).get("mock") is False, body
    if not body.get("meta", {}).get("snapshot_id"):
        codes = {
            issue.get("code")
            for issue in body.get("meta", {}).get("issues", [])
            if isinstance(issue, dict)
        }
        assert "READ_LIMIT_EXCEEDED" in codes, body
    assert body.get("data", {}).get("context", {}).get("subject_type") == "portfolio", body
