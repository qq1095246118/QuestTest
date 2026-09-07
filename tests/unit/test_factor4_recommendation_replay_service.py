"""Offline counterexamples for the actual recommendation-driven read sequence."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any

import pytest

from api.factor4_read_api import Factor4ReadAPI
from api.factor_data_mcp_api import MCPResponse
from db.factor4_calculation_repository import CalculationAuditSnapshot, CalculationRepositoryError, PublishedRouteSnapshot
from db.factor4_read_repository import DailyReadSnapshot, EnvironmentMetricSample
from service.factor4_read_service import Factor4ReadService, ReadCheck, _METRIC_FIELDS
from service.factor4_recommendation_service import lifecycle_time
from service.factor4_recommendation_replay_service import Factor4RecommendationReplayService
from tests.unit.test_factor4_calculation_service import _formula, _metric, _route, _snapshot

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError("unsupported fixture value")


def _response(data: dict[str, Any]) -> MCPResponse:
    body = json.loads(json.dumps({"data": data, "meta": {"truncated": False, "next_cursor": None}}, default=_json_value))
    return MCPResponse(200, "application/json", {"result": {"structuredContent": body,
        "content": [{"type": "text", "text": json.dumps(body)}], "isError": False}}, "2025-06-18")


def _bound(kind: str = "sub_factor", market: str = "all", profile: str = "default", identifier: int = 6) -> CalculationAuditSnapshot:
    ref = f"{kind}:10"
    metric = replace(_metric(1, ref, "time_series", formula_links=True), factor_type=kind,
                     eval_batch_id=identifier, market_scope=market)
    formula = replace(_formula(ref), factor_type=kind, is_sub_factor_id=kind == "sub_factor",
                      calculation_mode="child_aggregate" if kind == "factor" else "direct")
    metric = replace(metric, metric_identity={**metric.metric_identity, "calculation_mode": formula.calculation_mode})
    route = replace(_route(1, ref, 1, 1, "80"), factor_type=kind, market_scope=market,
                    route_profile_key=profile, eval_batch_id=identifier,
                    publication_uid=f"publication-{identifier}")
    snapshot = _snapshot(metrics=(metric,), formulas=(formula,), routes=(route,))
    return replace(snapshot, batch=replace(snapshot.batch, id=identifier, batch_uid=f"batch-{identifier}",
        publication_uid=f"publication-{identifier}", market_scope=market, route_profile_key=profile))


def _daily() -> DailyReadSnapshot:
    return DailyReadSnapshot(NOW, ({"id": 10, "environment_date": "2026-09-07", "label_kind": "forecast",
        "label_status": "ready", "label_code": "WIDE_RANGE", "revision": 2, "available_at": NOW,
        "is_current": 1},))


class ReplayFixture:
    """Record real API adapters' tool calls against isolated persisted-result fixtures."""

    def __init__(self, snapshots: tuple[CalculationAuditSnapshot, ...]) -> None:
        """Prepare independent expected and public dictionaries; no I/O."""
        self.snapshots = snapshots
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.discovery: list[dict[str, Any]] = []
        self.pointer_calls: dict[tuple[str, str], int] = {}
        self.drift = False
        self.replay_error = False
        self.metric_sample_error = False
        self.pages: dict[tuple[str, str], dict[str, Any]] = {}
        self.samples: dict[tuple[str, str], EnvironmentMetricSample] = {}
        self.metric_pages: dict[tuple[str, str], dict[str, Any]] = {}
        self.formula_pages: dict[str, dict[str, Any]] = {}
        forecast = {key: value for key, value in _daily().rows[0].items() if key not in {"id", "label_kind", "is_current"}}
        for snapshot in snapshots:
            batch = asdict(snapshot.batch)
            public_batch = {**batch, **{key: lifecycle_time(batch[key], database=True).isoformat()
                                      for key in ("as_of_time", "published_at")}}
            routes = tuple(asdict(route) for route in snapshot.routes)
            projection = ({key: value for key, value in route.items() if key in {
                "factor_ref", "factor_type", "factor_id", "factor_version", "rank_no", "routing_score", "confidence",
                "time_series_score", "cross_sectional_score", "score_rule_version"}} for route in routes)
            self.pages[(batch["market_scope"], batch["route_profile_key"])] = {
                "publication": public_batch, "forecast": forecast, "items": list(projection), "status": "ready", "returned_count": len(routes),
            }
            for route in snapshot.routes:
                rows = []
                for metric in snapshot.evaluation_metrics:
                    if metric.factor_ref != route.factor_ref:
                        continue
                    raw = asdict(metric)
                    raw.update(total_sample_count=100, valid_sample_count=100)
                    raw["metric_payload"] = deepcopy({**(metric.metric_payload or {}), "metric_identity": metric.metric_identity})
                    rows.append({key: raw.get(key) for key in _METRIC_FIELDS})
                key = (batch["batch_uid"], route.factor_ref)
                self.samples[key] = EnvironmentMetricSample(batch, route.factor_ref, tuple(rows), routes)
                self.metric_pages[key] = {"batch": deepcopy(public_batch), "factor_ref": route.factor_ref,
                                          "items": deepcopy(rows), "returned_count": len(rows)}
            for formula in snapshot.formula_evidence:
                self.formula_pages[formula.factor_ref] = {"factor_ref": formula.factor_ref, "run_id": formula.run_id,
                    "formula_hash": formula.formula_hash, "formula_version": formula.formula_version,
                    "source_detail_id": formula.source_detail_id, "expression": formula.expression,
                    "required_fields": list(formula.required_fields), "metric_identity": {
                        key: getattr(formula, key) for key in ("calculation_mode", "factor_bar_interval", "factor_window_bars",
                                                              "return_bar_interval", "forward_return_bars")}}

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> MCPResponse:
        """Use the requested immutable selectors, not defaults; unknown tools raise AssertionError."""
        self.calls.append((tool, deepcopy(arguments)))
        if tool == "environment_get_recommendations":
            if self.replay_error and sum(name == tool for name, _ in self.calls) > 1:
                body = {"error": {"code": "SERVICE_UNAVAILABLE"}}
                return MCPResponse(200, None, {"result": {"isError": True, "structuredContent": body,
                    "content": [{"type": "text", "text": json.dumps(body)}]}}, None)
            return _response(self.pages[(arguments["market_scope"], arguments["route_profile_key"])])
        if tool == "factor_get_environment_metrics":
            return _response(self.metric_pages[(arguments["batch_uid"], arguments["factor_ref"])])
        if tool == "factor_get_formula":
            return _response(self.formula_pages[arguments["factor_ref"]])
        raise AssertionError("unexpected tool")

    def get_formula(self, factor_ref: str, **arguments: Any) -> MCPResponse:
        """Forward formula adapter arguments to the same observable tool-call sequence."""
        return self.call_tool("factor_get_formula", {"factor_ref": factor_ref, **arguments})

    def metric_sample(self, kind: str, *, batch_uid: str, factor_ref: str) -> EnvironmentMetricSample | None:
        """Return the exact requested DB sample, recording that selection follows the public ref."""
        self.discovery.append({"kind": kind, "batch_uid": batch_uid, "factor_ref": factor_ref})
        if self.metric_sample_error:
            raise CalculationRepositoryError("metric_sample", "offline controlled missing DB sample")
        return self.samples.get((batch_uid, factor_ref))

    def read_published_route_snapshot(self, market: str, profile: str) -> PublishedRouteSnapshot:
        """Return the same publication, or a controlled later pointer on the second read."""
        snapshot = next(row for row in self.snapshots if (row.batch.market_scope, row.batch.route_profile_key) == (market, profile))
        key = (market, profile)
        self.pointer_calls[key] = self.pointer_calls.get(key, 0) + 1
        batch = snapshot.batch
        return PublishedRouteSnapshot(NOW, batch.id, "changed" if self.drift and self.pointer_calls[key] > 1 else batch.publication_uid,
                                      batch.publish_version, market, profile, ())

    def check(self, kind: str = "sub_factor") -> ReadCheck:
        """Execute real Service/API orchestration against these offline fixtures; errors propagate."""
        return Factor4RecommendationReplayService(Factor4ReadService(Factor4ReadAPI(self)), self, self).check_kind(
            self.snapshots, _daily(), kind)


@pytest.mark.parametrize("kind", ["factor", "sub_factor"])
def test_recommendation_actual_ref_drives_exact_batch_and_formula_run_mode_windows(kind: str) -> None:
    fixture = ReplayFixture((_bound(kind),))
    result = fixture.check(kind)
    assert not result.issues and not result.evidence["blocked"]
    assert result.checked_count == 1
    assert [tool for tool, _ in fixture.calls] == ["environment_get_recommendations", "factor_get_environment_metrics",
                                                 "factor_get_formula", "environment_get_recommendations"]
    metric, formula = fixture.calls[1][1], fixture.calls[2][1]
    assert fixture.discovery == [{"kind": kind, "batch_uid": "batch-6", "factor_ref": f"{kind}:10"}]
    assert metric["batch_uid"] == "batch-6" and metric["factor_ref"] == f"{kind}:10"
    assert metric["label_code"] == "WIDE_RANGE" and "as_of" not in metric
    assert formula["run_id"] == "run-10" and formula["factor_window_bars"] == "24H"
    assert formula["calculation_mode"] == ("child_aggregate" if kind == "factor" else "direct")
    assert all(args["as_of"] == NOW.isoformat() for tool, args in fixture.calls if tool != "factor_get_environment_metrics")


@pytest.mark.parametrize("field", ["run_id", "formula_version", "formula_hash", "expression", "factor_ref"])
def test_default_latest_formula_fallback_is_rejected(field: str) -> None:
    fixture = ReplayFixture((_bound(),))
    fixture.formula_pages["sub_factor:10"][field] = "new-latest"
    assert fixture.check().issues


@pytest.mark.parametrize("field", ["id", "factor_version", "eval_batch_id", "mean_ic"])
def test_metric_lookup_cannot_substitute_other_batch_version_or_value(field: str) -> None:
    fixture = ReplayFixture((_bound(),))
    fixture.metric_pages[("batch-6", "sub_factor:10")]["items"][0][field] = 999
    assert fixture.check().issues


def test_recommendation_wrong_version_cannot_choose_a_different_formula() -> None:
    fixture = ReplayFixture((_bound(),))
    fixture.pages[("all", "default")]["items"][0]["factor_version"] = "wrong"
    assert fixture.check().issues
    assert not [call for call in fixture.calls if call[0] == "factor_get_formula"]


def test_publication_pointer_drift_blocks_even_when_all_returned_values_match() -> None:
    fixture = ReplayFixture((_bound(),))
    fixture.drift = True
    result = fixture.check()
    assert not result.issues
    assert any("publication_drift" in reason for reason in result.evidence["blocked"])


def test_missing_formula_evidence_does_not_reuse_mutable_latest_formula() -> None:
    fixture = ReplayFixture((replace(_bound(), formula_evidence=()),))
    result = fixture.check()
    assert not result.issues and result.evidence["blocked"]
    assert any(tool == "factor_get_environment_metrics" for tool, _ in fixture.calls)
    assert not any(tool == "factor_get_formula" for tool, _ in fixture.calls)


def test_other_partition_missing_evidence_does_not_mask_confirmed_formula_failure() -> None:
    first = _bound()
    second = replace(_bound(profile="other", market="spot", identifier=7), formula_evidence=())
    fixture = ReplayFixture((first, second))
    fixture.formula_pages["sub_factor:10"]["run_id"] = "wrong"
    result = fixture.check()
    assert result.issues and result.evidence["blocked"]
    assert result.evidence["partition_count"] == 2
    assert {args["market_scope"] for tool, args in fixture.calls if tool == "environment_get_recommendations"} == {"all", "spot"}


def test_replay_dependency_block_preserves_prior_formula_failure() -> None:
    fixture = ReplayFixture((_bound(),))
    fixture.formula_pages["sub_factor:10"]["formula_hash"] = "wrong"
    fixture.replay_error = True
    result = fixture.check()
    assert result.issues and result.evidence["blocked"]


def test_kind_not_recommended_is_not_claimed_as_live_chain_coverage() -> None:
    fixture = ReplayFixture((_bound(),))
    result = fixture.check("factor")
    assert result.checked_count == 0 and result.evidence["blocked"]
    assert result.evidence["kind_absent_partitions"]


def test_one_partition_without_requested_kind_blocks_even_when_another_replays() -> None:
    fixture = ReplayFixture((_bound(), _bound("factor", market="spot", profile="other", identifier=7)))
    result = fixture.check()
    assert not result.issues and result.checked_count == 1
    assert result.evidence["kind_absent_partitions"] == ("batch=7:sub_factor",)
    assert "batch=7:recommended_kind_sample_absent=sub_factor" in result.evidence["blocked"]


def test_metric_repository_failure_preserves_prior_route_identity_failure() -> None:
    snapshot = _bound()
    route = replace(snapshot.routes[0], eval_batch_id=999)
    fixture = ReplayFixture((replace(snapshot, routes=(route,)),))
    fixture.metric_sample_error = True
    result = fixture.check()
    assert any("route_publication_field=eval_batch_id" in issue for issue in result.issues)
    assert result.evidence["blocked"] and result.checked_count == 0
    assert any(tool == "factor_get_formula" for tool, _ in fixture.calls)


def test_every_discovered_market_profile_runs_the_actual_replay() -> None:
    fixture = ReplayFixture((_bound(), _bound(market="spot", profile="momentum", identifier=7)))
    result = fixture.check()
    assert not result.issues and not result.evidence["blocked"]
    assert result.checked_count == 2 and result.evidence["partition_count"] == 2
    assert {args["batch_uid"] for tool, args in fixture.calls if tool == "factor_get_environment_metrics"} == {"batch-6", "batch-7"}


def test_wrong_initial_publication_is_not_silently_used_for_followup_reads() -> None:
    fixture = ReplayFixture((_bound(),))
    fixture.pages[("all", "default")]["publication"]["publication_uid"] = "other"
    result = fixture.check()
    assert result.issues
    assert not [tool for tool, _ in fixture.calls if tool != "environment_get_recommendations"]


def test_formula_mismatch_still_fails_when_publication_changes_afterward() -> None:
    fixture = ReplayFixture((_bound(),))
    fixture.drift = True
    fixture.formula_pages["sub_factor:10"]["formula_version"] = "wrong"
    result = fixture.check()
    assert result.issues and result.evidence["blocked"]


def test_same_metric_id_with_changed_formula_binding_is_not_a_coherent_chain() -> None:
    fixture = ReplayFixture((_bound(),))
    sample = fixture.samples[("batch-6", "sub_factor:10")]
    sample.metrics[0]["metric_payload"]["metric_identity"]["run_id"] = "changed"
    fixture.metric_pages[("batch-6", "sub_factor:10")]["items"] = deepcopy(list(sample.metrics))
    result = fixture.check()
    assert not result.issues
    assert result.checked_count == 0
    assert any("formula_link_changed_between_db_reads" in reason for reason in result.evidence["blocked"])


def test_changed_metric_values_in_both_new_db_and_mcp_reads_do_not_prove_original_snapshot() -> None:
    fixture = ReplayFixture((_bound(),))
    sample = fixture.samples[("batch-6", "sub_factor:10")]
    sample.metrics[0]["mean_ic"] = Decimal("0.123")
    fixture.metric_pages[("batch-6", "sub_factor:10")]["items"] = deepcopy(list(sample.metrics))
    result = fixture.check()
    assert not result.issues and result.checked_count == 0
    assert any("metric_snapshot_changed" in reason for reason in result.evidence["blocked"])


def test_legal_no_forecast_response_blocks_chain_without_inventing_publication_requirement() -> None:
    fixture = ReplayFixture((_bound(),))
    fixture.pages[("all", "default")] = {"publication": None, "forecast": None, "items": [],
        "returned_count": 0, "status": "no_recommendation", "reason_code": "ACTIVE_FORECAST_NOT_FOUND"}
    service = Factor4RecommendationReplayService(Factor4ReadService(Factor4ReadAPI(fixture)), fixture, fixture)
    result = service.check_kind(fixture.snapshots, DailyReadSnapshot(NOW, ()), "sub_factor")
    assert not result.issues and result.evidence["blocked"]
    assert not [tool for tool, _ in fixture.calls if tool != "environment_get_recommendations"]
