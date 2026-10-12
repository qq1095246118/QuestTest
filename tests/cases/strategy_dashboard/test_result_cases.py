"""策略看板结果级用例的 pytest 注册入口。

元数据检查仅由 ``test_strategy_dashboard_case_definition_is_detailed`` 执行。
默认 ``contract`` 模式下业务入口明确跳过，不把重复元数据检查显示为业务通过。
设置 ``QUEST_STRATEGY_DASHBOARD_MODE=live`` 后，执行器接收
``StrategyDashboardCase``，负责调用策略结果接口、看板接口和页面替代入口。
live 模式缺少真实适配器时会明确失败，不会把业务用例静默跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from service.strategy_dashboard_case_registry import (
    StrategyDashboardAutomationBlocked,
    StrategyDashboardCase,
    execute_strategy_dashboard_case,
    load_strategy_dashboard_cases,
    strategy_dashboard_mode,
    validate_strategy_dashboard_case_contract,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DESIGN_ROOT = _PROJECT_ROOT / "tests" / "cases" / "strategy_dashboard" / "design"
_CASES = load_strategy_dashboard_cases(_DESIGN_ROOT)


def _case_id(case: StrategyDashboardCase) -> str:
    """返回 pytest 参数显示名称。"""

    return case.case_id


def test_strategy_dashboard_case_catalog_is_complete() -> None:
    """确认主用例和本轮补充用例已全部注册且编号唯一。"""

    assert len(_CASES) == 226
    assert len({case.case_id for case in _CASES}) == 226


@pytest.mark.unit
@pytest.mark.parametrize("case", _CASES, ids=_case_id)
def test_strategy_dashboard_case_definition_is_detailed(case: StrategyDashboardCase) -> None:
    """确认每条注册用例具备可执行的前置、步骤、优先级和结果断言。"""

    validate_strategy_dashboard_case_contract(case)


@pytest.mark.strategy_dashboard
@pytest.mark.regression
@pytest.mark.parametrize("case", _CASES, ids=_case_id)
def test_strategy_dashboard_result_case(case: StrategyDashboardCase) -> None:
    """按 contract/live 模式执行一条策略看板结果级用例。"""
    if strategy_dashboard_mode() != "live":
        pytest.skip("NOT_RUN_BUSINESS: 仅登记历史主用例；元数据通过不代表真实业务通过")
    try:
        execute_strategy_dashboard_case(case)
    except StrategyDashboardAutomationBlocked as exc:
        pytest.fail(str(exc), pytrace=False)
    except AssertionError:
        raise
    except Exception as exc:  # pragma: no cover - 由外部执行器负责具体协议和业务异常
        pytest.fail(f"{case.case_id} 执行器异常: {type(exc).__name__}: {exc}", pytrace=False)
