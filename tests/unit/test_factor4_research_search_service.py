"""Counterexamples for result-level research membership and consumer selection."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from typing import Any, Callable

import pytest

from api.factor4_summary_api import Factor4SummaryAPI
from api.factor_data_mcp_api import MCPResponse
from db.factor4_read_repository import ResearchCatalogSnapshot, SummarySample
from service.factor4_read_service import ReadContractError, ReadPrecondition
from service.factor4_research_search_service import Factor4ResearchSearchService, _FIELDS
from service.factor4_summary_service import Factor4SummaryService

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)
SCOPE = {"ic_scope": "cross_sectional", "calculation_mode": "direct", "factor_bar_interval": "1h",
         "factor_window_bars": "24", "return_bar_interval": "1h", "forward_return_bars": 1,
         "universe_key": "all", "symbol": "", "window_scope": "1y", "scoring_version": "v1"}


def _row(identifier: int, name: str, status: str, icir: float = 3, score: float = 3) -> dict[str, Any]:
    return {**SCOPE, **dict.fromkeys(_FIELDS), "factor_id": identifier, "name": name, "cn_name": None,
            "metric_id": identifier + 100, "run_id": "run-" + str(identifier), "validity_run_id": "run-" + str(identifier),
            "validity_id": identifier + 200,
            "validity_status": status, "icir": icir, "rank_icir": icir, "final_score": score,
            "coverage_mean": 1, "valid_slice_count": 30, "mean_ic": icir / 10}


def _snapshot(*, empty: bool = False, kind: str = "sub_factor") -> ResearchCatalogSnapshot:
    rows = [
        _row(1, "alpha strongest", "valid"),
        _row(2, "alpha lowic", "valid", icir=1),
        _row(3, "alpha lowscore", "valid", score=1),
        _row(4, "outside strongest", "valid"),
        _row(5, "alpha invalid", "invalid"),
    ]
    if empty:
        rows.pop(0)
    unknown = {**_row(6, "alpha unknown", "unknown"), **dict.fromkeys(_FIELDS),
               "metric_id": None, "run_id": None, "validity_run_id": None, "validity_id": None, "scoring_version": None}
    rows.append(unknown)
    scope = {**SCOPE, "calculation_mode": "child_aggregate" if kind == "factor" else "direct"}
    rows = [{**row, "calculation_mode": scope["calculation_mode"]} for row in rows]
    summaries = tuple({**row, "id": row["metric_id"], "is_sub_factor_id": int(kind == "sub_factor")}
                      for row in rows if row["metric_id"] is not None)
    return ResearchCatalogSnapshot(SummarySample(kind, NOW, scope, summaries, ()), tuple(rows))


def _item(row: dict[str, Any], kind: str) -> dict[str, Any]:
    return {**{key: row[key] for key in _FIELDS}, "id": row["factor_id"], "kind": kind,
            "factor_ref": f"{kind}:{row['factor_id']}", "metric_run_id": row["run_id"],
            "factor_bar_interval": row["factor_bar_interval"], "scoring_version": row["scoring_version"],
            "validity_status": row["validity_status"], "validity_run_id": row["validity_run_id"]}


def _response(data: dict[str, Any], meta: dict[str, Any] | None = None) -> MCPResponse:
    body = {"data": data, "meta": meta or {"next_cursor": None, "truncated": False}}
    return MCPResponse(200, "application/json", {"result": {"structuredContent": body,
        "content": [{"type": "text", "text": json.dumps(body)}], "isError": False}}, "2025-06-18")


class _ResearchMCP:
    def __init__(self, snapshot: ResearchCatalogSnapshot, *, page_size: int = 50,
                 mutate: Callable[[str, dict[str, Any]], None] | None = None,
                 ignore: str | None = None, budget: bool = False) -> None:
        self.snapshot, self.page_size = snapshot, page_size
        self.mutate, self.ignore, self.budget = mutate, ignore, budget
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResponse:
        """Return independently filtered offline data; unknown tools raise AssertionError."""
        self.calls.append((name, deepcopy(arguments)))
        request = {key: value for key, value in arguments.items() if key != self.ignore}
        if name == "factor_get_validity":
            row = next(row for row in self.snapshot.rows
                       if request["factor_ref"] == f"{self.snapshot.sample.kind}:{row['factor_id']}")
            data = {"id": row["validity_id"], "metric_id": row["metric_id"], "factor_id": row["factor_id"],
                    "run_id": row["validity_run_id"], "validity_status": row["validity_status"]}
            if self.mutate:
                self.mutate(name, data)
            return _response({"item": data})
        if name == "factor_rank":
            candidates = sorted(self.snapshot.sample.rows, key=lambda row: row[request["metric"]], reverse=True)
            top = [{**row, "kind": self.snapshot.sample.kind,
                    "factor_ref": f"{self.snapshot.sample.kind}:{row['factor_id']}",
                    "raw_metric_value": row[request["metric"]], "ranking_value": row[request["metric"]]}
                   for row in candidates[:request["top_k"]]]
            data = {"top_items": top, "bottom_items": [], "returned_count": len(top), "validity_evaluated": False}
            if self.mutate:
                self.mutate(name, data)
            return _response(data)
        rows = list(self.snapshot.rows)
        if "validity" not in request:
            rows = [row for row in rows if row["metric_id"] is not None]
        else:
            rows = [row for row in rows if row["validity_status"] == request["validity"]]
        if "query" in request:
            rows = [row for row in rows if request["query"].lower() in row["name"].lower()]
        for argument, field in (("min_icir", "icir"), ("min_rank_icir", "rank_icir"), ("min_score", "final_score")):
            if argument in request:
                rows = [row for row in rows if row[field] is not None and row[field] >= request[argument]]
        if name == "factor_catalog_stats":
            data = {"total": len(rows), "groups": [{"kind": self.snapshot.sample.kind,
                    "validity": request.get("validity"), "count": len(rows)}]}
            if self.mutate:
                self.mutate(name, data)
            return _response(data)
        assert name == "factor_search"
        start = int(request.get("cursor", "0"))
        end = start + self.page_size
        data = {"items": [_item(row, self.snapshot.sample.kind) for row in rows[start:end]]}
        cursor = str(end) if end < len(rows) else None
        meta = {"next_cursor": cursor, "truncated": bool(cursor)}
        if self.budget:
            meta = {"next_cursor": None, "truncated": True, "warnings": ["CATALOG_CURSOR_BUDGET_REACHED"]}
        if self.mutate:
            self.mutate(name, data)
        return _response(data, meta)


def _service(mcp: _ResearchMCP) -> Factor4ResearchSearchService:
    return Factor4ResearchSearchService(Factor4SummaryService(Factor4SummaryAPI(mcp)))


@pytest.mark.parametrize("status", ["valid", "invalid", "unknown"])
def test_status_search_checks_actual_members_and_identical_stats_scope(status: str) -> None:
    snapshot = _snapshot()
    mcp = _ResearchMCP(snapshot, page_size=2)
    result = _service(mcp).check_explicit_validity_members(snapshot, status)
    assert not result.issues and not result.evidence["blocked"]
    assert result.evidence["expected_count"] == {"valid": 4, "invalid": 1, "unknown": 1}[status]
    search_arguments = [{k: v for k, v in args.items() if k not in {"limit", "cursor"}}
                        for name, args in mcp.calls if name == "factor_search"]
    stats_arguments = next(arguments for name, arguments in mcp.calls if name == "factor_catalog_stats")
    assert all(arguments == stats_arguments for arguments in search_arguments)
    if status == "unknown":
        assert result.evidence["research_validity_readback_status"] == "not_applicable"
        assert not any(name == "factor_get_validity" for name, _ in mcp.calls)
    else:
        assert result.evidence["research_validity_readback_status"] == "verified"
        assert result.evidence["research_validity_readback_count"] == 1
        name, arguments = mcp.calls[-1]
        assert name == "factor_get_validity"
        assert arguments["validity_scope"] == SCOPE["ic_scope"] and "ic_scope" not in arguments
        assert arguments["as_of"] == NOW.isoformat()
        assert arguments["run_id"] == ("run-1" if status == "valid" else "run-5")


@pytest.mark.parametrize("field,value,issue", [
    ("factor_ref", "sub_factor:999", "research:filtered_membership"),
    ("kind", "factor", "research:factor_identity"),
    ("metric_run_id", "old-run", "research:not_latest_metric_run"),
    ("validity_status", "invalid", "research_validity:member_status"),
    ("validity_run_id", "other-run", "research_validity:member_run"),
    ("icir", 99, "research:value=icir"),
])
def test_equal_count_with_wrong_research_member_is_rejected(field: str, value: Any, issue: str) -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_search":
            data["items"][0][field] = value
    snapshot = _snapshot()
    result = _service(_ResearchMCP(snapshot, mutate=mutate)).check_explicit_validity_members(snapshot, "valid")
    assert issue in result.issues


@pytest.mark.parametrize("fault,issue", [("duplicate", "research:duplicate_factor"),
                                        ("missing", "research:filtered_membership"),
                                        ("statistics", "research_validity:total")])
def test_research_membership_and_counts_fail_independently(fault: str, issue: str) -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_search" and fault == "duplicate":
            data["items"][-1] = data["items"][0]
        elif name == "factor_search" and fault == "missing":
            data["items"].pop()
        elif name == "factor_catalog_stats" and fault == "statistics":
            data["total"] = 0
    snapshot = _snapshot()
    result = _service(_ResearchMCP(snapshot, mutate=mutate)).check_explicit_validity_members(snapshot, "valid")
    assert issue in result.issues


def test_unknown_member_cannot_expose_unbacked_metric_values() -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_search":
            data["items"][0]["final_score"] = 5
    snapshot = _snapshot()
    result = _service(_ResearchMCP(snapshot, mutate=mutate)).check_explicit_validity_members(snapshot, "unknown")
    assert "research_validity:unknown_member_has_unbacked_metric" in result.issues


@pytest.mark.parametrize("field,value", [("id", 999), ("metric_id", 999), ("factor_id", 999),
                                       ("run_id", "old-run"), ("validity_status", "invalid")])
def test_search_validity_readback_checks_independent_persisted_identity(field: str, value: Any) -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_get_validity":
            data[field] = value
    snapshot = _snapshot()
    result = _service(_ResearchMCP(snapshot, mutate=mutate)).check_explicit_validity_members(snapshot, "valid")
    assert "research_validity:readback:" + field in result.issues
    assert result.evidence["research_validity_readback_status"] == "failed"
    assert result.evidence["research_validity_readback_count"] == 1


def test_unknown_with_persisted_validity_is_read_back_instead_of_skipping() -> None:
    snapshot = _snapshot()
    snapshot = replace(snapshot, rows=(*snapshot.rows, _row(7, "alpha persisted unknown", "unknown")))
    mcp = _ResearchMCP(snapshot)
    result = _service(mcp).check_explicit_validity_members(snapshot, "unknown")
    assert not result.issues and not result.evidence["blocked"]
    assert result.evidence["research_validity_readback_status"] == "verified"
    assert mcp.calls[-1][1]["factor_ref"] == "sub_factor:7"
    assert mcp.calls[-1][1]["run_id"] == "run-7"


def test_validity_readback_uses_actual_search_run_instead_of_repairing_it_from_database() -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_search":
            data["items"][0]["validity_run_id"] = "actual-search-run"
    snapshot = _snapshot()
    mcp = _ResearchMCP(snapshot, mutate=mutate)
    result = _service(mcp).check_explicit_validity_members(snapshot, "valid")
    assert "research_validity:member_run" in result.issues
    assert mcp.calls[-1][1]["run_id"] == "actual-search-run"


def test_missing_search_run_does_not_fall_back_to_database_validity_run() -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_search":
            data["items"][0]["validity_run_id"] = None
    snapshot = _snapshot()
    mcp = _ResearchMCP(snapshot, mutate=mutate)
    result = _service(mcp).check_explicit_validity_members(snapshot, "valid")
    assert "research_validity:member_run" in result.issues
    assert result.evidence["research_validity_readback_status"] == "blocked"
    assert not any(name == "factor_get_validity" for name, _ in mcp.calls)


def test_readback_protocol_failure_keeps_preceding_search_mismatch() -> None:
    class FaultMCP(_ResearchMCP):
        def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResponse:
            """Inject a later safe protocol failure without issuing network requests."""
            result = super().call_tool(name, arguments)
            if name == "factor_get_validity":
                raise ReadContractError("offline_readback_shape")
            return result
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_search":
            data["items"][0]["validity_status"] = "invalid"
    snapshot = _snapshot()
    result = _service(FaultMCP(snapshot, mutate=mutate)).check_explicit_validity_members(snapshot, "valid")
    assert "research_validity:member_status" in result.issues
    assert "research_validity:readback:offline_readback_shape" in result.issues
    assert result.evidence["research_validity_readback_status"] == "failed"


def test_validity_readback_missing_item_envelope_is_a_contract_failure() -> None:
    class MissingItemMCP(_ResearchMCP):
        def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResponse:
            """Return a malformed validity envelope only, without network requests."""
            result = super().call_tool(name, arguments)
            return _response({"id": 201}) if name == "factor_get_validity" else result
    snapshot = _snapshot()
    result = _service(MissingItemMCP(snapshot)).check_explicit_validity_members(snapshot, "valid")
    assert "research_validity:readback:validity readback requires a data.item object" in result.issues
    assert result.evidence["research_validity_readback_status"] == "failed"


@pytest.mark.parametrize("corrupt", [False, True])
def test_declared_catalog_budget_stops_and_preserves_preceding_business_failure(corrupt: bool) -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if corrupt and name == "factor_search":
            data["items"][0]["validity_status"] = "invalid"
    snapshot = _snapshot()
    mcp = _ResearchMCP(snapshot, page_size=1, budget=True, mutate=mutate)
    result = _service(mcp).check_explicit_validity_members(snapshot, "valid")
    assert bool(result.issues) is corrupt
    assert result.evidence["blocked"] == ("research_membership_not_fully_read:bounded",)
    assert [name for name, _ in mcp.calls] == ["factor_search", "factor_catalog_stats", "factor_get_validity"]


def test_absent_positive_status_is_verified_empty_but_not_counted_as_positive_coverage() -> None:
    snapshot = _snapshot()
    snapshot = replace(snapshot, rows=tuple(row for row in snapshot.rows if row["validity_status"] != "invalid"))
    result = _service(_ResearchMCP(snapshot)).check_explicit_validity_members(snapshot, "invalid")
    assert not result.issues
    assert result.evidence["blocked"] == ("positive_research_validity_sample_missing:invalid",)


def test_ambiguous_validity_oracle_blocks_before_any_request() -> None:
    snapshot = replace(_snapshot(), ambiguous_count=1)
    mcp = _ResearchMCP(snapshot)
    with pytest.raises(ReadPrecondition, match="AMBIGUITY"):
        _service(mcp).check_explicit_validity_members(snapshot, "valid")
    assert not mcp.calls


@pytest.mark.parametrize("scenario", ["hit", "empty", "relaxed"])
def test_combined_filters_use_independent_witnesses_and_no_threshold_stats(scenario: str) -> None:
    snapshot = _snapshot(empty=scenario == "empty")
    mcp = _ResearchMCP(snapshot)
    result = _service(mcp).check_combined_filters(snapshot, scenario)
    assert not result.issues and not result.evidence["blocked"]
    assert result.evidence["independent_filter_witnesses"] == ("min_icir", "min_score")
    assert len(mcp.calls) == (5 if scenario == "relaxed" else 1)
    assert {name for name, _ in mcp.calls} == {"factor_search"}
    assert {arguments["as_of"] for _, arguments in mcp.calls} == {NOW.isoformat()}


@pytest.mark.parametrize("ignored", ["query", "validity", "min_icir", "min_score"])
def test_ignoring_any_combined_condition_is_detected(ignored: str) -> None:
    snapshot = _snapshot()
    result = _service(_ResearchMCP(snapshot, ignore=ignored)).check_combined_filters(snapshot, "hit")
    assert "combined:research:filtered_membership" in result.issues


def test_combined_filters_cannot_pass_using_redundant_natural_conditions() -> None:
    snapshot = _snapshot()
    snapshot = replace(snapshot, rows=tuple(row for row in snapshot.rows if row["factor_id"] == 1))
    mcp = _ResearchMCP(snapshot)
    with pytest.raises(ReadPrecondition, match="independent two-metric witnesses"):
        _service(mcp).check_combined_filters(snapshot, "hit")
    assert not mcp.calls


@pytest.mark.parametrize("scenario", ["hit", "empty", "relaxed"])
def test_combination_requires_only_two_metric_witnesses_not_five_catalog_variants(scenario: str) -> None:
    snapshot = _snapshot(empty=scenario == "empty")
    snapshot = replace(snapshot, rows=tuple(row for row in snapshot.rows if row["factor_id"] <= 3))
    result = _service(_ResearchMCP(snapshot)).check_combined_filters(snapshot, scenario)
    assert not result.issues and not result.evidence["blocked"]


def test_invalid_later_stats_preserves_already_confirmed_member_mismatch() -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_search":
            data["items"][0]["validity_status"] = "invalid"
        elif name == "factor_catalog_stats":
            data["groups"] = None
    snapshot = _snapshot()
    result = _service(_ResearchMCP(snapshot, mutate=mutate)).check_explicit_validity_members(snapshot, "valid")
    assert "research_validity:member_status" in result.issues
    assert any(issue.startswith("research_validity:statistics:") for issue in result.issues)


def test_later_malformed_relaxation_preserves_prior_intersection_failure() -> None:
    requests = 0
    def mutate(name: str, data: dict[str, Any]) -> None:
        nonlocal requests
        requests += 1
        if requests == 1:
            data["items"][0]["factor_ref"] = "sub_factor:999"
        elif requests == 2:
            data["items"] = None
    snapshot = _snapshot()
    result = _service(_ResearchMCP(snapshot, mutate=mutate)).check_combined_filters(snapshot, "relaxed")
    assert "combined:research:filtered_membership" in result.issues
    assert any("business items must be an object array" in issue for issue in result.issues)


def test_parent_research_and_rank_keep_parent_aggregate_identity() -> None:
    snapshot = _snapshot(kind="factor")
    mcp = _ResearchMCP(snapshot)
    result = _service(mcp).check_parent_research_and_rank(snapshot.sample)
    assert not result.issues
    assert [name for name, _ in mcp.calls] == ["factor_search", "factor_catalog_stats", "factor_rank"]
    assert all(arguments["kind"] == "factor" and arguments["calculation_mode"] == "child_aggregate"
               for _, arguments in mcp.calls)
    assert mcp.calls[-1][1]["ranking_mode"] == "raw_signed" and mcp.calls[-1][1]["bottom_k"] == 0


def test_parent_rank_cannot_substitute_child_of_same_numeric_id() -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_rank":
            row = data["top_items"][0]
            row.update(kind="sub_factor", factor_ref=f"sub_factor:{row['factor_id']}")
    snapshot = _snapshot(kind="factor")
    result = _service(_ResearchMCP(snapshot, mutate=mutate)).check_parent_research_and_rank(snapshot.sample)
    assert "rank:factor_identity" in result.issues


def test_parent_search_failure_is_not_hidden_by_later_rank_preconditions() -> None:
    def mutate(name: str, data: dict[str, Any]) -> None:
        if name == "factor_search":
            data["items"][0]["kind"] = "sub_factor"
    snapshot = _snapshot(kind="factor")
    mcp = _ResearchMCP(snapshot, mutate=mutate)
    result = _service(mcp).check_parent_research_and_rank(snapshot.sample)
    assert "research:factor_identity" in result.issues
    assert all(name != "factor_rank" for name, _ in mcp.calls)
