"""Offline counterexamples for migrated request, cursor, search and rank assertions."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from typing import Any

import pytest

from api.factor4_summary_api import Factor4SummaryAPI
from api.factor_data_mcp_api import MCPResponse
from db.factor4_read_repository import SliceSample, SummarySample, ValiditySample
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition
from service.factor4_rank_filter_service import Factor4RankFilterService
from service.factor4_research_search_service import Factor4ResearchSearchService, _FIELDS
from service.factor4_slice_service import Factor4SliceService
from service.factor4_summary_service import Factor4SummaryService, _PERIOD_FIELDS, _SUMMARY_FIELDS, compare_validity, metric_slice_arguments
from service.factor4_validity_service import Factor4ValidityService

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)
SCOPE = {"ic_scope": "time_series", "calculation_mode": "direct", "factor_bar_interval": "1h",
         "factor_window_bars": "24", "return_bar_interval": "1h", "forward_return_bars": 1,
         "universe_key": "all", "symbol": "", "window_scope": "rolling", "scoring_version": "v1"}


def _response(data: dict[str, Any], *, error: str | None = None, cursor: str | None = None) -> MCPResponse:
    body = {"data": data, "meta": {"next_cursor": cursor, "truncated": bool(cursor)}}
    if error:
        body["error"] = {"code": error}
    return MCPResponse(200, "application/json", {"result": {"structuredContent": body,
        "content": [{"type": "text", "text": json.dumps(body, default=str)}], "isError": bool(error)}}, "2025-06-18")


class _MCP:
    def __init__(self, responses: list[MCPResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> MCPResponse:
        """Record a copied request and consume one offline response; exhaustion raises IndexError."""
        self.calls.append((tool, deepcopy(arguments)))
        return self.responses.pop(0)


def _summary(identifier: int = 1) -> dict[str, Any]:
    row = {key: None for key in (*_SUMMARY_FIELDS, *_PERIOD_FIELDS)}
    row.update(**SCOPE, id=identifier + 10, factor_id=identifier, is_sub_factor_id=1, run_id="run",
               mean_ic=0.1, mean_rank_ic=0.2, icir=0.3, rank_icir=0.4, final_score=0.5,
               coverage_mean=1, valid_slice_count=3)
    return row


def _validity() -> ValiditySample:
    row = {**SCOPE, "id": 101, "factor_id": 1, "is_sub_factor_id": 1, "run_id": "run",
           "time_series_scoring_version": "v1", "cross_sectional_scoring_version": "v1",
           "time_series_status": "valid", "time_series_is_valid": 1, "time_series_score": 2,
           "cross_sectional_status": "invalid", "cross_sectional_is_valid": 0, "cross_sectional_score": 3,
           "run_completed_at": NOW}
    return ValiditySample(row, {"ts": _summary(), "cs": {**_summary(), "ic_scope": "cross_sectional"}})


def _validity_item() -> dict[str, Any]:
    return {"id": 101, "metric_id": 11, "factor_id": 1, "run_id": "run", "validity_status": "valid",
            "time_series_is_valid": 1, "time_series_score": 2}


@pytest.mark.parametrize("field", ["id", "metric_id", "run_id", "factor_id", "validity_status", "time_series_is_valid", "time_series_score"])
def test_validity_missing_public_evidence_is_not_silently_accepted(field: str) -> None:
    row = _validity_item()
    del row[field]
    assert f"validity:missing_{field}" in compare_validity(row, _validity(), "ts").issues


@pytest.mark.parametrize("variant,field", [("symbol_omitted", "symbol"), ("run_omitted", "run_id"), ("run_null", "run_id")])
def test_validity_optional_fields_still_compare_actual_identity(variant: str, field: str) -> None:
    mcp = _MCP([_response({"item": _validity_item()})])
    service = Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    assert not service.check_argument_variant(_validity(), "validity", variant).issues
    if variant.endswith("omitted"):
        assert field not in mcp.calls[0][1]
    else:
        assert mcp.calls[0][1][field] is None


@pytest.mark.parametrize("variant", ["run_omitted", "run_null"])
@pytest.mark.parametrize("stale", [False, True])
def test_default_metrics_variant_compares_independent_run_not_validity_backfill(variant: str, stale: bool) -> None:
    historical = _validity()
    newer = {**historical.summaries["ts"], "id": 999, "run_id": "new-completed-run"}
    expected = SummarySample("sub_factor", NOW, SCOPE, (newer,), ())
    returned = historical.summaries["ts"] if stale else newer
    mcp = _MCP([_response({"ic_summaries": [returned]})])
    service = Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    result = service.check_argument_variant(historical, "metrics", variant, as_of=NOW.isoformat(), metric_expected=expected)
    assert bool(result.issues) is stale
    args = mcp.calls[0][1]
    assert args["as_of"] == NOW.isoformat()
    assert ("run_id" not in args) if variant == "run_omitted" else args["run_id"] is None


@pytest.mark.parametrize("code,passes", [("INVALID_ARGUMENT", True), ("QUERY_TIMEOUT", False), ("INTERNAL_ERROR", False)])
def test_invalid_asof_accepts_only_contract_rejection(code: str, passes: bool) -> None:
    mcp = _MCP([_response({}, error=code)])
    service = Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    assert (not service.check_argument_variant(_validity(), "validity", "as_of_null").issues) is passes


def _slices(symbol: str = "BTCUSDT") -> SliceSample:
    row = {**SCOPE, "factor_id": 1, "is_sub_factor_id": 1, "run_id": "run", "symbol": symbol}
    rows = tuple({"id": i, "slice_start": NOW, "slice_end": NOW, "run_id": "run"} for i in range(8))
    return SliceSample(row, rows)


@pytest.mark.parametrize("mutation", ["scope", "limit", "symbol", "tamper"])
def test_cursor_mutations_use_a_real_control_cursor_and_specific_rejection(mutation: str) -> None:
    mcp = _MCP([_response({"items": [{"id": i} for i in range(7)]}, cursor="signed-cursor-token"),
                _response({}, error="INVALID_ARGUMENT")])
    service = Factor4SliceService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    assert not service.check_cursor_binding(_slices(), target=_slices(""), mutation=mutation).issues
    before, after = mcp.calls[0][1], mcp.calls[1][1]
    assert "cursor" not in before and "cursor" in after
    assert after["as_of"] == before["as_of"]
    if mutation == "tamper":
        assert after["cursor"] != "signed-cursor-token"


def test_cursor_control_missing_cursor_is_failure_not_data_skip() -> None:
    mcp = _MCP([_response({"items": [{"id": i} for i in range(7)]})])
    with pytest.raises(ReadContractError):
        Factor4SliceService(Factor4SummaryService(Factor4SummaryAPI(mcp))).check_cursor_binding(_slices(), mutation="limit")


def test_slice_request_caps_end_at_asof_without_testing_deferred_equality() -> None:
    as_of = "2026-09-01T01:00:00+00:00"
    args = metric_slice_arguments(_slices(), as_of=as_of)
    assert args["end_time"] == as_of
    assert args["start_time"] == NOW.isoformat()
    assert args["run_id"] == "run"


def test_shared_slice_snapshot_drift_is_not_reported_as_a_product_failure() -> None:
    class Repository:
        def __init__(self) -> None:
            self.counter = 0

        def slice_watermark(self, sample: SliceSample) -> dict[str, Any]:
            """Return two distinct offline watermarks without executing SQL."""
            self.counter += 1
            return {"row_count": self.counter}

    class Summaries:
        def check_metric_slices(self, sample: SliceSample, **kwargs: Any) -> ReadCheck:
            """Inject a protocol mismatch to prove concurrent drift is classified separately."""
            raise ReadContractError("inconclusive response after an external update")

    with pytest.raises(ReadPrecondition, match="SNAPSHOT_DRIFT"):
        Factor4SliceService(Summaries()).check_snapshot_pages(Repository(), _slices())


def _research_row(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["factor_id"], "kind": "sub_factor", "factor_ref": f"sub_factor:{row['factor_id']}",
            "metric_run_id": row["run_id"], "factor_bar_interval": "1h", "scoring_version": "v1",
            **{key: row[key] for key in _FIELDS}}


@pytest.mark.parametrize("threshold", ["min_icir", "min_rank_icir", "min_score"])
@pytest.mark.parametrize("above", [False, True])
def test_research_thresholds_use_search_and_stats_not_rank(threshold: str, above: bool) -> None:
    row = _summary()
    mcp = _MCP([_response({"items": [] if above else [_research_row(row)]}), _response({"total": 1})])
    service = Factor4ResearchSearchService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    sample = SummarySample("sub_factor", NOW, SCOPE, (row,), ())
    assert not service.check_research_search(sample, threshold=threshold, above_maximum=above).issues
    assert [name for name, _ in mcp.calls] == ["factor_search", "factor_catalog_stats"]
    assert threshold in mcp.calls[0][1] and threshold not in mcp.calls[1][1]


@pytest.mark.parametrize("mutation,issue", [
    (lambda row: row.update(metric_run_id="old"), "research:not_latest_metric_run"),
    (lambda row: row.update(icir=99), "research:value=icir"),
    (lambda row: row.pop("final_score"), "research:missing_final_score"),
])
def test_research_result_corruption_is_rejected(mutation: Any, issue: str) -> None:
    row = _summary()
    item = _research_row(row)
    mutation(item)
    mcp = _MCP([_response({"items": [item]}), _response({"total": 1})])
    service = Factor4ResearchSearchService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    assert issue in service.check_research_search(SummarySample("sub_factor", NOW, SCOPE, (row,), ())).issues


def test_zero_rank_sides_is_expected_validation_and_slice_max_uses_current_rows() -> None:
    row = _summary()
    mcp = _MCP([_response({}, error="INVALID_ARGUMENT"),
                _response({"top_items": [], "bottom_items": [], "returned_count": 0, "candidate_count": 0, "evaluated_count": 1})])
    service = Factor4RankFilterService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    sample = SummarySample("sub_factor", NOW, SCOPE, (row,), ())
    assert not service.check_no_requested_side_rejected(sample).issues
    assert not service.check_filters(sample, "slices_above").issues
    assert mcp.calls[1][1]["min_valid_slice_count"] == 4


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_validity_pit_uses_actual_publication_after_run_completion(offset: int) -> None:
    sample = _validity()
    recorded = NOW + timedelta(milliseconds=42)
    sample.validity["updated_at"] = recorded
    sample.validity["created_at"] = recorded
    response = _response({}, error="VALIDITY_SCOPE_NOT_FOUND") if offset < 0 else _response({"item": _validity_item()})
    mcp = _MCP([response])
    service = Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    class Repository:
        def validity_candidates(self, selected, scope, as_of, *, include_unavailable):
            assert include_unavailable is True
            return (sample,)
    assert not service.check_completion_boundary(sample, "validity", offset, repository=Repository()).issues
    assert datetime.fromisoformat(mcp.calls[0][1]["as_of"]) == recorded + timedelta(microseconds=offset)


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("wrong", [False, True])
def test_validity_later_revision_boundary_keeps_previously_visible_same_run(offset: int, wrong: bool) -> None:
    old = _validity()
    old.validity.update(created_at=NOW + timedelta(minutes=1), updated_at=NOW + timedelta(minutes=1))
    new = deepcopy(old)
    new.validity.update(id=102, created_at=NOW + timedelta(minutes=2), updated_at=NOW + timedelta(minutes=2))
    previous_item, latest_item = _validity_item(), {**_validity_item(), "id": 102}
    expected = previous_item if offset < 0 else latest_item
    returned = latest_item if offset < 0 else previous_item
    mcp = _MCP([_response({"item": returned if wrong else expected})])
    class Repository:
        def validity_candidates(self, selected, scope, as_of, *, include_unavailable):
            assert include_unavailable is True
            return (new, old)
    service = Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    result = service.check_completion_boundary(new, "validity", offset, repository=Repository())
    assert bool(result.issues) is wrong
    assert mcp.calls[0][1]["run_id"] == old.validity["run_id"]


def test_validity_mutated_row_without_old_state_blocks_instead_of_guessing_empty() -> None:
    sample = _validity()
    sample.validity.update(created_at=NOW + timedelta(minutes=1), updated_at=NOW + timedelta(minutes=2))
    mcp = _MCP([])
    class Repository:
        def validity_candidates(self, selected, scope, as_of, *, include_unavailable):
            return (sample,)
    service = Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    with pytest.raises(ReadPrecondition, match="BLOCKED_DOC.*historical state"):
        service.check_completion_boundary(sample, "validity", -1, repository=Repository())
    assert not mcp.calls


def test_metrics_before_run_completion_still_requires_absence_without_validity_history() -> None:
    sample = _validity()
    mcp = _MCP([_response({}, error="METRIC_SCOPE_NOT_FOUND")])
    service = Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    assert not service.check_completion_boundary(sample, "metrics", -1).issues


@pytest.mark.parametrize("corrupt", [False, True])
def test_explicit_research_unknown_counts_are_reconciled_not_assumed_zero(corrupt: bool) -> None:
    data = {"total": 0 if corrupt else 7, "groups": [{"kind": "sub_factor", "validity": "unknown", "count": 7}]}
    mcp = _MCP([_response(data)])
    service = Factor4ResearchSearchService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    result = service.check_explicit_validity_stats(SummarySample("sub_factor", NOW, SCOPE, (_summary(),), ()),
                                                  {"valid": 1, "invalid": 2, "unknown": 7}, "unknown")
    assert bool(result.issues) is corrupt


@pytest.mark.parametrize("code,passes", [("VALIDITY_SCOPE_NOT_FOUND", True), ("INTERNAL_ERROR", False)])
def test_incomplete_batch_requires_specific_suppression_and_isolated_absent_factor(code: str, passes: bool) -> None:
    data = {"items": [{"factor_ref": "sub_factor:1", "success": False, "error": {"code": code}},
                      {"factor_ref": "sub_factor:999", "success": False, "error": {"code": "FACTOR_NOT_FOUND"}}]}
    service = Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(_MCP([_response(data)]))))
    assert (not service.check_incomplete_batch(object(), _validity(), "sub_factor:999").issues) is passes


@pytest.mark.parametrize("good_count", [2, 3])
def test_mixed_metric_batch_verifies_every_existing_entity_and_missing_ref(good_count: int) -> None:
    rows = tuple(_summary(index) for index in range(1, good_count + 1))
    items = [{"factor_ref": f"sub_factor:{row['factor_id']}", "success": True, "data": row} for row in rows]
    items.append({"factor_ref": "sub_factor:999", "success": False, "error": {"code": "FACTOR_NOT_FOUND"}})
    mcp = _MCP([_response({"items": items})])
    service = Factor4ResearchSearchService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    result = service.check_mixed_metric_batch(SummarySample("sub_factor", NOW, SCOPE, rows, ()), "sub_factor:999", good_count=good_count)
    assert not result.issues and result.checked_count == good_count + 1
    assert len(mcp.calls[0][1]["factor_refs"]) == good_count + 1


def test_final_score_unreachable_filter_retains_exact_metric_and_candidate_counts() -> None:
    mcp = _MCP([_response({"top_items": [], "bottom_items": [], "returned_count": 0,
                           "candidate_count": 0, "evaluated_count": 1})])
    service = Factor4RankFilterService(Factor4SummaryService(Factor4SummaryAPI(mcp)))
    assert not service.check_filters(SummarySample("sub_factor", NOW, SCOPE, (_summary(),), ()), "final_score_empty").issues
    assert mcp.calls[0][1]["metric"] == "final_score"


def test_summary_scope_mismatch_is_rejected_even_with_correct_metric_row() -> None:
    data = {"factor_ref": "sub_factor:1", "ic_summaries": [_summary()], "resolved_scope": {**SCOPE, "symbol": "WRONG"}}
    service = Factor4SummaryService(Factor4SummaryAPI(_MCP([_response(data)])))
    result = service.check_metrics(SummarySample("sub_factor", NOW, SCOPE, (_summary(),), ()))
    assert "summary:resolved_scope=symbol" in result.issues


@pytest.mark.parametrize("omit_period", [False, True])
def test_independent_metrics_reconcile_every_same_run_period(omit_period: bool) -> None:
    rows = (_summary(), {**_summary(), "id": 12})

    class Repository:
        def summaries_for_validity_scope(self, sample: ValiditySample, scope: str) -> tuple[dict[str, Any], ...]:
            """Return two real-shaped periods for the same requested Run/scope, without I/O."""
            return rows

    data = {"ic_summaries": list(rows[:1] if omit_period else rows)}
    service = Factor4ValidityService(Factor4SummaryService(Factor4SummaryAPI(_MCP([_response(data)]))))
    result = service.check_incomplete_metrics(Repository(), _validity(), "ts")
    assert bool(result.issues) is omit_period
