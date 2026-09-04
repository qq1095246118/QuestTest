"""真实投研 Agent 到登记后 Performance Refresh 的端到端测试。"""

from __future__ import annotations

import pytest

from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import (
    FactorComboService,
    FlowOutcome,
    RealResearchFlowResult,
)


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
            refresh_summary = flow.registration.refresh.data.get("summary")
            assert isinstance(refresh_summary, dict), flow.registration.refresh.data
            assert refresh_summary["completed_units"] == refresh_summary["total_units"], flow.registration.refresh.data
            assert not flow.registration.refresh.data["incomplete_factors"], flow.registration.refresh.data
            assert flow.registration.database_refresh.calculation_runs, flow.registration.database_refresh
            assert flow.registration.database_refresh.validity_snapshots, flow.registration.database_refresh
            assert flow.registration.database_refresh.matched_run_ids, flow.registration.database_refresh
            assert flow.registration.core_metric_coverage.get("validated") is True, flow.registration
            scopes = flow.registration.core_metric_coverage.get("scopes")
            slice_counts = flow.registration.core_metric_coverage.get("slice_counts")
            assert isinstance(scopes, dict) and scopes, flow.registration.core_metric_coverage
            assert isinstance(slice_counts, dict) and slice_counts, flow.registration.core_metric_coverage
            for scope, diagnostics in scopes.items():
                assert diagnostics["aggregate_row_count"] > 0, {
                    "scope": scope,
                    "diagnostics": diagnostics,
                    "coverage": flow.registration.core_metric_coverage,
                }
                assert diagnostics["scoring_row_count"] > 0, {
                    "scope": scope,
                    "diagnostics": diagnostics,
                    "coverage": flow.registration.core_metric_coverage,
                }
                assert diagnostics["windows"], {
                    "scope": scope,
                    "diagnostics": diagnostics,
                    "coverage": flow.registration.core_metric_coverage,
                }
            for scope, count in slice_counts.items():
                assert count > 0, {
                    "scope": scope,
                    "slice_counts": slice_counts,
                    "coverage": flow.registration.core_metric_coverage,
                }
            assert flow.registration.registration_persistence, flow.registration
            formula_consistency = flow.registration.formula_source_consistency
            assert formula_consistency["formula"], formula_consistency
            formula_snapshots = formula_consistency.get("formula_snapshots")
            assert isinstance(formula_snapshots, dict), formula_consistency
            for field_name in (
                "sub_factors.formula_summary",
                "factors_details.calc_logic",
                "factors_details.params",
                "sub_factors.metadata",
            ):
                assert formula_snapshots.get(field_name) == formula_consistency["formula"], formula_consistency
            components = formula_consistency.get("components")
            assert isinstance(components, (list, tuple)) and components, formula_consistency
            assert all(
                component.get("sub_factor_id")
                and component.get("direction") in (-1, 1)
                and component.get("weight_contract")
                for component in components
            ), formula_consistency
            source_relations = formula_consistency.get("source_relations")
            assert isinstance(source_relations, dict), formula_consistency
            assert any(source_relations.values()), formula_consistency
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


@pytest.mark.integration
@pytest.mark.regression
class TestRegisteredFactorCoreData:
    """验证真实已登记复合子因子的核心指标、回测指标、公式和来源链路。"""

    def test_registered_factor_metrics_formula_weights_and_sources_are_consistent(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """动态读取具备刷新证据的真实复合子因子，并执行详情接口与数据库深度对账。"""

        choice = factor_combo_repository.find_registered_factor_with_refresh_evidence()
        assert choice is not None, "测试库不存在具备完整刷新证据的真实已登记复合子因子"

        audit = factor_combo_service.audit_registered_factor_core_data(choice)
        api_sub_factor = audit["api_sub_factor"]
        database_refresh = audit["database_refresh"]
        coverage = audit["core_metric_coverage"]
        formula_consistency = audit["formula_source_consistency"]
        diagnostics = {
            "registration_id": choice.registration_id,
            "sub_factor_id": choice.sub_factor_id,
            "version_id": choice.version_id,
            "matched_run_ids": database_refresh.matched_run_ids,
            "api_db_match_count": len(database_refresh.api_db_matches),
            "metric_row_count": len(database_refresh.calculation_metrics),
            "slice_row_count": len(database_refresh.slice_metrics),
            "coverage": coverage,
            "formula": formula_consistency.get("formula"),
            "component_count": len(formula_consistency.get("components", ())),
            "source_relation_counts": {
                name: len(relations)
                for name, relations in formula_consistency.get("source_relations", {}).items()
            },
        }

        assert api_sub_factor["id"] == choice.sub_factor_id, diagnostics
        assert database_refresh.api_db_matches, diagnostics
        assert database_refresh.calculation_metrics, diagnostics
        assert database_refresh.slice_metrics, diagnostics
        assert coverage.get("validated") is True, diagnostics
        assert set(coverage["scopes"]) == {"time_series", "cross_sectional"}, diagnostics
        for scope_diagnostics in coverage["scopes"].values():
            assert scope_diagnostics["aggregate_row_count"] > 0, diagnostics
            assert scope_diagnostics["scoring_row_count"] > 0, diagnostics
            assert scope_diagnostics["oos_row_count"] > 0, diagnostics
            assert scope_diagnostics["windows"], diagnostics
        assert coverage["scopes"]["cross_sectional"]["backtest_row_count"] > 0, diagnostics
        assert all(count > 0 for count in coverage["slice_counts"].values()), diagnostics

        assert formula_consistency["formula"], diagnostics
        assert len(formula_consistency["components"]) >= 2, diagnostics
        assert all(
            component["direction"] in (-1, 1) and component["weight_contract"]
            for component in formula_consistency["components"]
        ), diagnostics
        assert any(formula_consistency["source_relations"].values()), diagnostics
