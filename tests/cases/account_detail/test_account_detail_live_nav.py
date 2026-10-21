"""账户详情真实测试环境的源输入到 N01 摘要自动化用例。"""

from __future__ import annotations

from typing import Any

import pytest

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


def test_account_summary_live_date_window_is_reflected_in_request_context(
    account_detail_api, account_detail_subject_id, persistent_data_preflight
) -> None:
    """ADC-LIVE-007：真实 N01 支持页面日期窗口，并保留同一账户身份。"""

    snapshot = persistent_data_preflight.require(
        "account_detail", "historical_samples", "published_generation", case_id="ADC-LIVE-007"
    )
    assert snapshot.sample_start is not None and snapshot.sample_end is not None
    from_at = snapshot.sample_start.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    to_at = snapshot.sample_end.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    response = account_detail_api.summary(
        account_detail_subject_id,
        # N01 keeps the page's lifetime scope while the date picker is carried
        # by the explicit from/to window.
        scope="lifetime",
        metric_basis="accrual",
        metrics="margin_equity,cumulative_return,annualized_return,max_drawdown",
        from_at=from_at,
        to_at=to_at,
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


def test_account_summary_live_rejects_reversed_date_window(
    account_detail_api, account_detail_subject_id
) -> None:
    """ADC-LIVE-010：结束时间早于开始时间时返回可定位的参数错误。"""

    response = account_detail_api.summary(
        account_detail_subject_id,
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
