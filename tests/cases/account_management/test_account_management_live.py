"""账号管理真实测试环境的多账号聚合用例。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from service.account_management_service import AccountAggregationInput, AccountManagementService
from service.account_management_source_flow import AccountManagementSourceContractBlocked
from api.account_management_api import ACCOUNT_MANAGEMENT_METRICS


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


_PAGE_METRICS = tuple(ACCOUNT_MANAGEMENT_METRICS.split(","))


def _assert_real_success(response: dict[str, Any], *, endpoint: str) -> dict[str, Any]:
    """校验真实页面 GET 的公共协议，不把业务数据状态当成 HTTP 失败。"""

    assert response.get("http_status") == 200, {"endpoint": endpoint, "response": response}
    body = response.get("body")
    assert isinstance(body, dict), {"endpoint": endpoint, "response": response}
    assert body.get("success") is True, {"endpoint": endpoint, "body": body}
    meta = body.get("meta")
    assert isinstance(meta, dict), {"endpoint": endpoint, "body": body}
    assert meta.get("mock") is False, {"endpoint": endpoint, "meta": meta}
    data = body.get("data")
    assert isinstance(data, dict), {"endpoint": endpoint, "body": body}
    return data


def test_account_directory_default_request_covers_all_core_metrics(
    account_management_default_page,
) -> None:
    """AM-LIVE-009：页面默认目录真实请求必须携带 16 个核心指标且不伪造快照。"""

    response = account_management_default_page
    data = _assert_real_success(response, endpoint="/admin/quant-accounts")
    params = response.get("request_params")
    assert isinstance(params, dict), response
    assert "snapshot_id" not in params, params
    assert params.get("population") == "all", params
    assert params.get("scope") == "lifetime", params
    assert params.get("metric_basis") == "accrual", params
    assert params.get("metrics") == ACCOUNT_MANAGEMENT_METRICS, params
    assert isinstance(data.get("items"), list), data
    page_info = data.get("page_info")
    assert isinstance(page_info, dict), data
    assert page_info.get("mode") == "page", page_info


def test_account_portfolio_summary_returns_all_core_metric_objects(
    account_management_default_summary,
) -> None:
    """AM-LIVE-010：portfolio 摘要必须逐项返回页面 16 张核心指标卡的数据对象。"""

    response = account_management_default_summary
    data = _assert_real_success(response, endpoint="/quantitative-trading/analytics/summary")
    params = response.get("request_params")
    assert isinstance(params, dict), response
    assert "snapshot_id" not in params, params
    assert params.get("subject_type") == "portfolio", params
    assert params.get("metrics") == ACCOUNT_MANAGEMENT_METRICS, params
    assert data.get("context", {}).get("subject_type") == "portfolio", data
    metrics = data.get("metrics")
    assert isinstance(metrics, dict), data
    missing = sorted(set(_PAGE_METRICS) - set(metrics))
    assert not missing, {"missing_metrics": missing, "metrics": metrics, "body": response.get("body")}
    for name in _PAGE_METRICS:
        metric = metrics[name]
        assert isinstance(metric, dict), {"metric": name, "value": metric}
        assert isinstance(metric.get("status"), str), {"metric": name, "value": metric}


def test_account_directory_read_limit_does_not_fabricate_totals(
    account_management_default_page,
) -> None:
    """AM-LIVE-011：读上限导致总数不可用时，目录必须保留明确原因而非返回假总数。"""

    response = account_management_default_page
    data = _assert_real_success(response, endpoint="/admin/quant-accounts")
    page_info = data.get("page_info")
    assert isinstance(page_info, dict), data
    status = page_info.get("total_status")
    if status == "unavailable":
        assert page_info.get("total") is None, page_info
        assert page_info.get("total_reason_code") == "READ_LIMIT_EXCEEDED", page_info
        assert data.get("total") is None, data
        meta = response["body"].get("meta", {})
        issue_codes = {
            issue.get("code")
            for issue in meta.get("issues", [])
            if isinstance(issue, dict)
        }
        assert "READ_LIMIT_EXCEEDED" in issue_codes, {"meta": meta, "page_info": page_info}
    else:
        assert status in {"available", "complete"}, page_info


def test_account_directory_page_and_filter_parameters_are_serialized_on_real_request(
    account_management_api_live,
) -> None:
    """AM-LIVE-012：筛选参数真实发送，跳页没有首页快照时按协议拒绝。"""

    page_two = account_management_api_live.accounts_page(
        page=2,
        limit=2,
        runtime_status="running",
        exchange="binance",
        account_ids=["2999", "3000"],
    )
    assert page_two.get("http_status") == 400, page_two
    error = page_two.get("body", {}).get("error", {})
    assert error.get("code") == "PAGINATION_SNAPSHOT_REQUIRED", page_two
    params = page_two.get("request_params")
    assert isinstance(params, dict), page_two
    assert params.get("pagination") == "page", params
    assert params.get("page") == 2, params
    assert params.get("limit") == 2, params
    assert params.get("runtime_status") == "running", params
    assert params.get("exchange") == "binance", params
    assert params.get("account_ids") == "2999,3000", params
    assert "snapshot_id" not in params, params

    response = account_management_api_live.accounts_page(
        page=1,
        limit=2,
        runtime_status="running",
        exchange="binance",
        account_ids=["2999", "3000"],
    )
    data = _assert_real_success(response, endpoint="/admin/quant-accounts")
    params = response.get("request_params")
    assert isinstance(params, dict), response
    assert params.get("page") == 1, params
    assert params.get("limit") == 2, params
    assert params.get("runtime_status") == "running", params
    assert params.get("exchange") == "binance", params
    assert params.get("account_ids") == "2999,3000", params
    assert "snapshot_id" not in params, params
    page_info = data.get("page_info")
    assert isinstance(page_info, dict), data
    assert page_info.get("mode") == "page", page_info
    assert page_info.get("page") == 1, page_info
    assert page_info.get("limit") == 2, page_info


def test_account_directory_items_keep_identity_and_metric_shape_when_data_is_available(
    account_management_default_page,
) -> None:
    """AM-LIVE-013：目录有数据时逐行保留账号身份及核心指标结构；空集合允许为空。"""

    response = account_management_default_page
    data = _assert_real_success(response, endpoint="/admin/quant-accounts")
    items = data.get("items")
    assert isinstance(items, list), data
    for item in items:
        assert isinstance(item, dict), {"item": item, "body": response.get("body")}
        identity = next(
            (item.get(key) for key in ("account_id", "accountId", "id", "AccountID") if item.get(key) is not None),
            None,
        )
        assert identity not in {None, ""}, {"item": item, "body": response.get("body")}
        if "metrics" in item:
            assert isinstance(item["metrics"], dict), item
            for metric_name, metric in item["metrics"].items():
                assert isinstance(metric, dict), {"metric": metric_name, "item": item}


def test_account_directory_unknown_account_filter_returns_empty_selection_or_explicit_status(
    account_management_api_live,
) -> None:
    """AM-LIVE-014：筛选不存在账号时不回退到全量账号，也不伪造一条目录记录。"""

    response = account_management_api_live.accounts_page(account_ids=["999999999999"], limit=100)
    data = _assert_real_success(response, endpoint="/admin/quant-accounts")
    items = data.get("items")
    assert isinstance(items, list), data
    assert items == [], {"unexpected_fallback_items": items, "body": response.get("body")}
    page_info = data.get("page_info")
    assert isinstance(page_info, dict), data
    if page_info.get("total_status") in {"available", "complete"}:
        assert page_info.get("total") == 0, page_info
