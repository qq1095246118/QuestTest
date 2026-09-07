"""Factor 4.0 历史用例总清单的完整性测试。"""

import pytest

from service.factor4_case_registry import (
    factor4_case_summary,
    factor4_script_migrations,
    historical_factor4_cases,
)


pytestmark = pytest.mark.unit


def test_historical_registry_contains_all_107_documented_cases() -> None:
    cases = historical_factor4_cases()
    assert len(cases) == 107
    assert len({case.case_id for case in cases}) == 107


def test_historical_registry_module_counts_match_document() -> None:
    counts: dict[str, int] = {}
    for case in historical_factor4_cases():
        counts[case.module] = counts.get(case.module, 0) + 1
    assert counts == {
        "mcp.protocol": 19,
        "security.hmac": 7,
        "environment": 12,
        "recommendation": 13,
        "metric": 11,
        "lifecycle": 19,
        "calculation": 13,
        "database": 13,
    }


def test_registry_summary_does_not_call_partial_cases_pass() -> None:
    summary = factor4_case_summary()
    assert sum(summary.values()) == 107
    assert summary.get("IMPLEMENTED_AND_EXECUTED", 0) == 0
    mapped = {"-".join(case_id.split("-")[:2]) for item in factor4_script_migrations()
              if item.test_nodes for case_id in item.source_case_ids}
    for case in historical_factor4_cases():
        if case.case_id in mapped:
            assert case.state in {"PARTIALLY_IMPLEMENTED", "DEFERRED_SCOPE"}


def test_script_migration_registry_has_no_duplicate_sources_or_case_ids() -> None:
    """每个迁移源和每个正式 Case 映射都必须可追溯，避免脚本被重复计数。"""
    migrations = factor4_script_migrations()
    assert migrations
    assert len({item.script_name for item in migrations}) == len(migrations)
    all_case_ids = [case_id for item in migrations for case_id in item.source_case_ids]
    assert all_case_ids
    registered = {case.case_id for case in historical_factor4_cases()}
    assert {case_id.split("-", 2)[0] + "-" + case_id.split("-", 2)[1] for case_id in all_case_ids} <= registered
    from pathlib import Path
    inventory = {path.name for path in (Path(__file__).parents[2] / "tmp").glob("*.py")}
    assert {item.script_name for item in migrations if not item.source_removed} == inventory


def test_script_migration_registry_never_calls_inventory_business_coverage() -> None:
    """未知/未完整迁移的来源如实保留；纯报告辅助不作为业务 Case。"""
    states = {item.status for item in factor4_script_migrations()}
    assert states <= {"ASSERTED", "PARTIAL", "REMAINING", "NON_CASE_TOOL"}
    assert "ASSERTED" in states
    assert all("inventory" not in item.case_module for item in factor4_script_migrations())
