"""组合因子核心指标、公式、权重和来源关系验收的离线单元测试。"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from service.factor_combo_models import DatabaseRefreshEvidence, FactorComboFlowError, FlowOutcome
from service.factor_combo_service import FactorComboService


pytestmark = pytest.mark.unit


def _service() -> FactorComboService:
    """创建不执行网络或数据库操作的组合因子 Service 对象。"""

    return FactorComboService.__new__(FactorComboService)


def _static_fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """构造静态权重组合的报告、登记实体、组件和来源关系。"""

    formula = "0.6*factor_a-0.4*factor_b"
    version = {
        "id": 10,
        "combo_id": 100,
        "combo_version_hash": "a" * 64,
    }
    components = [
        {
            "combo_id": 10,
            "component_factor_id": 1,
            "component_sub_factor_id": 11,
            "sub_factor_name": "factor_a",
            "direction": 1,
            "weight": Decimal("0.6"),
            "transform_json": {"normalization": "zscore"},
        },
        {
            "combo_id": 10,
            "component_factor_id": 2,
            "component_sub_factor_id": 22,
            "sub_factor_name": "factor_b",
            "direction": -1,
            "weight": Decimal("0.4"),
            "transform_json": {"normalization": "zscore"},
        },
    ]
    report = {
        "factor_name": "combined-factor",
        "combo": {"formula": formula},
        "components": [
            {"sub_factor_code": "factor_a", "direction": 1, "weight": 0.6},
            {"sub_factor_code": "factor_b", "direction": -1, "weight": 0.4},
        ],
    }
    sub_factor = {
        "id": 900,
        "formula_summary": formula,
        "metadata": json.dumps({"report": {"combo": {"formula": formula}}}),
    }
    factor_detail = {
        "id": 901,
        "calc_logic": formula,
        "params": json.dumps({"combo": {"formula": formula}}),
    }
    source_graph = {
        "version": dict(version),
        "components": list(components),
        "parent_factor_relations": [{"factor_id": 1, "sub_factor_id": 900}],
        "parent_sub_factor_relations": [],
    }
    return report, sub_factor, factor_detail, version, components, source_graph


def _model_fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """构造模型产物组合，验证 NULL 静态权重由模型重放契约解释。"""

    report, sub_factor, factor_detail, version, components, source_graph = _static_fixture()
    report["components"] = [{"sub_factor_code": "factor_a", "direction": 1, "weight": None}]
    report["combo"]["formula"] = "model(factor_a)"
    sub_factor["formula_summary"] = "model(factor_a)"
    sub_factor["metadata"] = json.dumps({"report": {"combo": {"formula": "model(factor_a)"}}})
    factor_detail["calc_logic"] = "model(factor_a)"
    factor_detail["params"] = json.dumps({"combo": {"formula": "model(factor_a)"}})
    components[:] = [
        {
            "combo_id": 10,
            "component_factor_id": 1,
            "component_sub_factor_id": 11,
            "sub_factor_name": "factor_a",
            "direction": 1,
            "weight": None,
            "transform_json": {
                "weight_contract": "model_artifact",
                "algorithm": "ElasticNet",
                "feature_column": "factor_a",
                "model_replay": {"sub_factor_id": 11, "direction": 1},
            },
        }
    ]
    source_graph["components"] = list(components)
    source_graph["parent_factor_relations"] = [{"factor_id": 1, "sub_factor_id": 900}]
    return report, sub_factor, factor_detail, version, components, source_graph


def _metric_evidence(*, include_slices: bool = True) -> DatabaseRefreshEvidence:
    """构造同时包含时序和截面汇总及原始切片的刷新证据。"""

    common = {
        "factor_id": 900,
        "is_sub_factor_id": 1,
        "run_id": "ic-run-900",
        "universe_key": "main",
        "window_scope": "rolling",
        "symbol": "",
        "mean_ic": 0.1,
        "icir": 1.2,
        "mean_rank_ic": 0.08,
        "rank_icir": 0.9,
        "is_icir": 1.1,
        "oos_icir": 0.8,
        "icir_oos_retention": 0.72,
        "rank_is_icir": 1.0,
        "rank_oos_icir": 0.7,
        "rank_icir_oos_retention": 0.7,
        "ic_score": 60,
        "rank_ic_score": 58,
        "icir_score": 62,
        "rank_icir_score": 57,
        "t_stat_score": 55,
        "oos_retention_score": 52,
        "monotonicity_score": 50,
        "long_short_score": 51,
        "final_score": 56,
        "ic_t_stat": None,
        "rank_ic_t_stat": None,
        "monotonicity_ratio": None,
        "mean_long_short_return": None,
        "long_short_annual_return": None,
        "long_short_t_stat": None,
    }
    time_summary = {"id": 1001, "summary_id": 1001, "ic_scope": "time_series", **common}
    cross_summary = {
        **common,
        "id": 1002,
        "summary_id": 1002,
        "ic_scope": "cross_sectional",
        "ic_t_stat": 2.1,
        "rank_ic_t_stat": 1.9,
        "monotonicity_ratio": 0.8,
        "mean_long_short_return": 0.03,
        "long_short_annual_return": 0.2,
        "long_short_t_stat": 2.4,
    }
    validity = {
        "id": 2001,
        "factor_id": 900,
        "is_sub_factor_id": 1,
        "run_id": "ic-run-900",
        "time_series_summary_id": 1001,
        "cross_sectional_summary_id": 1002,
    }
    slices = [
        {
            "id": 3001,
            "run_id": "ic-run-900",
            "factor_id": 900,
            "is_sub_factor_id": 1,
            "ic_scope": "time_series",
            "universe_key": "main",
            "symbol": "",
            "window_scope": "rolling",
            "sample_segment": "is",
            "slice_start": "2026-01-01",
            "slice_end": "2026-01-31",
            "ic": 0.1,
            "rank_ic": 0.08,
        },
        {
            "id": 3002,
            "run_id": "ic-run-900",
            "factor_id": 900,
            "is_sub_factor_id": 1,
            "ic_scope": "cross_sectional",
            "universe_key": "main",
            "symbol": "",
            "window_scope": "rolling",
            "sample_segment": "is",
            "slice_start": "2026-01-01",
            "slice_end": "2026-01-31",
            "ic": 0.09,
            "rank_ic": 0.07,
        },
    ]
    return DatabaseRefreshEvidence(
        sub_factor_id=900,
        calculation_runs=[{"run_id": "ic-run-900"}],
        validity_snapshots=[validity],
        refresh_run_ids=("ic-run-900",),
        matched_run_ids=("ic-run-900",),
        calculation_metrics=(time_summary, cross_summary),
        slice_metrics=tuple(slices if include_slices else []),
    )


class TestFormulaWeightAndSourceConsistency:
    """验证登记后的公式、权重和来源图验收。"""

    def test_static_components_and_formula_are_reconciled(self) -> None:
        """静态权重、方向、公式快照和母因子来源关系一致时通过。"""

        fixture = _static_fixture()
        result = _service().validate_registered_formula_and_sources(*fixture)

        assert result["formula"] == "0.6*factor_a-0.4*factor_b", result
        assert len(result["components"]) == 2, result
        assert result["source_relations"]["parent_factor"], result

    def test_model_artifact_null_weight_requires_replay_contract(self) -> None:
        """模型产物可以没有静态权重，但必须保留算法、特征和重放信息。"""

        fixture = _model_fixture()
        result = _service().validate_registered_formula_and_sources(*fixture)

        assert result["components"][0]["weight_contract"] == "model_artifact", result

    def test_missing_source_relation_is_rejected(self) -> None:
        """没有任何可追溯来源关系时必须判定登记契约失败。"""

        fixture = list(_static_fixture())
        fixture[-1] = {**fixture[-1], "parent_factor_relations": []}

        with pytest.raises(FactorComboFlowError) as error:
            _service().validate_registered_formula_and_sources(*fixture)

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert "traceable source relation" in str(error.value), error.value


class TestCoreMetricCoverage:
    """验证新版 summary、回测字段和原始 slice 的完整性。"""

    def test_time_series_cross_sectional_metrics_and_slices_pass(self) -> None:
        """时序/截面汇总、截面回测字段和同一 Run 的原始切片齐全时通过。"""

        result = _service().validate_core_metric_coverage(_metric_evidence())

        assert set(result["scopes"]) == {"time_series", "cross_sectional"}, result
        assert result["slice_counts"] == {"cross_sectional": 1, "time_series": 1}, result

    def test_missing_slice_metrics_are_rejected(self) -> None:
        """只有 summary 没有原始切片时不能把刷新标记为核心指标通过。"""

        with pytest.raises(FactorComboFlowError) as error:
            _service().validate_core_metric_coverage(_metric_evidence(include_slices=False))

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value
        assert "slice_metrics" in str(error.value), error.value
