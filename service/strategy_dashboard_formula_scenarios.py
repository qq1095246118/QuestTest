"""读取公式输入和独立预期；不在导入时计算预期或自动生成重复场景。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Any


def formula_catalog_paths(root: Path) -> tuple[Path, ...]:
    """Return the single authoritative list of scenario sources; missing files fail.

    Keeping the files explicit prevents an exploratory report or backup JSON from
    silently becoming part of the formal collection.
    """
    data = root / "tests/data/strategy_dashboard"
    return tuple(data / name for name in (
        "formula_finance.json", "formula_contract_pending.json", "formula_extended.json",
        "formula_ledger.json",
        "formula_receipt_registration.json",
        "formula_deposit_change.json",
    ))


@dataclass(frozen=True)
class FormulaScenario:
    """一条公式场景，inputs 只含源事实，expected 只在测试侧使用。"""

    scenario_id: str
    title: str
    family: str
    case_ids: tuple[str, ...]
    inputs: Mapping[str, Any]
    expected: Mapping[str, Any]
    tolerance: Decimal = Decimal("0.000000000001")
    acceptance_basis: str = "documented_business"


def load_formula_scenarios(path: Path) -> tuple[FormulaScenario, ...]:
    """读取固定 JSON 场景；空文件、重复编号或无断言时拒绝执行。"""

    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("公式场景文件必须是非空列表")
    scenarios: list[FormulaScenario] = []
    ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("每个公式场景必须为对象")
        for key in ("id", "title", "family"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f"公式场景缺少 {key}")
        if record["id"] in ids:
            raise ValueError(f"重复公式场景编号: {record['id']}")
        ids.add(record["id"])
        if not isinstance(record.get("inputs"), dict) or not isinstance(record.get("expected"), dict) or not record["expected"]:
            raise ValueError(f"{record['id']}: inputs/expected 不完整")
        if not isinstance(record.get("case_ids"), list) or not record["case_ids"] or not all(isinstance(v, str) for v in record["case_ids"]):
            raise ValueError(f"{record['id']}: 缺少历史用例映射")
        tolerance = Decimal(str(record.get("tolerance", "0.000000000001")))
        if not tolerance.is_finite() or tolerance < 0:
            raise ValueError(f"{record['id']}: 无效绝对误差")
        basis = record.get("acceptance_basis", "observed_implementation" if record["inputs"].get("implementation_profile") else "documented_business")
        if basis not in {"documented_business", "observed_implementation"}:
            raise ValueError(f"{record['id']}: invalid acceptance basis")
        scenarios.append(FormulaScenario(record["id"], record["title"], record["family"], tuple(record["case_ids"]), record["inputs"], record["expected"], tolerance, basis))
    return tuple(scenarios)


def load_formula_catalog(paths: Sequence[Path]) -> tuple[FormulaScenario, ...]:
    """合并明确公式与规则待定场景，跨文件编号冲突不能静默覆盖。"""
    if not paths:
        raise ValueError("公式目录至少包含一个场景文件")
    scenarios = tuple(case for path in paths for case in load_formula_scenarios(path))
    ids: set[str] = set()
    for case in scenarios:
        if case.scenario_id in ids:
            raise ValueError(f"跨文件重复公式场景编号: {case.scenario_id}")
        ids.add(case.scenario_id)
    return scenarios
