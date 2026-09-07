"""Offline fault injection for formula-catalog migration business checks."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from db.factor4_formula_repository import Factor4FormulaRepository, FormulaCatalogSnapshot, FormulaResultSnapshot
from service.factor4_formula_service import Factor4FormulaService, formula_candidates
from service.factor4_read_service import ReadCheck, ReadPrecondition
from service.factor4_summary_service import _SUMMARY_FIELDS, _PERIOD_FIELDS
from tests.unit.test_factor4_read_service import _response
from tests.unit.test_factor4_summary_repository import _DB

pytestmark = pytest.mark.unit


def _detail(expression: str = "close.rolling(window).mean()", **overrides: Any) -> dict[str, Any]:
    return {"id": 1, "factor_id": 8, "is_sub_factor_id": 1, "name": "momentum_24h",
            "calc_logic": expression, "params": {"window": 24, "fields": ["close"], "factor_bar_interval": "1h"},
            "data_source_metadata": {"required_fields": ["close"], "factor_bar_interval": "1h"},
            "calc_function": "", "updated_at": "2026-08-01", **overrides}


def _catalog(*details: dict[str, Any]) -> FormulaCatalogSnapshot:
    return FormulaCatalogSnapshot(datetime(2026, 9, 6, tzinfo=timezone.utc), details or (_detail(),), (), (), ())


class _API:
    def __init__(self, formula: dict[str, Any] | None = None) -> None:
        self.data = formula or {}
        self.calls = 0

    def raw_schema(self) -> MCPResponse:
        """Return a deterministic approved schema without transport."""
        return _response({"data": {"mappings": [{"field_name": "close"}]}, "meta": {}})

    def formula(self, row: dict[str, Any], **kwargs: Any) -> MCPResponse:
        """Return queued formula content and count exact identity reads."""
        self.calls += 1
        return _response({"data": self.data, "meta": {}})


class _Repo:
    def __init__(self, snapshot: FormulaResultSnapshot) -> None:
        self.snapshot = snapshot

    def factor_results(self, factor_id: int) -> FormulaResultSnapshot:
        """Return only final-data fixtures; no raw market input is fabricated."""
        return self.snapshot


@pytest.mark.parametrize(("expression", "category"), [
    ("", "empty_formula"), ("close.(", "syntax"), ("unknown_raw + close", "unresolved_inputs"),
    ("forward_return + close", "future_target"), ("close * 8760", "annualization"),
    ("close.shift(-1)", "negative_temporal"), ("shift(close, -1)", "negative_temporal"),
    ("close.rolling(12, center=True).mean()", "center_or_backfill"),
    ("close.bfill()", "center_or_backfill"), ("close.mean()", "unbounded_aggregate"),
    ("close.diff(12)", "temporal_constants"), ("close.diff(12)", "independent_temporal_family"),
])
def test_semantic_scan_discovers_each_expression_branch(expression: str, category: str) -> None:
    assert category in {row["category"] for row in formula_candidates(_catalog(_detail(expression)))}


def test_rolling_aggregate_and_negative_operand_are_not_temporal_candidates() -> None:
    candidates = formula_candidates(_catalog(_detail("close.rolling(12).mean() + diff(-close, 1)")))
    assert not {"negative_temporal", "unbounded_aggregate"} & {row["category"] for row in candidates}


def test_wrapper_fields_resolve_params_only_missing_inputs() -> None:
    row = _detail("close + volume", calc_function="required_fields = ['close', 'volume']")
    candidates = formula_candidates(_catalog(row))
    assert "unresolved_inputs" not in {item["category"] for item in candidates}
    assert "params_only_input_miss" in {item["category"] for item in candidates}


def test_frequency_metadata_and_wrapper_fields_are_distinct_candidates() -> None:
    row = _detail(data_source_metadata={"factor_bar_interval": "4h", "datasets": [{"source_interval": "8h"}],
                                      "field_resolution": [{"frequency": "1d", "field_name": "close"}]})
    categories = {item["category"] for item in formula_candidates(_catalog(row))}
    assert {"interval_mismatch", "dataset_frequency", "field_frequency"} <= categories


def test_fixed_family_requires_different_declared_windows_and_same_expression() -> None:
    first = _detail("close.diff(12)", params={"window": 24, "original_sub_factor": "parent"})
    second = _detail("close.diff(12)", id=2, factor_id=9, params={"window": 48, "original_sub_factor": "parent"})
    candidates = formula_candidates(_catalog(first, second))
    assert len([row for row in candidates if row["category"] == "fixed_formula_family"]) == 2


def test_dynamic_window_family_is_not_misclassified_as_fixed_horizon() -> None:
    first = _detail(params={"window": 24, "original_sub_factor": "parent"})
    second = _detail(id=2, factor_id=9, params={"window": 48, "original_sub_factor": "parent"})
    assert not [row for row in formula_candidates(_catalog(first, second)) if row["category"] == "fixed_formula_family"]


def test_unestablished_aggregate_contract_is_not_faked_as_pass_or_defect() -> None:
    service = Factor4FormulaService(_API(), None)
    result = service.check_semantic_category(_catalog(_detail("close.mean()")), "unbounded_aggregate")
    assert result.checked_count == 1
    assert not result.issues
    assert {row["reason"] for row in result.evidence["blocked"]} == {
        "SEMANTIC_CONTRACT_REQUIRED:unbounded_aggregate", "NO_COMPLETED_FORMULA_EVIDENCE"}


def test_old_detail_does_not_fail_current_schema() -> None:
    old = _detail("unknown_raw + close", updated_at="2026-07-01")
    current = _detail(id=2, updated_at="2026-08-01")
    result = Factor4FormulaService(_API(), None).check_semantic_category(_catalog(old, current), "unresolved_inputs")
    assert not result.issues
    assert result.evidence["blocked"][0]["reason"] == "HISTORICAL_DETAIL_NOT_CURRENT"


def test_approved_global_input_resolves_local_unknown() -> None:
    row = _detail("close", params={}, data_source_metadata={})
    result = Factor4FormulaService(_API(), None).check_semantic_category(_catalog(row), "unresolved_inputs")
    assert not result.issues


def test_latest_persisted_value_without_same_completed_run_is_failure() -> None:
    value = {"id": 2, "run_id": "new", "factor_bar_interval": "1h", "factor_window_bars": "12H", "factor_value": 2, "adjusted_factor_value": None}
    evidence = {"run_id": "old", "factor_bar_interval": "1h", "factor_window_bars": "12H"}
    result = Factor4FormulaService(_API(), _Repo(FormulaResultSnapshot((evidence,), (), (), (value,)))).check_persisted_value_run(8)
    assert result.issues == ("sub_factor:8:latest_value_without_exact_completed_formula",)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not-numeric"])
def test_nonfinite_or_invalid_final_values_are_failed(value: str) -> None:
    row = {"id": 2, "run_id": "new", "factor_bar_interval": "1h", "factor_window_bars": "12H", "factor_value": value}
    result = Factor4FormulaService(_API(), _Repo(FormulaResultSnapshot((), (), (), (row,)))).check_persisted_value_run(8)
    assert any("factor_value" in issue for issue in result.issues)


def test_absent_persisted_values_block_after_real_data_discovery() -> None:
    with pytest.raises(ReadPrecondition, match="no persisted factor value"):
        Factor4FormulaService(_API(), _Repo(FormulaResultSnapshot((), (), (), ()))).check_persisted_value_run(8)


def test_exact_formula_projection_checks_hash_window_and_lookback() -> None:
    row = {"id": 1, "factor_id": 8, "is_sub_factor_id": 1, "run_id": "run", "calculation_mode": "direct",
           "factor_bar_interval": "1h", "factor_window_bars": "12H", "return_bar_interval": "1h", "forward_return_bars": 1,
           "expression": "close.diff(12)", "formula_hash": "hash", "formula_version": "v1", "source_detail_id": 7,
           "required_fields": '["close"]', "lookback_json": '{"bars":13}', "lag_json": None}
    data = {**row, "factor_ref": "sub_factor:8", "required_fields": ["close"], "lookback": {"bars": 13}, "lag": None,
            "metric_identity": {key: row[key] for key in ("calculation_mode", "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars")}}
    api = _API(data)
    service = Factor4FormulaService(api, None)
    assert not service.check_exact_formula(row).issues
    assert not service.check_exact_formula(row).issues
    assert api.calls == 1
    altered = {**data, "formula_hash": "wrong", "lookback": {"bars": 4}, "metric_identity": {**data["metric_identity"], "factor_window_bars": "24H"}}
    result = Factor4FormulaService(_API(altered), None).check_exact_formula(row)
    assert len(result.issues) == 3


def test_catalog_repository_is_atomic_read_only_and_final_results_are_parameterized() -> None:
    db = _DB([{"as_of_time": datetime(2026, 9, 6)}], [[], [], [], []])
    snapshot = Factor4FormulaRepository(db).catalog()
    assert snapshot.as_of.tzinfo is timezone.utc
    assert db.calls[-1] == ("ROLLBACK", ())
    assert any("START TRANSACTION WITH CONSISTENT SNAPSHOT" == sql for sql, _ in db.calls)
    db = _DB([], [[], [], [], []])
    Factor4FormulaRepository(db).factor_results(8)
    queries = [(sql, args) for sql, args in db.calls if "SELECT" in sql]
    assert len(queries) == 4
    assert all(args == (8,) and "%s" in sql for sql, args in queries)
    assert db.calls[-1] == ("ROLLBACK", ())


@pytest.mark.parametrize("value", [True, 0, -1, "8 OR 1=1"])
def test_invalid_factor_id_never_opens_database(value: Any) -> None:
    db = _DB([], [])
    with pytest.raises(ValueError):
        Factor4FormulaRepository(db).factor_results(value)
    assert not db.calls


def test_unknown_semantic_category_never_false_passes_empty_selection() -> None:
    with pytest.raises(ValueError, match="unsupported formula semantic category"):
        Factor4FormulaService(_API(), None).check_semantic_category(_catalog(), "unknown")


def test_declared_inputs_missing_from_executable_projection_are_detected() -> None:
    definition = {"id": 8, "window": "24H", "formula_summary": "close.diff(24)"}
    detail = _detail("close.diff(24)")
    snapshot = replace(_catalog(detail), definitions=(definition,))
    class DetailAPI(_API):
        def detail(self, factor_ref: str, level: str) -> MCPResponse:
            """Return the requested identity but deliberately omit executable field declarations."""
            data = {"factor_ref": factor_ref, "window": "24H", "formula_summary": "close.diff(24)"}
            if level == "executable":
                data.update(calc_logic="close.diff(24)", source_detail_id=1)
            return _response({"data": data, "meta": {}})
    result = Factor4FormulaService(DetailAPI(), None).check_detail_levels(snapshot, 8)
    assert result.issues == ("sub_factor:8:executable:declared_field_projection",)


@pytest.mark.parametrize("factor_id", [161628, 161629, 161630, 5921])
def test_detail_case_split_retains_default_output_and_internal_mathematics(factor_id: int) -> None:
    """The four original detail branches explicitly select False/True in their split entrypoints."""
    from tests.cases.factor4 import test_formula_catalog_business as cases

    calls: list[tuple[int, bool]] = []
    snapshot = _catalog()

    class RecordingService:
        def check_detail_levels(
            self, actual: FormulaCatalogSnapshot, selected_id: int, *, include_internal_semantics: bool,
        ) -> ReadCheck:
            """Record explicit scope selection and return an offline successful projection."""
            assert actual is snapshot
            calls.append((selected_id, include_internal_semantics))
            return ReadCheck(3, ())

    service = RecordingService()
    if factor_id == 5921:
        cases.test_targeted_formula_current_definition_projects_its_current_window(service, snapshot)
    else:
        cases.test_iv_rv_all_detail_levels_and_global_input_schema(service, snapshot, factor_id)
    cases.test_formula_detail_levels_internal_semantics(service, snapshot, factor_id)
    assert calls == [(factor_id, False), (factor_id, True)]


@pytest.mark.parametrize("drift", [False, True])
def test_result_catalog_branches_do_not_run_semantic_scanners(
    monkeypatch: pytest.MonkeyPatch, drift: bool,
) -> None:
    """All retained catalog/detail/evidence paths run without expression or family oracles."""
    import service.factor4_formula_service as formula_module

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("result-only catalog path invoked mathematical scanner")

    for name in ("formula_candidates", "_expression_inputs", "_formula_candidate", "_is_iv_rv_difference", "formula_dependency_offsets"):
        monkeypatch.setattr(formula_module, name, forbidden)
    detail = _detail("producer_specific_operator(close, window)")
    definition = {"id": 8, "window": "24H", "formula_summary": "producer summary"}
    evidence = {"id": 71, "factor_id": 8, "is_sub_factor_id": 1, "run_status": "completed",
                "run_id": "actual-run", "run_completed_at": "2026-09-01", "recorded_at": "2026-09-01",
                "calculation_mode": "direct", "factor_bar_interval": "1h", "factor_window_bars": "24H",
                "return_bar_interval": "1h", "forward_return_bars": 1, "source_detail_id": 1,
                "expression": detail["calc_logic"], "formula_hash": "actual-hash", "formula_version": "v1",
                "required_fields": ["close"], "metadata_complete": True}
    snapshot = replace(_catalog(detail), definitions=(definition,), evidence=(evidence,),
                       routes=({"factor_ref": "sub_factor:8"},))

    class ProjectionAPI(_API):
        def __init__(self) -> None:
            data = {**evidence, "factor_ref": "sub_factor:8", "metric_identity": evidence,
                    "formula_hash": "wrong-hash" if drift else "actual-hash"}
            super().__init__(data)
            self.mcp = self

        def detail(self, factor_ref: str, level: str) -> MCPResponse:
            """Return captured current metadata with an optional output-only drift."""
            data = {**detail, "factor_ref": factor_ref, "window": "24H", "formula_summary": "producer summary",
                    "source_detail_id": 1, "calc_logic": "wrong(close)" if drift else detail["calc_logic"]}
            return _response({"data": data, "meta": {}})

        def get_factor_details_batch(self, refs: list[str], *, detail_level: str) -> MCPResponse:
            """Return active references without making a real MCP call."""
            data = {**detail, "source_detail_id": 1,
                    "calc_logic": "wrong(close)" if drift else detail["calc_logic"]}
            return _response({"data": {"items": [{"factor_ref": ref, "success": True, "data": data} for ref in refs]}, "meta": {}})

    service = Factor4FormulaService(ProjectionAPI(), None)
    active = service.check_active_catalog(snapshot, include_internal_semantics=False)
    levels = service.check_detail_levels(snapshot, 8, include_internal_semantics=False)
    source = service.check_evidence_catalog(snapshot, include_internal_semantics=False)
    candidate = service.check_candidate_projection(snapshot, 8)
    assert bool(active.issues) is drift
    assert bool(levels.issues) is drift
    assert not source.issues
    assert bool(candidate.issues) is drift
    if drift:
        assert "sub_factor:8:formula:formula_hash" in candidate.issues


class _ChainAPI(_API):
    def __init__(self, row: dict[str, Any], *, metric_drift: bool = False, wrong_window_accepted: bool = False) -> None:
        formula = {key: row[key] for key in ("run_id", "factor_id", "is_sub_factor_id", "calculation_mode", "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars")}
        formula.update(id=71, formula_hash="hash", formula_version="v1", expression="close.diff(12)", source_detail_id=1, required_fields=["close"])
        self.db_formula = formula
        self.row, self.metric_drift, self.wrong_window_accepted = row, metric_drift, wrong_window_accepted
        super().__init__({**formula, "factor_ref": "sub_factor:5921", "metric_identity": formula})
        self.mcp = self

    def formula(self, row: dict[str, Any], **kwargs: Any) -> MCPResponse:
        """Reject only the deliberately absent window unless fault injection overrides it."""
        if kwargs.get("window") and not self.wrong_window_accepted:
            return _response({"error": {"code": "FORMULA_EVIDENCE_NOT_FOUND"}, "meta": {}}, error=True)
        return super().formula(row, **kwargs)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResponse:
        """Return the exact final scope; reject any unintended tool or inferred Run."""
        assert name == "factor_get_metrics"
        assert arguments["run_id"] == self.row["run_id"]
        row = {**self.row, "mean_ic": 99} if self.metric_drift else self.row
        return _response({"data": {"ic_summaries": [row]}, "meta": {}})


@pytest.mark.parametrize(("metric_drift", "wrong_window_accepted"), [(False, False), (True, False), (False, True)])
def test_exact_formula_to_final_metric_chain_checks_values_and_absent_window(metric_drift: bool, wrong_window_accepted: bool) -> None:
    row = {key: None for key in (*_SUMMARY_FIELDS, *_PERIOD_FIELDS)}
    row.update(id=3, factor_id=5921, is_sub_factor_id=1, run_id="actual-run", ic_scope="time_series",
               calculation_mode="direct", factor_bar_interval="1h", factor_window_bars="12H",
               return_bar_interval="1h", forward_return_bars=1, universe_key="all", symbol="BTCUSDT",
               window_scope="min_window", scoring_version="v1", mean_ic=0.3)
    api = _ChainAPI(row, metric_drift=metric_drift, wrong_window_accepted=wrong_window_accepted)
    repository = _Repo(FormulaResultSnapshot((api.db_formula,), (row,), (), ()))
    result = Factor4FormulaService(api, repository).check_exact_final_chain(5921, reject_other_window=True)
    assert bool(result.issues) == (metric_drift or wrong_window_accepted)
    assert result.evidence["blocked"] == ["EXACT_RUN_VALIDITY_SAMPLE_MISSING"]
    assert result.evidence["summary_scope_count"] == 1
