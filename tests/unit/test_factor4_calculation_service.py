"""Offline oracles and business judgments for Factor 4.0 calculations."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
import requests

from api.factor_data_mcp_api import FactorDataMCPAPI, MCPProtocolError, MCPResponse
from db.factor4_calculation_repository import (
    CalculationAuditSnapshot,
    EnvironmentDailyRecord,
    EvaluationMetric,
    FactorDefinition,
    FactorDetail,
    FactorIdentity,
    FactorMembershipDifferences,
    FactorMembershipVersionIssue,
    FormulaEvidence,
    PublishedEvaluationBatch,
    PublishedRoute,
    PublishedRouteSnapshot,
    RouteRankingEntry,
)
from service.factor4_calculation_service import (
    CalculationCheckResult,
    CalculationIssue,
    Factor4CalculationReport,
    Factor4CalculationService,
    FormulaOffsetError,
    _is_correct_dpo,
    dpo_reference_values,
    formula_dependency_offsets,
)
from tests.cases.factor4.test_calculation_logic import _diagnostic


pytestmark = pytest.mark.unit


def _batch(**changes: Any) -> PublishedEvaluationBatch:
    values: dict[str, Any] = {
        "id": 6,
        "batch_uid": "batch-6",
        "market_scope": "all",
        "label_kind": "fact",
        "route_profile_key": "default",
        "start_date": date(2024, 9, 2),
        "end_date": date(2026, 9, 1),
        "as_of_time": datetime(2026, 9, 2, 1, 17),
        "published_at": datetime(2026, 9, 2, 14, 20),
        "publication_uid": "publication-6",
        "publish_version": "publish-v1",
        "evaluation_config_version": "env-eval-v1",
        "score_rule_version": "env-score-v1",
        "code_version": "git-sha",
        "status": "success",
        "publish_status": "published",
        "is_active": True,
        "expected_metric_count": 8,
        "completed_metric_count": 8,
        "insufficient_metric_count": 0,
        "failed_metric_count": 0,
        "factor_set_snapshot_hash": "factor-hash",
        "environment_snapshot_hash": "environment-hash",
        "release_manifest_hash": "release-hash",
        "factor_set_snapshot": {"members": []},
        "evaluation_config": {
            "score_rule_version": "env-score-v1",
            "minimum_route_score": "60",
            "profile_weights": {
                "time_series": "0.500000",
                "cross_sectional": "0.500000",
            },
        },
        "environment_status": {"WIDE_RANGE": {"status": "success"}},
    }
    values.update(changes)
    return PublishedEvaluationBatch(**values)


def _factor_id(factor_ref: str) -> int:
    return int(factor_ref.split(":", 1)[1])


def _definition(
    factor_ref: str = "sub_factor:10",
    *,
    expression: str = "close.pct_change(24)",
    name: str = "factor-ten",
    window: str = "24H",
) -> FactorDefinition:
    factor_id = _factor_id(factor_ref)
    return FactorDefinition(
        factor_ref=factor_ref,
        factor_type="sub_factor",
        factor_id=factor_id,
        batch_factor_version="updated_at:2026-09-01T00:00:00Z",
        serial_number=f"SF-{factor_id}",
        name=name,
        window=window,
        factor_bar_interval="1h",
        formula_summary=expression,
        definition_updated_at=datetime(2026, 9, 1),
    )


def _detail(
    factor_ref: str = "sub_factor:10",
    *,
    expression: str = "close.pct_change(24)",
    fields: tuple[str, ...] = ("close",),
    name: str = "factor-ten",
    window: int = 24,
) -> FactorDetail:
    factor_id = _factor_id(factor_ref)
    return FactorDetail(
        id=1000 + factor_id,
        factor_ref=factor_ref,
        factor_type="sub_factor",
        factor_id=factor_id,
        batch_factor_version="updated_at:2026-09-01T00:00:00Z",
        is_sub_factor_id=True,
        serial_number=f"SF-{factor_id}",
        name=name,
        status=2,
        calc_logic=expression,
        params={"window": window, "fields": list(fields), "declared_fields": list(fields)},
        data_source_metadata={"required_fields": list(fields), "resolved_raw_fields": list(fields)},
        updated_at=datetime(2026, 9, 1),
    )


def _formula(
    factor_ref: str = "sub_factor:10",
    *,
    expression: str = "close.pct_change(24)",
    fields: tuple[str, ...] = ("close",),
    window: str = "24H",
) -> FormulaEvidence:
    factor_id = _factor_id(factor_ref)
    return FormulaEvidence(
        id=2000 + factor_id,
        run_id=f"run-{factor_id}",
        factor_ref=factor_ref,
        factor_type="sub_factor",
        factor_id=factor_id,
        batch_factor_version="updated_at:2026-09-01T00:00:00Z",
        is_sub_factor_id=True,
        calculation_mode="direct",
        factor_bar_interval="1h",
        factor_window_bars=window,
        return_bar_interval="1h",
        forward_return_bars=1,
        formula_version=f"python-ast-v1:formula-{factor_id}",
        formula_hash=f"formula-{factor_id}",
        hash_algorithm="sha256",
        normalization_version="python-ast-v1",
        expression=expression,
        required_fields=fields,
        lookback=24,
        lag=None,
        missing_policy="drop",
        output_unit=None,
        metadata_complete=True,
        metadata_warnings=(),
        source_detail_id=1000 + factor_id,
        recorded_at=datetime(2026, 9, 2),
        run_status="completed",
        run_completed_at=datetime(2026, 9, 2),
    )


def _eligibility(
    valid_scopes: list[str],
    *,
    routing_score: str | None = "80",
    minimum: str = "60",
    is_eligible: bool | None = None,
) -> dict[str, Any]:
    if is_eligible is None:
        is_eligible = bool(valid_scopes) and routing_score is not None and Decimal(routing_score) >= Decimal(minimum)
    return {
        "admission_mode": "any_valid_scope",
        "valid_scopes": list(valid_scopes),
        "invalid_scopes": [scope for scope in ("time_series", "cross_sectional") if scope not in valid_scopes],
        "routing_score": routing_score,
        "minimum_route_score": minimum,
        "is_eligible": is_eligible,
    }


def _metric(
    metric_id: int,
    factor_ref: str,
    scope: str,
    *,
    label_code: str = "WIDE_RANGE",
    valid: bool = True,
    metric_status: str = "success",
    eligibility: dict[str, Any] | None = None,
    score: str = "80.000000",
    confidence: str = "0.900000000",
    formula_links: bool = False,
    direction: int = 1,
    pair_identity_hash: str | None = "auto",
) -> EvaluationMetric:
    factor_id = _factor_id(factor_ref)
    identity: dict[str, Any] = {
        "eval_batch_uid": "batch-6",
        "factor_ref": factor_ref,
        "factor_version": f"sha256:factor-{factor_id}",
        "definition_factor_version": "updated_at:2026-09-01T00:00:00Z",
        "evaluation_type": scope,
        "label_code": label_code,
        "factor_window_bars": "24H",
        "interval": "1h",
    }
    if formula_links:
        identity.update(
            {
                "formula_hash": f"formula-{factor_id}",
                "formula_version": f"python-ast-v1:formula-{factor_id}",
                "run_id": f"run-{factor_id}",
                "source_detail_id": 1000 + factor_id,
            }
        )
    raw_values = {
        "mean_ic": Decimal("-0.040000"),
        "mean_rank_ic": Decimal("-0.030000"),
        "icir": Decimal("-0.500000"),
        "rank_icir": Decimal("-0.400000"),
    }
    payload = {
        "directed_mean_ic": raw_values["mean_ic"] * direction,
        "directed_mean_rank_ic": raw_values["mean_rank_ic"] * direction,
        "directed_icir": raw_values["icir"] * direction,
        "directed_rank_icir": raw_values["rank_icir"] * direction,
    }
    return EvaluationMetric(
        id=metric_id,
        eval_batch_id=6,
        factor_ref=factor_ref,
        factor_type="sub_factor",
        factor_id=factor_id,
        factor_version=f"sha256:factor-{factor_id}",
        market_scope="all",
        label_kind="fact",
        label_code=label_code,
        evaluation_type=scope,
        interval="1h",
        return_bar_interval="1h",
        forward_return_bars=1,
        window_scope="trailing_730d",
        sample_start_date=date(2024, 9, 2),
        sample_end_date=date(2026, 9, 1),
        mean_ic=raw_values["mean_ic"],
        mean_rank_ic=raw_values["mean_rank_ic"],
        icir=raw_values["icir"],
        rank_icir=raw_values["rank_icir"],
        time_series_score=Decimal(score) if scope == "time_series" else None,
        cross_sectional_score=Decimal(score) if scope == "cross_sectional" else None,
        routing_score=Decimal(eligibility["routing_score"]) if eligibility and eligibility.get("routing_score") is not None else None,
        confidence=Decimal(confidence),
        metric_status=metric_status,
        is_valid=valid,
        scoring_version="env-score-v1",
        metric_payload=payload,
        metric_identity=identity,
        metric_pair_identity_hash=(
            f"pair-{factor_id}-{label_code}" if pair_identity_hash == "auto" else pair_identity_hash
        ),
        score_components={"strength": Decimal(score)},
        route_eligibility=eligibility,
        direction={"predictive_direction": direction},
        aggregation={"calculation_mode": "direct"},
        error_code=None,
        error_message=None,
    )


def _route(
    route_id: int,
    factor_ref: str,
    metric_id: int,
    rank_no: int,
    routing_score: str,
    *,
    label_code: str = "WIDE_RANGE",
    evidence: dict[str, Any] | None = None,
) -> PublishedRoute:
    factor_id = _factor_id(factor_ref)
    return PublishedRoute(
        id=route_id,
        publication_uid="publication-6",
        eval_batch_id=6,
        metric_id=metric_id,
        market_scope="all",
        route_profile_key="default",
        environment_date=date(2026, 9, 1),
        label_kind="fact",
        label_code=label_code,
        as_of_time=datetime(2026, 9, 2, 1, 17),
        factor_ref=factor_ref,
        factor_type="sub_factor",
        factor_id=factor_id,
        factor_version=f"sha256:factor-{factor_id}",
        rank_no=rank_no,
        routing_score=Decimal(routing_score),
        confidence=Decimal("0.894405039"),
        time_series_score=Decimal("95.644261"),
        cross_sectional_score=None,
        is_eligible=True,
        reject_reason_code=None,
        evidence=evidence or {},
        score_rule_version="env-score-v1",
        publish_version="publish-v1",
        is_active=True,
    )


def _snapshot(
    *,
    batch: PublishedEvaluationBatch | None = None,
    membership_differences: FactorMembershipDifferences | None = None,
    definitions: tuple[FactorDefinition, ...] = (),
    details: tuple[FactorDetail, ...] = (),
    formulas: tuple[FormulaEvidence, ...] = (),
    metrics: tuple[EvaluationMetric, ...] = (),
    routes: tuple[PublishedRoute, ...] = (),
    environment_daily: tuple[EnvironmentDailyRecord, ...] = (),
) -> CalculationAuditSnapshot:
    return CalculationAuditSnapshot(
        captured_at=datetime(2026, 9, 4, 12),
        batch=batch or _batch(),
        membership_differences=(
            membership_differences
            if membership_differences is not None
            else FactorMembershipDifferences((), ())
        ),
        definitions=definitions,
        details=details,
        formula_evidence=formulas,
        evaluation_metrics=metrics,
        routes=routes,
        environment_daily=environment_daily,
    )


def _mcp_response(data: dict[str, Any]) -> MCPResponse:
    return MCPResponse(
        status_code=200,
        content_type="application/json",
        envelope={
            "jsonrpc": "2.0",
            "id": "unit",
            "result": {"isError": False, "structuredContent": {"data": data}},
        },
        protocol_version="2025-06-18",
    )


class StubMCPAPI:
    """Serve factor detail/formula projections without network access."""

    def __init__(self, details: dict[str, dict[str, Any]], formulas: dict[str, dict[str, Any]]) -> None:
        self.details = details
        self.formulas = formulas
        self.detail_batch_sizes: list[int] = []
        self.formula_calls: list[tuple[str, str, str, str, str, int]] = []
        self.initialized = False
        self.notified = False

    def initialize(self, **kwargs: Any) -> MCPResponse:
        self.initialized = kwargs.get("protocol_version") == "2025-06-18"
        return _mcp_response({"protocolVersion": "2025-06-18"})

    def notify_initialized(self) -> MCPResponse:
        self.notified = True
        return _mcp_response({})

    def get_factor_details_batch(
        self,
        factor_refs: list[str],
        *,
        detail_level: str,
    ) -> MCPResponse:
        assert detail_level == "executable"
        self.detail_batch_sizes.append(len(factor_refs))
        return _mcp_response(
            {
                "items": [
                    {
                        "factor_ref": factor_ref,
                        "success": factor_ref in self.details,
                        "data": self.details.get(factor_ref),
                        "error": None if factor_ref in self.details else {"code": "NOT_FOUND"},
                    }
                    for factor_ref in factor_refs
                ]
            }
        )

    def get_formula(
        self,
        factor_ref: str,
        run_id: str,
        interval: str,
        factor_window_bars: str,
        return_bar_interval: str,
        forward_return_bars: int,
        *,
        calculation_mode: str,
    ) -> MCPResponse:
        self.formula_calls.append(
            (
                factor_ref,
                run_id,
                interval,
                factor_window_bars,
                return_bar_interval,
                forward_return_bars,
            )
        )
        del calculation_mode
        data = self.formulas.get(factor_ref)
        if data is None:
            return MCPResponse(
                status_code=200,
                content_type="application/json",
                envelope={
                    "jsonrpc": "2.0",
                    "id": "unit",
                    "result": {"isError": True, "structuredContent": {"error": {"code": "NOT_FOUND"}}},
                },
                protocol_version="2025-06-18",
            )
        return _mcp_response(data)


def _mcp_detail(definition: FactorDefinition, detail: FactorDetail) -> dict[str, Any]:
    return {
        "id": definition.factor_id,
        "factor_ref": definition.factor_ref,
        "name": definition.name,
        "serial_number": definition.serial_number,
        "window": definition.window,
        "factor_bar_interval": definition.factor_bar_interval,
        "formula_summary": definition.formula_summary,
        "calc_logic": detail.calc_logic,
        "params": detail.params,
        "data_source_metadata": detail.data_source_metadata,
    }


def _mcp_formula(formula: FormulaEvidence) -> dict[str, Any]:
    return {
        "factor_ref": formula.factor_ref,
        "run_id": formula.run_id,
        "formula_hash": formula.formula_hash,
        "formula_version": formula.formula_version,
        "source_detail_id": formula.source_detail_id,
        "expression": formula.expression,
        "required_fields": list(formula.required_fields),
        "metric_identity": {
            "calculation_mode": formula.calculation_mode,
            "factor_bar_interval": formula.factor_bar_interval,
            "factor_window_bars": formula.factor_window_bars,
            "return_bar_interval": formula.return_bar_interval,
            "forward_return_bars": formula.forward_return_bars,
        },
    }


def _service(
    snapshot: CalculationAuditSnapshot,
    *,
    mcp: StubMCPAPI | None = None,
) -> tuple[Factor4CalculationService, StubMCPAPI]:
    api = mcp or StubMCPAPI({}, {})

    class Repository:
        def read_calculation_snapshot(
            self,
            market_scope: str = "all",
            route_profile_key: str = "default",
        ) -> CalculationAuditSnapshot:
            assert market_scope == "all"
            assert route_profile_key == "default"
            return snapshot

        def read_published_route_snapshot(
            self,
            market_scope: str = "all",
            route_profile_key: str = "default",
        ) -> PublishedRouteSnapshot:
            assert market_scope == "all"
            assert route_profile_key == "default"
            return PublishedRouteSnapshot(
                captured_at=snapshot.captured_at,
                batch_id=snapshot.batch.id,
                publication_uid=snapshot.batch.publication_uid,
                publish_version=snapshot.batch.publish_version,
                market_scope=snapshot.batch.market_scope,
                route_profile_key=snapshot.batch.route_profile_key,
                routes=tuple(
                    RouteRankingEntry(
                        id=route.id,
                        metric_id=route.metric_id,
                        environment_date=route.environment_date,
                        label_kind=route.label_kind,
                        label_code=route.label_code,
                        as_of_time=route.as_of_time,
                        factor_ref=route.factor_ref,
                        factor_type=route.factor_type,
                        factor_id=route.factor_id,
                        factor_version=route.factor_version,
                        rank_no=route.rank_no,
                        routing_score=route.routing_score,
                        confidence=route.confidence,
                        time_series_score=route.time_series_score,
                        cross_sectional_score=route.cross_sectional_score,
                        score_rule_version=route.score_rule_version,
                    )
                    for route in snapshot.routes
                    if route.is_active
                ),
            )

    return Factor4CalculationService(Repository(), cast(FactorDataMCPAPI, api)), api


class TestResultOnlyCalculationPath:
    """Scope separation preserves final identity/output checks without operator judgments."""

    @pytest.mark.parametrize("changed", [None, "formula_hash", "expression", "detail_expression", "formula_version"])
    def test_result_formula_path_skips_mathematics_but_detects_output_drift(
        self, monkeypatch: pytest.MonkeyPatch, changed: str | None,
    ) -> None:
        import service.factor4_calculation_service as calculation

        def forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("result-only path invoked internal formula mathematics")

        for name in ("_definition_formula_candidates", "_expressions_equivalent", "_compare_normalized_formula_metadata", "_is_correct_dpo", "_dependency_offsets"):
            monkeypatch.setattr(calculation, name, forbidden)
        expression = "producer_specific_operator(close)"
        definition, detail, formula = _definition(expression=expression), _detail(expression=expression), _formula(expression=expression)
        snapshot = _snapshot(definitions=(definition,), details=(detail,), formulas=(formula,),
                             metrics=(_metric(301, definition.factor_ref, "time_series", formula_links=True),))
        data, exact = _mcp_detail(definition, detail), _mcp_formula(formula)
        if changed == "detail_expression":
            data["calc_logic"] = "different(close)"
        elif changed:
            exact[changed] = "different"
        api = StubMCPAPI({definition.factor_ref: data}, {definition.factor_ref: exact})
        service, _ = _service(snapshot, mcp=api)
        result = service.check_formula_result_consistency(snapshot)
        assert result.status == ("FAIL" if changed else "PASS"), result.findings
        assert len(api.formula_calls) == 1
        assert api.detail_batch_sizes == [1]

    def test_default_report_never_repeats_score_rank_or_historical_math_checks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, api = _service(_snapshot())
        called: list[str] = []

        def forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("default report invoked duplicate scoring/ranking or historical mathematics")

        for name in ("check_formula_static_consistency", "check_known_formula_regressions", "check_formula_integrity",
                     "check_route_score_recalculation", "check_rank_stability", "check_final_result_ranking"):
            monkeypatch.setattr(service, name, forbidden)
        monkeypatch.setattr(service._repository, "read_published_route_snapshot", forbidden)
        for name, identifier in (("check_formula_result_consistency", "CALC-510-A"),
                                 ("check_any_valid_scope", "CALC-501-C")):
            def check(*args: Any, selected: str = name, case_id: str = identifier) -> CalculationCheckResult:
                called.append(selected)
                return CalculationCheckResult(case_id, selected, "PASS", "ok", 1)
            monkeypatch.setattr(service, name, check)
        report = service.run_result_checks()
        assert called == ["check_formula_result_consistency", "check_any_valid_scope"]
        assert {check.case_id for check in report.checks} == {"CALC-510-A", "CALC-501-C"}
        assert report.status == "PASS"
        assert api.initialized and api.notified

    def test_default_case_ids_keep_only_formula_and_admission(self) -> None:
        """Default R0 Cases must not recollect score/rank checks owned by all-partition Cases."""
        from tests.cases.factor4.test_calculation_logic import _CASE_IDS

        assert _CASE_IDS == ("CALC-510-A", "CALC-501-C")

    def test_default_case_fixture_uses_only_result_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.cases.factor4.test_calculation_logic import factor4_calculation_report

        service, _ = _service(_snapshot())
        expected = Factor4CalculationReport("batch", datetime(2026, 9, 7), "all", "default", "PASS", ())
        calls: list[dict[str, str]] = []

        def result_report(**kwargs: str) -> Factor4CalculationReport:
            calls.append(kwargs)
            return expected

        def internal_report(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("default fixture invoked historical R0 math scan")

        monkeypatch.setattr(service, "run_result_checks", result_report)
        monkeypatch.setattr(service, "run_r0_checks", internal_report)
        assert factor4_calculation_report.__wrapped__(service) is expected
        assert calls == [{"market_scope": "all", "route_profile_key": "default"}]

    @pytest.mark.parametrize("metadata_drift", [False, True])
    def test_result_projection_keeps_parameter_equivalence_and_normalized_metadata(
        self, monkeypatch: pytest.MonkeyPatch, metadata_drift: bool,
    ) -> None:
        import service.factor4_calculation_service as calculation

        def forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("projection compared normalized metadata to mathematical execution")

        monkeypatch.setattr(calculation, "_compare_normalized_formula_metadata", forbidden)
        definition = _definition(expression="close.pct_change(24)")
        detail = replace(_detail(expression="close.pct_change(window)"),
                         params={**_detail().params, "normalized_formula": "producer_transform(close, window)"})
        formula = _formula(expression="close.pct_change(24)")
        data = _mcp_detail(definition, detail)
        data["calc_logic"] = "factor = close.pct_change(24 + 0)"
        data["params"] = {**detail.params, "normalized_formula": "producer_transform(close, 12)" if metadata_drift else "producer_transform(close, 24 + 0)"}
        exact = {**_mcp_formula(formula), "expression": "factor = close.pct_change(window + 0)"}
        snapshot = _snapshot(definitions=(definition,), details=(detail,), formulas=(formula,),
                             metrics=(_metric(301, definition.factor_ref, "time_series", formula_links=True),))
        service, _ = _service(snapshot, mcp=StubMCPAPI({definition.factor_ref: data}, {definition.factor_ref: exact}))
        result = service.check_formula_result_consistency(snapshot)
        assert result.status == ("FAIL" if metadata_drift else "PASS"), result.findings
        if metadata_drift:
            assert any(issue.evidence.get("field") == "params.normalized_formula" for issue in result.findings)


class TestFormulaOffsetOracle:
    """Verify exact temporal dependency expansion without executing formulas."""

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("x.diff(12).diff(12)", (0, 12, 24)),
            ("x.pct_change(48)", (0, 48)),
            ("x.shift(3).rolling(4).mean()", (3, 4, 5, 6)),
            ("mean(close, 4)", (0, 1, 2, 3)),
            ("mean(close, window=4)", (0, 1, 2, 3)),
            ("std(close, periods=4)", (0, 1, 2, 3)),
            ("sum(close, period=4)", (0, 1, 2, 3)),
            ("min(close, window=4)", (0, 1, 2, 3)),
            ("max(close, periods=4)", (0, 1, 2, 3)),
            ("close.rolling(4).mean()", (0, 1, 2, 3)),
            (
                "returns.rolling(4, min_periods=2).corr(returns.shift(1))",
                (0, 1, 2, 3, 4),
            ),
            ("close.shift((60 + 0) // 2 + 1)", (31,)),
        ],
    )
    def test_static_offsets_are_exact(self, expression: str, expected: tuple[int, ...]) -> None:
        assert formula_dependency_offsets(expression) == expected

    @pytest.mark.parametrize(
        "expression",
        [
            "x.shift(window)",
            "x.rolling(n).mean()",
            "x.diff(-1)",
            "mean(close)",
            "mean(close, window=n)",
            "mean(close, window=4, periods=5)",
            "mean(close, unknown=4)",
            "mean(window=4)",
        ],
    )
    def test_dynamic_or_negative_temporal_argument_is_blocked(self, expression: str) -> None:
        with pytest.raises(FormulaOffsetError):
            formula_dependency_offsets(expression)

    @pytest.mark.parametrize(
        "expression",
        [
            "unknown(close)",
            "unknown(window=24)",
            "close.unknown(3)",
        ],
    )
    def test_unknown_formula_function_is_blocked_instead_of_treated_as_a_series(
        self,
        expression: str,
    ) -> None:
        """未知函数不能被 Oracle 静默当成当前时点输入。"""

        with pytest.raises(FormulaOffsetError, match="unknown formula function"):
            formula_dependency_offsets(expression)

    def test_control_names_do_not_become_fake_data_dependencies(self) -> None:
        assert formula_dependency_offsets("where(close > 0, close, window)") == (0,)

    def test_known_names_are_case_insensitive_and_namespace_roots_are_ignored(self) -> None:
        assert formula_dependency_offsets("NP.LOG(CLOSE)") == (0,)

    @pytest.mark.parametrize(
        "expression",
        [
            "np.mean(close, 24)",
            "numpy.std(close, 24)",
            "pd.rolling(close, 24)",
            "pandas.DataFrame.rolling(close, window=24)",
            "close.mean(window=24)",
            "close.vwap(24)",
            "close.rolling(24).mean(window=4)",
            "close.rolling(24).mean(axis=1)",
            "close.rolling(24).mean(skipna=True)",
            "close.diff().mean()",
            "close.pct_change().sum()",
        ],
    )
    def test_qualified_temporal_calls_fail_closed(self, expression: str) -> None:
        """限定名/直接聚合调用不能绕过有限窗口解析。"""

        with pytest.raises(FormulaOffsetError, match="qualified temporal"):
            formula_dependency_offsets(expression)

    @pytest.mark.parametrize(
        "expression",
        [
            "cumsum(close)",
            "close.cumsum()",
            "np.cumsum(close)",
            "close.expanding(24).mean()",
            "close.ewm(span=24).mean()",
        ],
    )
    def test_unbounded_temporal_calls_are_explicitly_blocked(self, expression: str) -> None:
        """累计、expanding 和指数窗口不能被当作有限 lookback。"""

        with pytest.raises(FormulaOffsetError, match="unbounded temporal"):
            formula_dependency_offsets(expression)

    def test_finite_method_chain_remains_supported(self) -> None:
        assert formula_dependency_offsets("close.rolling(24).mean()") == tuple(range(24))
        assert formula_dependency_offsets(
            "close.rolling(24).mean(numeric_only=True)"
        ) == tuple(range(24))
        assert formula_dependency_offsets("close.rolling(24).std(ddof=0)") == tuple(
            range(24)
        )

    def test_finite_rolling_corr_expands_both_operands(self) -> None:
        """A rolling correlation must include the second series' full window."""

        expression = "close.pct_change(24).rolling(window).corr(open_interest.pct_change(24))"
        assert formula_dependency_offsets(expression, variables={"window": 24}) == tuple(range(48))

    @pytest.mark.parametrize(
        "expression",
        (
            "np.corr(close, volume, 24)",
            "close.rolling(24).corr()",
            "close.rolling(24).corr(volume, False)",
        ),
    )
    def test_rolling_corr_rejects_ambiguous_or_dynamic_forms(self, expression: str) -> None:
        """Only an explicit finite rolling receiver and one static other series are supported."""

        with pytest.raises(FormulaOffsetError):
            formula_dependency_offsets(expression)

    def test_rolling_corr_rejects_dynamic_window_without_binding(self) -> None:
        with pytest.raises(FormulaOffsetError):
            formula_dependency_offsets("close.rolling(window).corr(volume)")

    @pytest.mark.parametrize(
        "expression",
        (
            "close.rolling(3, center=True).corr(volume)",
            "close.rolling(3, closed='left').corr(volume)",
            "close.rolling(3, step=2).corr(volume)",
            "close.rolling(3, win_type='gaussian').corr(volume)",
            "close.rolling(3, on='timestamp').corr(volume)",
            "close.rolling(3, axis=1).corr(volume)",
            "close.rolling(3, method='table').corr(volume)",
        ),
    )
    def test_rolling_corr_rejects_non_trailing_window_modifiers(
        self,
        expression: str,
    ) -> None:
        """Modifiers that alter alignment/window membership must fail closed."""

        with pytest.raises(FormulaOffsetError, match="rolling modifier"):
            formula_dependency_offsets(expression)

    @pytest.mark.parametrize("min_periods", (0, 3))
    def test_rolling_min_periods_accepts_pandas_boundaries(
        self,
        min_periods: int,
    ) -> None:
        assert formula_dependency_offsets(
            f"close.rolling(3, min_periods={min_periods}).corr(volume)"
        ) == (0, 1, 2)

    @pytest.mark.parametrize(
        "expression",
        (
            "close.rolling(3, min_periods=4).corr(volume)",
            "close.rolling(3, min_periods=-1).corr(volume)",
            "close.rolling(3, min_periods=True).corr(volume)",
            "close.rolling(3, min_periods=1.5).corr(volume)",
            "close.rolling(3, min_periods=required).corr(volume)",
        ),
    )
    def test_rolling_min_periods_rejects_out_of_range_or_non_integer_values(
        self,
        expression: str,
    ) -> None:
        with pytest.raises(FormulaOffsetError):
            formula_dependency_offsets(expression)

    @pytest.mark.parametrize(
        "expression",
        [
            "rolling_vwap(close, window=window_size)",
            "rolling_zscore(close, window=window_size)",
            "vwap(close, volume, window=window_size)",
            "correlation(close, volume, window=window_size)",
        ],
    )
    def test_generic_temporal_helpers_reject_dynamic_windows(self, expression: str) -> None:
        """动态窗口不能在 generic helper 分支被静默当成点值。"""

        with pytest.raises(FormulaOffsetError, match="dynamic"):
            formula_dependency_offsets(expression)

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("rolling_vwap(close, volume, window=3)", (0, 1, 2)),
            ("vwap(close, volume, 3)", (0, 1, 2)),
            ("rolling_zscore(close, 3)", (0, 1, 2)),
            ("correlation(close, volume, 3)", (0, 1, 2)),
            ("vwap(3)", (0, 1, 2)),
        ],
    )
    def test_generic_temporal_helpers_expand_all_data_inputs(
        self,
        expression: str,
        expected: tuple[int, ...],
    ) -> None:
        assert formula_dependency_offsets(expression) == expected

    @pytest.mark.parametrize(
        "expression",
        [
            "close.rolling(24, unsupported=1).mean()",
            "where(close, unsupported=1)",
            "close.pct_change(periods=4, periods2=4)",
            "close.shift(periods=4, fill_value=dynamic_fill)",
            "close.rolling(4, window=4).mean()",
        ],
    )
    def test_unknown_or_dynamic_control_keywords_are_rejected(self, expression: str) -> None:
        """未知参数或动态控制参数不得改变 offset 结论。"""

        with pytest.raises(FormulaOffsetError):
            formula_dependency_offsets(expression)

    @pytest.mark.parametrize(
        "expression",
        [
            "lambda value: value",
            "[value for value in close]",
            "close if enabled else other",
            "(value := close)",
            "close[dynamic_index]",
        ],
    )
    def test_unsupported_ast_constructs_fail_closed(self, expression: str) -> None:
        """复杂 Python 语法不能通过兜底遍历伪装成可审计公式。"""

        with pytest.raises(FormulaOffsetError, match="unsupported"):
            formula_dependency_offsets(expression)

    def test_explicit_variable_bindings_are_required_for_parameterized_windows(self) -> None:
        with pytest.raises(FormulaOffsetError):
            formula_dependency_offsets("close.rolling(WINDOW).mean()")
        assert formula_dependency_offsets(
            "close.rolling(WINDOW).mean()",
            variables={"window": 3},
        ) == (0, 1, 2)

    def test_unreasonably_large_static_window_is_rejected(self) -> None:
        with pytest.raises(FormulaOffsetError, match="bound"):
            formula_dependency_offsets("close.rolling(1000001).mean()")

    def test_unknown_call_is_not_a_canonical_formula_candidate(self) -> None:
        from service.factor4_calculation_service import _canonical_expression

        assert _canonical_expression("unknown(close)") is None

    def test_formula_window_variable_lookup_is_case_insensitive(self) -> None:
        assert _is_correct_dpo(
            "close.rolling(WINDOW).mean() - close.shift(WINDOW // 2 + 1)",
            60,
        )

    def test_dpo_oracle_preserves_the_known_nonlinear_counterexample(self) -> None:
        correct, historical_wrong = dpo_reference_values()

        assert correct == Decimal("39.1515098039215686274509805")
        assert historical_wrong == Decimal("-834.6895784313725490196078427")


class TestFormulaConsistency:
    """Verify explicit MCP/DB formula links and fail-closed missing links."""

    def test_exact_definition_formula_schema_and_metric_link_pass(self) -> None:
        definition = _definition()
        detail = _detail()
        formula = _formula()
        metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)
        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(formula,),
            metrics=(metric,),
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "PASS", result.findings
        assert result.checked_count == 1

    def test_unknown_formula_function_cannot_produce_a_false_pass(self) -> None:
        """DB/MCP 重复返回未知函数时仍必须阻断或失败，不能互相印证后放行。"""

        expression = "mystery(close, window=24)"
        definition = _definition(expression=expression)
        detail = _detail(expression=expression)
        formula = _formula(expression=expression)
        metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)
        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(formula,),
            metrics=(metric,),
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status != "PASS", result.findings
        assert any(
            finding.code == "MCP_FORMULA_EXPRESSION_MISMATCH"
            for finding in result.findings
        )

    def test_definition_and_executable_versions_are_checked_in_separate_namespaces(self) -> None:
        """A frozen catalog version may differ from the executable formula hash."""

        definition = _definition()
        detail = _detail()
        formula = replace(
            _formula(),
            formula_factor_version="sha256:factor-10",
        )
        metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)
        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(formula,),
            metrics=(metric,),
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )

        result = _service(snapshot, mcp=mcp)[0].check_formula_static_consistency(snapshot)

        assert result.status == "PASS", result.findings
        assert not any(
            finding.code == "METRIC_FORMULA_EVIDENCE_FACTOR_MISMATCH"
            for finding in result.findings
        )

    def test_exact_old_run_is_found_when_a_newer_completed_run_also_exists(self) -> None:
        """Resolution must retain a metric-linked historical run, not only the latest row."""

        definition = _definition()
        detail = _detail()
        old = replace(
            _formula(),
            id=2010,
            run_id="run-old",
            formula_hash="hash-old",
            formula_version="version-old",
            formula_factor_version="sha256:factor-10",
        )
        newest = replace(
            _formula(),
            id=2011,
            run_id="run-new",
            formula_hash="hash-new",
            formula_version="version-new",
            formula_factor_version="sha256:factor-10",
        )
        metric = _metric(301, definition.factor_ref, "time_series", formula_links=False)
        metric = replace(
            metric,
            metric_identity={
                **(metric.metric_identity or {}),
                "formula_hash": "hash-old",
                "formula_version": "version-old",
                "run_id": "run-old",
            },
        )
        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(old, newest),
            metrics=(metric,),
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(old)},
        )

        result = _service(snapshot, mcp=mcp)[0].check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION", result.findings
        assert not any(
            finding.code == "METRIC_FORMULA_EVIDENCE_MISSING"
            for finding in result.findings
        )
        assert any(
            finding.code == "HISTORICAL_FORMULA_EVIDENCE_UNBOUND"
            for finding in result.findings
        )

    def test_hash_and_version_links_are_blocked_when_context_still_matches_multiple_runs(self) -> None:
        """Content hashes/versions alone must not silently select one duplicate run."""

        definition = _definition()
        detail = _detail()
        first = replace(_formula(), id=2010, run_id="run-a")
        second = replace(_formula(), id=2011, run_id="run-b")
        metric = _metric(301, definition.factor_ref, "time_series", formula_links=False)
        metric = replace(
            metric,
            metric_identity={
                **(metric.metric_identity or {}),
                "formula_hash": first.formula_hash,
                "formula_version": first.formula_version,
                # Deliberately omit run_id/evidence_id.
            },
        )
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(first, second), metrics=(metric,)
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(first)},
        )

        result = _service(snapshot, mcp=mcp)[0].check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(finding.code == "METRIC_FORMULA_LINK_NOT_UNIQUE" for finding in result.findings)

    def test_source_detail_is_trace_checked_after_exact_formula_resolution(self) -> None:
        """A wrong trace pointer is a contradiction, not an unresolvable link."""

        definition = _definition()
        detail = _detail()
        formula = _formula()
        metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)
        metric = replace(
            metric,
            metric_identity={**(metric.metric_identity or {}), "source_detail_id": 999999},
        )
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(formula,), metrics=(metric,)
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )

        result = _service(snapshot, mcp=mcp)[0].check_formula_static_consistency(snapshot)

        assert result.status == "FAIL"
        assert any(
            finding.code == "METRIC_FORMULA_SOURCE_DETAIL_MISMATCH"
            for finding in result.findings
        )

    def test_current_definition_drift_blocks_cross_temporal_formula_comparison(self) -> None:
        """A newer catalog definition cannot be used to fail an older batch run."""

        definition = replace(
            _definition(expression="close.pct_change(24)"),
            batch_factor_version="updated_at:2026-08-01T00:00:00Z",
            definition_updated_at=datetime(2026, 9, 1),
        )
        detail = replace(
            _detail(expression="close.pct_change(24)"),
            batch_factor_version="updated_at:2026-08-01T00:00:00Z",
            updated_at=datetime(2026, 9, 1),
        )
        formula = replace(
            _formula(expression="close.pct_change(24)"),
            batch_factor_version="updated_at:2026-08-01T00:00:00Z",
        )
        metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)
        metric = replace(
            metric,
            metric_identity={
                **(metric.metric_identity or {}),
                "definition_factor_version": "updated_at:2026-08-01T00:00:00Z",
            },
        )
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(formula,), metrics=(metric,)
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )

        result = _service(snapshot, mcp=mcp)[0].check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(finding.code == "CURRENT_DEFINITION_VERSION_DRIFT" for finding in result.findings)
        assert not any(
            finding.code == "CURRENT_BATCH_FORMULA_VERSION_MISMATCH"
            for finding in result.findings
        )

    def test_unresolved_detail_definition_version_blocks_cross_temporal_comparison(self) -> None:
        """A detail-only frozen timestamp must not be treated as current formula proof."""

        factor_ref = "sub_factor:10"
        definition = None
        detail = replace(
            _detail(factor_ref),
            batch_factor_version="updated_at:2026-08-01T00:00:00Z",
            updated_at=None,
            params={
                **(_detail(factor_ref).params or {}),
                "formula_version": "current-version",
            },
        )
        formula = replace(
            _formula(factor_ref),
            formula_version="batch-version",
            formula_hash="batch-hash",
            formula_factor_version="sha256:factor-10",
            batch_factor_version="updated_at:2026-08-01T00:00:00Z",
        )
        metric = replace(
            _metric(301, factor_ref, "time_series", formula_links=False),
            metric_identity={
                **(_metric(301, factor_ref, "time_series").metric_identity or {}),
                "formula_version": "batch-version",
                "formula_hash": "batch-hash",
                "run_id": "run-10",
                "definition_factor_version": "updated_at:2026-08-01T00:00:00Z",
            },
        )
        snapshot = _snapshot(
            definitions=(),
            details=(detail,),
            formulas=(formula,),
            metrics=(metric,),
        )
        mcp_detail = _mcp_detail(
            _definition(factor_ref),
            detail,
        )
        mcp_detail["params"] = {
            **(mcp_detail.get("params") or {}),
            "formula_version": "current-version",
        }
        mcp = StubMCPAPI({factor_ref: mcp_detail}, {factor_ref: _mcp_formula(formula)})
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(
            finding.code == "CURRENT_DEFINITION_VERSION_UNRESOLVED"
            for finding in result.findings
        )
        assert not any(
            finding.code == "CURRENT_BATCH_FORMULA_VERSION_MISMATCH"
            for finding in result.findings
        )

    def test_null_factor_interval_in_metric_identity_falls_back_to_metric_context(self) -> None:
        """An explicit null alias must not override the persisted metric interval."""

        definition = _definition()
        detail = _detail()
        formula = _formula()
        base_metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)
        metric = replace(
            base_metric,
            metric_identity={
                **(base_metric.metric_identity or {}),
                "factor_bar_interval": None,
            },
        )
        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(formula,),
            metrics=(metric,),
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "PASS", result.findings
        assert not any(
            finding.code == "METRIC_FORMULA_CONTEXT_MISMATCH"
            for finding in result.findings
        )

    def test_nested_formula_link_is_accepted_when_top_level_is_omitted(self) -> None:
        """Nested formula identity remains a supported compatibility shape."""

        definition = _definition()
        detail = _detail()
        formula = _formula()
        base_metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)
        identity = dict(base_metric.metric_identity or {})
        nested = {
            name: identity.pop(name)
            for name in (
                "formula_hash",
                "formula_version",
                "run_id",
                "source_detail_id",
            )
        }
        nested["formula_evidence_id"] = str(formula.id)
        nested["source_detail_id"] = str(nested["source_detail_id"])
        identity["formula_identity"] = nested
        metric = replace(base_metric, metric_identity=identity)
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(formula,), metrics=(metric,)
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )

        result = _service(snapshot, mcp=mcp)[0].check_formula_static_consistency(snapshot)

        assert result.status == "PASS", result.findings
        assert not any(
            finding.code == "METRIC_FORMULA_LINK_INVALID" for finding in result.findings
        )

    def test_conflicting_top_level_and_nested_formula_links_are_blocked(self) -> None:
        """Two unequal compatibility representations must not be prioritized silently."""

        definition = _definition()
        detail = _detail()
        formula = _formula()
        base_metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)
        identity = {
            **(base_metric.metric_identity or {}),
            "formula": {"formula_hash": "different-hash"},
        }
        metric = replace(base_metric, metric_identity=identity)
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(formula,), metrics=(metric,)
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )

        result = _service(snapshot, mcp=mcp)[0].check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        invalid = [
            finding
            for finding in result.findings
            if finding.code == "METRIC_FORMULA_LINK_INVALID"
        ]
        assert len(invalid) == 1
        assert "formula_hash" in invalid[0].evidence["invalid_field_samples"]
        assert "conflicting_values" in invalid[0].evidence["invalid_reason_samples"]

    @pytest.mark.parametrize(
        "invalid_value",
        [
            ["formula-10"],
            True,
            1.9,
            0,
        ],
    )
    def test_non_scalar_or_illegal_formula_evidence_id_is_explicitly_blocked(
        self,
        invalid_value: Any,
    ) -> None:
        """IDs must be positive integral scalars; values are never truncated."""

        definition = _definition()
        detail = _detail()
        formula = _formula()
        base_metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)
        identity = {
            **(base_metric.metric_identity or {}),
            "formula_evidence_id": invalid_value,
        }
        metric = replace(base_metric, metric_identity=identity)
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(formula,), metrics=(metric,)
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )

        result = _service(snapshot, mcp=mcp)[0].check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        invalid = [
            finding
            for finding in result.findings
            if finding.code == "METRIC_FORMULA_LINK_INVALID"
        ]
        assert len(invalid) == 1
        assert "formula_evidence_id" in invalid[0].evidence["invalid_field_samples"]

    def test_non_object_metric_identity_is_reported_as_invalid_formula_link(self) -> None:
        """A scalar metric_identity cannot carry an executable formula link."""

        definition = _definition()
        detail = _detail()
        formula = _formula()
        metric = replace(
            _metric(301, definition.factor_ref, "time_series", formula_links=True),
            metric_identity=["invalid"],  # type: ignore[arg-type]
        )
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(formula,), metrics=(metric,)
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )

        result = _service(snapshot, mcp=mcp)[0].check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(
            finding.code == "METRIC_FORMULA_LINK_INVALID" for finding in result.findings
        )

    def test_metric_without_formula_hash_version_or_run_is_blocked(self) -> None:
        definition = _definition()
        detail = _detail()
        formula = _formula()
        metric = replace(
            _metric(301, definition.factor_ref, "time_series", formula_links=False),
            metric_identity=None,
        )
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(formula,), metrics=(metric,)
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(finding.code == "METRIC_FORMULA_VERSION_LINK_MISSING" for finding in result.findings)

    def test_repeated_metric_formula_link_gaps_are_aggregated_by_code(self) -> None:
        definition = _definition()
        detail = _detail()
        formula = _formula()
        metrics = (
            _metric(301, definition.factor_ref, "time_series", formula_links=False),
            _metric(302, definition.factor_ref, "cross_sectional", formula_links=False),
        )
        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(formula,),
            metrics=metrics,
        )
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: _mcp_formula(formula)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        matching = [
            finding
            for finding in result.findings
            if finding.code == "METRIC_FORMULA_VERSION_LINK_MISSING"
        ]
        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert len(matching) == 1
        assert matching[0].evidence == {
            "count": 2,
            "factor_ref_count": 1,
            "factor_ref_samples": [definition.factor_ref],
            "metric_id_samples": [301, 302],
        }

    def test_explicit_mcp_formula_expression_mismatch_fails(self) -> None:
        definition = _definition()
        detail = _detail()
        formula = _formula()
        snapshot = _snapshot(definitions=(definition,), details=(detail,), formulas=(formula,))
        returned = _mcp_formula(formula)
        returned["expression"] = "close.pct_change(12)"
        mcp = StubMCPAPI(
            {definition.factor_ref: _mcp_detail(definition, detail)},
            {definition.factor_ref: returned},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "FAIL"
        assert any(finding.code == "MCP_FORMULA_EXPRESSION_MISMATCH" for finding in result.findings)

    def test_executable_details_are_batched_at_fifty(self) -> None:
        definitions = tuple(_definition(f"sub_factor:{factor_id}") for factor_id in range(1, 102))
        details = tuple(_detail(row.factor_ref) for row in definitions)
        formulas = tuple(_formula(row.factor_ref) for row in definitions)
        snapshot = _snapshot(definitions=definitions, details=details, formulas=formulas)
        mcp = StubMCPAPI(
            {row.factor_ref: _mcp_detail(row, details[index]) for index, row in enumerate(definitions)},
            {row.factor_ref: _mcp_formula(formulas[index]) for index, row in enumerate(definitions)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        service.check_formula_static_consistency(snapshot)

        assert mcp.detail_batch_sizes == [50, 50, 1]

    def test_daily_export_budget_error_stops_remaining_detail_batches(self) -> None:
        """Do not repeat a terminal daily MCP budget failure for every chunk."""

        definitions = tuple(_definition(f"sub_factor:{factor_id}") for factor_id in range(1, 102))
        details = tuple(_detail(row.factor_ref) for row in definitions)

        class BudgetExhaustedMCP(StubMCPAPI):
            def get_factor_details_batch(
                self,
                factor_refs: list[str],
                *,
                detail_level: str,
            ) -> MCPResponse:
                assert detail_level == "executable"
                self.detail_batch_sizes.append(len(factor_refs))
                return MCPResponse(
                    status_code=200,
                    content_type="application/json",
                    envelope={
                        "jsonrpc": "2.0",
                        "id": "unit",
                        "result": {
                            "isError": True,
                            "structuredContent": {
                                "error": {"code": "EXPORT_BUDGET_EXCEEDED"},
                            },
                        },
                    },
                    protocol_version="2025-06-18",
                )

        snapshot = _snapshot(definitions=definitions, details=details)
        mcp = BudgetExhaustedMCP({}, {})
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert mcp.detail_batch_sizes == [50]
        missing = [
            finding
            for finding in result.findings
            if finding.code == "MCP_EXECUTABLE_DETAIL_MISSING"
        ]
        assert len(missing) == 101
        assert {finding.evidence["reason"] for finding in missing} == {
            "MCP_TOOL_ERROR:EXPORT_BUDGET_EXCEEDED"
        }

    def test_mcp_cache_isolated_between_distinct_snapshots(self) -> None:
        """A second snapshot must not reuse the first snapshot's MCP detail."""

        factor_ref = "sub_factor:10"
        first_definition = _definition(factor_ref, expression="close.pct_change(24)", window="24H")
        first_detail = _detail(factor_ref, expression="close.pct_change(24)", window=24)
        first_formula = _formula(factor_ref, expression="close.pct_change(24)", window="24H")
        first_snapshot = _snapshot(
            definitions=(first_definition,), details=(first_detail,), formulas=(first_formula,)
        )

        second_definition = replace(
            first_definition,
            formula_summary="close.pct_change(12)",
            window="12H",
        )
        second_detail = replace(first_detail, calc_logic="close.pct_change(12)", params={"window": 12, "fields": ["close"], "declared_fields": ["close"]})
        second_formula = replace(
            first_formula,
            expression="close.pct_change(12)",
            factor_window_bars="12H",
        )
        second_snapshot = replace(
            first_snapshot,
            definitions=(second_definition,),
            details=(second_detail,),
            formula_evidence=(second_formula,),
        )
        mcp = StubMCPAPI(
            {factor_ref: _mcp_detail(first_definition, first_detail)},
            {factor_ref: _mcp_formula(first_formula)},
        )
        service, _ = _service(first_snapshot, mcp=mcp)

        first_result = service.check_formula_static_consistency(first_snapshot)
        assert first_result.status == "PASS", first_result.findings

        mcp.details[factor_ref] = _mcp_detail(second_definition, second_detail)
        mcp.formulas[factor_ref] = _mcp_formula(second_formula)
        second_result = service.check_formula_static_consistency(second_snapshot)

        assert second_result.status == "PASS", second_result.findings
        assert mcp.detail_batch_sizes == [1, 1]

    def test_formula_cache_key_keeps_distinct_evidence_identities_separate(self) -> None:
        """Same request dimensions with different hashes must not collide."""

        first = _formula()
        second = replace(
            first,
            id=first.id + 1,
            formula_hash="different-hash",
            formula_version="different-version",
        )
        snapshot = _snapshot(
            definitions=(_definition(),),
            details=(_detail(),),
            formulas=(first, second),
        )
        mcp = StubMCPAPI(
            {first.factor_ref: _mcp_detail(_definition(), _detail())},
            {first.factor_ref: _mcp_formula(first)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert len(mcp.formula_calls) == 2
        assert result.status == "FAIL"
        assert any(
            finding.code == "MCP_FORMULA_PROJECTION_MISMATCH" for finding in result.findings
        )

    def test_duplicate_mcp_detail_items_are_data_blocked(self) -> None:
        """A batch response with two rows for one ref is ambiguous."""

        definition = _definition()
        detail = _detail()

        class DuplicateDetailMCP(StubMCPAPI):
            def get_factor_details_batch(
                self,
                factor_refs: list[str],
                *,
                detail_level: str,
            ) -> MCPResponse:
                assert detail_level == "executable"
                self.detail_batch_sizes.append(len(factor_refs))
                item = {
                    "factor_ref": factor_refs[0],
                    "success": True,
                    "data": self.details[factor_refs[0]],
                    "error": None,
                }
                return _mcp_response({"items": [item, dict(item)]})

        service, _ = _service(
            _snapshot(definitions=(definition,), details=(detail,)),
            mcp=DuplicateDetailMCP(
                {definition.factor_ref: _mcp_detail(definition, detail)}, {}
            ),
        )

        result = service.check_formula_static_consistency(
            _snapshot(definitions=(definition,), details=(detail,))
        )

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        missing = [
            finding
            for finding in result.findings
            if finding.code == "MCP_EXECUTABLE_DETAIL_MISSING"
        ]
        assert len(missing) == 1
        assert missing[0].evidence["reason"] == "MCP_BATCH_DUPLICATE_FACTOR_REF"

    def test_detail_protocol_error_is_propagated(self) -> None:
        definition = _definition()
        detail = _detail()

        class RaisingMCP(StubMCPAPI):
            def get_factor_details_batch(
                self,
                factor_refs: list[str],
                *,
                detail_level: str,
            ) -> MCPResponse:
                del factor_refs, detail_level
                raise MCPProtocolError("malformed detail response")

        snapshot = _snapshot(definitions=(definition,), details=(detail,))
        service, _ = _service(
            snapshot,
            mcp=RaisingMCP({}, {}),
        )

        with pytest.raises(MCPProtocolError):
            service.check_formula_static_consistency(snapshot)

    @pytest.mark.parametrize("error", [MCPProtocolError("malformed formula"), requests.RequestException("network")])
    def test_formula_transport_or_protocol_error_is_propagated(self, error: Exception) -> None:
        definition = _definition()
        detail = _detail()
        formula = _formula()
        metric = _metric(301, definition.factor_ref, "time_series", formula_links=True)

        class RaisingMCP(StubMCPAPI):
            def get_formula(
                self,
                factor_ref: str,
                run_id: str,
                interval: str,
                factor_window_bars: str,
                return_bar_interval: str,
                forward_return_bars: int,
                *,
                calculation_mode: str,
            ) -> MCPResponse:
                del (
                    factor_ref,
                    run_id,
                    interval,
                    factor_window_bars,
                    return_bar_interval,
                    forward_return_bars,
                    calculation_mode,
                )
                raise error

        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(formula,),
            metrics=(metric,),
        )
        service, _ = _service(snapshot, mcp=RaisingMCP({definition.factor_ref: _mcp_detail(definition, detail)}, {}))

        with pytest.raises(type(error)):
            service.check_formula_static_consistency(snapshot)

    def test_run_r0_checks_does_not_swallow_mcp_request_error(self) -> None:
        definition = _definition()
        detail = _detail()

        class RaisingMCP(StubMCPAPI):
            def get_factor_details_batch(
                self,
                factor_refs: list[str],
                *,
                detail_level: str,
            ) -> MCPResponse:
                del factor_refs, detail_level
                raise requests.RequestException("connection reset")

        snapshot = _snapshot(definitions=(definition,), details=(detail,))
        service, _ = _service(snapshot, mcp=RaisingMCP({}, {}))

        with pytest.raises(requests.RequestException):
            service.run_r0_checks()


class TestKnownFormulaRegressions:
    """Keep the three fixed Chinese Bug Registry titles stable."""

    @staticmethod
    def _complete_fixture() -> tuple[CalculationAuditSnapshot, StubMCPAPI]:
        specs: dict[str, tuple[str, tuple[str, ...], str, int]] = {
            **{ref: ("mean(close, 60) - close.shift(31)", ("close",), "dpo", 60) for ref in ("sub_factor:161104", "sub_factor:161106", "sub_factor:161108")},
            "sub_factor:180": ("funding_rate.diff(12).diff(12)", ("funding_rate",), "funding_acceleration__fr_diff2_24h", 24),
            "sub_factor:181": ("funding_rate.diff(24).diff(24)", ("funding_rate",), "funding_acceleration__fr_diff2_48h", 48),
            "sub_factor:183": ("funding_rate.diff(36).diff(36)", ("funding_rate",), "funding_acceleration__fr_diff2_72h", 72),
            "sub_factor:274": ("long_short_ratio.pct_change(48)", ("long_short_ratio",), "long_short_ratio__ls_chg_48h", 48),
            "sub_factor:276": ("long_short_ratio.pct_change(72)", ("long_short_ratio",), "long_short_ratio__ls_chg_72h", 72),
            **{ref: ("ATM_IV - realized_vol", ("ATM_IV", "realized_vol"), "iv_rv", 24) for ref in ("sub_factor:161628", "sub_factor:161629", "sub_factor:161630")},
        }
        definitions: list[FactorDefinition] = []
        details: list[FactorDetail] = []
        formulas: list[FormulaEvidence] = []
        detail_payloads: dict[str, dict[str, Any]] = {}
        formula_payloads: dict[str, dict[str, Any]] = {}
        for factor_ref, (expression, fields, name, window) in specs.items():
            definition = _definition(
                factor_ref,
                expression=expression,
                name=name,
                window=f"{window}H",
            )
            detail = _detail(
                factor_ref,
                expression=expression,
                fields=fields,
                name=name,
                window=window,
            )
            formula = _formula(
                factor_ref,
                expression=expression,
                fields=fields,
                window=f"{window}H",
            )
            definitions.append(definition)
            details.append(detail)
            formulas.append(formula)
            detail_payloads[factor_ref] = _mcp_detail(definition, detail)
            formula_payloads[factor_ref] = _mcp_formula(formula)
        return (
            _snapshot(
                definitions=tuple(definitions),
                details=tuple(details),
                formulas=tuple(formulas),
            ),
            StubMCPAPI(detail_payloads, formula_payloads),
        )

    def test_all_known_correct_semantics_pass(self) -> None:
        snapshot, mcp = self._complete_fixture()
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_known_formula_regressions(snapshot)

        assert result.status == "PASS", result.findings
        assert result.checked_count == 11

    @pytest.mark.parametrize(
        ("factor_ref", "expression"),
        [
            ("sub_factor:181", "funding_rate.diff(window // 2).diff(window // 2)"),
            ("sub_factor:274", "long_short_ratio.pct_change(periods=window)"),
        ],
    )
    def test_fixed_horizon_accepts_declared_window_parameter(
        self,
        factor_ref: str,
        expression: str,
    ) -> None:
        """固定周期公式使用已解析的 window 参数时不应被字面量 Oracle 误报。"""

        snapshot, mcp = self._complete_fixture()
        definitions = tuple(
            replace(row, formula_summary=expression) if row.factor_ref == factor_ref else row
            for row in snapshot.definitions
        )
        details = tuple(
            replace(row, calc_logic=expression) if row.factor_ref == factor_ref else row
            for row in snapshot.details
        )
        formulas = tuple(
            replace(row, expression=expression) if row.factor_ref == factor_ref else row
            for row in snapshot.formula_evidence
        )
        changed = replace(
            snapshot,
            definitions=definitions,
            details=details,
            formula_evidence=formulas,
        )
        detail = next(row for row in details if row.factor_ref == factor_ref)
        definition = next(row for row in definitions if row.factor_ref == factor_ref)
        formula = next(row for row in formulas if row.factor_ref == factor_ref)
        mcp.details[factor_ref] = _mcp_detail(definition, detail)
        mcp.formulas[factor_ref] = _mcp_formula(formula)
        service, _ = _service(changed, mcp=mcp)

        result = service.check_known_formula_regressions(changed)

        assert result.status == "PASS", result.findings

    @pytest.mark.parametrize(
        ("factor_ref", "wrong_expression", "expected_title"),
        [
            (
                "sub_factor:161104",
                "-(close - mean(close, 60).shift(31))",
                "DPO 公式错误地位移均线而非价格序列",
            ),
            (
                "sub_factor:181",
                "funding_rate.diff(12).diff(12)",
                "固定周期因子公式未应用声明窗口",
            ),
            (
                "sub_factor:161628",
                "close.pct_change().rolling(24).std()",
                "IV/RV 因子定义与实际执行公式及输入字段不一致",
            ),
        ],
    )
    def test_known_wrong_semantics_keep_fixed_bug_title(
        self,
        factor_ref: str,
        wrong_expression: str,
        expected_title: str,
    ) -> None:
        snapshot, mcp = self._complete_fixture()
        formulas = tuple(
            replace(row, expression=wrong_expression) if row.factor_ref == factor_ref else row
            for row in snapshot.formula_evidence
        )
        mcp.formulas[factor_ref] = _mcp_formula(next(row for row in formulas if row.factor_ref == factor_ref))
        changed = replace(snapshot, formula_evidence=formulas)
        service, _ = _service(changed, mcp=mcp)

        result = service.check_known_formula_regressions(changed)

        assert result.status == "FAIL"
        assert any(finding.message == expected_title for finding in result.findings)

    def test_unbound_historical_wrong_evidence_does_not_fail_current_formula(self) -> None:
        """历史 completed evidence 未被 metric 强链接时只能形成数据阻断。"""

        factor_ref = "sub_factor:161104"
        definition = _definition(
            factor_ref,
            expression="mean(close, 60) - close.shift(31)",
            name="dpo",
            window="60H",
        )
        detail = _detail(
            factor_ref,
            expression="mean(close, window) - close.shift(window // 2 + 1)",
            fields=("close",),
            name="dpo",
            window=60,
        )
        historical = _formula(
            factor_ref,
            expression="-(close - mean(close, 60).shift(31))",
            fields=("close",),
            window="60H",
        )
        metric = _metric(301, factor_ref, "time_series", formula_links=False)
        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(historical,),
            metrics=(metric,),
        )
        mcp = StubMCPAPI(
            {factor_ref: _mcp_detail(definition, detail)},
            {},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_known_formula_regressions(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert not any(finding.status == "FAIL" for finding in result.findings)
        assert any(
            finding.code == "HISTORICAL_FORMULA_EVIDENCE_UNBOUND"
            for finding in result.findings
        )

    def test_batch_bound_wrong_evidence_keeps_fixed_bug_title(self) -> None:
        factor_ref = "sub_factor:161104"
        definition = _definition(
            factor_ref,
            expression="mean(close, 60) - close.shift(31)",
            name="dpo",
            window="60H",
        )
        detail = _detail(
            factor_ref,
            expression="mean(close, window) - close.shift(window // 2 + 1)",
            fields=("close",),
            name="dpo",
            window=60,
        )
        wrong = _formula(
            factor_ref,
            expression="-(close - mean(close, 60).shift(31))",
            fields=("close",),
            window="60H",
        )
        metric = _metric(301, factor_ref, "time_series", formula_links=True)
        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(wrong,),
            metrics=(metric,),
        )
        mcp = StubMCPAPI(
            {factor_ref: _mcp_detail(definition, detail)},
            {factor_ref: _mcp_formula(wrong)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_known_formula_regressions(snapshot)

        assert result.status == "FAIL"
        assert any(
            finding.message == "DPO 公式错误地位移均线而非价格序列"
            for finding in result.findings
        )

    def test_source_detail_only_link_is_blocked_and_not_bound(self) -> None:
        factor_ref = "sub_factor:161104"
        definition = _definition(
            factor_ref,
            expression="mean(close, 60) - close.shift(31)",
            name="dpo",
            window="60H",
        )
        detail = _detail(
            factor_ref,
            expression="mean(close, window) - close.shift(window // 2 + 1)",
            fields=("close",),
            name="dpo",
            window=60,
        )
        historical = _formula(factor_ref, expression="-(close - mean(close, 60).shift(31))", window="60H")
        identity = {
            "eval_batch_uid": "batch-6",
            "factor_ref": factor_ref,
            "factor_version": "sha256:factor-161104",
            "evaluation_type": "time_series",
            "label_code": "WIDE_RANGE",
            "factor_window_bars": "60H",
            "interval": "1h",
            "source_detail_id": historical.source_detail_id,
        }
        metric = replace(_metric(301, factor_ref, "time_series"), metric_identity=identity)
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(historical,), metrics=(metric,)
        )
        mcp = StubMCPAPI({factor_ref: _mcp_detail(definition, detail)}, {})
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(
            finding.code == "METRIC_FORMULA_LINK_SOURCE_DETAIL_ONLY"
            for finding in result.findings
        )
        assert not any(finding.status == "FAIL" for finding in result.findings)

    def test_definition_literal_and_detail_parameterized_window_are_equivalent(self) -> None:
        factor_ref = "sub_factor:10"
        definition = _definition(
            factor_ref,
            expression="mean(close, 60) - close.shift(31)",
            name="dpo",
            window="60H",
        )
        detail = _detail(
            factor_ref,
            expression="mean(close, window) - close.shift(window // 2 + 1)",
            fields=("close",),
            name="dpo",
            window=60,
        )
        formula = _formula(
            factor_ref,
            expression="mean(close, 60) - close.shift(31)",
            fields=("close",),
            window="60H",
        )
        snapshot = _snapshot(definitions=(definition,), details=(detail,), formulas=(formula,))
        mcp = StubMCPAPI(
            {factor_ref: _mcp_detail(definition, detail)},
            {factor_ref: _mcp_formula(formula)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "PASS", result.findings
        assert not any(
            finding.code in {"DB_DETAIL_FORMULA_MISMATCH", "MCP_DETAIL_FORMULA_MISMATCH"}
            for finding in result.findings
        )

    def test_natural_language_formula_summary_is_not_treated_as_expression(self) -> None:
        factor_ref = "sub_factor:10"
        definition = _definition(
            factor_ref,
            expression="中文摘要：使用收盘价计算短期动量变化率。",
            window="24H",
        )
        detail = _detail(
            factor_ref,
            expression="close.pct_change(window)",
            fields=("close",),
            window=24,
        )
        detail = replace(
            detail,
            params={**(detail.params or {}), "formula": "close.pct_change(window)"},
        )
        formula = _formula(factor_ref, expression="close.pct_change(24)")
        snapshot = _snapshot(definitions=(definition,), details=(detail,), formulas=(formula,))
        mcp_payload = _mcp_detail(definition, detail)
        mcp_payload["formula_summary"] = "中文摘要：使用收盘价计算短期动量变化率。"
        mcp_payload["metadata"] = {"formula": "close.pct_change(window)"}
        mcp = StubMCPAPI({factor_ref: mcp_payload}, {factor_ref: _mcp_formula(formula)})
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "PASS", result.findings
        assert not any(
            finding.code in {"DB_DETAIL_FORMULA_MISMATCH", "MCP_DETAIL_FORMULA_MISMATCH"}
            for finding in result.findings
        )

    def test_two_parseable_current_formulas_that_differ_fail(self) -> None:
        factor_ref = "sub_factor:10"
        definition = _definition(factor_ref, expression="close.pct_change(24)")
        detail = _detail(factor_ref, expression="close.pct_change(12)")
        formula = _formula(factor_ref, expression="close.pct_change(12)")
        snapshot = _snapshot(definitions=(definition,), details=(detail,), formulas=(formula,))
        mcp = StubMCPAPI(
            {factor_ref: _mcp_detail(definition, detail)},
            {factor_ref: _mcp_formula(formula)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_formula_static_consistency(snapshot)

        assert result.status == "FAIL"
        assert any(finding.code == "DB_DETAIL_FORMULA_MISMATCH" for finding in result.findings)

    def test_iv_rv_historical_close_formula_does_not_fail_current_definition(self) -> None:
        factor_ref = "sub_factor:161628"
        definition = _definition(
            factor_ref,
            expression="ATM_IV - realized_vol",
            name="iv_rv",
            window="24H",
        )
        detail = _detail(
            factor_ref,
            expression="ATM_IV - realized_vol",
            fields=("ATM_IV", "realized_vol"),
            name="iv_rv",
            window=24,
        )
        historical = _formula(
            factor_ref,
            expression="close.pct_change().rolling(24).std()",
            fields=("close",),
            window="24H",
        )
        metric = _metric(301, factor_ref, "time_series", formula_links=False)
        snapshot = _snapshot(
            definitions=(definition,), details=(detail,), formulas=(historical,), metrics=(metric,)
        )
        mcp = StubMCPAPI({factor_ref: _mcp_detail(definition, detail)}, {})
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_known_formula_regressions(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert not any(finding.status == "FAIL" for finding in result.findings)
        assert any(
            finding.code == "HISTORICAL_FORMULA_EVIDENCE_UNBOUND"
            for finding in result.findings
        )

    def test_fixed_horizon_current_mcp_detail_is_checked_outside_batch(self) -> None:
        factor_ref = "sub_factor:181"
        definition = _definition(
            factor_ref,
            expression="funding_rate.diff(12).diff(12)",
            name="funding_acceleration__fr_diff2_48h",
            window="48H",
        )
        detail = _detail(
            factor_ref,
            expression="funding_rate.diff(12).diff(12)",
            fields=("funding_rate",),
            name="funding_acceleration__fr_diff2_48h",
            window=48,
        )
        snapshot = _snapshot()
        mcp = StubMCPAPI({factor_ref: _mcp_detail(definition, detail)}, {})
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_known_formula_regressions(snapshot)

        assert result.status == "FAIL"
        finding = next(
            finding
            for finding in result.findings
            if finding.factor_ref == factor_ref
            and finding.message == "固定周期因子公式未应用声明窗口"
        )
        assert finding.evidence["expected_period"] == 24

    def test_malformed_dpo_mean_call_is_fail_closed(self) -> None:
        """A parseable but incomplete mean call must not crash the oracle."""

        factor_ref = "sub_factor:161104"
        expression = "mean(window=60) - close.shift(31)"
        definition = _definition(
            factor_ref,
            expression=expression,
            name="dpo",
            window="60H",
        )
        detail = _detail(
            factor_ref,
            expression=expression,
            fields=("close",),
            name="dpo",
            window=60,
        )
        formula = _formula(
            factor_ref,
            expression=expression,
            fields=("close",),
            window="60H",
        )
        snapshot = _snapshot(
            definitions=(definition,),
            details=(detail,),
            formulas=(formula,),
        )
        mcp = StubMCPAPI(
            {factor_ref: _mcp_detail(definition, detail)},
            {factor_ref: _mcp_formula(formula)},
        )
        service, _ = _service(snapshot, mcp=mcp)

        result = service.check_known_formula_regressions(snapshot)

        assert result.status == "FAIL"
        assert any(
            finding.factor_ref == factor_ref
            and finding.message == "DPO 公式错误地位移均线而非价格序列"
            for finding in result.findings
        )


class TestMembershipIntegrity:
    """Ensure frozen batch membership drift is never reported as a pass."""

    @staticmethod
    def _missing_identity() -> FactorIdentity:
        return FactorIdentity(
            factor_ref="sub_factor:901",
            factor_type="sub_factor",
            factor_id=901,
            factor_version="sha256:factor-901",
        )

    @staticmethod
    def _unexpected_identity() -> FactorIdentity:
        return FactorIdentity(
            factor_ref="sub_factor:902",
            factor_type="sub_factor",
            factor_id=902,
            factor_version="sha256:factor-902",
        )

    def test_missing_frozen_member_is_a_structured_failure(self) -> None:
        snapshot = _snapshot(
            metrics=TestAnyValidScope._truth_table_metrics(),
            membership_differences=FactorMembershipDifferences(
                (self._missing_identity(),),
                (),
            ),
        )
        service, _ = _service(snapshot)

        result = service.check_any_valid_scope(snapshot)

        assert result.status == "FAIL"
        finding = next(
            item
            for item in result.findings
            if item.code == "FACTOR_MEMBERSHIP_MISSING_FROM_METRICS"
        )
        assert finding.evidence["count"] == 1
        assert finding.evidence["factor_ref_samples"] == ["sub_factor:901"]

    def test_unexpected_metric_member_is_a_structured_failure(self) -> None:
        snapshot = _snapshot(
            metrics=TestAnyValidScope._truth_table_metrics(),
            membership_differences=FactorMembershipDifferences(
                (),
                (self._unexpected_identity(),),
            ),
        )
        service, _ = _service(snapshot)

        result = service.check_route_score_recalculation(snapshot)

        assert result.status == "FAIL"
        assert any(
            item.code == "FACTOR_MEMBERSHIP_UNEXPECTED_IN_METRICS"
            for item in result.findings
        )

    def test_missing_membership_reconciliation_is_data_blocked(self) -> None:
        snapshot = _snapshot(metrics=TestAnyValidScope._truth_table_metrics())
        changed = replace(snapshot, membership_differences=cast(Any, None))
        service, _ = _service(changed)

        result = service.check_any_valid_scope(changed)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(
            item.code == "FACTOR_MEMBERSHIP_DIFFERENCES_MISSING"
            for item in result.findings
        )

    def test_missing_definition_version_is_data_blocked_not_version_drift(self) -> None:
        snapshot = _snapshot(
            metrics=TestAnyValidScope._truth_table_metrics(),
            membership_differences=FactorMembershipDifferences(
                (),
                (),
                (
                    FactorMembershipVersionIssue(
                        factor_ref="sub_factor:903",
                        factor_type="sub_factor",
                        factor_id=903,
                        reason="definition_factor_version_missing_or_invalid",
                        executable_factor_version="sha256:formula-903",
                    ),
                ),
            ),
        )
        service, _ = _service(snapshot)

        result = service.check_any_valid_scope(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(
            item.code == "FACTOR_MEMBERSHIP_DEFINITION_VERSION_MISSING"
            for item in result.findings
        )
        assert not any(
            item.code in {
                "FACTOR_MEMBERSHIP_MISSING_FROM_METRICS",
                "FACTOR_MEMBERSHIP_UNEXPECTED_IN_METRICS",
            }
            for item in result.findings
        )

    def test_report_guard_survives_replaced_check_implementations(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        snapshot = _snapshot(
            membership_differences=FactorMembershipDifferences(
                (self._missing_identity(),),
                (self._unexpected_identity(),),
            )
        )
        service, _ = _service(snapshot)
        checks = {
            "check_formula_static_consistency": CalculationCheckResult(
                "CALC-510-A", "formula", "PASS", "ok", 1
            ),
            "check_known_formula_regressions": CalculationCheckResult(
                "CALC-510-C", "regression", "PASS", "ok", 1
            ),
            "check_any_valid_scope": CalculationCheckResult(
                "CALC-501-C", "validity", "PASS", "ok", 1
            ),
            "check_route_score_recalculation": CalculationCheckResult(
                "CALC-506-A", "score", "PASS", "ok", 1
            ),
            "check_rank_stability": CalculationCheckResult(
                "CALC-507-A", "rank", "BLOCKED_DOC", "doc gap", 1
            ),
        }
        for name, result in checks.items():
            monkeypatch.setattr(service, name, lambda *args, value=result: value)

        report = service.run_r0_checks()

        assert report.status == "FAIL"
        validity = next(item for item in report.checks if item.case_id == "CALC-501-C")
        assert any(
            item.code == "FACTOR_MEMBERSHIP_MISSING_FROM_METRICS"
            for item in validity.findings
        )
        assert any(
            item.code == "FACTOR_MEMBERSHIP_UNEXPECTED_IN_METRICS"
            for item in validity.findings
        )


class TestAnyValidScope:
    """Exercise every branch of the task-level TS-or-CS acceptance rule."""

    @staticmethod
    def _truth_table_metrics() -> tuple[EvaluationMetric, ...]:
        metrics: list[EvaluationMetric] = []
        cases = [
            ("sub_factor:21", ["time_series"]),
            ("sub_factor:22", ["cross_sectional"]),
            ("sub_factor:23", ["time_series", "cross_sectional"]),
            ("sub_factor:24", []),
        ]
        metric_id = 300
        for index, (factor_ref, valid_scopes) in enumerate(cases):
            eligibility = _eligibility(
                valid_scopes,
                routing_score=None if not valid_scopes else str(80 + index),
            )
            for scope in ("time_series", "cross_sectional"):
                metric_id += 1
                metrics.append(
                    _metric(
                        metric_id,
                        factor_ref,
                        scope,
                        valid=scope in valid_scopes,
                        eligibility=eligibility,
                    )
                )
        return tuple(metrics)

    def test_ts_only_cs_only_both_and_neither_pass(self) -> None:
        snapshot = _snapshot(metrics=self._truth_table_metrics())
        service, _ = _service(snapshot)

        result = service.check_any_valid_scope(snapshot)

        assert result.status == "PASS", result.findings
        assert result.evidence["truth_classes"] == ["both", "cs_only", "neither", "ts_only"]

    def test_repository_pair_hash_is_preferred_over_divergent_full_identity(self) -> None:
        metrics = list(self._truth_table_metrics())
        target = next(
            index
            for index, metric in enumerate(metrics)
            if metric.factor_ref == "sub_factor:21" and metric.evaluation_type == "cross_sectional"
        )
        metrics[target] = replace(
            metrics[target],
            metric_identity={**(metrics[target].metric_identity or {}), "unrelated": "different"},
        )
        snapshot = _snapshot(metrics=tuple(metrics))
        service, _ = _service(snapshot)

        result = service.check_any_valid_scope(snapshot)

        assert result.status == "PASS", result.findings

    def test_full_identity_is_used_when_repository_pair_hash_is_missing(self) -> None:
        metrics = tuple(
            replace(metric, metric_pair_identity_hash=None)
            for metric in self._truth_table_metrics()
        )
        snapshot = _snapshot(metrics=metrics)
        service, _ = _service(snapshot)

        result = service.check_any_valid_scope(snapshot)

        assert result.status == "PASS", result.findings

    def test_pair_hash_allows_pairing_without_full_metric_identity(self) -> None:
        metrics = tuple(
            replace(metric, metric_identity=None)
            for metric in self._truth_table_metrics()
        )
        snapshot = _snapshot(metrics=metrics)
        service, _ = _service(snapshot)

        result = service.check_any_valid_scope(snapshot)

        assert result.status == "PASS", result.findings

    def test_missing_pair_hash_and_full_identity_is_data_blocked(self) -> None:
        metrics = list(self._truth_table_metrics())
        metrics[0] = replace(
            metrics[0],
            metric_pair_identity_hash=None,
            metric_identity=None,
        )
        snapshot = _snapshot(metrics=tuple(metrics))
        service, _ = _service(snapshot)

        result = service.check_any_valid_scope(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(finding.code == "METRIC_IDENTITY_MISSING" for finding in result.findings)

    def test_valid_scope_rejected_above_threshold_fails(self) -> None:
        metrics = list(self._truth_table_metrics())
        for index, metric in enumerate(metrics):
            if metric.factor_ref == "sub_factor:21":
                metrics[index] = replace(
                    metric,
                    route_eligibility=_eligibility(["time_series"], routing_score="80", is_eligible=False),
                )
        snapshot = _snapshot(metrics=tuple(metrics))
        service, _ = _service(snapshot)

        result = service.check_any_valid_scope(snapshot)

        assert result.status == "FAIL"
        assert any(finding.code == "VALIDITY_ANY_SCOPE_RESULT_MISMATCH" for finding in result.findings)

    def test_missing_truth_table_branch_is_data_blocked(self) -> None:
        metrics = tuple(
            metric for metric in self._truth_table_metrics() if metric.factor_ref != "sub_factor:22"
        )
        snapshot = _snapshot(metrics=metrics)
        service, _ = _service(snapshot)

        result = service.check_any_valid_scope(snapshot)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(finding.code == "VALIDITY_TRUTH_TABLE_INCOMPLETE" for finding in result.findings)


class TestRouteScoreRecalculation:
    """Verify weight renormalization, directed metrics and DB-scale rounding."""

    @staticmethod
    def _fixture() -> CalculationAuditSnapshot:
        factor_ref = "sub_factor:31"
        metric = _metric(
            401,
            factor_ref,
            "time_series",
            score="95.644261",
            confidence="0.894405039",
            direction=-1,
        )
        evidence = {
            "admission_mode": "any_valid_scope",
            "valid_scopes": ["time_series"],
            "invalid_scopes": ["cross_sectional"],
            "metric_ids": {"time_series": 401},
            "configured_profile_weights": {
                "time_series": "0.500000",
                "cross_sectional": "0.500000",
            },
            "effective_profile_weights": {
                "time_series": "1.000000",
                "cross_sectional": "0.000000",
            },
            "time_series": {
                "metric_score": "95.644261",
                "confidence": "0.894405039",
            },
            "base_score": "95.644261",
            "confidence": "0.894405039",
            "routing_score": "85.544708989831179",
        }
        route = _route(501, factor_ref, 401, 1, "85.544709", evidence=evidence)
        return _snapshot(metrics=(metric,), routes=(route,))

    def test_ts_only_weight_is_renormalized_and_score_uses_half_up_scale(self) -> None:
        snapshot = self._fixture()
        service, _ = _service(snapshot)

        result = service.check_route_score_recalculation(snapshot)

        assert result.status == "PASS", result.findings
        assert result.evidence["rounding"] == "ROUND_HALF_UP"

    def test_route_score_mismatch_fails_without_epsilon(self) -> None:
        snapshot = self._fixture()
        bad_route = replace(snapshot.routes[0], routing_score=Decimal("85.544708"))
        changed = replace(snapshot, routes=(bad_route,))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "FAIL"
        assert any(finding.code == "ROUTE_STORED_SCORE_MISMATCH" for finding in result.findings)

    def test_missing_directed_metric_values_is_data_blocked(self) -> None:
        snapshot = self._fixture()
        metric = replace(snapshot.evaluation_metrics[0], metric_payload={})
        changed = replace(snapshot, evaluation_metrics=(metric,))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(finding.code == "METRIC_DIRECTED_VALUE_MISSING" for finding in result.findings)

    def test_none_metric_payload_is_data_blocked_instead_of_crashing(self) -> None:
        snapshot = self._fixture()
        metric = replace(snapshot.evaluation_metrics[0], metric_payload=None)
        changed = replace(snapshot, evaluation_metrics=(metric,))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(finding.code == "METRIC_DIRECTED_VALUE_MISSING" for finding in result.findings)

    def test_fractional_predictive_direction_is_not_truncated(self) -> None:
        snapshot = self._fixture()
        metric = replace(
            snapshot.evaluation_metrics[0],
            direction={"predictive_direction": 1.9},
        )
        changed = replace(snapshot, evaluation_metrics=(metric,))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(
            finding.code == "METRIC_PREDICTIVE_DIRECTION_MISSING"
            for finding in result.findings
        )

    @pytest.mark.parametrize(
        ("field_name", "value", "expected_code"),
        [
            ("admission_mode", "all_scopes", "ROUTE_ADMISSION_MODE_MISMATCH"),
            ("valid_scopes", ["cross_sectional"], "ROUTE_SCOPE_EVIDENCE_MISMATCH"),
            ("invalid_scopes", [], "ROUTE_SCOPE_EVIDENCE_MISMATCH"),
        ],
    )
    def test_route_scope_evidence_must_match_metric_truth(
        self,
        field_name: str,
        value: Any,
        expected_code: str,
    ) -> None:
        """Route 准入模式和有效维度证据必须与所引用 metric 的真值一致。"""

        snapshot = self._fixture()
        evidence = dict(snapshot.routes[0].evidence)
        evidence[field_name] = value
        changed = replace(snapshot, routes=(replace(snapshot.routes[0], evidence=evidence),))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "FAIL"
        assert any(finding.code == expected_code for finding in result.findings)

    def test_batch_weights_are_required_even_when_route_evidence_has_them(self) -> None:
        """Evidence must not become an implicit configuration fallback."""

        snapshot = self._fixture()
        changed_batch = replace(
            snapshot.batch,
            evaluation_config={"minimum_route_score": "60"},
        )
        changed = replace(snapshot, batch=changed_batch)
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(
            finding.code == "ROUTE_BATCH_PROFILE_WEIGHTS_MISSING"
            for finding in result.findings
        )

    def test_conflicting_batch_weight_aliases_are_data_blocked(self) -> None:
        """Conflicting immutable config aliases must fail closed."""

        snapshot = self._fixture()
        changed_batch = replace(
            snapshot.batch,
            evaluation_config={
                "minimum_route_score": "60",
                "route_profiles": {
                    "default": {
                        "weights": {
                            "time_series": "0.700000",
                            "cross_sectional": "0.300000",
                        }
                    }
                },
                "profile_weights": {
                    "time_series": "0.500000",
                    "cross_sectional": "0.500000",
                },
            },
        )
        changed = replace(snapshot, batch=changed_batch)
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        finding = next(
            item
            for item in result.findings
            if item.code == "ROUTE_BATCH_PROFILE_WEIGHTS_CONFLICT"
        )
        assert "route_profiles.default.weights" in str(finding.evidence["path"])
        assert "profile_weights" in str(finding.evidence["path"])

    def test_equal_batch_weight_aliases_are_accepted(self) -> None:
        """Equivalent aliases may coexist when their normalized decimals agree."""

        snapshot = self._fixture()
        changed_batch = replace(
            snapshot.batch,
            evaluation_config={
                "minimum_route_score": "60",
                "route_profiles": {
                    "default": {
                        "weights": {
                            "time_series": "0.500000",
                            "cross_sectional": "0.500000",
                        }
                    }
                },
                "profile_weights": {
                    "time_series": "0.5",
                    "cross_sectional": "0.5",
                },
            },
        )
        changed = replace(snapshot, batch=changed_batch)
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "PASS", result.findings

    def test_evidence_configured_weights_must_match_batch_snapshot(self) -> None:
        snapshot = self._fixture()
        evidence = dict(snapshot.routes[0].evidence)
        evidence["configured_profile_weights"] = {
            "time_series": "0.700000",
            "cross_sectional": "0.300000",
        }
        changed = replace(snapshot, routes=(replace(snapshot.routes[0], evidence=evidence),))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "FAIL"
        assert any(
            finding.code == "ROUTE_CONFIGURED_WEIGHT_MISMATCH"
            for finding in result.findings
        )

    def test_valid_scope_evidence_fields_are_required(self) -> None:
        snapshot = self._fixture()
        evidence = dict(snapshot.routes[0].evidence)
        evidence.pop("time_series")
        changed = replace(snapshot, routes=(replace(snapshot.routes[0], evidence=evidence),))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(
            finding.code == "ROUTE_SCOPE_EVIDENCE_DETAIL_MISSING"
            for finding in result.findings
        )

    def test_fractional_metric_id_is_not_truncated(self) -> None:
        snapshot = self._fixture()
        evidence = dict(snapshot.routes[0].evidence)
        evidence["metric_ids"] = {"time_series": 401.9}
        changed = replace(snapshot, routes=(replace(snapshot.routes[0], evidence=evidence),))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(finding.code == "ROUTE_METRIC_ID_INVALID" for finding in result.findings)

    def test_complete_metric_pair_is_reconstructed_independently_of_evidence_ids(self) -> None:
        factor_ref = "sub_factor:32"
        eligibility = _eligibility(
            ["time_series", "cross_sectional"],
            routing_score="59.500000",
            is_eligible=False,
        )
        ts = _metric(
            411,
            factor_ref,
            "time_series",
            score="80.000000",
            confidence="0.800000000",
            eligibility=eligibility,
        )
        cs = _metric(
            412,
            factor_ref,
            "cross_sectional",
            score="60.000000",
            confidence="0.900000000",
            eligibility=eligibility,
        )
        evidence = {
            "admission_mode": "any_valid_scope",
            "valid_scopes": ["time_series", "cross_sectional"],
            "invalid_scopes": [],
            # Deliberately omit CS: the complete batch metric set still makes
            # the expected pair unambiguous, so this must fail the evidence.
            "metric_ids": {"time_series": 411},
            "configured_profile_weights": {
                "time_series": "0.500000",
                "cross_sectional": "0.500000",
            },
            "effective_profile_weights": {
                "time_series": "0.500000",
                "cross_sectional": "0.500000",
            },
            "time_series": {"metric_score": "80.000000", "confidence": "0.800000000"},
            "cross_sectional": {"metric_score": "60.000000", "confidence": "0.900000000"},
            "base_score": "70.000000",
            "confidence": "0.850000000",
            "routing_score": "59.500000000000000",
        }
        route = replace(
            _route(511, factor_ref, 411, 1, "59.500000", evidence=evidence),
            is_eligible=False,
            confidence=Decimal("0.850000000"),
            time_series_score=Decimal("80.000000"),
            cross_sectional_score=Decimal("60.000000"),
        )
        snapshot = _snapshot(metrics=(ts, cs), routes=(route,))
        service, _ = _service(snapshot)

        result = service.check_route_score_recalculation(snapshot)

        assert result.status == "FAIL"
        assert any(
            finding.code == "ROUTE_METRIC_IDS_MISMATCH"
            for finding in result.findings
        )

    def test_invalid_scope_route_score_must_be_null(self) -> None:
        snapshot = self._fixture()
        invalid_cs = _metric(
            402,
            "sub_factor:31",
            "cross_sectional",
            valid=False,
            score="77.000000",
        )
        route = replace(snapshot.routes[0], cross_sectional_score=Decimal("77.000000"))
        changed = replace(snapshot, evaluation_metrics=(snapshot.evaluation_metrics[0], invalid_cs), routes=(route,))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "FAIL"
        assert any(
            finding.code == "ROUTE_SCOPE_SCORE_MUST_BE_NULL"
            for finding in result.findings
        )

    @pytest.mark.parametrize(
        ("field", "expected_code"),
        [
            ("is_eligible", "ROUTE_ELIGIBILITY_MISMATCH"),
            ("reject_reason_code", "ROUTE_REJECT_REASON_ON_ELIGIBLE"),
        ],
    )
    def test_route_admission_fields_are_reconciled(
        self,
        field: str,
        expected_code: str,
    ) -> None:
        snapshot = self._fixture()
        values: dict[str, Any] = {"is_eligible": False} if field == "is_eligible" else {"reject_reason_code": "unexpected"}
        changed = replace(snapshot, routes=(replace(snapshot.routes[0], **values),))
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "FAIL"
        assert any(finding.code == expected_code for finding in result.findings)

    def test_route_as_of_time_is_reconciled(self) -> None:
        snapshot = self._fixture()
        changed = replace(
            snapshot,
            routes=(
                replace(
                    snapshot.routes[0],
                    as_of_time=datetime(2026, 9, 2, 1, 18),
                ),
            ),
        )
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "FAIL"
        assert any(
            finding.code == "ROUTE_AS_OF_TIME_MISMATCH"
            for finding in result.findings
        )

    def test_undefined_route_date_semantics_is_not_guessed(self) -> None:
        snapshot = self._fixture()
        changed = replace(
            snapshot,
            routes=(replace(snapshot.routes[0], environment_date=date(2026, 9, 2)),),
        )
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "PASS", result.findings
        assert not any(
            finding.code == "ROUTE_ENVIRONMENT_DATE_MISMATCH"
            for finding in result.findings
        )

    def test_publication_effective_route_date_is_reconciled(self) -> None:
        snapshot = self._fixture()
        batch = replace(
            snapshot.batch,
            environment_snapshot={
                "route_environment_date_semantics": "publication_effective"
            },
        )
        changed = replace(
            snapshot,
            batch=batch,
            routes=(replace(snapshot.routes[0], environment_date=date(2026, 9, 2)),),
        )
        service, _ = _service(changed)

        result = service.check_route_score_recalculation(changed)

        assert result.status == "FAIL"
        assert any(
            finding.code == "ROUTE_ENVIRONMENT_DATE_MISMATCH"
            for finding in result.findings
        )


class TestRankStability:
    """Verify full partition ranking while preserving the tie-breaker doc gap."""

    @staticmethod
    def _fixture() -> CalculationAuditSnapshot:
        first = _route(601, "sub_factor:41", 701, 1, "90.000000")
        second = _route(602, "sub_factor:42", 702, 2, "80.000000")
        return _snapshot(routes=(first, second))

    def test_stable_decimal_ranking_remains_blocked_by_undefined_tie_breaker(self) -> None:
        snapshot = self._fixture()
        repeated = replace(snapshot, captured_at=datetime(2026, 9, 4, 12, 0, 1))
        service, _ = _service(snapshot)

        result = service.check_rank_stability(snapshot, repeated)

        assert result.status == "BLOCKED_DOC"
        assert any(finding.code == "RANK_TIE_BREAKER_UNDEFINED" for finding in result.findings)

    def test_score_order_regression_fails_before_doc_block(self) -> None:
        snapshot = self._fixture()
        wrong = replace(snapshot.routes[1], routing_score=Decimal("95.000000"))
        changed = replace(snapshot, routes=(snapshot.routes[0], wrong))
        service, _ = _service(changed)

        result = service.check_rank_stability(changed, changed)

        assert result.status == "FAIL"
        assert any(finding.code == "RANK_SCORE_NOT_DESCENDING" for finding in result.findings)

    def test_publication_change_is_data_precondition_block(self) -> None:
        snapshot = self._fixture()
        new_batch = replace(snapshot.batch, publication_uid="publication-7", publish_version="publish-v2")
        changed_routes = tuple(
            replace(route, publication_uid="publication-7", publish_version="publish-v2")
            for route in snapshot.routes
        )
        repeated = replace(snapshot, batch=new_batch, routes=changed_routes)
        service, _ = _service(snapshot)

        result = service.check_rank_stability(snapshot, repeated)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(finding.code == "RANK_PUBLICATION_CHANGED" for finding in result.findings)

    def test_same_day_timestamp_drift_stays_in_one_date_partition(self) -> None:
        snapshot = self._fixture()
        drifted = replace(
            snapshot.routes[1],
            rank_no=1,
            as_of_time=datetime(2026, 9, 2, 2, 17),
        )
        changed = replace(snapshot, routes=(snapshot.routes[0], drifted))
        service, _ = _service(changed)

        result = service.check_rank_stability(changed, changed)

        assert result.status == "FAIL"
        assert result.evidence["partition_count"] == 1
        assert any(item.code == "RANK_NOT_CONTIGUOUS" for item in result.findings)
        assert any(
            item.code == "RANK_ROUTE_AS_OF_TIME_MISMATCH"
            for item in result.findings
        )

    def test_route_as_of_time_must_equal_batch_snapshot_time(self) -> None:
        snapshot = self._fixture()
        changed_batch = replace(
            snapshot.batch,
            as_of_time=datetime(2026, 9, 2, 1, 18),
        )
        changed = replace(snapshot, batch=changed_batch)
        service, _ = _service(changed)

        result = service.check_rank_stability(changed, changed)

        assert result.status == "FAIL"
        assert sum(
            item.code == "RANK_ROUTE_AS_OF_TIME_MISMATCH"
            for item in result.findings
        ) == 4

    def test_repeated_route_snapshot_is_checked_against_first_batch_time(self) -> None:
        snapshot = self._fixture()
        service, _ = _service(snapshot)
        repeated = PublishedRouteSnapshot(
            captured_at=snapshot.captured_at,
            batch_id=snapshot.batch.id,
            publication_uid=snapshot.batch.publication_uid,
            publish_version=snapshot.batch.publish_version,
            market_scope=snapshot.batch.market_scope,
            route_profile_key=snapshot.batch.route_profile_key,
            routes=tuple(
                replace(
                    RouteRankingEntry(
                        id=route.id,
                        metric_id=route.metric_id,
                        environment_date=route.environment_date,
                        label_kind=route.label_kind,
                        label_code=route.label_code,
                        as_of_time=route.as_of_time,
                        factor_ref=route.factor_ref,
                        factor_type=route.factor_type,
                        factor_id=route.factor_id,
                        factor_version=route.factor_version,
                        rank_no=route.rank_no,
                        routing_score=route.routing_score,
                        confidence=route.confidence,
                        time_series_score=route.time_series_score,
                        cross_sectional_score=route.cross_sectional_score,
                        score_rule_version=route.score_rule_version,
                    ),
                    as_of_time=datetime(2026, 9, 2, 2, 17),
                )
                for route in snapshot.routes
            ),
        )

        result = service.check_rank_stability(snapshot, repeated)

        assert result.status == "FAIL"
        assert any(
            item.code == "RANK_ROUTE_AS_OF_TIME_MISMATCH"
            and item.evidence.get("read") == "repeated"
            for item in result.findings
        )

    def test_as_of_date_midnight_is_a_partition_boundary(self) -> None:
        snapshot = self._fixture()
        before_midnight = replace(
            snapshot.routes[0],
            as_of_time=datetime(2026, 9, 1, 23, 59, 59),
            rank_no=1,
        )
        after_midnight = replace(
            snapshot.routes[1],
            as_of_time=datetime(2026, 9, 2, 0, 0, 0),
            rank_no=1,
        )
        changed = replace(snapshot, routes=(before_midnight, after_midnight))
        service, _ = _service(changed)

        result = service.check_rank_stability(changed, changed)

        assert result.evidence["partition_count"] == 2
        assert not any(item.code == "RANK_NOT_CONTIGUOUS" for item in result.findings)
        assert result.status == "FAIL"
        assert any(
            item.code == "RANK_ROUTE_AS_OF_TIME_MISMATCH" for item in result.findings
        )

    def test_invalid_route_as_of_time_is_data_blocked(self) -> None:
        snapshot = self._fixture()
        changed = replace(
            snapshot,
            routes=(replace(snapshot.routes[0], as_of_time=cast(Any, None)),),
        )
        service, _ = _service(changed)

        result = service.check_rank_stability(changed, changed)

        assert result.status == "BLOCKED_DATA_PRECONDITION"
        assert any(
            item.code == "RANK_ROUTE_AS_OF_TIME_MISSING" for item in result.findings
        )


class TestReportContract:
    """Keep the Service report aligned with the live Case adapter."""

    def test_run_report_exposes_checks_findings_summary_and_partition(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        snapshot = _snapshot()
        service, mcp = _service(snapshot)
        checks = {
            "check_formula_static_consistency": CalculationCheckResult(
                "CALC-510-A", "formula", "PASS", "ok", 1
            ),
            "check_known_formula_regressions": CalculationCheckResult(
                "CALC-510-C", "regression", "PASS", "ok", 1
            ),
            "check_any_valid_scope": CalculationCheckResult(
                "CALC-501-C", "validity", "PASS", "ok", 1
            ),
            "check_route_score_recalculation": CalculationCheckResult(
                "CALC-506-A", "score", "FAIL", "one failure", 1
            ),
            "check_rank_stability": CalculationCheckResult(
                "CALC-507-A", "rank", "BLOCKED_DOC", "doc gap", 1
            ),
        }
        for name, result in checks.items():
            monkeypatch.setattr(service, name, lambda *args, value=result: value)

        report = service.run_r0_checks()

        assert report.status == "FAIL"
        assert report.market_scope == "all"
        assert report.route_profile_key == "default"
        assert len(report.checks) == 5
        assert report.results == report.checks
        assert report.checks[0].findings == ()
        assert report.checks[0].issues == report.checks[0].findings
        assert mcp.initialized is True
        assert mcp.notified is True

    def test_case_diagnostic_groups_codes_and_bounds_factor_samples(self) -> None:
        repeated = tuple(
            CalculationIssue(
                "BLOCKED_DATA_PRECONDITION",
                "METRIC_IDENTITY_MISSING",
                "missing pair identity",
                f"sub_factor:{factor_id}",
            )
            for factor_id in range(1, 101)
        )
        aggregated = CalculationIssue(
            "BLOCKED_DATA_PRECONDITION",
            "METRIC_FORMULA_VERSION_LINK_MISSING",
            "batch metric has no formula link",
            evidence={
                "count": 344,
                "factor_ref_count": 172,
                "factor_ref_samples": [f"sub_factor:{factor_id}" for factor_id in range(201, 206)],
            },
        )
        check = CalculationCheckResult(
            "CALC-510-A",
            "formula",
            "BLOCKED_DATA_PRECONDITION",
            "blocked",
            477,
            findings=(*repeated, aggregated),
        )
        report = Factor4CalculationReport(
            batch_uid="batch-6",
            captured_at=datetime(2026, 9, 4),
            market_scope="all",
            route_profile_key="default",
            status="BLOCKED_DATA_PRECONDITION",
            checks=(check,),
        )

        rendered = _diagnostic(report, check)

        assert "METRIC_IDENTITY_MISSING count=100" in rendered
        assert "METRIC_FORMULA_VERSION_LINK_MISSING count=344" in rendered
        assert rendered.count("sub_factor:") == 10
        assert "sub_factor:99" not in rendered
        assert len(rendered) < 1000

    def test_case_diagnostic_keeps_safe_block_reason_without_secrets(self) -> None:
        """诊断应保留配额/配置路径，但不复制响应正文或 Token/Session。"""

        check = CalculationCheckResult(
            "CALC-510-A",
            "formula",
            "BLOCKED_DATA_PRECONDITION",
            "blocked",
            1,
            findings=(
                CalculationIssue(
                    "BLOCKED_DATA_PRECONDITION",
                    "MCP_EXECUTABLE_DETAIL_MISSING",
                    "MCP detail unavailable",
                    evidence={
                        "reason": "EXPORT_BUDGET_EXCEEDED",
                        "path": "factor_get_details_batch",
                        "response_body": "server body naf_mcp_should-not-appear",
                        "session_id": "session-secret-value-123456",
                    },
                ),
                CalculationIssue(
                    "BLOCKED_DATA_PRECONDITION",
                    "MCP_EXECUTABLE_DETAIL_MISSING",
                    "MCP detail unavailable",
                    evidence={"reason": "Bearer naf_mcp_another-secret"},
                ),
            ),
        )
        report = Factor4CalculationReport(
            batch_uid="batch-6",
            captured_at=datetime(2026, 9, 4),
            market_scope="all",
            route_profile_key="default",
            status="BLOCKED_DATA_PRECONDITION",
            checks=(check,),
        )

        rendered = _diagnostic(report, check)

        assert "EXPORT_BUDGET_EXCEEDED" in rendered
        assert "factor_get_details_batch" in rendered
        assert "response_body" not in rendered
        assert "naf_mcp_should-not-appear" not in rendered
        assert "naf_mcp_another-secret" not in rendered
        assert "session-secret-value-123456" not in rendered
