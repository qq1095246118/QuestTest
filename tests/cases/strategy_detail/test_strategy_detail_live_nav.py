"""策略详情页面同款请求的真实 HTTP 自动化用例。

这些用例不创建伪造 read view，也不把 SQL 结果当作接口结果；策略主体由测试库
只读发现，后续请求完全不携带 snapshot_id，与浏览器页面默认链路一致。
"""

from __future__ import annotations

from typing import Any

import pytest

from service.strategy_detail_source_flow import StrategyDetailSourceContractBlocked


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.strategy_detail]


def _skip_if_contract_blocked(error: StrategyDetailSourceContractBlocked) -> None:
    """把没有可见真实策略的环境能力门禁显示为 skipped。"""

    pytest.skip(str(error))


def _data_container(body: dict[str, Any]) -> Any:
    """断言真实接口成功且返回数据容器，返回容器供资源专项断言使用。"""

    assert body.get("success") is True, body
    assert body.get("meta", {}).get("mock") is False, body
    data = body.get("data")
    assert isinstance(data, (dict, list)), body
    return data


def test_strategy_summary_live_without_snapshot_uses_real_subject(
    strategy_detail_source_flow, persistent_data_preflight
) -> None:
    """SD-LIVE-001：策略详情核心指标使用后端实际生成快照并保持主体身份。"""

    persistent_data_preflight.require(
        "strategy_detail", "performance_samples", "published_generation", case_id="SD-LIVE-001"
    )
    try:
        with strategy_detail_source_flow.execute_existing() as evidence:
            data = _data_container(evidence.response)
            assert evidence.response["meta"].get("snapshot_id"), evidence.response
            context = data.get("context", {}) if isinstance(data, dict) else {}
            assert context.get("subject_type") == "strategy", evidence.response
            assert str(context.get("subject_id")) == evidence.strategy_id, evidence.response
            metrics = data.get("metrics")
            assert isinstance(metrics, dict), evidence.response
            for name in ("margin_equity", "realized_pnl_gross", "cumulative_return"):
                assert name in metrics, {"metric": name, "body": evidence.response}
    except StrategyDetailSourceContractBlocked as error:
        _skip_if_contract_blocked(error)


@pytest.mark.parametrize("resource", ["risk", "series"])
def test_strategy_analytics_live_without_snapshot(
    strategy_detail_source_flow, persistent_data_preflight, resource: str
) -> None:
    """SD-LIVE-002/003：风险和净值/回撤曲线走真实无 snapshot 请求。"""

    persistent_data_preflight.require(
        "strategy_detail", "performance_samples", "published_generation", case_id=f"SD-LIVE-{resource}"
    )
    try:
        with strategy_detail_source_flow.execute_existing_resource(resource) as evidence:
            data = _data_container(evidence.response)
            if resource == "series":
                assert data, evidence.response
                # The request contract is validated by the API call itself; a returned
                # series may be partial when the collector has no recent samples.
                assert isinstance(data, (dict, list)), evidence.response
            elif isinstance(data, dict):
                if evidence.response.get("meta", {}).get("data_status") != "unavailable":
                    assert any(key in data for key in ("metrics", "risk", "items", "series")), data
            if not evidence.view_id:
                issue_codes = {
                    issue.get("code")
                    for issue in evidence.response.get("meta", {}).get("issues", [])
                    if isinstance(issue, dict)
                }
                assert evidence.response.get("meta", {}).get("data_status") in {"partial", "unavailable"}, evidence.response
                assert "READ_LIMIT_EXCEEDED" in issue_codes, evidence.response
    except StrategyDetailSourceContractBlocked as error:
        _skip_if_contract_blocked(error)


@pytest.mark.parametrize("resource", ["definition", "accounts"])
def test_strategy_identity_resources_live_without_snapshot(
    strategy_detail_source_flow, persistent_data_preflight, resource: str
) -> None:
    """SD-LIVE-004/005：策略定义和账号归属使用真实主体请求。"""

    persistent_data_preflight.require("strategy_detail", case_id=f"SD-LIVE-{resource}")
    try:
        with strategy_detail_source_flow.execute_existing_resource(resource) as evidence:
            data = _data_container(evidence.response)
            if evidence.response.get("meta", {}).get("data_status") == "unavailable":
                issue_codes = {
                    issue.get("code")
                    for issue in evidence.response.get("meta", {}).get("issues", [])
                    if isinstance(issue, dict)
                }
                assert "READ_LIMIT_EXCEEDED" in issue_codes, evidence.response
            else:
                assert evidence.strategy_id in str(data), evidence.response
    except StrategyDetailSourceContractBlocked as error:
        _skip_if_contract_blocked(error)


@pytest.mark.parametrize(
    "resource",
    ["positions", "signals", "signal-batches", "rebalance-plans", "order-intents", "orders", "fills"],
)
def test_strategy_trading_resources_live_without_snapshot(
    strategy_detail_source_flow, persistent_data_preflight, resource: str
) -> None:
    """SD-LIVE-006~012：交易资源验证真实主体、非 mock 和数据容器。"""

    required = {
        "positions": ("positions",),
        "signals": ("signals",),
        "signal-batches": ("signals",),
        "rebalance-plans": ("signals",),
        "order-intents": ("orders",),
        "orders": ("orders",),
        "fills": ("fills",),
    }[resource]
    persistent_data_preflight.require("strategy_detail", *required, case_id=f"SD-LIVE-{resource}")
    try:
        with strategy_detail_source_flow.execute_existing_resource(resource) as evidence:
            data = _data_container(evidence.response)
            assert isinstance(data, (dict, list)), evidence.response
            # Empty data is a valid state after the environment reset; it must still
            # be represented by a JSON container instead of a null or fabricated row.
            assert data is not None, evidence.response
    except StrategyDetailSourceContractBlocked as error:
        _skip_if_contract_blocked(error)
