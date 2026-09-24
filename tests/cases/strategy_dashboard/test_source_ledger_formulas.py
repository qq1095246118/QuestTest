"""真实摘要 API 对账本金额重算汇总、余额和收款进度，不执行分润动作。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from api.strategy_dashboard_source_fixture_api import SourceFixtureAPI
from service.strategy_dashboard_formula_scenarios import load_formula_scenarios
from service.strategy_dashboard_ledger_source_flow import LedgerSourceFlow
from tests.cases.strategy_dashboard.test_source_financial_formulas import financial_source_runtime  # noqa: F401
from tests.strategy_dashboard_formula_assertions import assert_formula_values
from tests.strategy_dashboard_formula_routes import SOURCE_LEDGER_CASE_IDS


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]
_ROOT = Path(__file__).resolve().parents[3]
_CASES = tuple(case for case in load_formula_scenarios(_ROOT / "tests/data/strategy_dashboard/formula_ledger.json")
               if case.scenario_id in SOURCE_LEDGER_CASE_IDS)


@pytest.mark.parametrize("scenario", _CASES, ids=lambda case: case.scenario_id)
def test_ledger_amounts_reach_deployed_collection_formula(scenario: Any, financial_source_runtime: Any) -> None:
    """每条输入只创建独立读视图；断言实际计算后核实全部资源清理。"""
    client, repository, actor_id = financial_source_runtime
    path = _ROOT / "reports" / ("quant-ledger-source-" + scenario.scenario_id + "-" + uuid4().hex + ".json")

    def save(evidence: dict[str, Any]) -> None:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"case_id": scenario.scenario_id, "title": scenario.title,
                                   **evidence}, ensure_ascii=False, indent=2), encoding="utf-8")

    with LedgerSourceFlow(repository, SourceFixtureAPI(client), actor_id, save).execute(scenario.inputs) as evidence:
        response = evidence["summary_response"]
        assert response["http_status"] == 200, "BLOCKED_LEDGER_QUERY"
        body = response["body"]
        assert body["success"] is True
        assert body["meta"]["mock"] is False
        assert body["meta"]["snapshot_id"] == evidence["fixture_id"]
        assert body["data"]["context"]["subject_id"] == str(evidence["account_id"])
        actual = body["data"]["profit_sharing"]
        expected = {key: value for key, value in scenario.expected.items() if key != "status"}
        for key, value in expected.items():
            # Amounts must remain exact to eight decimals. Only serialized ratios
            # have an absolute 1e-8 allowance, never a money-wide loose tolerance.
            tolerance = Decimal("0.00000001") if key == "collection_progress" else Decimal(0)
            assert_formula_values(actual.get(key), value, tolerance=tolerance, path=key)
            assert key in actual, f"missing field {key}"
        evidence["assertion"] = {"expected": expected, "actual": actual, "passed": True}
    assert evidence["cleanup_verified_zero"] is True
