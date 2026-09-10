"""Offline corruption checks for public-selector handoff; not live business passes."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from api.factor4_summary_api import Factor4SummaryAPI
from api.factor_data_mcp_api import MCPResponse
from db.factor4_consumer_repository import Factor4ConsumerRepository, SelectedResultEvidence
from db.factor4_read_repository import SUMMARY_KEYS, SliceSample, SummarySample, ValiditySample
from service.factor4_consumer_journey_service import Factor4ConsumerJourneyService
from service.factor4_summary_service import Factor4SummaryService, _PERIOD_FIELDS, _SUMMARY_FIELDS, metric_slice_arguments

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)
SCOPE = {"ic_scope": "time_series", "calculation_mode": "direct", "factor_bar_interval": "1h",
         "factor_window_bars": "24", "return_bar_interval": "1h", "forward_return_bars": 1,
         "universe_key": "all", "symbol": "BTCUSDT", "window_scope": "rolling", "scoring_version": "v1"}


def _row() -> dict[str, Any]:
    row = dict.fromkeys((*_SUMMARY_FIELDS, *_PERIOD_FIELDS))
    return {**row, **SCOPE, "id": 12, "factor_id": 2, "is_sub_factor_id": 1, "run_id": "public-run",
            "mean_ic": 0.2, "mean_rank_ic": 0.3, "icir": 1.2, "rank_icir": 1.3, "final_score": 70,
            "period_start": "2026-09-01T00:00:00+00:00", "period_end": "2026-09-06T00:00:00+00:00"}


class _Repository:
    def __init__(self) -> None:
        self.row = _row()
        self.scopes: list[dict[str, Any]] = []
        self.formula = {**SCOPE, "factor_id": 2, "is_sub_factor_id": 1, "run_id": "public-run",
                        "expression": "mean(close, 24)", "formula_hash": "hash-public", "formula_version": "v1",
                        "source_detail_id": 8, "required_fields": ["close"]}
        self.validity = {"id": 21, "factor_id": 2, "run_id": "public-run", "time_series_status": "valid",
                         "time_series_is_valid": 1, "time_series_score": 70, "overall_is_valid": 1}
        self.slice = {**SCOPE, "id": 31, "factor_id": 2, "is_sub_factor_id": 1, "run_id": "public-run",
                      "slice_start": "2026-09-02T00:00:00+00:00", "slice_end": "2026-09-03T00:00:00+00:00",
                      "as_of_time": "2026-09-03T00:00:00+00:00", "ic": 0.2}
        self.missing_formula = False
        self.missing_validity = False
        self.missing_slices = False

    def summaries_at(self, sample: SummarySample, as_of: datetime) -> SummarySample:
        """Record public scope lookup and return independent final rows; no I/O."""
        self.scopes.append(sample.scope)
        return replace(sample, rows=(copy.deepcopy(self.row),))

    def selected_result_evidence(self, summary: dict[str, Any], as_of: datetime) -> SelectedResultEvidence:
        """Return independent optional evidence; no API data is used as expectation."""
        return SelectedResultEvidence(() if self.missing_formula else (self.formula,),
            () if self.missing_validity else (ValiditySample(self.validity, {"ts": self.row}),))

    def selected_run_summaries(self, summary: dict[str, Any], as_of: datetime) -> tuple[dict[str, Any], ...]:
        """Return all independent exact-Run periods; no singleton API assumption."""
        return (copy.deepcopy(self.row),)

    def slices_for_summary(self, summary: dict[str, Any], *, discover_symbol: bool = False) -> SliceSample | None:
        """Return the exact persisted series, never silently change a symbol."""
        assert not discover_symbol
        return None if self.missing_slices else SliceSample(self.row, (self.slice,))


class _MCP:
    def __init__(self, repository: _Repository, mutations: dict[str, Any] | None = None) -> None:
        self.repository = repository
        self.mutations = mutations or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResponse:
        """Return controlled public payloads; unknown tools raise KeyError without I/O."""
        self.calls.append((name, copy.deepcopy(arguments)))
        row = copy.deepcopy(self.repository.row)
        public = {**row, "factor_ref": "sub_factor:2", "kind": "sub_factor"}
        ranking = {**public, "metric_id": row["id"], "raw_metric_value": row["mean_ic"], "ranking_value": row["mean_ic"]}
        formula = {**copy.deepcopy(self.repository.formula), "factor_ref": "sub_factor:2", "metric_identity": dict(SCOPE)}
        data = {
            "factor_list_metric_scopes": {"items": [{**SCOPE, "kind": "sub_factor", "available_factor_count": 1}]},
            "factor_rank": {"top_items": [ranking], "bottom_items": [], "returned_count": 1, "validity_evaluated": False},
            "factor_search": {"items": [{**public, "id": 2, "metric_run_id": "public-run"}]},
            "factor_get_metrics": {"factor_ref": "sub_factor:2", "ic_summaries": [public], "resolved_scope": dict(SCOPE)},
            "factor_get_formula": formula,
            "factor_get_validity": {"item": {**self.repository.validity, "metric_id": 12, "validity_status": "valid"}},
            "factor_get_metric_slices": {"items": [copy.deepcopy(self.repository.slice)]},
        }[name]
        if name in self.mutations:
            self.mutations[name](data)
        body = {"data": data, "meta": {"next_cursor": None, "truncated": False}}
        return MCPResponse(200, "application/json", {"result": {"structuredContent": body,
            "content": [{"type": "text", "text": json.dumps(body)}], "isError": False}}, "2025-06-18")

    def get_formula(self, factor_ref: str, **arguments: Any) -> MCPResponse:
        """Keep the exact public formula identity and return its controlled response."""
        return self.call_tool("factor_get_formula", {"factor_ref": factor_ref, **arguments})


def _journey(mutations: dict[str, Any] | None = None) -> tuple[Factor4ConsumerJourneyService, _Repository, _MCP, SummarySample]:
    repository = _Repository()
    mcp = _MCP(repository, mutations)
    service = Factor4ConsumerJourneyService(Factor4SummaryService(Factor4SummaryAPI(mcp)), repository)
    # Seed differs from the actual public choice, so request backfill is observable.
    seed = SummarySample("sub_factor", NOW, {**SCOPE, "factor_window_bars": "99"},
                         ({**repository.row, "run_id": "seed-run"},), ())
    return service, repository, mcp, seed


@pytest.mark.parametrize("entry", ["search", "rank"])
def test_public_handoff_uses_returned_window_run_and_period_not_seed_or_slice_dates(entry: str) -> None:
    """Every tail request must carry public choices even when DB seed values differ."""
    service, repository, mcp, seed = _journey()
    result = service.check_discovered_selection(seed, entry)
    assert not result.issues and not result.evidence["blocked"]
    assert set(result.evidence["stages"]) == {"scope_discovery", entry, "metrics", "validity", "formula", "slices"}
    assert repository.scopes == [SCOPE]
    for name, arguments in mcp.calls[1:]:
        assert arguments["factor_window_bars"] == "24"
        assert arguments["as_of"] == NOW.isoformat()
        if name not in {"factor_search", "factor_rank"}:
            assert arguments["run_id"] == "public-run"
    slices = next(args for name, args in mcp.calls if name == "factor_get_metric_slices")
    assert slices["start_time"] == "2026-09-01T00:00:00+00:00"
    assert slices["end_time"] == "2026-09-07T00:00:00+00:00"
    assert slices["symbol"] == "BTCUSDT"


@pytest.mark.parametrize("tool,mutation,issue", [
    ("factor_list_metric_scopes", lambda data: data["items"][0].pop("factor_window_bars"), "consumer:scope_missing_public_selector"),
    ("factor_rank", lambda data: data["top_items"][0].update(run_id="wrong-run"), "rank:not_latest_metric_run"),
    ("factor_rank", lambda data: data["top_items"][0].update(factor_ref="factor:2"), "rank:factor_identity"),
    ("factor_get_metrics", lambda data: data["ic_summaries"][0].update(mean_ic=0.8), "consumer:rank_to_metrics_binding"),
    ("factor_get_formula", lambda data: data.update(formula_hash="wrong-hash"), "sub_factor:2:formula:formula_hash"),
    ("factor_get_validity", lambda data: data["item"].update(metric_id=999), "validity:metric_id"),
    ("factor_get_metric_slices", lambda data: data["items"][0].update(symbol="ETHUSDT"), "slices:symbol"),
])
def test_corruptions_are_business_issues_not_missing_samples(tool: str, mutation: Any, issue: str) -> None:
    """Changed identity or values cannot pass through a coherent-looking chain."""
    service, _, _, seed = _journey({tool: mutation})
    assert issue in service.check_discovered_selection(seed, "rank").issues


def test_formula_failure_is_retained_when_later_slice_evidence_is_missing() -> None:
    """An independent missing tail must not hide an earlier formula contradiction."""
    service, repository, _, seed = _journey({"factor_get_formula": lambda data: data.update(formula_hash="wrong")})
    repository.missing_slices = True
    result = service.check_discovered_selection(seed, "rank")
    assert "sub_factor:2:formula:formula_hash" in result.issues
    assert result.evidence["blocked"]


def test_missing_evidence_keeps_checked_stages_without_claiming_complete_chain() -> None:
    """Missing formula, validity and slices are distinct evidence gaps, not passes."""
    service, repository, _, seed = _journey()
    repository.missing_formula = repository.missing_validity = repository.missing_slices = True
    result = service.check_discovered_selection(seed, "search")
    assert not result.issues
    assert len(result.evidence["blocked"]) == 3
    assert result.evidence["stages"] == ("scope_discovery", "search", "metrics")


@pytest.mark.parametrize("entry", ["search", "rank"])
def test_multiple_periods_in_exact_run_are_reconciled_before_selecting_public_item(entry: str) -> None:
    """Explicit Run may return multiple periods; ranking identifies one by metric ID."""
    service, repository, mcp, seed = _journey()
    older = {**repository.row, "id": 11, "icir": 0.8, "period_start": "2026-08-01T00:00:00+00:00"}
    repository.selected_run_summaries = lambda summary, as_of: (older, repository.row)
    mcp.mutations["factor_get_metrics"] = lambda data: data["ic_summaries"].insert(0, copy.deepcopy(older))
    result = service.check_discovered_selection(seed, entry)
    assert not result.issues and not result.evidence["blocked"]
    assert "slices" in result.evidence["stages"]


def test_scope_error_returns_before_a_later_missing_metric_can_hide_it() -> None:
    """Known invalid scope identity wins over a downstream no-finite-rank sample."""
    service, repository, mcp, seed = _journey({
        "factor_list_metric_scopes": lambda data: data["items"][0].update(kind="factor")})
    repository.row["mean_ic"] = None
    result = service.check_discovered_selection(seed, "rank")
    assert result.issues == ("consumer:scope_initial_filter_mismatch",)
    assert [name for name, _ in mcp.calls] == ["factor_list_metric_scopes"]


@pytest.mark.parametrize("exposes_period", [True, False])
def test_search_uses_public_period_to_resolve_otherwise_equal_metric_periods(exposes_period: bool) -> None:
    """Use an exposed period; identical summaries without that link remain ambiguous."""
    service, repository, mcp, seed = _journey()
    older = {**repository.row, "id": 11, "period_end": "2026-09-05T00:00:00+00:00"}
    repository.selected_run_summaries = lambda summary, as_of: (older, repository.row)
    mcp.mutations["factor_get_metrics"] = lambda data: data["ic_summaries"].insert(0, copy.deepcopy(older))
    if exposes_period:
        mcp.mutations["factor_search"] = lambda data: data["items"][0].update(metric_period_end=repository.row["period_end"])
    result = service.check_discovered_selection(seed, "search")
    assert not result.issues
    assert bool(result.evidence["blocked"]) is not exposes_period
    assert ("formula" in result.evidence["stages"]) is exposes_period


def test_explicit_slice_period_requires_aware_increasing_visible_times() -> None:
    """Public period validation cannot silently query future or timezone-less dates."""
    repository = _Repository()
    sample = SliceSample(repository.row, (repository.slice,))
    for times in (("2026-09-01", "2026-09-07"), (NOW.isoformat(), NOW.isoformat()),
                  ("2026-09-01T00:00:00Z", "2027-01-01T00:00:00Z")):
        with pytest.raises(ValueError):
            metric_slice_arguments(sample, as_of=NOW.isoformat(), time_range=times)


def test_selected_evidence_repository_uses_exact_fk_and_visibility_parameters() -> None:
    """The oracle queries formula identity and validity summary FK at the fixed instant."""
    queries = []

    class Transaction:
        def fetch_all(self, sql: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
            """Capture parameterized entity reads; return absence without raising."""
            queries.append((sql, parameters))
            return []

    class Repository(Factor4ConsumerRepository):
        @contextmanager
        def _snapshot(self):
            yield Transaction()

    result = Repository(None).selected_result_evidence(_row(), NOW)
    assert not result.formulas and not result.validities
    assert "time_series_summary_id=%s" in queries[1][0]
    assert queries[1][1][:4] == ("public-run", 2, 1, 12)
    assert queries[0][1][-1] == datetime(2026, 9, 8, 8)
