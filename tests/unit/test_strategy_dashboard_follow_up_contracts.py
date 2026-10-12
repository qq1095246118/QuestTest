"""Offline adversarial checks for the newly registered dashboard result contracts.

These tests prove that the contract helpers catch cross-scope corruption. They
are not a substitute for the live detail/page/workflow adapters named by the
corresponding cases.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from service.strategy_dashboard_follow_up_contracts import (
    check_action_readthrough,
    check_atomic_batch,
    check_concurrent_idempotency,
    check_dependency_projection,
    check_filter_contract,
    check_hierarchy_conservation,
    check_latest_request_wins,
    check_same_generation,
)
from service.strategy_dashboard_case_registry import load_strategy_dashboard_cases


pytestmark = pytest.mark.unit


_FOLLOW_UP_IDS = {
    "DATA-123", "DATA-124", "DATA-125", "DATA-127", "API-142", "API-143",
    "VIEW-142", "VIEW-143", "VIEW-144", "VIEW-145", "CALC-063", "CALC-064",
    "CALC-065", "FLOW-141", "FLOW-142", "FLOW-143", "API-144", "API-145",
    "VIEW-146", "DATA-128", "CALC-066", "API-146", "CALC-067", "CALC-068",
    "CALC-069", "CALC-070", "CALC-071", "CALC-051", "CALC-052", "CALC-053",
    "CALC-054", "CALC-055", "CALC-056", "CALC-057", "CALC-058", "CALC-059",
    "VIEW-141", "CALC-060", "CALC-061", "CALC-062", "DATA-121", "DATA-122",
    "ALERT-012",
}


def test_follow_up_catalog_contains_only_new_stable_cases() -> None:
    """Keep the supplemental catalog explicit and prevent accidental duplicates."""
    root = Path(__file__).resolve().parents[1] / "cases" / "strategy_dashboard" / "design"
    cases = {case.case_id: case for case in load_strategy_dashboard_cases(root)}
    assert _FOLLOW_UP_IDS <= cases.keys()
    assert len(_FOLLOW_UP_IDS) == 43
    for case_id in _FOLLOW_UP_IDS:
        assert any(
            marker in cases[case_id].body
            for marker in (
                "自动化状态：`DESIGNED_NOT_CONNECTED`",
                "自动化状态：`SOURCE_ADAPTER_READY`",
                "自动化状态：`LIVE_ADAPTER_REQUIRED`",
                "自动化状态：`RULE_PENDING`",
            )
        )


def test_cross_surface_generation_must_be_shared_or_explicitly_partial() -> None:
    records = [
        {"surface": "summary", "snapshot_id": "s1", "generation": "g1", "as_of": "t1"},
        {"surface": "series", "snapshot_id": "s1", "generation": "g1", "as_of": "t1"},
        {"surface": "risk", "snapshot_id": "s1", "generation": "g1", "as_of": "t1"},
    ]
    check_same_generation(records).require()
    mixed = deepcopy(records)
    mixed[-1]["generation"] = "g0"
    assert check_same_generation(mixed).issues == ("MIXED_SOURCE_GENERATION",)
    partial = deepcopy(records)
    partial[-1] = {"surface": "risk", "status": "partial", "reason_code": "SOURCE_REFRESHING"}
    check_same_generation(partial).require()
    partial[-1].pop("reason_code")
    assert "NON_COMPLETE_REASON_MISSING" in check_same_generation(partial).issues


def test_partial_batch_retry_completes_each_entity_once() -> None:
    first = [{"entity_id": "A", "status": "complete"}, {"entity_id": "B", "status": "failed"}]
    final = [{"entity_id": "A", "status": "complete"}, {"entity_id": "B", "status": "complete"}]
    check_atomic_batch(["A", "B"], first, final).require()
    duplicate = final + [{"entity_id": "A", "status": "complete"}]
    assert "RETRY_DUPLICATE_ENTITY" in check_atomic_batch(["A", "B"], first, duplicate).issues


@pytest.mark.parametrize(
    ("params", "response", "expected_issue"),
    [
        ({"from": "2026-09-02", "to": "2026-09-01"}, {"http_status": 400, "body": {}}, None),
        ({"from": "2026-09-02", "to": "2026-09-01"}, {"http_status": 200, "body": {"data": {"count": "all"}}}, "INVALID_WINDOW_FALLBACK"),
        ({"account_ids": []}, {"http_status": 200, "body": {"data": {"count": 0, "status": "empty"}}}, None),
        ({"account_ids": []}, {"http_status": 200, "body": {"data": {"count": 4, "status": "complete"}}}, "EMPTY_SELECTION_FALLBACK"),
    ],
)
def test_invalid_filter_cannot_fall_back_to_default_population(params, response, expected_issue) -> None:
    result = check_filter_contract(params, response)
    if expected_issue:
        assert expected_issue in result.issues
    else:
        result.require()


def test_late_old_query_response_cannot_replace_latest_selection() -> None:
    responses = [
        {"request_seq": 1, "query_key": "A", "generation": "g1"},
        {"request_seq": 2, "query_key": "B", "generation": "g2"},
    ]
    check_latest_request_wins(responses).require()
    stale = [responses[1], responses[0]]
    assert "STALE_RESPONSE_DISPLAYED" in check_latest_request_wins(stale).issues


def test_missing_source_degrades_only_dependent_metrics() -> None:
    source = {"E": "100", "U": None, "G": "10", "D": "100"}
    derived = {
        "wallet": {"value": None, "status": "unavailable"},
        "cumulative_return": {"value": "0.1", "status": "complete"},
    }
    check_dependency_projection(source, derived, {"wallet": ("E", "U"), "cumulative_return": ("G", "D")}).require()
    broken = deepcopy(derived)
    broken["wallet"] = {"value": "0", "status": "complete"}
    assert "wallet:MISSING_DEPENDENCY_ACCEPTED" in check_dependency_projection(
        source, broken, {"wallet": ("E", "U"), "cumulative_return": ("G", "D")}
    ).issues


def test_strategy_contribution_must_conserve_portfolio_and_unique_accounts() -> None:
    check_hierarchy_conservation(
        {"account_ids": ["a", "b"]},
        [{"strategy_id": "s1", "value": "60"}, {"strategy_id": "s2", "value": "30"}],
        {"value": "100", "unallocated": "10"},
    ).require()
    broken = check_hierarchy_conservation(
        {"account_ids": ["a", "a"]},
        [{"strategy_id": "s1", "value": "60"}, {"strategy_id": "s2", "value": "30"}],
        {"value": "100", "unallocated": "0"},
    )
    assert {"ACCOUNT_COUNTED_TWICE", "CONTRIBUTION_NOT_CONSERVED"} <= set(broken.issues)


def test_post_action_reads_share_new_generation_and_keep_frozen_bill() -> None:
    action = {"generation": "g2", "locked_bill_status": "locked", "current_cycle_status": "accruing"}
    check_action_readthrough(action, [{"generation": "g2"}, {"generation": "g2"}]).require()
    assert "POST_ACTION_STALE_READ" in check_action_readthrough(action, [{"generation": "g1"}]).issues


def test_concurrent_mutation_has_one_winner_and_stable_replay() -> None:
    results = [
        {"status": "success", "idempotency_key": "k1", "result_id": "bill-1", "created_cycle_count": 1},
        {"status": "conflict", "idempotency_key": "k1", "result_id": "bill-1", "created_cycle_count": 0},
    ]
    check_concurrent_idempotency(results).require()
    duplicate = results + [{"status": "success", "idempotency_key": "k1", "result_id": "bill-2", "created_cycle_count": 1}]
    assert "MULTIPLE_MUTATION_WINNERS" in check_concurrent_idempotency(duplicate).issues
