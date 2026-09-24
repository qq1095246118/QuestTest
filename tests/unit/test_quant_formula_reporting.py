"""Prevent inventory, local tests and setup failures from becoming business passes."""

import json
from pathlib import Path

import pytest

from scripts import report_quant_formula_results as reporting
from scripts.report_quant_formula_coverage import build_inventory, render_scenario_inventory


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


def _junit(root: Path, name: str, stamp: str, classname: str, outcome: str = "",
           test_name: str = "test_formula[FM-ONE]") -> Path:
    path = root / name
    path.write_text(f'<testsuites><testsuite timestamp="{stamp}"><testcase classname="{classname}" '
                    f'name="{test_name}">{outcome}</testcase></testsuite></testsuites>', encoding="utf-8")
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


def test_current_overview_keeps_same_metric_checks_separate_from_risk_and_controlled(report_root):
    stamp = "2026-09-24T12:00:00+08:00"
    overview_class = "tests.cases.strategy_dashboard.test_source_current_overview"
    risk = _junit(report_root, "risk.xml", stamp,
                  "tests.cases.strategy_dashboard.test_source_risk_statistics",
                  test_name="test_source_statistic[margin_equity]")
    detail = _junit(report_root, "detail.xml", stamp, overview_class,
                    test_name="test_running_account_metric_matches_exact_database_source[margin_equity]")
    total = _junit(report_root, "total.xml", stamp, overview_class, "<failure/>",
                   test_name="test_current_portfolio_amount_equals_database_running_account_sum[margin_equity]")
    result = reporting.reconcile_runs(report_root, [risk, detail, total])
    checks = result["current_overview_source_checks"]
    assert len(checks) == 2
    assert {row["result"] for row in checks} == {"passed", "failed"}
    assert len({row["id"] for row in checks}) == 2
    assert result["current_overview_results"] == {"failed": 1, "passed": 1}
    assert result["natural_source_checks"][0]["id"] == "margin_equity"
    assert result["natural_source_checks"][0]["result"] == "passed"
    assert result["current_distinct_fixed_scenarios"] == 2
    assert result["executed_distinct"] == 0
    assert result["results"] == {}
    assert result["not_run"] == ["FM-ONE"]
    assert result["ignored_out_of_scope_results"] == []


def test_current_overview_latest_setup_error_is_recorded_not_counted_as_pass(report_root):
    classname = "tests.cases.strategy_dashboard.test_source_current_overview"
    name = "test_current_overview_population_includes_all_directory_running_accounts"
    passed = _junit(report_root, "overview-pass.xml", "2026-09-24T12:00:00+08:00",
                    classname, test_name=name)
    error = _junit(report_root, "overview-error.xml", "2026-09-24T12:01:00+08:00",
                   classname, "<error/>", test_name=name)
    result = reporting.reconcile_runs(report_root, [error, passed])
    assert len(result["current_overview_source_checks"]) == 1
    assert result["current_overview_source_checks"][0]["result"] == "error"
    assert result["current_overview_results"] == {"error": 1}
    assert result["current_overview_source_checks"][0]["executed_at"] == "2026-09-24T12:01:00+08:00"
    assert result["natural_source_checks"] == []
    assert result["executed_distinct"] == 0
    assert result["last_completed_business_assertion_counts"] == {}
    assert result["results"] == {}


def test_overview_query_file_cannot_overwrite_same_named_original_check(report_root):
    """Module identity separates existing DB checks from added query variations."""
    stamp = "2026-09-24T13:00:00+08:00"
    name = "test_source_metric[margin_equity]"
    original = _junit(report_root, "original.xml", stamp,
                      "tests.cases.strategy_dashboard.test_source_current_overview", test_name=name)
    extension = _junit(report_root, "extension.xml", stamp,
                       "tests.cases.strategy_dashboard.test_source_current_overview_queries",
                       "<failure/>", test_name=name)
    result = reporting.reconcile_runs(report_root, [original, extension])
    assert result["current_overview_results"] == {"passed": 1}
    assert result["current_overview_query_results"] == {"failed": 1}
    assert result["current_overview_combined_results"] == {"failed": 1, "passed": 1}
    assert result["current_overview_source_checks"][0]["entrypoint"] == "test_source_current_overview.py"
    assert result["current_overview_query_checks"][0]["entrypoint"] == "test_source_current_overview_queries.py"
    assert result["executed_distinct"] == 0
    assert result["results"] == {}


def test_query_sample_gap_and_not_live_skip_are_distinct_from_success(report_root):
    """Missing natural samples remain visible and are not counted as passes."""
    classname = "tests.cases.strategy_dashboard.test_source_current_overview_queries"
    missing = _junit(report_root, "missing.xml", "2026-09-24T13:00:00+08:00", classname,
                     '<skipped message="SAMPLE_GAP: no stopped account"/>',
                     test_name="test_directory[stopped_filter]")
    not_live = _junit(report_root, "not-live.xml", "2026-09-24T13:00:00+08:00", classname,
                      '<skipped message="NOT_RUN_LIVE: requires --live"/>',
                      test_name="test_directory[running_filter]")
    result = reporting.reconcile_runs(report_root, [missing, not_live])
    assert result["current_overview_query_results"] == {"skipped": 2}
    assert len(result["current_overview_sample_gaps"]) == 1
    assert result["current_overview_sample_gaps"][0]["id"] == "test_directory[stopped_filter]"
    assert "no stopped account" in result["current_overview_sample_gaps"][0]["skip_reason"]
    assert result["current_overview_results"] == {}
    assert result["natural_source_checks"] == []


def test_latest_query_setup_error_does_not_reuse_previous_query_pass(report_root):
    classname = "tests.cases.strategy_dashboard.test_source_current_overview_queries"
    name = "test_account_summary[bound_known]"
    passed = _junit(report_root, "query-pass.xml", "2026-09-24T13:00:00+08:00",
                    classname, test_name=name)
    error = _junit(report_root, "query-error.xml", "2026-09-24T13:01:00+08:00",
                   classname, "<error/>", test_name=name)
    result = reporting.reconcile_runs(report_root, [passed, error])
    assert result["current_overview_query_results"] == {"error": 1}
    assert result["current_overview_query_checks"][0]["executed_at"] == "2026-09-24T13:01:00+08:00"
    assert result["current_overview_source_checks"] == []
    assert result["executed_distinct"] == 0


def test_inventory_separates_original_and_query_instances_from_fixed_input_scenarios():
    root = Path(__file__).resolve().parents[2]
    inventory = build_inventory(root)
    original = inventory["current_overview_source_audit"]
    extension = inventory["current_overview_query_audit"]
    assert original["assertion_instances"] == 18
    assert extension["assertion_instances"] == 28
    assert extension["assertion_groups"] == {
        "directory_queries": 6, "account_summary_queries": 3, "portfolio_subset_queries": 8,
        "metric_projection_queries": 8, "window_queries": 3,
    }
    assert inventory["current_overview_total_assertion_instances"] == 46
    assert inventory["all_distinct_fixed_input_scenarios"] == (
        inventory["parameterized_scenarios"] + inventory["implementation_profile_scenarios"]
    )
    markdown = render_scenario_inventory(inventory)
    assert "test_source_current_overview_queries.py" in markdown
    assert "28 个查询实例" in markdown
    assert "46 个实例" in markdown
    assert "登记数量，不代表真实执行通过数量" in markdown
