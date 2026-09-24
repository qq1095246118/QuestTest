"""公式场景到真实执行入口的登记；不包含产品计算或业务编排。"""

from tests.strategy_dashboard_history_routes import HISTORY_SOURCE_IDS

SOURCE_SUPPORTED_SCENARIOS = frozenset({
    "FM-POSITION-001", "FM-POSITION-005", "FM-POSFIELDS-001",
    "FM-POSFIELDS-002", "FM-POSFIELDS-004", "FM-POSFIELDS-005",
})

SOURCE_CUMULATIVE_CASE_IDS = frozenset({
    "FM-RETURN-001", "FM-RETURN-002", "FM-RETURN-003", "FM-RETURN-004",
    "FM-RETURN-005", "FM-RETURN-006",
})

SOURCE_LEDGER_CASE_IDS = frozenset(f"FM-LEDGER-{index:03d}" for index in range(1, 11))
SOURCE_EXECUTION_TIME_IDS = frozenset({
    "FM-LATESTEXEC-001", "FM-LATESTEXEC-002", "FM-LATESTEXEC-003", "FM-LATESTEXEC-004",
    "FXT-LATESTEXEC-001",
})
SOURCE_CYCLE_AMOUNT_IDS = frozenset({
    "FM-PRINCIPAL-002", "FM-PROFIT-001", "FM-PROFIT-003", "FM-PROFIT-004",
    "FM-BILLRETURN-004", "FXT-PROFIT-001",
})
SOURCE_RECEIPT_REGISTRATION_IDS = frozenset(f"FIM-RECEIPTREG-{index:03d}" for index in range(1, 11))
SOURCE_CAPITAL_ENDPOINT_IDS = frozenset({
    *(f"FM-INTERVAL-{index:03d}" for index in (1, 2, 3, 4, 5, 9)),
    "FXT-INTERVAL-001", "FXT-INTERVAL-003",
    *(f"FIM-DEPOSITDELTA-{index:03d}" for index in range(1, 7)),
})
SOURCE_POSITION_BOUNDARY_IDS = frozenset({
    "FXT-POSITION-001", "FXT-POSITION-002", "FXT-POSFIELDS-001",
})

SOURCE_ROUTES = {
    "test_source_position_aggregation.py": SOURCE_SUPPORTED_SCENARIOS,
    "test_source_financial_formulas.py": SOURCE_CUMULATIVE_CASE_IDS,
    "test_source_ledger_formulas.py": SOURCE_LEDGER_CASE_IDS,
    "test_source_history_formulas.py": HISTORY_SOURCE_IDS,
    "test_source_execution_times.py": SOURCE_EXECUTION_TIME_IDS,
    "test_source_cycle_amounts.py": SOURCE_CYCLE_AMOUNT_IDS,
    "test_source_receipt_registration.py": SOURCE_RECEIPT_REGISTRATION_IDS,
    "test_source_capital_endpoints.py": SOURCE_CAPITAL_ENDPOINT_IDS,
    "test_source_position_boundaries.py": SOURCE_POSITION_BOUNDARY_IDS,
}


def source_case_routes() -> dict[str, str]:
    """返回唯一场景到真实入口的映射，重复登记时立即报错。"""
    routes: dict[str, str] = {}
    for entrypoint, scenario_ids in SOURCE_ROUTES.items():
        for scenario_id in scenario_ids:
            if scenario_id in routes:
                raise ValueError(f"场景重复登记执行入口: {scenario_id}")
            routes[scenario_id] = entrypoint
    return routes
