"""Recommendation PIT and route reconciliation using final persisted results."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from api.factor4_read_api import Factor4ReadAPI
from db.factor4_publication_repository import PublicationHistory
from db.factor4_read_repository import DailyReadSnapshot
from service.factor4_read_service import (
    LABELS, ReadCheck, ReadContractError, ReadPrecondition, compare_rows,
    ToolPage, read_tool_page, visible_daily_rows,
)


def lifecycle_time(value: Any, *, database: bool = False) -> datetime:
    """Normalize lifecycle time to UTC; reject naive API values with ValueError."""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if not database:
            raise ValueError("API lifecycle timestamp requires timezone")
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed.astimezone(timezone.utc)


def visible_publication(
    history: PublicationHistory, market_scope: str, profile: str, as_of: datetime,
) -> dict[str, Any] | None:
    """Select newest published version within one partition; return None before first publication."""
    candidates = [b for b in history.batches if b["market_scope"] == market_scope
                  and b["route_profile_key"] == profile
                  and lifecycle_time(b["published_at"], database=True) <= as_of]
    return max(candidates, key=lambda b: (lifecycle_time(b["published_at"], database=True), b["id"]), default=None)


def check_forecast_probabilities(rows: tuple[dict[str, Any], ...]) -> ReadCheck:
    """Check ready forecasts' six probabilities, finite domain and normalization; no I/O.

    A missing ready forecast is a data precondition, not a vacuous pass. Returned
    issues contain only numeric row IDs and local field names.
    """
    ready = [r for r in rows if r.get("label_kind") == "forecast" and r.get("label_status") == "ready"]
    if not ready:
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no ready forecast")
    issues: list[str] = []
    for row in ready:
        prefix = f"forecast:id={row.get('id')}:"
        probabilities = row.get("probabilities")
        if row.get("label_code") not in LABELS:
            issues.append(prefix + "label")
        if not isinstance(probabilities, dict) or set(probabilities) != set(LABELS):
            issues.append(prefix + "probability_keys")
            continue
        try:
            if any(isinstance(v, bool) or v is None for v in probabilities.values()):
                raise ValueError("not numeric")
            values = [Decimal(str(v)) for v in probabilities.values()]
            if any(not v.is_finite() or not 0 <= v <= 1 for v in values):
                raise ValueError("out of domain")
            if abs(sum(values) - 1) > Decimal("0.000001"):
                issues.append(prefix + "probability_sum")
        except (ValueError, InvalidOperation):
            issues.append(prefix + "probability_domain")
    return ReadCheck(len(ready), tuple(issues))


def compare_recommendation_page(
    page: ToolPage, batch: Mapping[str, Any], routes: tuple[dict[str, Any], ...],
    forecast: Mapping[str, Any] | None, *, limit: int,
) -> ReadCheck:
    """Compare one actual recommendation page with its DB publication and forecast.

    This shared projection check sends no requests and never substitutes a public
    label for the independent forecast. Invalid timestamp values raise ValueError;
    business differences are returned without response bodies or credentials.
    """
    issues: list[str] = []
    actual_forecast = page.data.get("forecast")
    if forecast is None:
        if actual_forecast is not None or page.items or page.data.get("status") != "no_recommendation" or page.data.get("reason_code") != "ACTIVE_FORECAST_NOT_FOUND":
            issues.append("recommendation:no_forecast_contract")
        return ReadCheck(1, tuple(issues))
    if not isinstance(actual_forecast, Mapping) or any(
        str(actual_forecast.get(key)) != str(forecast.get(key))
        for key in ("environment_date", "label_code", "revision", "label_status")
    ):
        issues.append("recommendation:forecast_selection")
    elif lifecycle_time(actual_forecast.get("available_at")) != lifecycle_time(forecast["available_at"], database=True):
        issues.append("recommendation:forecast_available_at")
    publication = page.data.get("publication")
    if not isinstance(publication, Mapping) or publication.get("publication_uid") != batch["publication_uid"]:
        issues.append("recommendation:active_publication_identity")
    eligible = [row for row in routes if row["eval_batch_id"] == batch["id"]
                and row["is_active"] and row["is_eligible"] and row["label_kind"] == "fact"
                and row["label_code"] == forecast["label_code"]]
    eligible.sort(key=lambda row: (row["rank_no"], row["id"]))
    expected = eligible[:limit]
    fields = ("factor_ref", "factor_type", "factor_id", "factor_version", "rank_no",
              "routing_score", "confidence", "time_series_score", "cross_sectional_score", "score_rule_version")
    expected_keys = {(row["factor_ref"], row["factor_version"]): row for row in expected}
    actual_keys = [(row.get("factor_ref"), row.get("factor_version")) for row in page.items]
    if len(set(actual_keys)) != len(actual_keys) or set(actual_keys) != set(expected_keys):
        issues.append("recommendation:factor_membership")
    comparable = [{**row, "id": expected_keys[key]["id"]} for row, key in zip(page.items, actual_keys, strict=True)
                  if key in expected_keys]
    issues.extend(compare_rows(comparable, expected, fields).issues)
    if actual_keys != [(row["factor_ref"], row["factor_version"]) for row in expected]:
        issues.append("recommendation:route_order")
    if page.data.get("returned_count") != len(expected):
        issues.append("recommendation:returned_count")
    if not eligible and (page.data.get("status") != "no_recommendation" or page.data.get("reason_code") != "NO_ELIGIBLE_FACTOR"):
        issues.append("recommendation:no_eligible_reason")
    if eligible and page.data.get("status") != "ready":
        issues.append("recommendation:ready_status")
    return ReadCheck(max(1, len(expected)), tuple(issues))


class Factor4RecommendationService:
    """Read recommendations at fixed instants and reconcile final publication identity."""

    def __init__(self, api: Factor4ReadAPI) -> None:
        """Accept initialized read API; no requests are sent by construction."""
        self.api = api

    def check_publication_boundary(self, history: PublicationHistory, offset: int) -> ReadCheck:
        """Reconcile before/equal/after latest publication in every partition; errors propagate.

        Offsets are microseconds. Historical inactive records are still valid PIT
        candidates; no forecast or no eligible routes is not an empty success shortcut.
        """
        if offset not in {-1, 0, 1}:
            raise ValueError("boundary offset must be -1, 0, 1")
        if not history.batches:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no published batch")
        issues: list[str] = []
        partitions = {(b["market_scope"], b["route_profile_key"]) for b in history.batches}
        for market, profile in sorted(partitions):
            target = visible_publication(history, market, profile, history.as_of)
            if target is None:
                raise ReadContractError("persisted publication lies after DB clock")
            as_of = lifecycle_time(target["published_at"], database=True) + timedelta(microseconds=offset)
            check = self.check_publication_at(history, market, profile, as_of)
            issues.extend(check.issues)
        return ReadCheck(len(partitions), tuple(issues), {"offset_microseconds": offset})

    def check_publication_at(
        self, history: PublicationHistory, market_scope: str, profile: str, as_of: datetime,
    ) -> ReadCheck:
        """Compare response publication to latest DB-visible version; malformed responses fail."""
        page = read_tool_page(self.api.recommendations(market_scope, profile, as_of=as_of.isoformat()))
        expected = visible_publication(history, market_scope, profile, as_of)
        actual = page.data.get("publication")
        issues: list[str] = []
        if expected is None:
            if actual is not None or page.items:
                issues.append("recommendation:future_or_foreign_publication")
            if page.data.get("status") != "no_recommendation":
                issues.append("recommendation:missing_publication_status")
            if page.data.get("reason_code") not in {"ACTIVE_PUBLICATION_NOT_FOUND", "ACTIVE_FORECAST_NOT_FOUND"}:
                issues.append("recommendation:missing_publication_reason")
        elif not isinstance(actual, Mapping):
            issues.append("recommendation:visible_publication_missing")
        else:
            for key in ("batch_uid", "publication_uid", "market_scope", "route_profile_key", "publish_version"):
                if actual.get(key) != expected[key]:
                    issues.append(f"recommendation:publication_field={key}")
            try:
                published = lifecycle_time(actual.get("published_at"))
                if published != lifecycle_time(expected["published_at"], database=True) or published > as_of:
                    issues.append("recommendation:publication_time")
            except (TypeError, ValueError):
                issues.append("recommendation:publication_time_invalid")
        forecast = page.data.get("forecast")
        if isinstance(forecast, Mapping):
            try:
                if ("label_kind" in forecast and forecast["label_kind"] != "forecast") or lifecycle_time(forecast.get("available_at")) > as_of:
                    issues.append("recommendation:future_or_fact_forecast")
            except (TypeError, ValueError):
                issues.append("recommendation:forecast_time_invalid")
        elif page.items:
            issues.append("recommendation:items_without_forecast")
        if page.data.get("returned_count") != len(page.items) or len(page.items) > 20:
            issues.append("recommendation:count")
        return ReadCheck(1, tuple(issues))

    def check_current_routes(
        self, history: PublicationHistory, daily: DailyReadSnapshot, *, limit: int,
    ) -> ReadCheck:
        """Reconcile each active partition's recommended rows/ranks/scores with final DB routes.

        Fixed as-of repeats must return the same business data. This is serial replay,
        not a performance/concurrency test and does not recompute raw factor returns.
        """
        if not 1 <= limit <= 200:
            raise ValueError("limit outside recommendation contract")
        active = [b for b in history.batches if b["is_active"]]
        if not active:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no active publication")
        as_of = min(history.as_of, daily.as_of)
        forecasts = [r for r in visible_daily_rows(daily, "forecast", as_of=as_of)
                     if r.get("label_status") == "ready"]
        expected_forecast = forecasts[0] if forecasts else None
        issues: list[str] = []
        for batch in active:
            args = (batch["market_scope"], batch["route_profile_key"])
            page = read_tool_page(self.api.recommendations(*args, as_of=as_of.isoformat(), limit=limit))
            replay = read_tool_page(self.api.recommendations(*args, as_of=as_of.isoformat(), limit=limit))
            if page.data != replay.data:
                issues.append("recommendation:fixed_asof_replay_changed")
            issues.extend(compare_recommendation_page(page, batch, history.routes, expected_forecast, limit=limit).issues)
        return ReadCheck(len(active), tuple(issues))
