"""真实投研 Agent 到登记后 Performance Refresh 的端到端测试。"""

from __future__ import annotations

import pytest

from service.factor_combo_service import FactorComboFlowError, FlowOutcome, RealResearchFlowResult


@pytest.mark.integration
@pytest.mark.external_agent
class TestFactorComboRealResearchFlow:
    """验证真实计算结果不会在登记接口返回后提前结束。"""

    def test_real_flow_reaches_registered_refresh_or_expected_invalid_outcome(
        self,
        factor_combo_real_e2e_context: dict[str, object],
    ) -> None:
        """执行真实研究轮次，并要求有效结果完成刷新验收、无效结果明确分类。"""

        flow = factor_combo_real_e2e_context["flow"]
        assert isinstance(flow, RealResearchFlowResult), flow
        assert flow.outcome in {FlowOutcome.PASS_REGISTERED, FlowOutcome.PASS_INVALID}, flow
        assert flow.rounds, flow
        for round_record in flow.rounds:
            assert round_record.get("pipeline_status", {}).get("pipeline_status") == "completed", round_record

        if flow.outcome == FlowOutcome.PASS_REGISTERED:
            assert flow.registration is not None, flow
            assert flow.registration.refresh.status == "completed", flow.registration
            assert flow.registration.refresh.data["summary"]["completed_units"] == flow.registration.refresh.data["summary"]["total_units"], flow.registration.refresh.data
            assert not flow.registration.refresh.data["incomplete_factors"], flow.registration.refresh.data
            assert flow.registration.database_refresh.calculation_runs, flow.registration.database_refresh
            assert flow.registration.database_refresh.validity_snapshots, flow.registration.database_refresh
            assert flow.registration.database_refresh.matched_run_ids, flow.registration.database_refresh
        else:
            assert flow.registration is None, flow
            assert flow.last_pipeline_result is not None, flow

    def test_real_flow_failures_are_not_silently_skipped(
        self,
        factor_combo_real_e2e_context: dict[str, object],
    ) -> None:
        """确认完整流程 Fixture 不会把技术、刷新或契约失败转换成 xfail/skip。"""

        flow = factor_combo_real_e2e_context["flow"]
        assert isinstance(flow, RealResearchFlowResult), flow
        assert flow.outcome not in {
            FlowOutcome.FAIL_REFRESH,
            FlowOutcome.FAIL_TECHNICAL,
            FlowOutcome.FAIL_CONTRACT,
        }, flow
