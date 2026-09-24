"""Prevent inventory, local tests and setup failures from becoming business passes."""

import json
from pathlib import Path

import pytest

from scripts import report_quant_formula_results as reporting


@pytest.fixture
def report_root(tmp_path: Path, monkeypatch):
    data = tmp_path / "tests/data/strategy_dashboard"
    data.mkdir(parents=True)
    (data / "formula_bug_registry.json").write_text(json.dumps([
        {"title": "固定中文问题名称", "scenario_ids": ["FM-ONE"], "representative_scenario": "FM-ONE"}
    ]), encoding="utf-8")
    monkeypatch.setattr(reporting, "build_inventory", lambda _: {
        "scope": "formula", "all_distinct_fixed_input_scenarios": 2,
        "scenarios": [{"id": "FM-ONE", "title": "原始业务样本", "automation_state": "source_adapter_ready"},
                      {"id": "FM-UNBOUND", "title": "未接通", "automation_state": "source_adapter_unbound"}],
        "implementation_profiles": [], "automation_states": {"source_adapter_unbound": 1},
        "implementation_profile_states": {},
    })
    return tmp_path


def _junit(root: Path, name: str, stamp: str, classname: str, outcome: str = "") -> Path:
    path = root / name
    path.write_text(f'<testsuites><testsuite timestamp="{stamp}"><testcase classname="{classname}" '
                    f'name="test_formula[FM-ONE]">{outcome}</testcase></testsuite></testsuites>', encoding="utf-8")
    return path


def test_later_setup_error_does_not_erase_previous_calculation_failure(report_root):
    failed = _junit(report_root, "failed.xml", "2026-09-23T18:00:00+08:00",
                    "tests.cases.test_source_amount", "<failure/>")
    blocked = _junit(report_root, "blocked.xml", "2026-09-24T01:00:00+08:00",
                    "tests.cases.test_source_amount", "<error/>")
    result = reporting.reconcile_runs(report_root, [blocked, failed])
    assert result["results"] == {"error": 1}
    assert result["last_completed_business_assertion_counts"] == {"failed": 1}
    assert result["known_bug_matches"][0]["title"] == "固定中文问题名称"
    assert result["controlled_input_results"][0]["last_completed_business_assertion"]["executed_at"].startswith("2026-09-23")


def test_local_golden_pass_cannot_fill_missing_real_execution(report_root):
    local = _junit(report_root, "local.xml", "2026-09-24T01:00:00+08:00", "tests.unit.test_formula_oracles")
    result = reporting.reconcile_runs(report_root, [local])
    assert result["results"] == {}
    assert result["not_run"] == ["FM-ONE"]
    assert result["source_unbound"] == 1


def test_conflicting_same_time_outcomes_fail_instead_of_selecting_green(report_root):
    passed = _junit(report_root, "pass.xml", "2026-09-24T01:00:00+08:00", "tests.cases.test_source_amount")
    failed = _junit(report_root, "fail.xml", "2026-09-24T01:00:00+08:00", "tests.cases.test_source_amount", "<failure/>")
    with pytest.raises(ValueError, match="Conflicting same-time"):
        reporting.reconcile_runs(report_root, [passed, failed])
