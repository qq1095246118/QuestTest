"""原始累计毛收益和周期基准到真实计费收益，不生成任何真实账单。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from api.strategy_dashboard_raw_business_api import RawBusinessFixtureAPI
from service.strategy_dashboard_cycle_formula_inputs import bind_cycle_source
from service.strategy_dashboard_formula_scenarios import formula_catalog_paths, load_formula_catalog
from service.strategy_dashboard_raw_business_fixture import RawBusinessFixtureFlow
from tests.cases.strategy_dashboard.test_source_financial_formulas import financial_source_runtime  # noqa: F401
from tests.strategy_dashboard_formula_assertions import assert_formula_values
from tests.strategy_dashboard_formula_routes import SOURCE_CYCLE_AMOUNT_IDS


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]
_CASES = tuple(case for case in load_formula_catalog(formula_catalog_paths(_ROOT)) if case.scenario_id in SOURCE_CYCLE_AMOUNT_IDS)


@pytest.mark.parametrize("scenario", _CASES, ids=lambda case: case.scenario_id)
def test_cycle_source_amounts_are_computed(scenario: Any, financial_source_runtime: Any) -> None:
    """保留原始金额、锁单基准和独立对象ID，核对真实返回并精确清理。"""
    client, repository, actor_id = financial_source_runtime
    inputs = dict(scenario.inputs)
    # D and first-cycle clearance do not participate in G-Gbase. Supplying known
    # valid dependencies lets this test isolate the numerator without replacing it.
    if scenario.family == "billing_profit":
        inputs.setdefault("D", "10000")
        inputs.setdefault("Gclear", "0")
    path = _ROOT / "reports" / ("quant-cycle-source-" + scenario.scenario_id + "-" + uuid4().hex + ".json")

    def save(evidence: dict[str, Any]) -> None:
        path.write_text(json.dumps({"case_id": scenario.scenario_id, "title": scenario.title,
                                   **evidence}, ensure_ascii=False, indent=2), encoding="utf-8")

    with RawBusinessFixtureFlow(repository, RawBusinessFixtureAPI(client), actor_id, save).execute(
        lambda base: bind_cycle_source(base, inputs), metrics="realized_pnl_gross,cumulative_return",
        context={"from": "2026-09-01T00:00:00Z", "to": "2026-09-08T00:00:00Z"},
        scope="cycle", metric_basis="display",
    ) as evidence:
        response = evidence["summary_response"]
        assert response["http_status"] == 200
        body = response["body"]
        assert body["success"] is True and body["meta"]["mock"] is False
        assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
        assert body["data"]["context"]["subject_id"] == str(evidence["account_id"])
        resolved = body["data"]["context"]["resolved_cycles"]
        assert len(resolved) == 1 and resolved[0]["cycle_id"] == evidence["fixture_id"] + "_cycle"
        metric = body["data"]["profit_sharing"]["return_rate"]
        key = {"billing_profit": "eligible_profit", "billing_principal": "capital_base", "billing_return": "value"}[scenario.family]
        expected = scenario.expected[scenario.family]
        assert key in metric, f"{key}: missing field is not an explicit unavailable value"
        if expected is None:
            assert metric["status"] == "unavailable" and metric.get("reason_code")
            if inputs.get("Gbase") is None:
                assert metric["reason_code"] == "BASELINE_UNAVAILABLE", "missing cycle baseline was masked by another failure"
        else:
            assert metric["status"] == "complete"
            assert metric["formula_version"] == "account_realized_gross_settled_capital_v1"
        # Financial amounts are exact at the stored eight-decimal unit. Only a
        # serialized ratio may differ by its last decimal place.
        tolerance = Decimal("0.00000001") if key == "value" else Decimal("0")
        assert_formula_values(metric.get(key), expected, tolerance=tolerance, path=key)
        assert body["data"]["profit_sharing"]["bill_count"] == 0
        evidence["assertion"] = {"expected": expected, "actual": metric, "passed": True}
    assert evidence["cleanup_verified_zero"] is True
