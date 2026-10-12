"""目录逐项对账及六种真实详情入口 × 当前/历史；不会省略页面交易所参数。"""

from dataclasses import replace
from typing import Any

import pytest

from api.strategy_management_api import StrategyManagementAPI
from db.client import DatabaseClient
from db.strategy_management_source_repository import StrategyManagementSourceRepository
from service.strategy_management_live_audit import StrategyManagementLiveAudit


pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard]
_ENTRIES = (
    ("qv_leon_test111", "binance"), ("qv_grd_test", "binance"), ("qv_grd_test", "okx"),
    ("qv_cm_test", "binance"), ("qv_zhile_test", "binance"), ("qv_wilson_test_e62", "binance"),
)


@pytest.fixture(scope="module")
def strategy_live_runtime(current_overview_runtime: dict[str, Any], settings: Any) -> dict[str, Any]:
    """复用本次登录，但独立创建策略查询器及只读源仓储，不读取旧报告。"""
    current = current_overview_runtime
    audit = StrategyManagementLiveAudit(StrategyManagementAPI(current["audit"].api.client),
        StrategyManagementSourceRepository(DatabaseClient.from_settings(replace(settings.database, read_only=True))))
    catalog = audit.catalog(current["from_at"], current["to_at"])
    current["save"]({"scope": "strategy-management", "case": "SM-DATA-CATALOG", **catalog})
    return {"audit": audit, "catalog": catalog, "save": current["save"],
            "from_at": current["from_at"], "to_at": current["to_at"]}


def test_strategy_catalog_all_fields(strategy_live_runtime: dict[str, Any]) -> None:
    """完整目录身份、分页快照、金额、单账号比例、统计输入独立对账。"""
    failures = [row for row in strategy_live_runtime["catalog"]["checks"] if row["status"] == "FAIL"]
    assert not failures, failures


@pytest.mark.parametrize("population", ("running", "all"), ids=("current", "history"))
@pytest.mark.parametrize("name,exchange", _ENTRIES, ids=[name + "-" + exchange for name, exchange in _ENTRIES])
def test_strategy_entry_full_overview(name: str, exchange: str, population: str,
                                      strategy_live_runtime: dict[str, Any]) -> None:
    """所有列表入口均验证同名集合；双向敞口验证金额/比例的恒等关系。"""
    runtime = strategy_live_runtime
    entry = next((row for row in runtime["catalog"]["items"] if row["name"] == name and row["exchange"] == exchange), None)
    if entry is None:
        pytest.skip("SAMPLE_GAP: strategy entry absent from the fresh complete catalog")
    evidence = runtime["audit"].overview(runtime["catalog"]["items"], entry["strategy_definition_id"],
        exchange, population, runtime["from_at"], runtime["to_at"])
    runtime["save"]({"scope": "strategy-management", "case": "SM-DATA-ENTRY", "name": name, **evidence})
    failures = [row for row in evidence["checks"] if row["status"] == "FAIL"]
    assert not failures, failures
