"""策略详情真实接口用例。

每条 Case 都从持久化策略数据集发现主体，随后通过真实 HTTP GET 验证策略
详情。没有动态 fixture、read view、snapshot 写入或用例清理。
"""

from __future__ import annotations

from typing import Any

import pytest

from api.account_management_api import ACCOUNT_MANAGEMENT_METRICS


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.strategy_detail]

_STRATEGY_METRICS = ACCOUNT_MANAGEMENT_METRICS.split(",")
_VALID_STATUSES = {"complete", "partial", "unavailable", "estimated"}


def _require(persistent_data_preflight, case_id: str, *capabilities: str):
    """按 Case 能力检查持久化数据。"""

    return persistent_data_preflight.require(*capabilities, case_id=case_id)


def _real(result: dict[str, Any], *, strategy_id: str, endpoint: str) -> dict[str, Any]:
    """校验真实策略接口的公共响应并返回 data。"""

    assert result.get("http_status") == 200, {"endpoint": endpoint, "response": result}
    body = result.get("body")
    assert isinstance(body, dict), result
    assert body.get("success") is True, result
    meta = body.get("meta")
    assert isinstance(meta, dict), body
    assert meta.get("mock") is False, body
    if endpoint.startswith(("analytics/", "trading/")):
        assert meta.get("snapshot_id"), {"endpoint": endpoint, "body": body}
    assert meta.get("snapshot_id"), body
    data = body.get("data")
    assert isinstance(data, (dict, list)), body
    if isinstance(data, dict):
        context = data.get("context")
        if isinstance(context, dict) and context.get("subject_id") is not None:
            assert str(context["subject_id"]) == strategy_id, {"endpoint": endpoint, "body": body}
    return data


def _metric(data: dict[str, Any], name: str) -> dict[str, Any]:
    """读取核心指标对象。"""

    metrics = data.get("metrics")
    assert isinstance(metrics, dict), data
    metric = metrics.get(name)
    assert isinstance(metric, dict), {"metric": name, "data": data}
    return metric


def test_strategy_detail_core_metrics_are_traceable(
    strategy_detail_api, strategy_detail_subject_id, persistent_data_preflight
) -> None:
    """SD-LIVE-001：策略详情当前核心 16 指标保持策略主体身份。"""

    _require(
        persistent_data_preflight,
        "SD-LIVE-001",
        "strategy_detail",
        "performance_samples",
        "published_generation",
    )
    result = strategy_detail_api.analytics(
        "summary",
        subject_id=strategy_detail_subject_id,
        subject_type="strategy",
        scope="lifetime",
        metric_basis="accrual",
        metrics=",".join(_STRATEGY_METRICS),
    )
    data = _real(result, strategy_id=strategy_detail_subject_id, endpoint="analytics/summary")
    metrics = data.get("metrics")
    assert isinstance(metrics, dict), data
    assert set(_STRATEGY_METRICS).issubset(metrics), metrics
    for name in _STRATEGY_METRICS:
        metric = _metric(data, name)
        assert metric.get("status") in _VALID_STATUSES, {"metric": name, "value": metric}
        if metric.get("value") is None:
            assert metric.get("reason_code") or metric.get("status") == "unavailable", {
                "metric": name,
                "value": metric,
            }


def test_strategy_detail_history_window_keeps_strategy_identity(
    strategy_detail_api, strategy_detail_subject_id, persistent_data_preflight
) -> None:
    """SD-LIVE-002：历史日期区间按分钟事实读取，主体不切换到账号维度。"""

    _require(
        persistent_data_preflight,
        "SD-LIVE-002",
        "strategy_detail",
        "historical_samples",
        "published_generation",
    )
    assert persistent_data_preflight.snapshot.sample_start is not None
    assert persistent_data_preflight.snapshot.sample_end is not None
    from_at = persistent_data_preflight.snapshot.sample_start.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    to_at = persistent_data_preflight.snapshot.sample_end.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    result = strategy_detail_api.analytics(
        "summary",
        subject_id=strategy_detail_subject_id,
        subject_type="strategy",
        scope="lifetime",
        metric_basis="accrual",
        metrics="margin_equity,cumulative_return,annualized_return,max_drawdown",
        from_at=from_at,
        to_at=to_at,
    )
    data = _real(result, strategy_id=strategy_detail_subject_id, endpoint="analytics/summary(range)")
    assert isinstance(data.get("metrics"), dict), data
    assert result["request_params"].get("subject_type") == "strategy", result
    assert result["request_params"].get("from") and result["request_params"].get("to"), result


def test_strategy_definition_is_read_from_real_subject(
    strategy_detail_api, strategy_detail_subject_id, persistent_data_preflight
) -> None:
    """SD-LIVE-003：策略定义接口返回指定策略而不是任意当前策略。"""

    _require(persistent_data_preflight, "SD-LIVE-003", "strategy_detail")
    result = strategy_detail_api.definition(strategy_detail_subject_id)
    data = _real(result, strategy_id=strategy_detail_subject_id, endpoint="strategy-definition")
    assert strategy_detail_subject_id in str(data), data


def test_strategy_accounts_expose_assignment_chain(
    strategy_detail_api, strategy_detail_subject_id, persistent_data_preflight
) -> None:
    """SD-LIVE-004：策略账号接口可回溯账号归属区间。"""

    _require(persistent_data_preflight, "SD-LIVE-004", "strategy_detail", "assignments")
    result = strategy_detail_api.accounts(strategy_detail_subject_id)
    data = _real(result, strategy_id=strategy_detail_subject_id, endpoint="strategy-accounts")
    assert strategy_detail_subject_id in str(data), data


@pytest.mark.parametrize(
    "resource,capabilities",
    [
        ("risk", ("strategy_detail", "performance_samples", "published_generation")),
        ("series", ("strategy_detail", "curves", "published_generation")),
    ],
)
def test_strategy_analytics_resources_are_real(
    strategy_detail_api, strategy_detail_subject_id, persistent_data_preflight, resource, capabilities
) -> None:
    """SD-LIVE-005/006：风险与净值/回撤曲线走真实资源接口。"""

    _require(persistent_data_preflight, f"SD-LIVE-{resource}", *capabilities)
    params = {
        "subject_id": strategy_detail_subject_id,
        "subject_type": "strategy",
        "scope": "lifetime",
        "metric_basis": "accrual",
    }
    if resource == "series":
        params["types"] = "nav,drawdown"
    result = strategy_detail_api.analytics(resource, **params)
    data = _real(result, strategy_id=strategy_detail_subject_id, endpoint=f"analytics/{resource}")
    if resource == "series":
        assert data, result
    else:
        assert isinstance(data, (dict, list)), result


@pytest.mark.parametrize(
    "resource,capabilities",
    [
        ("positions", ("strategy_detail", "positions")),
        ("signals", ("strategy_detail", "signals")),
        ("signal-batches", ("strategy_detail", "signals")),
        ("rebalance-plans", ("strategy_detail", "signals")),
        ("order-intents", ("strategy_detail", "orders")),
        ("orders", ("strategy_detail", "orders")),
        ("fills", ("strategy_detail", "fills")),
    ],
)
def test_strategy_trading_resources_keep_subject_and_container(
    strategy_detail_api, strategy_detail_subject_id, persistent_data_preflight, resource, capabilities
) -> None:
    """SD-LIVE-007：持仓、信号、计划、委托、成交都通过真实资源读取。"""

    _require(persistent_data_preflight, f"SD-LIVE-{resource}", *capabilities)
    result = strategy_detail_api.trading(
        resource,
        subject_id=strategy_detail_subject_id,
        subject_type="strategy",
    )
    data = _real(result, strategy_id=strategy_detail_subject_id, endpoint=f"trading/{resource}")
    assert isinstance(data, (dict, list)), result
    rows = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
    if rows:
        assert isinstance(rows, list), data
        for row in rows:
            if isinstance(row, dict):
                for key in ("strategy_id", "strategyId"):
                    if row.get(key) is not None:
                        assert str(row[key]) == strategy_detail_subject_id, row


def test_strategy_detail_reads_are_without_snapshot_id(
    strategy_detail_api, strategy_detail_subject_id, persistent_data_preflight
) -> None:
    """SD-LIVE-009：页面同款读取不把 snapshot_id 当作输入依赖。"""

    _require(persistent_data_preflight, "SD-LIVE-009", "strategy_detail")
    result = strategy_detail_api.analytics(
        "summary",
        subject_id=strategy_detail_subject_id,
        subject_type="strategy",
        scope="lifetime",
        metric_basis="accrual",
        metrics="cumulative_return",
    )
    _real(result, strategy_id=strategy_detail_subject_id, endpoint="analytics/summary")
    assert "snapshot_id" not in result.get("request_params", {}), result


def test_strategy_detail_resources_are_not_null_when_data_is_published(
    strategy_detail_api, strategy_detail_subject_id, persistent_data_preflight
) -> None:
    """SD-LIVE-010：已发布数据即使业务集合为空，也返回 JSON 容器并带真实身份。"""

    _require(persistent_data_preflight, "SD-LIVE-010", "strategy_detail", "published_generation")
    result = strategy_detail_api.trading(
        "positions",
        subject_id=strategy_detail_subject_id,
        subject_type="strategy",
    )
    data = _real(result, strategy_id=strategy_detail_subject_id, endpoint="trading/positions")
    assert data is not None
