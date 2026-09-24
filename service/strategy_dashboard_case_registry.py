"""策略看板结果级用例目录和可插拔执行器契约。

策略看板的设计文件只保存测试场景和结果断言，仓库目前没有覆盖完整链路的
看板结果级适配器（部分只读 Backend 接口可以独立访问，但尚未接入本套用例）。
因此本模块提供两个明确的执行模式：

``contract``（默认）
    对每一条场景执行可执行元数据契约检查。这个模式验证用例已经具备前置、
    步骤/统一执行约定和结果断言，不能冒充真实业务结果通过。

``live``
    调用 ``QUEST_STRATEGY_DASHBOARD_EXECUTOR=module:function`` 注入的真实适配器。
    如果没有适配器，会由测试入口报告阻塞失败，而不是静默跳过业务用例。
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import re
from typing import Any, Callable


_CASE_HEADING = re.compile(r"^###\s+([A-Z]+-\d+)\s+(.+?)\s*$", re.MULTILINE)
_CASE_ID = re.compile(r"^[A-Z]+-\d+$")
_SUPPORTED_MODES = frozenset({"contract", "live"})
_MODE_ENV = "QUEST_STRATEGY_DASHBOARD_MODE"
_EXECUTOR_ENV = "QUEST_STRATEGY_DASHBOARD_EXECUTOR"


class StrategyDashboardContractError(AssertionError):
    """用例元数据不满足执行契约。"""


class StrategyDashboardAutomationBlocked(RuntimeError):
    """live 模式没有真实策略看板适配器。"""


@dataclass(frozen=True)
class StrategyDashboardCase:
    """保存一条策略看板结果级用例的可执行元数据。

    ``case_id`` 是稳定业务编号，``title`` 是中文场景标题，``source_file`` 是设计用例文件，
    ``body`` 是该标题下的完整步骤和断言文本。测试适配器可以基于这组元数据选择真实接口、
    数据样本和页面断言；目录本身不拼接 URL、不执行 SQL，也不推断未定义的业务公式。
    """

    case_id: str
    title: str
    source_file: Path
    body: str

    @property
    def priority(self) -> str | None:
        """返回用例优先级；设计文本未提供时返回 ``None``。"""

        match = re.search(r"^-\s*优先级：([^\n]+)", self.body, re.MULTILINE)
        return match.group(1).strip() if match else None

    @property
    def has_steps(self) -> bool:
        """判断用例是否声明具体操作步骤或引用统一步骤。"""

        return bool(re.search(r"^-\s*步骤：|每条用例.*顺序执行", self.body, re.MULTILINE))

    @property
    def has_assertion(self) -> bool:
        """判断用例是否声明期望或断言。"""

        return bool(re.search(r"^-\s*(?:期望|断言)：", self.body, re.MULTILINE))

    @property
    def has_precondition(self) -> bool:
        """判断用例是否声明了样本或业务前置。"""

        return bool(re.search(r"^-\s*(?:前置(?:/数据)?|数据)：", self.body, re.MULTILINE))

    @property
    def numbered_steps(self) -> tuple[str, ...]:
        """返回显式编号步骤；没有独立步骤的场景使用章节统一执行约定。"""

        return tuple(
            match.group(1).strip()
            for match in re.finditer(r"^\s*\d+\.\s+(.+?)\s*$", self.body, re.MULTILINE)
        )

    @property
    def has_execution_contract(self) -> bool:
        """判断用例是否具备显式步骤或可由统一约定执行的最小契约。

        计算结果文件中部分用例省略重复的 ``步骤``，直接沿用文件开头的五步
        统一执行顺序；只要这类用例具备前置和期望，它仍然是可执行的场景。
        """

        return bool(self.numbered_steps) or (self.has_precondition and self.has_assertion)


def load_strategy_dashboard_cases(design_root: Path) -> tuple[StrategyDashboardCase, ...]:
    """读取策略看板设计目录中的全部稳定用例。

    参数 ``design_root`` 是包含 ``01-data-and-pages.md``、``02-calculation-cases.md`` 和
    ``03-alerts-and-settlement.md`` 的目录。返回按文件名和文档顺序排列的用例元组；
    设计文件缺失、编号重复或没有任何用例时抛出 ``ValueError``。
    """

    cases: list[StrategyDashboardCase] = []
    for source_file in sorted(design_root.glob("*.md")):
        text = source_file.read_text(encoding="utf-8")
        headings = list(_CASE_HEADING.finditer(text))
        for index, heading in enumerate(headings):
            body_start = heading.start()
            body_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            cases.append(
                StrategyDashboardCase(
                    case_id=heading.group(1),
                    title=heading.group(2).strip(),
                    source_file=source_file,
                    body=text[body_start:body_end].strip(),
                )
            )
    if not cases:
        raise ValueError(f"策略看板设计目录没有可用用例: {design_root}")
    case_ids = [case.case_id for case in cases]
    duplicate_ids = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    if duplicate_ids:
        raise ValueError(f"策略看板用例编号重复: {', '.join(duplicate_ids)}")
    return tuple(cases)


def load_strategy_dashboard_executor() -> Callable[[StrategyDashboardCase], Any] | None:
    """按环境变量加载真实策略看板执行器。

    环境变量 ``QUEST_STRATEGY_DASHBOARD_EXECUTOR`` 必须是 ``module:function`` 形式。
    未配置时返回 ``None``；调用方在 ``live`` 模式下必须将其报告为明确的
    ``BLOCKED_AUTOMATION`` 失败，在默认 ``contract`` 模式下不需要适配器；
    配置了不可导入对象或对象不可调用时抛出 ``RuntimeError``，避免把适配器配置错误吞成跳过。
    """

    spec = os.getenv(_EXECUTOR_ENV, "").strip()
    if not spec:
        return None
    module_name, separator, function_name = spec.partition(":")
    if not separator or not module_name or not function_name:
        raise RuntimeError("QUEST_STRATEGY_DASHBOARD_EXECUTOR 必须使用 module:function 格式")
    try:
        target = getattr(importlib.import_module(module_name), function_name)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(f"无法加载策略看板执行器: {spec}") from exc
    if not callable(target):
        raise RuntimeError(f"策略看板执行器不可调用: {spec}")
    return target


def strategy_dashboard_mode(value: str | None = None) -> str:
    """返回策略看板执行模式，默认使用 ``contract``。

    参数 ``value`` 主要供测试和调用方显式覆盖；省略时读取
    ``QUEST_STRATEGY_DASHBOARD_MODE``。非法模式直接抛出 ``ValueError``，避免
    拼写错误悄悄退回离线检查或真实执行。
    """

    mode = (value if value is not None else os.getenv(_MODE_ENV, "contract")).strip().lower()
    if mode not in _SUPPORTED_MODES:
        supported = ", ".join(sorted(_SUPPORTED_MODES))
        raise ValueError(f"{_MODE_ENV} 必须是 {supported} 之一，实际为: {mode!r}")
    return mode


def validate_strategy_dashboard_case_contract(case: StrategyDashboardCase) -> None:
    """验证一条用例的可执行元数据契约。

    该检查只验证测试定义是否完整，不调用接口、不读取数据库，也不生成业务
    结果。它的通过含义是“这条场景可以交给 contract/live 执行入口”，不是产品
    业务通过。
    """

    errors: list[str] = []
    if not _CASE_ID.fullmatch(case.case_id):
        errors.append("case_id 必须符合大写前缀-数字格式")
    first_line = case.body.splitlines()[0].strip() if case.body.splitlines() else ""
    expected_prefix = f"### {case.case_id} {case.title}"
    if not first_line.startswith(expected_prefix):
        errors.append("正文首行必须保留稳定编号和中文标题")
    if case.priority not in {"P0", "P1", "P2"}:
        errors.append("缺少 P0/P1/P2 优先级")
    if not case.source_file.name.endswith(".md"):
        errors.append("source_file 必须来自 Markdown 设计文件")
    if not case.has_precondition:
        errors.append("缺少 前置 或 数据 条件")
    if not case.has_steps:
        errors.append("缺少 步骤；每条业务用例必须提供可执行操作步骤")
    if not case.has_execution_contract:
        errors.append("缺少显式步骤，且没有可复用的统一执行前置/断言")
    if not case.has_assertion:
        errors.append("缺少 期望 或 断言")
    if case.numbered_steps and any(not step for step in case.numbered_steps):
        errors.append("存在空的编号步骤")
    if errors:
        raise StrategyDashboardContractError(f"{case.case_id} 用例契约不完整: " + "; ".join(errors))


def execute_strategy_dashboard_case(
    case: StrategyDashboardCase,
    *,
    mode: str | None = None,
    executor: Callable[[StrategyDashboardCase], Any] | None = None,
) -> Any:
    """按明确模式执行一条策略看板场景。

    ``contract`` 返回 ``None``，代表元数据契约通过；它不会伪造接口数据。
    ``live`` 必须拿到真实 executor，否则抛出可识别的阻塞异常。
    """

    selected_mode = strategy_dashboard_mode(mode)
    validate_strategy_dashboard_case_contract(case)
    if selected_mode == "contract":
        return None
    target = executor if executor is not None else load_strategy_dashboard_executor()
    if target is None:
        raise StrategyDashboardAutomationBlocked(
            "BLOCKED_AUTOMATION: live 模式未配置 "
            f"{_EXECUTOR_ENV}=module:function；需要策略结果查询、看板接口 Schema "
            "和页面结果适配器"
        )
    return target(case)
