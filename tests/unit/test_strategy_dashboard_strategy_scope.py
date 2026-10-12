"""Regression guards for same-name strategy overviews, not live business passes."""

from pathlib import Path
from typing import Any

import pytest

from scripts.run_quant_interface_regression import Regression
from scripts.run_quant_readonly_supplement import Supplement
from service.strategy_dashboard_strategy_scope import (
    strategy_overview_differences,
    strategy_overview_scopes,
)


def _rows() -> list[dict[str, Any]]:
    return [
        {"name": "A", "strategy_definition_id": "s1", "exchange": "binance",
         "account_ids": ["bn1"], "account_count": 1, "runtime_status": "running"},
        {"name": "A", "strategy_definition_id": "s1", "exchange": "okx",
         "account_ids": ["ok1"], "account_count": 1, "runtime_status": "stopped"},
        {"name": "A", "strategy_definition_id": "s2", "exchange": "bybit",
         "account_ids": ["by1"], "account_count": 1, "runtime_status": "running"},
        {"name": "A_extra", "strategy_definition_id": "s3", "exchange": "binance",
         "account_ids": ["other"], "account_count": 1, "runtime_status": "running"},
    ]


def _detail() -> dict[str, Any]:
    return {"name": "A", "account_ids": ["bn1", "ok1", "by1"],
            "account_count": 3, "runtime_status": "running"}


def test_scope_unions_exchanges_and_different_entry_ids_but_not_similar_names():
    scopes = strategy_overview_scopes(_rows())
    assert len(scopes) == 2
    assert scopes[0].definition_ids == ("s1", "s2")
    assert scopes[0].account_ids == {"bn1", "ok1", "by1"}
    assert scopes[1].account_ids == {"other"}


def test_duplicate_relations_do_not_inflate_overview_membership():
    rows = _rows()
    scope = strategy_overview_scopes(rows + [dict(rows[0])])[0]
    assert len(scope.account_ids) == 3
    assert not any(f["kind"] == "BUG_CANDIDATE" for f in strategy_overview_differences(scope, _detail()))


def test_stopped_clicked_row_does_not_require_stopped_overview():
    scope = strategy_overview_scopes(_rows())[0]
    findings = strategy_overview_differences(scope, _detail())
    assert not any(f["kind"] == "BUG_CANDIDATE" for f in findings)
    assert any(f["kind"] == "OBSERVATION" for f in findings)


@pytest.mark.parametrize("ids,expected_title", [
    (["bn1"], "策略总览关联账号漏汇总或混入其他策略"),
    (["bn1", "ok1", "by1", "other"], "策略总览关联账号漏汇总或混入其他策略"),
    (["bn1", "ok1", "by1", "bn1"], "策略总览关联账号重复汇总"),
])
def test_missing_extra_and_duplicate_members_are_still_detected(ids, expected_title):
    detail = _detail() | {"account_ids": ids, "account_count": len(set(ids))}
    findings = strategy_overview_differences(strategy_overview_scopes(_rows())[0], detail)
    assert any(f["kind"] == "BUG_CANDIDATE" and f["title"] == expected_title for f in findings)


def test_account_count_is_compared_to_unique_full_membership():
    findings = strategy_overview_differences(strategy_overview_scopes(_rows())[0], _detail() | {"account_count": 1})
    assert any(f["title"] == "策略详情关联账号数量与账号 ID 列表长度不一致" for f in findings)


@pytest.mark.parametrize("change", [{"account_ids": None}, {"account_count": 2}])
def test_incomplete_member_ids_are_not_treated_as_zero_or_full_population(change):
    rows = _rows()
    rows[0].update(change)
    findings = strategy_overview_differences(strategy_overview_scopes(rows)[0], _detail())
    assert any(f["kind"] == "DATA_GAP" for f in findings)
    assert not any(f["title"] == "策略总览关联账号漏汇总或混入其他策略" for f in findings)


def test_nameless_different_ids_are_not_merged():
    scopes = strategy_overview_scopes([
        {"strategy_definition_id": "s1", "account_ids": ["a"]},
        {"strategy_definition_id": "s2", "account_ids": ["b"]},
    ])
    assert len(scopes) == 2


def test_homogeneous_stopped_members_still_detect_incorrect_running_overview():
    rows = _rows()[:2]
    for row in rows:
        row["runtime_status"] = "stopped"
    findings = strategy_overview_differences(strategy_overview_scopes(rows)[0], _detail())
    assert any(f["title"] == "策略总览与全部关联记录运行状态不一致" for f in findings)


def test_missing_runtime_is_not_assumed_to_match_known_member_state():
    rows = _rows()[:2]
    rows[0]["runtime_status"] = None
    findings = strategy_overview_differences(strategy_overview_scopes(rows)[0], _detail())
    assert not any(f["title"] == "策略总览与全部关联记录运行状态不一致" for f in findings)


def test_other_name_detail_is_rejected():
    findings = strategy_overview_differences(strategy_overview_scopes(_rows())[0], _detail() | {"name": "A_extra"})
    assert any(f["title"] == "策略详情加载了其他名称的策略" for f in findings)


def _runner(tmp_path: Path) -> Regression:
    return Regression("https://example.invalid/api/v1", {}, tmp_path)


@pytest.mark.parametrize("catalog,params", [
    ({"has_more": True}, {}),
    ({"has_more": False}, {"runtime_status": "stopped"}),
    ({"has_more": False}, {"exchange": "okx"}),
])
def test_filtered_or_first_page_catalog_cannot_define_full_overview(tmp_path, catalog, params):
    runner = _runner(tmp_path)
    runner.results = [
        {"name": "strategies", "params": params, "body": {"data": {"items": _rows()[:2], **catalog}}},
        {"name": "strategy-s1-overview-detail", "status": 200, "body": {"data": {"strategy": _detail()}}},
    ]
    runner.check_identity_consistency()
    assert runner.findings[0]["kind"] == "DATA_GAP"
    assert not any(f["kind"] == "BUG_CANDIDATE" for f in runner.findings)


def test_runner_queries_full_overview_for_every_detail_section(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    scopes = {ident: scope for scope in strategy_overview_scopes(_rows()) for ident in scope.definition_ids}

    def fake_call(name, path, params=None):
        data: dict[str, Any] = {"items": [], "has_more": False}
        if path == "/quantitative-trading/strategies":
            data["items"] = _rows()
        elif path.startswith("/quantitative-trading/strategy-definitions/") and not path.endswith("/accounts"):
            scope = scopes[path.rsplit("/", 1)[1]]
            data["strategy"] = {"name": scope.name, "account_ids": sorted(scope.account_ids),
                                "account_count": len(scope.account_ids), "runtime_status": "running"}
        entry = {"name": name, "path": path, "params": dict(params or {}), "status": 200,
                 "body": {"data": data, "meta": {"data_status": "complete"}}}
        runner.results.append(entry)
        return entry

    monkeypatch.setattr(runner, "call", fake_call)
    runner.run()
    strategy_requests = [entry for entry in runner.results if entry["name"].startswith("strategy-")]
    assert strategy_requests
    assert all("strategy_exchange" not in entry["params"] for entry in strategy_requests)
    assert {entry["name"] for entry in strategy_requests if entry["name"].endswith("-detail")} == {
        "strategy-s1-overview-detail", "strategy-s2-overview-detail", "strategy-s3-overview-detail",
    }
    for section in ("accounts", "summary", "risk", "series", "position-series", "positions",
                    "signals", "signal-batches", "rebalance-plans", "order-intents", "orders", "fills"):
        assert any(entry["name"] == f"strategy-s1-overview-{section}" for entry in strategy_requests)
    assert not any(f["kind"] == "BUG_CANDIDATE" for f in runner.findings)


def test_cross_exchange_ambiguity_is_not_an_accepted_overview_refusal(tmp_path):
    runner = _runner(tmp_path)
    runner.check_entry({"name": "strategy-s1-overview-detail", "status": 409,
                        "body": {"error": {"code": "SOURCE_STRATEGY_AMBIGUOUS"}}})
    assert any(f["kind"] == "BUG_CANDIDATE" for f in runner.findings)


def test_list_keeps_two_accounts_of_same_definition_and_exchange(tmp_path):
    runner = _runner(tmp_path)
    rows = _rows()[:2]
    rows[1]["exchange"] = "binance"
    runner.check_pagination({"name": "strategies", "path": "/quantitative-trading/strategies",
                             "body": {"data": {"items": rows, "has_more": False}}})
    assert not runner.findings


def test_supplement_cross_page_identity_preserves_same_exchange_account_rows(tmp_path, monkeypatch):
    runner = Supplement("https://example.invalid/api/v1", {}, tmp_path)
    rows = _rows()[:2]
    rows[1]["exchange"] = "binance"
    calls = []

    def fake_call(name, path, params):
        calls.append(dict(params))
        second = "cursor" in params
        return {"name": name, "status": 200, "body": {
            "meta": {"snapshot_id": "fixed-membership"},
            "data": {"items": [rows[int(second)]], "has_more": not second,
                     "next_cursor": None if second else "p2", "total": 2, "total_status": "exact"},
        }}

    monkeypatch.setattr(runner, "call", fake_call)
    result = runner.full_pages("strategies", "/quantitative-trading/strategies", {},
                               identity_key=("strategy_definition_id", "exchange", "account_ids"))
    assert len(result) == 2
    assert runner.checks[-1]["passed"] is True
    assert calls[1]["snapshot_id"] == "fixed-membership"
