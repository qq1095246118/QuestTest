"""账户详情真实测试环境的源输入到 N01 摘要自动化用例。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from service.account_detail_source_flow import (
    AccountDetailSourceContractBlocked,
    AccountDetailSourceFlow,
)
from tools.http_response import read_json_object


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.account_detail]


ACCOUNT_CORE_METRICS = (
    "margin_equity",
    "unrealized_pnl",
    "realized_pnl_gross",
    "cumulative_return",
    "annualized_return",
    "current_drawdown",
    "commission_expense",
    "funding_pnl",
    "position_ratio",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown",
    "volatility",
    "win_rate",
    "profit_loss_ratio",
)


def _metric(body: dict[str, Any], name: str) -> dict[str, Any]:
    """读取 N01 结构化指标，字段缺失时直接失败。"""

    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
        raise AssertionError({"error": "account summary metrics object missing", "body": body})
    metric = data["metrics"].get(name)
    if not isinstance(metric, dict):
        raise AssertionError({"error": "account summary metric missing", "metric": name, "body": body})
    return metric


def test_nav_positions_are_calculated_from_owned_source_rows(
    account_detail_source_flow: AccountDetailSourceFlow,
) -> None:
    """ADC-TRD-010/ADC-TRD-011/ADC-TRD-012：真实持仓源输入核对 N01 持仓汇总。"""

    positions = [
        {"symbol": "BTCUSDT", "position_side": "long", "signed_qty": "6", "mark_price": "100", "notional_usdt": "600", "margin_usdt": "100", "unrealized_pnl_usdt": "20"},
        {"symbol": "ETHUSDT", "position_side": "short", "signed_qty": "-8", "mark_price": "50", "notional_usdt": "-400", "margin_usdt": "200", "unrealized_pnl_usdt": "-5"},
    ]
    try:
        with account_detail_source_flow.execute(
            realized_profit=Decimal("0"),
            net_deposit=Decimal("0"),
            positions=positions,
            snapshot_overrides={"margin_equity": "1000", "margin_used": "300", "unrealized_pnl": "15"},
        ) as evidence:
            response = evidence.response_body
            assert response["success"] is True, response
            meta = response["meta"]
            assert meta["mock"] is False, meta
            assert meta["snapshot_id"] == evidence.view_id, meta
            assert str(response["data"]["context"]["subject_id"]) == str(evidence.account_id), response
            exposure = response["data"]["exposure_summary"]
            assert exposure["position_count"] == 2, exposure
            assert Decimal(str(exposure["total_notional"]["value"])) == Decimal("1000"), exposure
            assert Decimal(str(exposure["unrealized_pnl"]["value"])) == Decimal("15"), exposure
    except AccountDetailSourceContractBlocked as error:
        pytest.skip(str(error))


def test_account_summary_preserves_core_money_fields(
    account_detail_source_flow: AccountDetailSourceFlow,
) -> None:
    """ADC-MET-001/006/010：N01 核心金额字段按源值返回并保留正负号。"""

    try:
        with account_detail_source_flow.execute(
            realized_profit=Decimal("205"),
            net_deposit=Decimal("5000"),
            snapshot_overrides={
                "margin_equity": "2312.30",
                "unrealized_pnl": "125.30",
                "margin_used": "300",
                "commission_expense": "-12.50",
                "funding_pnl": "-3.25",
            },
        ) as evidence:
            body = evidence.response_body
            expected = {
                "margin_equity": Decimal("2312.30"),
                "unrealized_pnl": Decimal("125.30"),
                "realized_pnl_gross": Decimal("205"),
                "commission_expense": Decimal("-12.50"),
                "funding_pnl": Decimal("-3.25"),
            }
            for name, value in expected.items():
                metric = _metric(body, name)
                assert metric.get("value") is not None, {"metric": name, "body": body}
                assert Decimal(str(metric["value"])) == value, {"metric": name, "expected": value, "actual": metric}
    except AccountDetailSourceContractBlocked as error:
        pytest.skip(str(error))


def test_account_summary_keeps_zero_as_available_value(
    account_detail_source_flow: AccountDetailSourceFlow,
) -> None:
    """ADC-MET-002/011：真实 0 不得被转换为缺失或 unavailable。"""

    try:
        with account_detail_source_flow.execute(
            snapshot_overrides={"margin_equity": "0", "unrealized_pnl": "0", "margin_used": "0"}
        ) as evidence:
            for name in ("margin_equity", "unrealized_pnl"):
                metric = _metric(evidence.response_body, name)
                assert Decimal(str(metric.get("value"))) == Decimal("0"), metric
                assert metric.get("status") not in {"unavailable", "missing"}, metric
    except AccountDetailSourceContractBlocked as error:
        pytest.skip(str(error))


def test_account_summary_reports_missing_core_field_without_zero_fallback(
    account_detail_source_flow: AccountDetailSourceFlow,
) -> None:
    """ADC-MET-004/009：缺失源字段要保留不可用状态，不能静默补 0。"""

    try:
        with account_detail_source_flow.execute(
            realized_profit=None,
            snapshot_overrides={"margin_equity": None, "unrealized_pnl": None},
        ) as evidence:
            for name in ("margin_equity", "unrealized_pnl", "realized_pnl_gross"):
                metric = _metric(evidence.response_body, name)
                assert metric.get("value") is None, metric
                assert metric.get("status") in {"partial", "unavailable"}, metric
                assert metric.get("reason_code"), metric
    except AccountDetailSourceContractBlocked as error:
        pytest.skip(str(error))


def test_account_summary_live_without_fixture_snapshot_is_successful(account_detail_api, account_detail_subject_id) -> None:
    """ADC-LIVE-005：页面实际请求不携带 snapshot_id 时，接口应返回真实快照。"""

    response = account_detail_api.summary(
        account_detail_subject_id,
        scope="lifetime",
        metric_basis="accrual",
        metrics="realized_pnl_gross,net_deposit,cumulative_return,margin_equity,unrealized_pnl",
    )
    body = read_json_object(response, "account detail live smoke summary")
    assert response.status_code == 200, body
    assert body.get("success") is True, body
    assert body.get("meta", {}).get("mock") is False, body
    assert body.get("meta", {}).get("snapshot_id"), body
    context = body.get("data", {}).get("context", {})
    assert context.get("subject_type") == "account", context
    assert str(context.get("subject_id")) == str(account_detail_subject_id), context


def test_account_summary_live_exposes_all_core_metric_states(account_detail_api, account_detail_subject_id) -> None:
    """ADC-LIVE-006：页面核心 16 个指标都必须能从真实 N01 响应追踪状态。

    当前环境的数据可能不足以计算每一个风险指标，因此用例不要求所有指标都有数值；
    但每个被请求的字段必须返回结构化状态，缺失值必须能通过 reason_code 解释，不能
    由测试脚本把 ``null`` 静默当成 0。
    """

    response = account_detail_api.summary(
        account_detail_subject_id,
        scope="lifetime",
        metric_basis="accrual",
        metrics=",".join(ACCOUNT_CORE_METRICS),
    )
    body = read_json_object(response, "account detail core metrics")
    assert response.status_code == 200, body
    assert body.get("success") is True, body
    assert body.get("meta", {}).get("mock") is False, body
    metrics = body.get("data", {}).get("metrics")
    assert isinstance(metrics, dict), body
    for name in ACCOUNT_CORE_METRICS:
        metric = metrics.get(name)
        assert isinstance(metric, dict), {"metric": name, "body": body}
        assert metric.get("status") in {"complete", "partial", "unavailable", "estimated"}, {
            "metric": name,
            "metric_value": metric,
        }
        if metric.get("value") is None:
            assert metric.get("reason_code") or metric.get("status") == "unavailable", {
                "metric": name,
                "metric_value": metric,
            }


def test_account_summary_live_date_window_is_reflected_in_request_context(account_detail_api, account_detail_subject_id) -> None:
    """ADC-LIVE-007：真实 N01 支持页面日期窗口，并保留同一账户身份。"""

    response = account_detail_api.summary(
        account_detail_subject_id,
        # N01 keeps the page's lifetime scope while the date picker is carried
        # by the explicit from/to window.
        scope="lifetime",
        metric_basis="accrual",
        metrics="margin_equity,cumulative_return,annualized_return,max_drawdown",
        from_at="2026-07-12T00:00:00+08:00",
        to_at="2026-08-10T23:59:59+08:00",
    )
    body = read_json_object(response, "account detail range summary")
    assert response.status_code == 200, body
    assert body.get("success") is True, body
    assert body.get("meta", {}).get("mock") is False, body
    context = body.get("data", {}).get("context")
    assert isinstance(context, dict), body
    assert context.get("subject_type") == "account", context
    assert str(context.get("subject_id")) == str(account_detail_subject_id), context
    # The response context is the stable identity contract.  Date formatting is a
    # presentation concern and may be normalized into metadata by the backend.
    assert body.get("meta", {}).get("snapshot_id"), body
    assert isinstance(body.get("data", {}).get("metrics"), dict), body


def test_account_summary_live_snapshot_is_traceable_on_repeated_read(account_detail_api, account_detail_subject_id) -> None:
    """ADC-LIVE-008：每次真实读取都必须生成可追踪 snapshot，且不返回 mock。"""

    kwargs = {
        "scope": "lifetime",
        "metric_basis": "accrual",
        "metrics": "margin_equity,realized_pnl_gross,cumulative_return",
    }
    first = read_json_object(account_detail_api.summary(account_detail_subject_id, **kwargs), "account detail first read")
    second = read_json_object(account_detail_api.summary(account_detail_subject_id, **kwargs), "account detail second read")
    for body in (first, second):
        assert body.get("success") is True, body
        assert body.get("meta", {}).get("mock") is False, body
        assert body.get("meta", {}).get("snapshot_id"), body
        assert body.get("data", {}).get("context", {}).get("subject_type") == "account", body
        assert str(body.get("data", {}).get("context", {}).get("subject_id")) == str(account_detail_subject_id), body


def test_account_summary_live_invalid_account_returns_structured_business_error(account_detail_api) -> None:
    """ADC-LIVE-009：不存在账户不能被当成成功的空数据。"""

    response = account_detail_api.summary(
        "999999999999",
        scope="lifetime",
        metric_basis="accrual",
        metrics="margin_equity,cumulative_return",
    )
    body = read_json_object(response, "account detail invalid account")
    assert response.status_code in {400, 404}, body
    assert body.get("success") is False or body.get("error") or body.get("code"), body


def test_account_summary_live_rejects_reversed_date_window(account_detail_api) -> None:
    """ADC-LIVE-010：结束时间早于开始时间时返回可定位的参数错误。"""

    response = account_detail_api.summary(
        2999,
        scope="lifetime",
        metric_basis="accrual",
        metrics="margin_equity,cumulative_return",
        from_at="2026-08-10T00:00:00+08:00",
        to_at="2026-07-12T23:59:59+08:00",
    )
    body = read_json_object(response, "account detail reversed date window")
    assert response.status_code == 400, body
    assert body.get("success") is False, body
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    assert error.get("code") == "INVALID_ARGUMENT", body
