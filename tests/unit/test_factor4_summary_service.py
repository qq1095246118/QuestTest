"""真实业务断言的反例测试；离线通过不代表接口已通过。"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from api.factor4_summary_api import Factor4SummaryAPI
from api.factor_data_mcp_api import MCPResponse
from db.factor4_read_repository import MetricScopeSnapshot, SummarySample, ValiditySample
from service.factor4_read_service import ReadContractError, ReadPrecondition, ToolPage
from service.factor4_summary_service import (
    Factor4SummaryService, _PERIOD_FIELDS, _SUMMARY_FIELDS, compare_rank, compare_summary,
    summary_scope, visible_metric_scopes,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
SCOPE = {"ic_scope": "time_series", "calculation_mode": "direct", "factor_bar_interval": "1h",
         "factor_window_bars": "24", "return_bar_interval": "1h", "forward_return_bars": 1,
         "universe_key": "all", "symbol": "BTCUSDT", "window_scope": "1y", "scoring_version": "v1"}


def _sample() -> SummarySample:
    rows = tuple({**SCOPE, "id": fid + 10, "factor_id": fid, "run_id": f"run-{fid}",
                  "mean_ic": Decimal(value), "direction_sign": 1} for fid, value in [(1, "-0.3"), (2, "0.1"), (3, "0.2")])
    return SummarySample("sub_factor", NOW, SCOPE, rows, ())


def _page(sample: SummarySample, mode: str = "raw_signed") -> ToolPage:
    items = []
    for row in sample.rows:
        raw = float(row["mean_ic"])
        direction = row.get("direction_sign", 1)
        items.append({**row, "kind": sample.kind, "factor_ref": f"sub_factor:{row['factor_id']}",
                      "metric_id": row["id"], "raw_metric_value": raw, "direction_sign": direction,
                      "ranking_value": abs(raw) if mode == "absolute_diagnostic" else raw * direction if mode == "signed" else raw})
    items.sort(key=lambda row: row["ranking_value"], reverse=True)
    return ToolPage({"top_items": items[:2], "bottom_items": items[2:], "returned_count": 3,
                     "validity_evaluated": False}, {})


def _response(data: dict[str, Any], meta: dict[str, Any] | None = None) -> MCPResponse:
    body = {"data": data, "meta": meta or {"truncated": False, "next_cursor": None}}
    return MCPResponse(200, "application/json", {"result": {"structuredContent": body,
        "content": [{"type": "text", "text": json.dumps(body)}], "isError": False}}, "2025-06-18")


@pytest.mark.parametrize("mode", ["signed", "raw_signed", "absolute_diagnostic"])
def test_rank_oracle_accepts_all_three_modes(mode: str) -> None:
    sample = _sample()
    assert compare_rank(_page(sample, mode), sample, ranking_mode=mode, metric="mean_ic", top_k=2, bottom_k=1).issues == ()


@pytest.mark.parametrize("mutation,issue", [
    (lambda p: p.data["top_items"].reverse(), "rank:top_order"),
    (lambda p: p.data["top_items"][0].update(run_id="old"), "rank:not_latest_metric_run"),
    (lambda p: p.data["top_items"][0].update(metric_id=999), "rank:not_latest_metric_run"),
    (lambda p: p.data["top_items"][0].update(symbol="ETHUSDT"), "rank:scope=symbol"),
    (lambda p: p.data["top_items"][0].update(raw_metric_value=0.8), "rank:raw_metric_value"),
    (lambda p: p.data["top_items"][0].update(ranking_value=float("nan")), "rank:ranking_value"),
    (lambda p: p.data.update(returned_count=0), "rank:returned_count"),
    (lambda p: p.data.update(validity_evaluated=True), "rank:unexpected_validity_filter"),
    (lambda p: p.data["bottom_items"].__setitem__(0, p.data["top_items"][0]), "rank:duplicate_item"),
    (lambda p: p.data["top_items"].pop(), "rank:top_selection"),
])
def test_rank_oracle_rejects_business_corruption(mutation: Any, issue: str) -> None:
    sample = _sample()
    page = _page(sample)
    mutation(page)
    assert issue in compare_rank(page, sample, ranking_mode="raw_signed", metric="mean_ic", top_k=2, bottom_k=1).issues


def test_signed_direction_must_be_plus_or_minus_one() -> None:
    sample = _sample()
    page = _page(sample)
    page.data["top_items"][0]["direction_sign"] = 0
    assert "rank:direction_sign" in compare_rank(page, sample, ranking_mode="signed", metric="mean_ic", top_k=2, bottom_k=1).issues


def test_rank_does_not_pass_empty_samples_or_malformed_arrays() -> None:
    sample = _sample()
    with pytest.raises(ReadPrecondition):
        compare_rank(_page(sample), replace(sample, rows=()), ranking_mode="signed", metric="mean_ic", top_k=2, bottom_k=1)
    with pytest.raises(ReadContractError):
        compare_rank(ToolPage({"top_items": None}, {}), sample, ranking_mode="signed", metric="mean_ic", top_k=2, bottom_k=1)


def test_summary_checks_all_fields_including_null_and_utc_periods() -> None:
    expected = {key: None for key in (*_SUMMARY_FIELDS, *_PERIOD_FIELDS)}
    expected.update(id=1, **SCOPE, period_end=datetime(2026, 9, 5))
    actual = {**expected, "period_end": "2026-09-05T08:00:00+08:00"}
    assert compare_summary([actual], [expected]).issues == ()
    actual["mean_ic"] = 0
    actual["period_end"] = "2026-09-05T00:00:00+08:00"
    del actual["coverage_mean"]
    issues = compare_summary([actual], [expected]).issues
    assert "id=1:field=mean_ic" in issues
    assert "id=1:missing_field=coverage_mean" in issues
    assert "summary:field=period_end" in issues


def _scope_snapshot() -> MetricScopeSnapshot:
    base = {**SCOPE, "metric_period_end": datetime(2026, 9, 1), "run_completed_at": datetime(2026, 9, 5, 8)}
    rows = ({**base, "run_id": "old", "factor_id": 1},
            {**base, "run_id": "new", "factor_id": 1, "run_completed_at": datetime(2026, 9, 6, 8)},
            {**base, "run_id": "new", "factor_id": 2, "run_completed_at": datetime(2026, 9, 6, 8)})
    return MetricScopeSnapshot("sub_factor", NOW, {"ic_scope": "time_series", "interval": "1h", "universe_key": "all"}, rows)


def test_scope_union_and_completed_at_boundary_use_different_db_timezones() -> None:
    snapshot = _scope_snapshot()
    before = list(visible_metric_scopes(snapshot, NOW - timedelta(microseconds=1)).values())[0]
    equal = list(visible_metric_scopes(snapshot, NOW).values())[0]
    assert before["available_factor_count"] == 1
    assert equal["available_factor_count"] == 2  # Not 3 summed across runs.
    assert equal["run_completed_at"] == NOW
    assert equal["metric_period_end"] == datetime(2026, 9, 1, tzinfo=timezone.utc)


class _MCP:
    def __init__(self, responses: list[MCPResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResponse:
        """记录端点参数并返回离线响应；用尽时 IndexError，不发网络请求。"""
        self.calls.append((name, arguments))
        return self.responses.pop(0)


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_scope_service_checks_actual_pit_target(offset: int) -> None:
    snapshot = _scope_snapshot()
    time = NOW + timedelta(microseconds=offset)
    rows = list(visible_metric_scopes(snapshot, time).values())
    api_rows = [{key: value.isoformat() if isinstance(value, datetime) else value for key, value in row.items()} for row in rows]
    mcp = _MCP([_response({"items": api_rows})])
    check = Factor4SummaryService(Factor4SummaryAPI(mcp)).check_scopes(snapshot, offset=offset)
    assert check.issues == ()
    assert mcp.calls[0][1]["as_of"] == time.isoformat()


@pytest.mark.parametrize("mutation,issue", [
    (lambda d: d["items"][0].update(available_factor_count=3), "scopes:field=available_factor_count"),
    (lambda d: d["items"][0].update(run_completed_at="2026-09-07T00:00:00Z"), "scopes:field=run_completed_at"),
    (lambda d: d["items"].clear(), "scopes:missing_identity"),
    (lambda d: d["items"].append(copy.deepcopy(d["items"][0])), "scopes:duplicate_identity"),
])
def test_scope_service_rejects_wrong_union_future_missing_and_duplicate(mutation: Any, issue: str) -> None:
    snapshot = _scope_snapshot()
    rows = list(visible_metric_scopes(snapshot, NOW).values())
    data = {"items": [{key: value.isoformat() if isinstance(value, datetime) else value for key, value in row.items()} for row in rows]}
    mutation(data)
    mcp = _MCP([_response(data)])
    assert issue in Factor4SummaryService(Factor4SummaryAPI(mcp)).check_scopes(snapshot).issues


def test_api_rank_preserves_complete_scope_and_zero_sizes() -> None:
    mcp = _MCP([_response({})])
    Factor4SummaryAPI(mcp).rank(summary_scope(SCOPE), kind="sub_factor", as_of=NOW.isoformat(), top_k=0, bottom_k=0)
    tool, args = mcp.calls[0]
    assert tool == "factor_rank"
    assert args["top_k"] == args["bottom_k"] == 0
    assert args["symbol"] == "BTCUSDT"
    assert args["interval"] == "1h" and "factor_bar_interval" not in args
    assert args["validity_scope"] == "time_series"


def test_overlap_ties_block_instead_of_passing_without_a_bottom_refill_contract() -> None:
    sample = _sample()
    rows = tuple({**row, "mean_ic": Decimal("0.2") if i == 0 else Decimal("-0.1")}
                 for i, row in enumerate(sample.rows))
    sample = replace(sample, rows=rows)
    page = _page(sample)
    page.data["bottom_items"] = []
    page.data["returned_count"] = 2
    with pytest.raises(ReadPrecondition, match="BLOCKED_DOC: overlapping Top/Bottom"):
        compare_rank(page, sample, ranking_mode="raw_signed", metric="mean_ic", top_k=2, bottom_k=1)


def test_overlapping_selection_preserves_confirmed_field_failures_before_contract_block() -> None:
    sample = replace(_sample(), rows=tuple({**row, "mean_ic": Decimal("0.2")} for row in _sample().rows))
    page = _page(sample)
    page.data["top_items"][0]["run_id"] = "old-run"
    assert "rank:not_latest_metric_run" in compare_rank(page, sample, ranking_mode="raw_signed", metric="mean_ic", top_k=2, bottom_k=1).issues


def test_signed_missing_persisted_direction_is_not_inferred_from_response_or_training_ic() -> None:
    sample = replace(_sample(), rows=tuple({key: value for key, value in row.items() if key != "direction_sign"}
                                         for row in _sample().rows))
    sample = replace(sample, rows=tuple({**row, "rank_is_icir": 9} for row in sample.rows))
    mcp = _MCP([_response(json.loads(json.dumps(_page(sample, "signed").data, default=float)))])
    with pytest.raises(ReadPrecondition, match="explicit persisted direction_sign"):
        Factor4SummaryService(Factor4SummaryAPI(mcp)).check_rank(sample, ranking_mode="signed")
    assert len(mcp.calls) == 1


@pytest.mark.parametrize("source", ["column", "payload", "json_summary"])
def test_signed_rank_uses_independent_persisted_direction_for_all_candidates(source: str) -> None:
    signed = replace(_sample(), rows=tuple({**row, "direction_sign": -1 if row["factor_id"] == 1 else 1}
                                          for row in _sample().rows))
    page = _page(signed, "signed")
    if source != "column":
        rows = []
        for row in signed.rows:
            direction = row["direction_sign"]
            payload = {"direction_sign": direction} if source == "payload" else json.dumps({"summary": {"direction_sign": direction}})
            rows.append({**{key: value for key, value in row.items() if key != "direction_sign"}, "metrics_json": payload})
        signed = replace(signed, rows=tuple(rows))
    assert not compare_rank(page, signed, ranking_mode="signed", metric="mean_ic", top_k=2, bottom_k=1).issues
    assert [row["factor_id"] for row in page.data["top_items"]] == [1, 3]


def test_signed_rank_rejects_self_consistent_but_wrong_response_direction() -> None:
    sample = _sample()
    page = _page(sample, "signed")
    row = page.data["top_items"][0]
    row.update(direction_sign=-1, ranking_value=-row["raw_metric_value"])
    issues = compare_rank(page, sample, ranking_mode="signed", metric="mean_ic", top_k=2, bottom_k=1).issues
    assert "rank:direction_sign" in issues
    assert "rank:ranking_value" in issues


@pytest.mark.parametrize("mode", ["signed", "raw_signed", "absolute_diagnostic"])
def test_rank_first_place_cannot_be_omitted_while_counts_remain_self_consistent(mode: str) -> None:
    sample = _sample()
    page = _page(sample, mode)
    page.data["top_items"] = page.data["top_items"][1:]
    page.data["bottom_items"] = []
    page.data["returned_count"] = 1
    assert "rank:top_selection" in compare_rank(page, sample, ranking_mode=mode, metric="mean_ic", top_k=1, bottom_k=0).issues


@pytest.mark.parametrize("empty", [False, True], ids=["partial_signed_candidates", "no_resolved_candidates"])
def test_signed_rank_without_complete_direction_cannot_assume_all_raw_candidates_are_eligible(empty: bool) -> None:
    sample = replace(_sample(), rows=tuple({key: value for key, value in row.items() if key != "direction_sign"}
                                         for row in _sample().rows))
    page = _page(sample, "signed")
    page.data["top_items"].pop(0)
    if empty:
        page.data["top_items"] = []
        page.data["bottom_items"] = []
    page.data["returned_count"] = len(page.data["top_items"]) + len(page.data["bottom_items"])
    with pytest.raises(ReadPrecondition, match="explicit persisted direction_sign"):
        compare_rank(page, sample, ranking_mode="signed", metric="mean_ic", top_k=2, bottom_k=1)


def test_signed_missing_direction_still_reports_independently_wrong_raw_value() -> None:
    sample = replace(_sample(), rows=tuple({key: value for key, value in row.items() if key != "direction_sign"}
                                         for row in _sample().rows))
    page = _page(sample, "signed")
    page.data["top_items"].pop(0)
    page.data["top_items"][0]["raw_metric_value"] = 99
    page.data["returned_count"] = 2
    result = compare_rank(page, sample, ranking_mode="signed", metric="mean_ic", top_k=2, bottom_k=1)
    assert "rank:raw_metric_value" in result.issues
    assert "rank:top_cardinality" not in result.issues
    assert result.evidence["direction_oracle_complete"] is False


def test_signed_missing_direction_retains_requested_result_size_upper_bound() -> None:
    sample = replace(_sample(), rows=tuple({key: value for key, value in row.items() if key != "direction_sign"}
                                         for row in _sample().rows))
    page = _page(sample, "signed")
    result = compare_rank(page, sample, ranking_mode="signed", metric="mean_ic", top_k=1, bottom_k=1)
    assert "rank:top_cardinality" in result.issues


def test_signed_ties_allow_alternative_db_members_without_a_new_tie_breaker() -> None:
    sample = replace(_sample(), rows=tuple({**row, "mean_ic": Decimal("0.2")} for row in _sample().rows))
    page = _page(sample, "signed")
    page.data["top_items"] = [page.data["bottom_items"][0]]
    page.data["bottom_items"] = []
    page.data["returned_count"] = 1
    assert not compare_rank(page, sample, ranking_mode="signed", metric="mean_ic", top_k=1, bottom_k=0).issues


def _complete_summary() -> dict[str, Any]:
    row = {key: None for key in (*_SUMMARY_FIELDS, *_PERIOD_FIELDS)}
    row.update(**SCOPE, id=11, factor_id=1, run_id="run-1", is_sub_factor_id=1)
    return row


@pytest.mark.parametrize("mode", ["single", "batch", "explicit_run"])
def test_summary_service_uses_each_endpoint_actual_response_shape(mode: str) -> None:
    row = _complete_summary()
    ref = "sub_factor:1"
    data = {"factor_ref": ref, "ic_summaries": [row], "resolved_scope": SCOPE}
    if mode == "batch":
        data = {"items": [{"factor_ref": ref, "success": True, "data": {**row, "factor_ref": ref}}]}
    mcp = _MCP([_response(data)])
    sample = replace(_sample(), rows=(row,))
    check = Factor4SummaryService(Factor4SummaryAPI(mcp)).check_metrics(sample, batch=mode == "batch", explicit_run=mode == "explicit_run")
    assert not check.issues
    assert ("run_id" in mcp.calls[0][1]) is (mode == "explicit_run")


def test_summary_batch_missing_entity_or_failed_item_fails() -> None:
    sample = replace(_sample(), rows=(_complete_summary(),))
    mcp = _MCP([_response({"items": []}), _response({"items": [{"factor_ref": "sub_factor:1", "success": False}]})])
    service = Factor4SummaryService(Factor4SummaryAPI(mcp))
    assert "summary:batch_membership" in service.check_metrics(sample, batch=True).issues
    assert "summary:batch_item_failed" in service.check_metrics(sample, batch=True).issues


@pytest.mark.parametrize("explicit_run", [False, True], ids=["default_run", "explicit_run"])
def test_validity_batch_preserves_fixed_asof_and_optional_explicit_run(explicit_run: bool) -> None:
    row = {**SCOPE, "id": 101, "factor_id": 1, "is_sub_factor_id": 0, "run_id": "historical-run",
           "time_series_scoring_version": "v1"}
    sample = ValiditySample(row, {"ts": _complete_summary()})
    mcp = _MCP([_response({"items": [{"factor_ref": "factor:1", "success": True, "data": {"id": 101}}]})])
    check = Factor4SummaryService(Factor4SummaryAPI(mcp)).check_validity_batch(
        sample, explicit_run=explicit_run, as_of=NOW.isoformat())
    assert not check.issues
    arguments = mcp.calls[0][1]
    assert arguments["factor_refs"] == ["factor:1"]
    assert arguments["as_of"] == NOW.isoformat()
    assert "factor_ref" not in arguments
    assert ("run_id" in arguments) is explicit_run
    if explicit_run:
        assert arguments["run_id"] == "historical-run"


def test_slice_pages_use_exact_run_and_compare_page_counts_and_timestamps() -> None:
    from db.factor4_read_repository import SliceSample
    scope = {**SCOPE, "factor_id": 1, "run_id": "old-run"}
    rows = tuple({"id": i, "run_id": "old-run", "symbol": "BTCUSDT", "ic": Decimal("0.1"),
                  "slice_start": NOW, "slice_end": NOW, "as_of_time": NOW} for i in (1, 2))
    payload = [{**row, "ic": 0.1, **{key: NOW.isoformat() for key in ("slice_start", "slice_end", "as_of_time")}} for row in rows]
    mcp = _MCP([_response({"items": payload[:1], "returned_count": 1}, {"next_cursor": "page-2"}),
                _response({"items": payload[1:], "returned_count": 1})])
    result = Factor4SummaryService(Factor4SummaryAPI(mcp)).check_metric_slices(SliceSample(scope, rows), limit=1)
    assert not result.issues
    assert all(args["run_id"] == "old-run" and args["scoring_version"] == "v1" for _, args in mcp.calls)
    assert mcp.calls[1][1]["cursor"] == "page-2"


@pytest.mark.parametrize("mutation,issue", [
    (lambda rows: rows.append(dict(rows[0])), "slices:duplicate_identity"),
    (lambda rows: rows[0].pop("ic"), "slices:missing_ic"),
    (lambda rows: rows[0].update(ic=0.9), "slices:ic"),
    (lambda rows: rows.reverse(), "slices:order_or_membership"),
])
def test_slice_reconciliation_detects_corrupt_results(mutation: Any, issue: str) -> None:
    from db.factor4_read_repository import SliceSample
    scope = {**SCOPE, "factor_id": 1, "run_id": "run"}
    rows = tuple({"id": i, "ic": 0.1, "slice_start": NOW, "slice_end": NOW} for i in (1, 2))
    payload = [{**row, "slice_start": NOW.isoformat(), "slice_end": NOW.isoformat()} for row in rows]
    mutation(payload)
    mcp = _MCP([_response({"items": payload, "returned_count": len(payload)})])
    result = Factor4SummaryService(Factor4SummaryAPI(mcp)).check_metric_slices(SliceSample(scope, rows))
    assert issue in result.issues


def test_rank_replay_detects_changed_business_data() -> None:
    sample = _sample()
    first = _page(sample).data
    # Fixtures may contain Decimal DB values; API only needs endpoint fields.
    first = json.loads(json.dumps(first, default=float))
    second = {**first, "candidate_count": 999}
    mcp = _MCP([_response(first), _response(second)])
    check = Factor4SummaryService(Factor4SummaryAPI(mcp)).check_rank(sample, ranking_mode="raw_signed", repeat=True)
    assert check.issues == ("rank:replay_changed",)


class _FormulaMCP(_MCP):
    def get_formula(self, factor_ref: str, **kwargs: Any) -> MCPResponse:
        """返回离线公式响应并捕获查询时点；无网络，队列用尽 IndexError。"""
        return self.call_tool("factor_get_formula", {"factor_ref": factor_ref, **kwargs})


def _formula_sample() -> SummarySample:
    row = {**_sample().rows[0], "run_completed_at": datetime(2026, 9, 6, 8)}
    formula = {"factor_id": row["factor_id"], "run_id": row["run_id"], "recorded_at": datetime(2026, 9, 6, 7),
               "formula_hash": "hash-v1", "formula_version": "v1", "expression": "close.diff(1)"}
    return replace(_sample(), rows=(row,), formulas=(formula,))


def _error_response(code: str) -> MCPResponse:
    body = {"error": {"code": code}}
    return MCPResponse(200, "application/json", {"result": {"isError": True, "structuredContent": body,
        "content": [{"type": "text", "text": json.dumps(body)}]}}, "2025-06-18")


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_formula_completion_boundary_checks_expression_and_immutable_identity(offset: int) -> None:
    sample = _formula_sample()
    response = _response({**sample.formulas[0], "factor_ref": "sub_factor:1", "recorded_at": None,
                          "bar_interval": "1h", "metric_identity": SCOPE})
    if offset < 0:
        response = _error_response("FORMULA_EVIDENCE_NOT_FOUND")
    mcp = _FormulaMCP([response])
    check = Factor4SummaryService(Factor4SummaryAPI(mcp)).check_formula_completion(sample, offset)
    assert not check.issues
    assert mcp.calls[0][1]["as_of"] == (NOW + timedelta(microseconds=offset)).isoformat()


@pytest.mark.parametrize("field", ["expression", "formula_hash", "formula_version", "run_id", "factor_ref",
                                  "metric_identity", "bar_interval"])
def test_formula_corrupted_identity_or_body_is_not_accepted(field: str) -> None:
    sample = _formula_sample()
    data = {**sample.formulas[0], "factor_ref": "sub_factor:1", "recorded_at": None,
            "bar_interval": "1h", "metric_identity": SCOPE}
    data[field] = "different"
    mcp = _FormulaMCP([_response(data)])
    assert Factor4SummaryService(Factor4SummaryAPI(mcp)).check_formula_completion(sample, 0).issues


def test_formula_dependency_error_before_completion_is_not_evidence_of_invisibility() -> None:
    mcp = _FormulaMCP([_error_response("SERVICE_UNAVAILABLE")])
    with pytest.raises(ReadPrecondition, match="BLOCKED_DEPENDENCY"):
        Factor4SummaryService(Factor4SummaryAPI(mcp)).check_formula_completion(_formula_sample(), -1)


def test_zero_size_business_error_does_not_pass_as_empty_or_skip() -> None:
    mcp = _MCP([_error_response("INVALID_ARGUMENT")])
    with pytest.raises(ReadContractError, match="INVALID_ARGUMENT"):
        Factor4SummaryService(Factor4SummaryAPI(mcp)).check_rank(_sample(), ranking_mode="raw_signed", top_k=0, bottom_k=0)
