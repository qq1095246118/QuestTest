"""当前完整持仓集合的聚合边界，不冒充历史去重或跨市场身份采集验证。"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from service.strategy_dashboard_formula_scenarios import formula_catalog_paths, load_formula_catalog
from service.strategy_dashboard_position_source_inputs import complete_position_financial_inputs
from tests.cases.strategy_dashboard.test_source_position_aggregation import source_fixture_flow


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_SOURCE_IDS = frozenset({"FXT-POSITION-001", "FXT-POSITION-002", "FXT-POSFIELDS-001"})
_CANONICAL = [case for case in load_formula_catalog(formula_catalog_paths(_ROOT)) if case.scenario_id in CANONICAL_SOURCE_IDS]
_PROFILES = json.loads((_ROOT / "tests/data/strategy_dashboard/position_source_profiles.json").read_text(encoding="utf-8"))


def _verify(response: dict[str, Any], evidence: dict[str, Any], expected: dict[str, Any]) -> None:
    assert response["http_status"] == 200
    body = response["body"]
    assert body["success"] and body["meta"]["mock"] is False
    assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
    assert body["meta"]["generation"] == evidence["fixture_id"] + "_batch"
    assert body["data"]["context"]["subject_id"] == str(evidence["account_id"])
    totals = body["data"]["exposure_summary"]
    assert totals["basis"] == "gross_notional_v1"
    mapping = {"gross_notional": "total_notional", "used_margin": "margin_used"}
    evidence["expected"] = expected
    evidence["actual_exposure"] = totals
    for key, value in expected.items():
        if key == "status":
            continue
        field = mapping.get(key, key)
        actual = totals[field]
        if field in {"position_count", "symbol_count"}:
            assert actual == value, field
        elif value is None:
            assert actual["value"] is None and actual["status"] in {"unavailable", "partial"}, field
            assert actual["reason_code"], field
        else:
            assert actual["status"] == "complete", (field, actual)
            assert Decimal(actual["value"]) == Decimal(value), (field, actual, value)
    evidence["assertion_passed"] = True


@pytest.mark.parametrize("case", _CANONICAL, ids=lambda case: case.scenario_id)
def test_current_source_position_boundary(case: Any, source_fixture_flow: Any) -> None:
    """把完整原始集合、负空头及极小盈亏交给实际聚合器，不预先求和。"""
    rows = list(case.inputs["positions"])
    if case.family == "position_totals":
        rows = complete_position_financial_inputs(rows)
    with source_fixture_flow.execute(source_positions=rows) as evidence:
        evidence["scenario_id"] = case.scenario_id
        evidence["title"] = case.title
        evidence["original_scenario_inputs"] = dict(case.inputs)
        _verify(evidence["summary_response"], evidence, dict(case.expected))
        assert len(evidence["account_positions"]) == len(case.inputs["positions"])
    assert evidence["cleanup_verified_zero"] is True


@pytest.mark.parametrize("case", _PROFILES, ids=lambda case: case["id"])
def test_current_exposure_implementation_profile(case: dict[str, Any], source_fixture_flow: Any) -> None:
    """验证当前声明的敞口实现版本，不替业务签认跨合约风险抵消规则。"""
    def prepare(payload: dict[str, Any]) -> dict[str, Any]:
        raw = payload["snapshots"][0]["Payload"]
        raw["margin_equity"] = case["inputs"]["equity"]
        raw["field_status"]["margin_equity"] = {
            "status": "available" if raw["margin_equity"] is not None else "unavailable",
            "reason_code": None if raw["margin_equity"] is not None else "SOURCE_FIELD_UNAVAILABLE"}
        return payload

    with source_fixture_flow.execute(source_positions=case["inputs"]["positions"], payload_builder=prepare) as evidence:
        evidence["scenario_id"] = case["id"]
        evidence["title"] = case["title"]
        evidence["acceptance_basis"] = case["acceptance_basis"]
        _verify(evidence["summary_response"], evidence, case["expected"])
    assert evidence["cleanup_verified_zero"] is True
