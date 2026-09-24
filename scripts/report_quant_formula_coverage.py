"""按当前固定场景与真实入口登记统计覆盖；不访问服务、不推断测试通过。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

from service.strategy_dashboard_case_registry import load_strategy_dashboard_cases
from service.strategy_dashboard_formula_scenarios import formula_catalog_paths, load_formula_catalog
from service.strategy_dashboard_current_overview_audit import AMOUNT_SOURCES, METRIC_SOURCES, RATIO_SOURCES
from service.strategy_dashboard_current_overview_queries import (
    ACCOUNT_SCENARIOS, DIRECTORY_SCENARIOS, SUBSET_SCENARIOS, WINDOW_SCENARIOS,
)
from tests.strategy_dashboard_formula_routes import source_case_routes


def build_inventory(root: Path) -> dict[str, Any]:
    """读取场景及主用例映射，区分有真实适配器、缺适配器和规则待定。"""
    catalog = load_formula_catalog(formula_catalog_paths(root))
    main_cases = load_strategy_dashboard_cases(root / "tests/cases/strategy_dashboard/design")
    known_ids = {case.scenario_id for case in catalog}
    routes = source_case_routes()
    unknown = set(routes) - known_ids
    if unknown:
        raise ValueError(f"真实执行入口登记了不存在的场景: {sorted(unknown)}")
    counts: Counter[str] = Counter()
    families: dict[str, Counter[str]] = defaultdict(Counter)
    scenarios = []
    bases: Counter[str] = Counter()
    for case in catalog:
        if case.expected.get("status") == "blocked_contract":
            if case.scenario_id in routes:
                raise ValueError(f"规则待定场景不能登记为真实数值验收: {case.scenario_id}")
            state = "contract_pending"
        elif case.scenario_id in routes:
            state = "source_adapter_ready"
        else:
            state = "source_adapter_unbound"
        counts[state] += 1
        bases[case.acceptance_basis] += 1
        families[case.family][state] += 1
        scenarios.append({
            "id": case.scenario_id, "title": case.title, "family": case.family,
            "case_ids": list(case.case_ids), "expected_status": case.expected.get("status"),
            "automation_state": state,
            "acceptance_basis": case.acceptance_basis,
            "entrypoint": routes.get(case.scenario_id, "test_formula_injection.py"),
            "raw_inputs": case.inputs, "fixed_expected": case.expected,
            "contract_requirements": case.inputs.get("contract_requirements"),
            "pending_assertion_fields": list(case.inputs.get("pending_fields", [])),
        })
    # These are deliberately separate algorithm profiles. They have real input
    # cases but do not turn unresolved business definitions into approved rules.
    profiles = []
    for filename in ("source_statistics_profiles.json", "portfolio_source_profiles.json", "position_source_profiles.json"):
        for record in json.loads((root / "tests/data/strategy_dashboard" / filename).read_text(encoding="utf-8")):
            identity = record["id"]
            if identity in known_ids:
                raise ValueError(f"业务/profile 场景编号重复: {identity}")
            known_ids.add(identity)
            source_ready = record.get("kind") != "nav"
            entrypoint = ("test_source_position_boundaries.py" if filename.startswith("position")
                          else "test_source_portfolio_aggregation.py" if filename.startswith("portfolio")
                          else "test_source_history_formulas.py" if source_ready
                          else "tests/unit/test_strategy_dashboard_source_statistics.py")
            profiles.append({"id": identity, "title": record["title"],
                             "acceptance_basis": "observed_implementation",
                             "automation_state": "source_adapter_ready" if source_ready else "local_reference_only",
                             "entrypoint": entrypoint, "source_file": filename, "scenario": record})
    profile_states = Counter(item["automation_state"] for item in profiles)
    current_overview_groups = {
        "running_account_population": 1,
        "per_account_source_metrics": len(METRIC_SOURCES),
        "portfolio_amount_sums": len(AMOUNT_SOURCES),
        "amount_source_completeness": 1,
        "ratio_source_completeness": len(RATIO_SOURCES),
    }
    overview_query_scenarios = {
        "directory_queries": list(DIRECTORY_SCENARIOS),
        "account_summary_queries": list(ACCOUNT_SCENARIOS),
        "portfolio_subset_queries": list(SUBSET_SCENARIOS),
        "metric_projection_queries": list(METRIC_SOURCES),
        "window_queries": list(WINDOW_SCENARIOS),
    }
    overview_query_groups = {name: len(values) for name, values in overview_query_scenarios.items()}
    return {
        "scope": "quantitative trading / profit-sharing formula source-input validation",
        "historical_main_cases": len(main_cases),
        "parameterized_scenarios": len(catalog),
        "formula_families": len(families),
        "automation_states": dict(sorted(counts.items())),
        "acceptance_bases": dict(sorted(bases.items())),
        "partial_contract_scenarios": sum(bool(case.inputs.get("pending_fields")) for case in catalog),
        "families": {name: dict(sorted(values.items())) for name, values in sorted(families.items())},
        "scenarios": scenarios,
        "implementation_profile_scenarios": len(profiles),
        "implementation_profile_states": dict(sorted(profile_states.items())),
        "implementation_profiles": profiles,
        "all_distinct_fixed_input_scenarios": len(catalog) + len(profiles),
        "natural_source_audit": {
            "entrypoint": "test_source_risk_statistics.py", "metric_checks": 5,
            "counting_rule": "Fresh natural samples only; never substitute for controlled input profiles.",
        },
        "current_overview_source_audit": {
            "entrypoint": "test_source_current_overview.py",
            "case_ids": ["BV-101"],
            "coverage_state": "partial_business_case_implementation",
            "assertion_instances": sum(current_overview_groups.values()),
            "assertion_groups": current_overview_groups,
            "counting_rule": "Fresh natural source assertions; not fixed-input scenarios and excluded from all_distinct_fixed_input_scenarios.",
            "coverage_limits": "BV-101 is partially implemented: directory membership plus same-generation DB amount reconciliation; no controlled boundary inputs or independently defined portfolio ratio formula.",
        },
        "current_overview_query_audit": {
            "entrypoint": "test_source_current_overview_queries.py",
            "case_ids": ["BV-101"],
            "coverage_state": "partial_business_case_implementation",
            "assertion_instances": sum(overview_query_groups.values()),
            "assertion_groups": overview_query_groups,
            "scenarios": overview_query_scenarios,
            "counting_rule": "Separate fresh query instances, not additional fixed-input scenarios, metric counts or offline framework passes.",
            "coverage_limits": "Read-only real-query variations. Missing natural populations are sample gaps, not product passes. Directory-only NT identities do not imply account-summary support; portfolio ratio formulas remain outside this audit.",
        },
        "current_overview_total_assertion_instances": sum(current_overview_groups.values()) + sum(overview_query_groups.values()),
        "execution_result": "NOT_EVALUATED: this is an inventory, not an execution report",
        "counting_rule": "Main cases, parameterized scenarios and local framework tests must not be added together.",
    }


def main() -> None:
    """输出即时统计；可选择保存完整 JSON 清单供回归筛选，不创建业务数据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="完整 JSON 清单输出位置")
    parser.add_argument("--markdown", type=Path, help="按公式族列出每条场景、输入、预期和实际适配状态")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    inventory = build_inventory(root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_scenario_inventory(inventory), encoding="utf-8")
    print(json.dumps({key: value for key, value in inventory.items() if key not in {"families", "scenarios", "implementation_profiles"}},
                     ensure_ascii=False, indent=2))


def render_scenario_inventory(inventory: dict[str, Any]) -> str:
    """Render every scenario from the same registry without inventing pass results."""
    labels = {
        "source_adapter_ready": "已接真实输入与接口，执行结论另见回归报告",
        "source_adapter_unbound": "固定输入/预期已建，真实消费入口未接通",
        "contract_pending": "输入已建，业务规则待确认，不能判数值通过",
        "local_reference_only": "仅本地参考算法验证",
    }
    lines = ["# 分润平台逐条公式与数据场景", "",
             "本文件由当前自动化目录生成，不代表所有场景已经在服务端执行。"
             "历史主 Case、本地单元测试、参数化场景分别统计，不能相加。", "",
             f"固定输入场景总数：{inventory['all_distinct_fixed_input_scenarios']}；"
             f"主目录 {inventory['parameterized_scenarios']}；"
             f"独立实现 profile {inventory['implementation_profile_scenarios']}。", ""]
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in inventory["scenarios"]:
        by_family[case["family"]].append(case)
    for family, cases in sorted(by_family.items()):
        lines.extend([f"## {family}（{len(cases)} 条）", ""])
        for case in cases:
            lines.extend([f"### {case['id']}：{case['title']}", "",
                          f"状态：{labels[case['automation_state']]}。"
                          f"依据：{case['acceptance_basis']}。"
                          f"历史主用例：{', '.join(case['case_ids'])}。", "",
                          f"自动化入口：`tests/cases/strategy_dashboard/{case['entrypoint']}`。", ""])
            if case["pending_assertion_fields"]:
                lines.extend([f"单独待确认字段：{', '.join(case['pending_assertion_fields'])}。", ""])
            lines.extend(["原始输入与独立预期：", "", "```json",
                          json.dumps({"inputs": case["raw_inputs"], "expected": case["fixed_expected"]},
                                     ensure_ascii=False, indent=2), "```", ""])
    lines.extend(["## 单列的当前实现 profile", "",
                  "这些用例用于核对已观察到的实现，不能替代尚未批准的业务算法。", ""])
    for case in inventory["implementation_profiles"]:
        lines.extend([f"### {case['id']}：{case['title']}", "",
                      f"状态：{labels[case['automation_state']]}。入口：`{case['entrypoint']}`。", "",
                      "```json", json.dumps(case["scenario"], ensure_ascii=False, indent=2), "```", ""])
    current_overview = inventory["current_overview_source_audit"]
    lines.extend(["## 当前数据自然样本核对（非固定输入场景）", "",
                  f"入口：`tests/cases/strategy_dashboard/{current_overview['entrypoint']}`；"
                  f"共 {current_overview['assertion_instances']} 个断言实例。", "",
                  "BV-101 部分落地：运行账户范围 1 项、账户明细 8 项、金额汇总 5 项、"
                  "金额完整性 1 项、比例完整性 3 项。", "",
                  "账户目录确定参与集合，数据库同代次原始样本独立核对数值；"
                  "不计入固定输入场景总数，也不代表组合收益率公式已经完成独立验证。", ""])
    current_queries = inventory["current_overview_query_audit"]
    lines.extend(["## 当前数据查询扩展（与原始核对分别统计）", "",
                  f"入口：`tests/cases/strategy_dashboard/{current_queries['entrypoint']}`；"
                  f"共 {current_queries['assertion_instances']} 个查询实例。", "",
                  "目录分页/筛选 6 项、单账户分析 3 项、组合子集 8 项、指标投影 8 项、"
                  "日期与时区 3 项。每个实例可以核对多个字段，字段数不重复计为用例。", "",
                  f"当前数据两组共 {inventory['current_overview_total_assertion_instances']} 个实例；"
                  "这些是登记数量，不代表真实执行通过数量。自然样本缺口、接口错误和断言失败必须分开输出。", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    main()
