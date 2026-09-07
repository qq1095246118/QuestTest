"""Offline counterexamples for exact-partition run orchestration, not live pass evidence."""

from datetime import datetime, timezone
from dataclasses import replace

import pytest

from db.factor4_read_repository import SliceSample, SummarySample, ValiditySample
from service.factor4_read_service import ReadCheck, ReadPrecondition
from service.factor4_run_selection_service import Factor4RunSelectionService

pytestmark = pytest.mark.unit


class _Summaries:
    def __init__(self) -> None:
        self.calls = []

    def check_metrics(self, sample: SummarySample, *, explicit_run: bool) -> ReadCheck:
        """Record metric selection and expose one injected mismatch without network I/O."""
        self.calls.append(("metrics", sample.rows[0]["run_id"], explicit_run))
        return ReadCheck(1, ("summary:injected",))

    def check_validity(self, sample: ValiditySample, scope: str, *, as_of: str, explicit_run: bool) -> ReadCheck:
        """Record validity selection; returns success without network I/O or exceptions."""
        self.calls.append((scope, sample.validity["run_id"], explicit_run))
        return ReadCheck(1)


def _history() -> tuple[ValiditySample, ...]:
    scope = {"ic_scope": "time_series", "calculation_mode": "direct", "factor_bar_interval": "1h",
             "factor_window_bars": "24", "return_bar_interval": "1h", "forward_return_bars": 1,
             "universe_key": "all", "symbol": "", "window_scope": "1y", "scoring_version": "v1"}
    return tuple(ValiditySample({"id": index + 1, "factor_id": 1, "run_id": run,
                                "run_completed_at": datetime(2026, 9, 6 - index, tzinfo=timezone.utc),
                                "updated_at": datetime(2026, 9, 6 - index, tzinfo=timezone.utc)},
                  {key: {**scope, "id": index, "factor_id": 1, "is_sub_factor_id": 1, "run_id": run}
                   for key in ("ts", "cs")}) for index, run in enumerate(("new", "old")))


class _Repository:
    def __init__(self, history: tuple[ValiditySample, ...]) -> None:
        self.history = history

    def summaries_at(self, sample: SummarySample, as_of: datetime) -> SummarySample:
        """Select the latest completed fixture independently of validity update order."""
        chosen = max(self.history, key=lambda item: item.validity["run_completed_at"])
        return replace(sample, as_of=as_of, rows=(chosen.summaries["ts"],))

    def validity_candidates(self, sample: ValiditySample, scope: str, as_of: datetime) -> tuple[ValiditySample, ...]:
        """Return all fixture candidates without any validity-shape filtering."""
        return self.history


@pytest.mark.parametrize("older,run", [(False, "new"), (True, "old")])
@pytest.mark.parametrize("scope", ["ts", "cs"])
def test_run_selection_keeps_endpoint_pair_and_propagates_mismatch(older: bool, run: str, scope: str) -> None:
    summaries = _Summaries()
    result = Factor4RunSelectionService(summaries).check_run_selection(_history(), scope, older=older, repository=_Repository(_history()))
    assert summaries.calls == [("metrics", run, older), (scope, run, older)]
    assert result.checked_count == 2 and result.issues == ("summary:injected",)


def test_run_selection_rejects_missing_or_cross_factor_history_before_calls() -> None:
    summaries = _Summaries()
    service = Factor4RunSelectionService(summaries)
    with pytest.raises(ReadPrecondition):
        service.check_run_selection((), "ts", older=False, repository=_Repository(()))
    history = _history()
    history[1].summaries["ts"]["factor_id"] = 99
    with pytest.raises(ValueError, match="same exact partition"):
        service.check_run_selection(history, "ts", older=False, repository=_Repository(history))
    assert not summaries.calls


def test_backfilled_old_validity_does_not_change_default_metric_run_or_hide_metric_failure() -> None:
    history = _history()
    history[1].validity["updated_at"] = datetime(2026, 9, 7, tzinfo=timezone.utc)
    reversed_history = tuple(reversed(history))
    summaries = _Summaries()
    result = Factor4RunSelectionService(summaries).check_run_selection(
        reversed_history, "ts", older=False, repository=_Repository(reversed_history))
    assert summaries.calls == [("metrics", "new", False)]
    assert result.issues == ("summary:injected",)
    assert result.evidence["blocked"].startswith("BLOCKED_DOC")


def test_ambiguous_default_validity_precedence_is_not_a_product_failure() -> None:
    history = _history()
    history[1].validity["updated_at"] = datetime(2026, 9, 7, tzinfo=timezone.utc)
    with pytest.raises(ReadPrecondition, match="BLOCKED_DOC"):
        Factor4RunSelectionService.latest_validity(history, history[0], "ts", datetime(2026, 9, 8, tzinfo=timezone.utc))


def test_latest_validity_does_not_filter_new_invalid_shape_or_reveal_future_rows() -> None:
    history = _history()
    history[0].validity.update(overall_is_valid=0, time_series_is_valid=0, cross_sectional_is_valid=0)
    history[1].validity.update(overall_is_valid=1, time_series_is_valid=1, cross_sectional_is_valid=0)
    choose = Factor4RunSelectionService.latest_validity
    assert choose(history, history[1], "ts", datetime(2026, 9, 6, tzinfo=timezone.utc)).validity["run_id"] == "new"
    assert choose(history, history[1], "ts", datetime(2026, 9, 5, 23, 59, 59, tzinfo=timezone.utc)).validity["run_id"] == "old"


@pytest.mark.parametrize("variant", ["run_omitted", "run_null"])
@pytest.mark.parametrize("endpoint", ["metrics", "validity"])
def test_default_argument_matrix_selects_independent_endpoint_evidence(
    monkeypatch: pytest.MonkeyPatch, variant: str, endpoint: str,
) -> None:
    from service.factor4_validity_service import Factor4ValidityService
    history = _history()
    calls = []

    def capture(self, sample, selected_endpoint, selected_variant, **kwargs):
        calls.append((sample, selected_endpoint, selected_variant, kwargs))
        return ReadCheck(1)

    monkeypatch.setattr(Factor4ValidityService, "check_argument_variant", capture)
    result = Factor4RunSelectionService(_Summaries()).check_default_argument_variant(
        _Repository(history), history[1], endpoint, variant)
    sample, actual_endpoint, actual_variant, kwargs = calls[0]
    assert actual_endpoint == endpoint and actual_variant == variant
    assert datetime.fromisoformat(kwargs["as_of"]).tzinfo is not None
    if endpoint == "metrics":
        assert sample is history[1]  # Keep real validity evidence; do not fabricate a new validity row.
        assert kwargs["metric_expected"].rows[0]["run_id"] == "new"
    else:
        assert sample.validity["run_id"] == "new"
        assert "metric_expected" not in kwargs
    assert not result.issues


def test_explicit_old_run_ignores_later_validity_backfill() -> None:
    history = _history()
    history[1].validity["updated_at"] = datetime(2026, 9, 7, tzinfo=timezone.utc)
    summaries = _Summaries()
    Factor4RunSelectionService(summaries).check_run_selection(
        tuple(reversed(history)), "ts", older=True, repository=_Repository(history))
    assert summaries.calls == [("metrics", "old", True), ("ts", "old", True)]


@pytest.mark.parametrize("stale_response", [False, True])
def test_default_slice_uses_latest_exact_symbol_summary_without_validity(stale_response: bool) -> None:
    history = _history()
    seed_row = {**history[1].summaries["ts"], "symbol": "BTCUSDT"}
    newest = {**seed_row, "id": 999, "run_id": "new-run-without-validity"}
    queried = []

    class Repository:
        def validity_history(self):
            raise AssertionError("slice selection must not depend on validity")
        def slice_sample(self, scope, *, symbol_mode):
            assert scope == "time_series" and symbol_mode == "symbol"
            return SliceSample(seed_row, ({"id": 1},))
        def summaries_at(self, sample, as_of):
            assert sample.scope["symbol"] == "BTCUSDT"
            queried.append(as_of.isoformat())
            return replace(sample, rows=(newest,))
        def slices_for_summary(self, summary, *, discover_symbol):
            assert summary is newest and discover_symbol is False
            return SliceSample(summary, ({"id": 2, "run_id": newest["run_id"]},))
        def slice_watermark(self, sample):
            return {"count": 1}

    class Summaries:
        def check_metric_slices(self, sample, **kwargs):
            assert kwargs["explicit_run"] is False
            assert kwargs["as_of"] == queried[0]
            assert sample.summary["run_id"] == "new-run-without-validity"
            returned = "old" if stale_response else "new-run-without-validity"
            return ReadCheck(1, ("slices:run_id",) if returned != sample.summary["run_id"] else ())

    check = Factor4RunSelectionService(Summaries()).check_default_slice_run(Repository())
    assert bool(check.issues) is stale_response


def test_default_slice_does_not_fall_back_when_latest_run_has_no_slices() -> None:
    row = {**_history()[0].summaries["ts"], "symbol": "BTCUSDT"}
    class Repository:
        def slice_sample(self, scope, *, symbol_mode):
            return SliceSample(row, ({"id": 1},))
        def summaries_at(self, sample, as_of):
            return replace(sample, rows=({**row, "run_id": "new-no-slices"},))
        def slices_for_summary(self, summary, *, discover_symbol):
            assert summary["run_id"] == "new-no-slices"
            return None
    with pytest.raises(ReadPrecondition, match="latest completed Run has no slices"):
        Factor4RunSelectionService(_Summaries()).check_default_slice_run(Repository())
