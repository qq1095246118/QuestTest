"""Cross-endpoint temporal and immutable-read contracts from historical probes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import uuid4

from api.factor4_formula_api import Factor4FormulaAPI
from api.factor4_summary_api import Factor4SummaryAPI
from api.factor_data_mcp_api import MCPJSONRPCError
from db.factor4_formula_repository import FormulaCatalogSnapshot
from db.factor4_publication_repository import PublicationHistory
from db.factor4_read_repository import DailyReadSnapshot, EnvironmentMetricSample, SummarySample
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadPrecondition, read_tool_body, read_tool_page, visible_daily_rows
from service.factor4_recommendation_service import Factor4RecommendationService, lifecycle_time
from service.factor4_summary_service import summary_scope


class Factor4CrossReadService:
    """Coordinate existing endpoint services without embedding SQL or transport details."""

    def __init__(self, read: Factor4ReadService) -> None:
        """Accept initialized read service; construction performs no I/O."""
        self.read = read

    def check_daily_availability(self, snapshot: DailyReadSnapshot, kind: str, offset: int) -> ReadCheck:
        """Reconcile exact-date first publication before/equal/after available_at with DB.

        Unlike revision replacement this requires only one real row. Missing rows block;
        invalid kind/offset raises ValueError; endpoint/contract errors propagate.
        """
        if kind not in {"fact", "forecast"} or offset not in {-1, 0, 1}:
            raise ValueError("unsupported daily availability boundary")
        rows = [r for r in snapshot.rows if r["label_kind"] == kind]
        if not rows:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: daily label kind missing")
        row = min(rows, key=lambda r: lifecycle_time(r["available_at"], database=True))
        as_of = lifecycle_time(row["available_at"], database=True) + timedelta(microseconds=offset)
        day = str(row["environment_date"])
        page = self.read.daily_pages(snapshot, kind, as_of=as_of, environment_date=day)  # type: ignore[arg-type]
        return self.read.check_daily(snapshot, kind, page, as_of=as_of, environment_date=day)  # type: ignore[arg-type]

    def check_forecast_recommendation_boundary(self, daily: DailyReadSnapshot, history: PublicationHistory, offset: int) -> ReadCheck:
        """At three recent ready forecast availability points, check each publication partition.

        Selects the DB-visible highest revision per date before choosing a ready
        forecast. Returned revision/label/availability must match that independent
        row, and routes are checked against its label rather than response claims.
        Missing real forecasts or partitions block; invalid offset ValueError, I/O propagates.
        """
        if offset not in {-1, 0, 1}:
            raise ValueError("unsupported forecast availability boundary")
        ready = [r for r in daily.rows if r["label_kind"] == "forecast" and r["label_status"] == "ready"]
        partitions = sorted({(b["market_scope"], b["route_profile_key"]) for b in history.batches})
        if not ready or not partitions:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: ready forecast/publication partition missing")
        ready.sort(key=lambda r: lifecycle_time(r["available_at"], database=True), reverse=True)
        service = Factor4RecommendationService(self.read.api)
        issues, count = [], 0
        for row in ready[:3]:
            as_of = lifecycle_time(row["available_at"], database=True) + timedelta(microseconds=offset)
            visible = [candidate for candidate in visible_daily_rows(daily, "forecast", as_of=as_of)
                       if candidate.get("label_status") == "ready"]
            expected_forecast = visible[0] if visible else None
            for market, profile in partitions:
                issues.extend(service.check_publication_at(history, market, profile, as_of).issues)
                page = read_tool_page(self.read.api.recommendations(market, profile, as_of=as_of.isoformat()))
                forecast = page.data.get("forecast")
                if expected_forecast is None:
                    if forecast is not None or page.items:
                        issues.append("recommendation:forecast_not_yet_available")
                    if page.data.get("status") != "no_recommendation" or page.data.get("reason_code") not in {"ACTIVE_FORECAST_NOT_FOUND", "ACTIVE_PUBLICATION_NOT_FOUND"}:
                        issues.append("recommendation:no_forecast_contract")
                elif not isinstance(forecast, Mapping):
                    issues.append("recommendation:visible_forecast_missing")
                else:
                    if any(str(forecast.get(key)) != str(expected_forecast.get(key))
                           for key in ("environment_date", "revision", "label_code", "label_status")):
                        issues.append("recommendation:forecast_selection")
                    try:
                        if lifecycle_time(forecast.get("available_at")) != lifecycle_time(expected_forecast["available_at"], database=True):
                            issues.append("recommendation:forecast_available_at")
                    except (TypeError, ValueError):
                        issues.append("recommendation:forecast_time_invalid")
                    publication = page.data.get("publication") or {}
                    matching_batches = {b["id"] for b in history.batches if b["publication_uid"] == publication.get("publication_uid")}
                    eligible_refs = {r["factor_ref"] for r in history.routes if r["eval_batch_id"] in matching_batches
                                     and r["label_code"] == expected_forecast["label_code"] and r["is_eligible"]}
                    if any(item.get("factor_ref") not in eligible_refs
                           or ("label_code" in item and item["label_code"] != expected_forecast["label_code"]) for item in page.items):
                        issues.append("recommendation:route_forecast_label")
                count += 1
        return ReadCheck(count, tuple(dict.fromkeys(issues)))

    def check_environment_missing_selector(self, sample: EnvironmentMetricSample, variant: str) -> ReadCheck:
        """Deferred unknown batch/scope selection must not leak another publication's data.

        Unknown variant raises ValueError; only structured business absence is accepted,
        never a network failure. No data is written.
        """
        if variant not in {"batch", "scope", "recommendation_scope"}:
            raise ValueError("unsupported environment selector")
        missing, batch = "questtest-missing-" + uuid4().hex, sample.batch
        try:
            if variant == "recommendation_scope":
                response = self.read.api.recommendations(missing, batch["route_profile_key"], as_of="2026-01-01T00:00:00+00:00")
            else:
                response = self.read.api.environment_metrics(sample.factor_ref, missing if variant == "scope" else batch["market_scope"],
                        batch["route_profile_key"], batch_uid=missing if variant == "batch" else None)
        except MCPJSONRPCError as exc:
            if exc.code not in {-32601, -32602}:
                raise
            return ReadCheck(1, ())
        if response.is_tool_error:
            body = read_tool_body(response)
            if body.get("error", {}).get("code") in {"NOT_FOUND", "BATCH_NOT_FOUND", "ACTIVE_PUBLICATION_NOT_FOUND", "INVALID_ARGUMENT", "INVALID_ARGUMENTS", "VALIDATION_ERROR"}:
                return ReadCheck(1, ())
            read_tool_page(response)
        page = read_tool_page(response)
        issues = []
        if page.items or page.data.get("publication") is not None:
            issues.append("environment:unknown_selector_fallback")
        if variant == "recommendation_scope" and (page.data.get("returned_count") != 0 or page.data.get("status") != "no_recommendation"):
            issues.append("environment:unknown_scope_empty_contract")
        return ReadCheck(1, tuple(issues))


def check_completed_formula_replay(api: Factor4FormulaAPI, snapshot: FormulaCatalogSnapshot) -> ReadCheck:
    """Select actual completed direct evidence and make three uncached exact-identity reads.

    Compare immutable expression/hash/required fields to DB and full business payload
    between requests. Missing evidence blocks; transport/contract exceptions propagate.
    """
    candidates = [row for row in snapshot.evidence if row.get("run_status") == "completed" and row.get("calculation_mode") == "direct" and row.get("expression")]
    if not candidates:
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: completed direct formula evidence missing")
    row = max(candidates, key=lambda r: int(r["id"]))
    pages = [read_tool_page(api.formula(row)).data for _ in range(3)]
    issues: list[str] = []
    ref = f"{'sub_factor' if row['is_sub_factor_id'] else 'factor'}:{row['factor_id']}"
    for data in pages:
        if data.get("factor_ref") != ref:
            issues.append("formula:replay_factor_ref")
        for key in ("run_id", "formula_hash", "formula_version", "expression", "required_fields"):
            expected = row.get(key)
            if key == "required_fields" and isinstance(expected, str):
                expected = json.loads(expected)
            if data.get(key) != expected:
                issues.append("formula:replay_database:" + key)
    if any(data != pages[0] for data in pages[1:]):
        issues.append("formula:immutable_replay_changed")
    return ReadCheck(3, tuple(dict.fromkeys(issues)))


def check_research_library_warning(api: Factor4SummaryAPI, sample: SummarySample, *, future: bool) -> ReadCheck:
    """Deferred warning contract: current library state must not masquerade as PIT history.

    Query a real research partition at current/future as-of; missing library projection
    blocks explicitly. Network/contract failures propagate, and no future IDs are rejected
    merely because they appeared after this client's DB snapshot.
    """
    as_of = sample.as_of + timedelta(days=3650 if future else 0)
    page = read_tool_page(api.research_search(summary_scope(sample.scope), kind=sample.kind, as_of=as_of.isoformat(), limit=5))
    if not any("library_status" in row for row in page.items):
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: research response has no current library status projection")
    warnings = page.meta.get("warnings") or []
    codes = {item if isinstance(item, str) else item.get("code") for item in warnings if isinstance(item, (str, dict))}
    required = "CURRENT_LIBRARY_STATUS_NOT_POINT_IN_TIME"
    return ReadCheck(len(page.items), () if required in codes else ("research:current_library_status_pit_warning_missing",))
