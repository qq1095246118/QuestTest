"""策略详情真实数据库到接口的最小核心链路。"""
from __future__ import annotations
from decimal import Decimal
from typing import Any
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.strategy_detail]


def _metric(body: dict[str, Any], name: str) -> dict[str, Any]:
    """读取策略摘要中的结构化指标，缺失时让 Case 明确失败。"""

    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
        raise AssertionError({"error": "strategy summary metrics object missing", "body": body})
    metric = data["metrics"].get(name)
    if not isinstance(metric, dict):
        raise AssertionError({"error": "strategy summary metric missing", "metric": name, "body": body})
    return metric


def test_strategy_detail_summary_reads_fixture(strategy_detail_source_flow) -> None:
    """SD-101/RES-035：真实 summary 必须返回 fixture 对应的策略快照和核心指标。"""
    expected_metrics = {
        "realized_pnl_gross": "880",
        "margin_equity": "42000",
        "cumulative_return": "0.02095238",
    }
    with strategy_detail_source_flow.execute(
        metrics=expected_metrics
    ) as evidence:
        body = evidence.summary
        assert body.get("success") is True, body
        assert body.get("meta", {}).get("mock") is False
        assert body.get("meta", {}).get("snapshot_id") == evidence.view_id
        context = body["data"]["context"]
        assert context["subject_type"] == "strategy", context
        assert str(context["subject_id"]) == evidence.strategy_id, context
        for name, expected in expected_metrics.items():
            metric = _metric(body, name)
            assert metric.get("value") is not None, metric
            assert Decimal(str(metric["value"])) == Decimal(expected), {
                "metric": name,
                "expected": expected,
                "actual": metric,
            }


def test_strategy_detail_summary_uses_strategy_identity(strategy_detail_source_flow) -> None:
    """SD-105/RES-029：返回结果必须保留 strategy_id，避免账户维度串入。"""
    with strategy_detail_source_flow.execute(metrics={"realized_pnl": "0"}) as evidence:
        body = evidence.summary
        assert body.get("success") is True, body
        data = body["data"]
        assert "strategy_id" in data, data
        assert data["strategy_id"] == evidence.strategy_id, data
        assert data["context"]["subject_type"] == "strategy", data
        assert str(data["context"]["subject_id"]) == evidence.strategy_id, data


_RESOURCE_PAYLOAD = {
    "positions": [{"symbol": "BTCUSDT", "side": "LONG", "quantity": "2", "unrealized_pnl": "12.5"}],
    "signals": [{"signal_id": "sig-live-1", "symbol": "BTCUSDT", "direction": "LONG"}],
    "signal_batches": [{"signal_id": "sig-live-1", "status": "SUCCEEDED", "planned_count": 1}],
    "rebalance_plans": [{"signal_id": "sig-live-1", "symbol": "BTCUSDT", "status": "SUCCEEDED", "quantity": "2"}],
    "order_intents": [{"signal_id": "sig-live-1", "symbol": "BTCUSDT", "real_position": "2", "baseline_position": "0"}],
    "orders": [{"signal_id": "sig-live-1", "symbol": "BTCUSDT", "side": "BUY", "status": "FILLED", "quantity": "2"}],
    "fills": [{"signal_id": "sig-live-1", "symbol": "BTCUSDT", "side": "BUY", "quantity": "2", "price": "100"}],
    "samples": [{"timestamp": "2026-10-20T00:00:00+08:00", "margin_equity": "42000", "cumulative_return": "0.02"}],
}


def _assert_resource_response(evidence) -> dict:
    """断言真实资源的统一协议字段和非空数据容器。"""

    body = evidence.response
    assert body.get("success") is True, body
    meta = body.get("meta", {})
    assert meta.get("mock") is False, meta
    assert meta.get("snapshot_id") == evidence.view_id, meta
    data = body.get("data")
    assert isinstance(data, (dict, list)), body
    return data


@pytest.mark.parametrize("resource", ["risk", "series"])
def test_strategy_detail_live_analytics_resources(strategy_detail_source_flow, resource: str) -> None:
    """SD-110/111：风险与曲线必须由真实快照返回并带资源数据。"""

    with strategy_detail_source_flow.execute_resource(
        resource, metrics={"samples": _RESOURCE_PAYLOAD["samples"], "volatility": "0.12", "sharpe": "1.3"}, analytics=True
    ) as evidence:
        data = _assert_resource_response(evidence)
        assert data, data


@pytest.mark.parametrize("resource", ["positions", "signals", "signal-batches", "rebalance-plans", "order-intents", "orders", "fills"])
def test_strategy_detail_live_trading_resources(strategy_detail_source_flow, resource: str) -> None:
    """SD-120~126：持仓、信号、调仓、订单和成交资源走真实接口并保留快照身份。"""

    key = resource.replace("-", "_")
    with strategy_detail_source_flow.execute_resource(
        resource, metrics={key: _RESOURCE_PAYLOAD[key]}, analytics=False
    ) as evidence:
        data = _assert_resource_response(evidence)
        assert data, data


@pytest.mark.parametrize("resource", ["definition", "accounts"])
def test_strategy_detail_live_definition_resources(strategy_detail_source_flow, resource: str) -> None:
    """SD-130/131：定义及账号归属必须从真实策略资源返回。"""

    with strategy_detail_source_flow.execute_resource(
        resource, metrics={}, accounts=[{"strategy_id": "placeholder", "status": "active"}], analytics=False
    ) as evidence:
        data = _assert_resource_response(evidence)
        assert data, data
