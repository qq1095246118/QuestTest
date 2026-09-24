"""策略看板执行模式和用例契约的单元验证。"""

from pathlib import Path

import pytest

from service.strategy_dashboard_case_registry import (
    StrategyDashboardAutomationBlocked,
    StrategyDashboardCase,
    StrategyDashboardContractError,
    execute_strategy_dashboard_case,
    strategy_dashboard_mode,
)


def _case() -> StrategyDashboardCase:
    return StrategyDashboardCase(
        case_id="SD-999",
        title="执行模式测试",
        source_file=Path("case.md"),
        body=(
            "### SD-999 执行模式测试\n\n"
            "- 优先级：P1\n"
            "- 前置/数据：有可读取的固定结果样本。\n"
            "- 步骤：\n"
            "  1. 读取只读结果接口。\n"
            "  2. 对照返回字段。\n"
            "- 期望：字段和状态符合用例契约。"
        ),
    )


def test_contract_mode_is_executable_without_external_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUEST_STRATEGY_DASHBOARD_MODE", raising=False)
    monkeypatch.delenv("QUEST_STRATEGY_DASHBOARD_EXECUTOR", raising=False)

    assert strategy_dashboard_mode() == "contract"
    assert execute_strategy_dashboard_case(_case()) is None


def test_live_mode_reports_missing_adapter_as_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUEST_STRATEGY_DASHBOARD_EXECUTOR", raising=False)

    with pytest.raises(StrategyDashboardAutomationBlocked, match="BLOCKED_AUTOMATION"):
        execute_strategy_dashboard_case(_case(), mode="live")


def test_live_mode_calls_injected_executor() -> None:
    seen: list[str] = []

    def executor(case: StrategyDashboardCase) -> str:
        seen.append(case.case_id)
        return "live-result"

    assert execute_strategy_dashboard_case(_case(), mode="live", executor=executor) == "live-result"
    assert seen == ["SD-999"]


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="QUEST_STRATEGY_DASHBOARD_MODE"):
        strategy_dashboard_mode("unknown")


def test_incomplete_case_contract_is_rejected() -> None:
    case = _case()
    incomplete = StrategyDashboardCase(
        case_id=case.case_id,
        title=case.title,
        source_file=case.source_file,
        body=case.body.replace("- 步骤：\n  1. 读取只读结果接口。\n  2. 对照返回字段。\n", ""),
    )

    with pytest.raises(StrategyDashboardContractError, match="可执行操作步骤"):
        execute_strategy_dashboard_case(incomplete)
