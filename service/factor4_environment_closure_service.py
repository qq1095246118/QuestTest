"""Read-only environment closure checks against persisted publication results.

No raw-series reconstruction is claimed: daily revisions, final TS/CS values,
frozen configuration and route evidence are the independent persisted sources.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from db.factor4_calculation_repository import CalculationAuditSnapshot, EvaluationMetric, PublishedRoute
from service.factor4_calculation_service import (
    CalculationCheckResult,
    CalculationIssue,
    _configured_route_profile_weights,
    _metric_pair_identity,
    _metric_pair_key,
    _metric_pairs,
    _same_timestamp_instant,
    _snapshot_datetime,
    _timestamp_is_at_or_before,
)
from service.factor4_result_service import ENVIRONMENT_LABELS, Factor4FinalResultService

ADMISSION_BRANCHES = ("both", "ts_only", "cs_only", "neither")
_SCOPES = ("time_series", "cross_sectional")


class Factor4EnvironmentClosureService:
    """Audit frozen environment membership and partitioned final admission."""

    @staticmethod
    def check_frozen_missing_dates(snapshot: CalculationAuditSnapshot) -> CalculationCheckResult:
        """Rebuild missing/member dates from full-range historical daily rows.

        Input must carry the repository's exact full-range query marker, not
        just selected-member history. Returns FAIL for proven PIT omissions,
        BLOCKED_DATA_PRECONDITION for missing evidence and BLOCKED_DOC when
        timestamp styles or non-ready selection semantics are undefined.
        Empty members/history are valid evidence when the entire frozen
        interval is explicitly missing. No I/O or mutation is performed.
        """
        batch = snapshot.batch
        issues: list[CalculationIssue] = []
        frozen = batch.environment_snapshot
        if not isinstance(frozen, Mapping):
            return _result("ENV-CLOSURE-MISSING-DATES", 0, [_blocked("ENV_FROZEN_MEMBERS_MISSING")])
        members_raw, missing_raw = frozen.get("members"), frozen.get("missing_dates")
        members_complete = isinstance(members_raw, list)
        missing_complete = isinstance(missing_raw, list)
        members: list[Mapping[str, Any]] = []
        member_dates: list[date] = []
        missing_dates: list[date] = []
        if not members_complete or not missing_complete:
            issues.append(_blocked("ENV_FROZEN_CALENDAR_DECLARATION_MISSING"))
        for member in members_raw if isinstance(members_raw, list) else []:
            day = _day(member.get("environment_date")) if isinstance(member, Mapping) else None
            if day is None:
                members_complete = False
                issues.append(_blocked("ENV_FROZEN_CALENDAR_DATE_INVALID"))
                continue
            members.append(member)
            member_dates.append(day)
            identifier = member.get("daily_id")
            if not _positive_int(identifier):
                issues.append(_blocked("ENV_FROZEN_MEMBER_IDENTITY_MISSING", environment_date=day.isoformat()))
        for raw_day in missing_raw if isinstance(missing_raw, list) else []:
            day = _day(raw_day)
            if day is None:
                missing_complete = False
                issues.append(_blocked("ENV_FROZEN_CALENDAR_DATE_INVALID"))
            else:
                missing_dates.append(day)
        expected = {batch.start_date + timedelta(days=i) for i in range((batch.end_date - batch.start_date).days + 1)}
        if batch.end_date < batch.start_date:
            issues.append(_fail("ENV_BATCH_CALENDAR_REVERSED", batch_id=batch.id))
        if (len(member_dates) != len(set(member_dates)) or len(missing_dates) != len(set(missing_dates))
                or set(member_dates) & set(missing_dates)):
            issues.append(_fail("ENV_FROZEN_MISSING_DATES_CONFLICT", batch_id=batch.id))
        if (set(member_dates) | set(missing_dates)) - expected:
            issues.append(_fail("ENV_FROZEN_DECLARED_DATE_OUTSIDE_BATCH", batch_id=batch.id))
        expected_range = (batch.start_date, batch.end_date, batch.label_kind)
        if not snapshot.environment_daily_history_loaded or snapshot.environment_daily_history_range != expected_range:
            issues.append(_blocked("ENV_FULL_CALENDAR_HISTORY_NOT_LOADED", batch_id=batch.id))
            return _result("ENV-CLOSURE-MISSING-DATES", 0, issues)
        cutoff = _snapshot_datetime(batch.as_of_time)
        if cutoff is None:
            issues.append(_blocked("ENV_FROZEN_AS_OF_MISSING"))
            return _result("ENV-CLOSURE-MISSING-DATES", 0, issues)
        by_day = defaultdict(list)
        for row in snapshot.environment_daily_history:
            if row.label_kind != batch.label_kind or row.environment_date not in expected:
                issues.append(_fail("ENV_FULL_CALENDAR_HISTORY_PARTITION_MISMATCH", daily_id=row.id))
                continue
            by_day[row.environment_date].append(row)
        ready_dates = 0
        unavailable_dates = 0
        for day in sorted(expected):
            visible = []
            unknown_visibility = []
            for row in by_day[day]:
                instant = _snapshot_datetime(row.available_at)
                if instant is None:
                    issues.append(_blocked("ENV_REVISION_AVAILABLE_AT_MISSING", daily_id=row.id))
                    unknown_visibility.append(row)
                    continue
                available = _timestamp_is_at_or_before(instant, cutoff)
                if available is None:
                    issues.append(_blocked_doc("ENV_REVISION_HISTORY_TIMEZONE_UNDEFINED", daily_id=row.id))
                    unknown_visibility.append(row)
                elif available:
                    visible.append(row)
            if unknown_visibility:
                known_latest = max((row.revision for row in visible), default=None)
                if known_latest is None or any(not _positive_int(row.revision) or row.revision >= known_latest
                                               for row in unknown_visibility):
                    continue
            if not visible:
                unavailable_dates += 1
                if day in member_dates:
                    issues.append(_fail("ENV_FROZEN_MEMBER_DATE_NOT_VISIBLE", environment_date=day.isoformat()))
                elif missing_complete and members_complete and day not in missing_dates:
                    issues.append(_fail("ENV_FROZEN_UNAVAILABLE_DATE_NOT_DECLARED", environment_date=day.isoformat()))
                continue
            revisions = [row.revision for row in visible]
            if len(revisions) != len(set(revisions)):
                issues.append(_fail("ENV_VISIBLE_REVISION_AMBIGUOUS", environment_date=day.isoformat()))
                continue
            latest = max(visible, key=lambda row: row.revision)
            if latest.label_status != "ready":
                issues.append(_blocked_doc("ENV_FROZEN_MISSING_DATE_SELECTION_POLICY_UNDEFINED", daily_id=latest.id))
                continue
            if latest.label_code not in ENVIRONMENT_LABELS:
                issues.append(_fail("ENV_VISIBLE_READY_LABEL_INVALID", daily_id=latest.id))
                continue
            ready_dates += 1
            if day in missing_dates:
                issues.append(_fail("ENV_FROZEN_MISSING_DATE_HAS_VISIBLE_ENVIRONMENT", daily_id=latest.id,
                                    environment_date=day.isoformat()))
            elif members_complete and day not in member_dates:
                issues.append(_fail("ENV_FROZEN_VISIBLE_DATE_OMITTED", daily_id=latest.id,
                                    environment_date=day.isoformat()))
            else:
                declared_ids = [member.get("daily_id") for member in members if _day(member.get("environment_date")) == day]
                if declared_ids and all(_positive_int(identifier) for identifier in declared_ids) and declared_ids != [latest.id]:
                    issues.append(_fail("ENV_FROZEN_REVISION_NOT_LATEST_VISIBLE", daily_id=latest.id))
        return _result("ENV-CLOSURE-MISSING-DATES", len(expected), issues,
                       verified_ready_date_count=ready_dates, verified_unavailable_date_count=unavailable_dates)

    @staticmethod
    def check_frozen_calendar(snapshot: CalculationAuditSnapshot) -> CalculationCheckResult:
        """Check inclusive batch calendar, mutually exclusive members and PIT revisions.

        Input is one consistent DB snapshot. Return failures for contradictory
        dates/identities and blocked findings for unavailable revision evidence.
        Historical revisions are grouped by date and kind, never required to
        have one label across all history. No I/O or exceptions for missing data.
        """
        issues: list[CalculationIssue] = []
        batch = snapshot.batch
        frozen = batch.environment_snapshot
        if not isinstance(frozen, Mapping):
            return _result("ENV-CLOSURE-CALENDAR", 0, [_blocked("ENV_FROZEN_MEMBERS_MISSING")])
        members = frozen.get("members")
        if not isinstance(members, list):
            return _result("ENV-CLOSURE-CALENDAR", 0, [_blocked("ENV_FROZEN_MEMBERS_MISSING")])
        expected = {batch.start_date + timedelta(days=i) for i in range((batch.end_date - batch.start_date).days + 1)}
        if batch.end_date < batch.start_date:
            issues.append(_fail("ENV_BATCH_CALENDAR_REVERSED", batch_id=batch.id))
        dates: list[date] = []
        ids: list[int] = []
        by_id = defaultdict(list)
        history = defaultdict(list)
        for row in snapshot.environment_daily:
            by_id[row.id].append(row)
        for row in snapshot.environment_daily_history:
            history[(row.environment_date, row.label_kind)].append(row)
        cutoff = _snapshot_datetime(batch.as_of_time)
        frozen_time = _snapshot_datetime(frozen.get("as_of_time"))
        if frozen_time is None or cutoff is None:
            issues.append(_blocked("ENV_FROZEN_AS_OF_MISSING"))
        elif _same_timestamp_instant(frozen_time, cutoff) is None:
            issues.append(_blocked_doc("ENV_FROZEN_AS_OF_TIMEZONE_UNDEFINED"))
        elif not _same_timestamp_instant(frozen_time, cutoff):
            issues.append(_fail("ENV_FROZEN_AS_OF_MISMATCH", batch_id=batch.id))
        checked = 0
        for member in members:
            if not isinstance(member, Mapping):
                issues.append(_fail("ENV_FROZEN_MEMBER_INVALID", batch_id=batch.id))
                continue
            day = _day(member.get("environment_date"))
            row_id = member.get("daily_id")
            if day is None or isinstance(row_id, bool) or not isinstance(row_id, int):
                issues.append(_blocked("ENV_FROZEN_MEMBER_IDENTITY_MISSING"))
                continue
            dates.append(day)
            ids.append(row_id)
            if member.get("label_code") not in ENVIRONMENT_LABELS:
                issues.append(_fail("ENV_FROZEN_LABEL_INVALID", daily_id=row_id))
            rows = by_id[row_id]
            if len(rows) != 1:
                issues.append(_blocked("ENV_FROZEN_DAILY_NOT_UNIQUE", daily_id=row_id))
                continue
            row = rows[0]
            checked += 1
            for name, actual, declared in (
                ("environment_date", row.environment_date, day),
                ("label_kind", row.label_kind, batch.label_kind),
                ("label_code", row.label_code, member.get("label_code")),
                ("revision", row.revision, member.get("revision")),
                ("schema_version", row.schema_version, member.get("schema_version")),
            ):
                if declared is None:
                    issues.append(_blocked("ENV_FROZEN_FIELD_MISSING", daily_id=row_id, field=name))
                elif actual != declared:
                    issues.append(_fail("ENV_FROZEN_DAILY_MISMATCH", daily_id=row_id, field=name))
            if "label_kind" in member and member["label_kind"] != batch.label_kind:
                issues.append(_fail("ENV_FROZEN_KIND_MISMATCH", daily_id=row_id))
            available = _snapshot_datetime(row.available_at)
            declared_available = _snapshot_datetime(member.get("available_at"))
            if declared_available is None or available is None:
                issues.append(_blocked("ENV_FROZEN_AVAILABLE_AT_MISSING", daily_id=row_id))
            elif _same_timestamp_instant(available, declared_available) is None:
                issues.append(_blocked_doc("ENV_FROZEN_AVAILABLE_AT_TIMEZONE_UNDEFINED", daily_id=row_id))
            elif not _same_timestamp_instant(available, declared_available):
                issues.append(_fail("ENV_FROZEN_AVAILABLE_AT_MISMATCH", daily_id=row_id))
            if available is not None and cutoff is not None:
                visible = _timestamp_is_at_or_before(available, cutoff)
                if visible is None:
                    issues.append(_blocked_doc("ENV_FROZEN_VISIBILITY_TIMEZONE_UNDEFINED", daily_id=row_id))
                elif not visible:
                    issues.append(_fail("ENV_FROZEN_FUTURE_REVISION", daily_id=row_id))
            if not snapshot.environment_daily_history_loaded:
                issues.append(_blocked("ENV_REVISION_HISTORY_NOT_LOADED", daily_id=row_id))
                continue
            candidates = []
            unknown_visibility = False
            for candidate in history[(day, batch.label_kind)]:
                candidate_available = _snapshot_datetime(candidate.available_at)
                if candidate_available is None or cutoff is None:
                    unknown_visibility = True
                    issues.append(_blocked("ENV_REVISION_AVAILABLE_AT_MISSING", daily_id=row_id))
                    continue
                visible = _timestamp_is_at_or_before(candidate_available, cutoff)
                if visible is None:
                    unknown_visibility = True
                    issues.append(_blocked_doc("ENV_REVISION_HISTORY_TIMEZONE_UNDEFINED", daily_id=row_id))
                elif visible:
                    candidates.append(candidate)
            if unknown_visibility:
                continue
            if not candidates:
                issues.append(_blocked("ENV_VISIBLE_REVISION_EVIDENCE_MISSING", daily_id=row_id))
                continue
            latest_revision = max(r.revision for r in candidates)
            latest = [r for r in candidates if r.revision == latest_revision]
            if len(latest) != 1:
                issues.append(_fail("ENV_VISIBLE_REVISION_AMBIGUOUS", daily_id=row_id))
            elif row.id != latest[0].id:
                issues.append(_fail("ENV_FROZEN_REVISION_NOT_LATEST_VISIBLE", daily_id=row_id))
        if len(dates) != len(set(dates)) or len(ids) != len(set(ids)):
            issues.append(_fail("ENV_FROZEN_MEMBERS_NOT_EXCLUSIVE", batch_id=batch.id))
        missing_raw = frozen.get("missing_dates")
        if not isinstance(missing_raw, list) or any(_day(value) is None for value in missing_raw):
            issues.append(_blocked("ENV_FROZEN_MISSING_DATES_UNAVAILABLE", batch_id=batch.id))
        else:
            missing = [_day(value) for value in missing_raw]
            if len(missing) != len(set(missing)) or set(missing) & set(dates):
                issues.append(_fail("ENV_FROZEN_MISSING_DATES_CONFLICT", batch_id=batch.id))
            if set(dates) | set(missing) != expected:
                issues.append(_fail("ENV_FROZEN_CALENDAR_NOT_COMPLETE", batch_id=batch.id,
                                    absent_count=len(expected - set(dates) - set(missing)),
                                    outside_count=len((set(dates) | set(missing)) - expected)))
        if not members:
            issues.append(_blocked("ENV_FROZEN_MEMBERS_EMPTY"))
        return _result("ENV-CLOSURE-CALENDAR", checked, issues)

    @staticmethod
    def check_label_results(snapshot: CalculationAuditSnapshot, label: str) -> CalculationCheckResult:
        """Reconcile one label's counts and full metric/route publication identities.

        Input label must be one of six codes, otherwise ValueError is raised.
        Only explicitly persisted per-label metric count fields are compared;
        absent optional counters are reported as coverage evidence, not invented.
        Zero eligible routes is valid. No daily-label == all-history-label claim.
        """
        _validate_label(label)
        metrics = tuple(row for row in snapshot.evaluation_metrics if row.label_code == label)
        routes = tuple(row for row in snapshot.routes if row.label_code == label)
        scoped = replace(snapshot, routes=routes)
        identity = Factor4FinalResultService.check_route_identity(scoped)
        counts = Factor4FinalResultService.check_environment_summary(scoped, label)
        issues = [item for result in (identity, counts) for item in result.findings if item.status == "FAIL"]
        if any(row.label_code not in ENVIRONMENT_LABELS for row in (*snapshot.evaluation_metrics, *snapshot.routes)):
            issues.append(_fail("ENV_RESULT_LABEL_INVALID"))
        if counts.status != "PASS" and counts.status != "FAIL":
            issues.append(_blocked("ENV_LABEL_ROUTE_SUMMARY_MISSING", label=label))
        batch = snapshot.batch
        for metric in metrics:
            for field, expected in (("eval_batch_id", batch.id), ("market_scope", batch.market_scope),
                                    ("label_kind", batch.label_kind), ("scoring_version", batch.score_rule_version)):
                if getattr(metric, field) != expected:
                    issues.append(_fail("ENV_LABEL_METRIC_BATCH_MISMATCH", metric_id=metric.id, field=field))
            if metric.factor_ref != f"{metric.factor_type}:{metric.factor_id}":
                issues.append(_fail("ENV_LABEL_METRIC_FACTOR_IDENTITY", metric_id=metric.id))
        identities = [(_metric_pair_key(metric), metric.evaluation_type) for metric in metrics if _metric_pair_identity(metric) is not None]
        if len(identities) != len(metrics):
            issues.append(_blocked("ENV_LABEL_METRIC_FULL_IDENTITY_MISSING", label=label))
        if len(identities) != len(set(identities)):
            issues.append(_fail("ENV_LABEL_METRIC_IDENTITY_DUPLICATE", label=label))
        summary = batch.environment_status.get(label)
        available_counts: list[str] = []
        if isinstance(summary, Mapping):
            status_counts = Counter(metric.metric_status for metric in metrics)
            actual = {"metric_count": len(metrics), "completed_metric_count": status_counts["success"],
                      "insufficient_metric_count": status_counts["insufficient_sample"],
                      "failed_metric_count": status_counts["failed"]}
            for field, value in actual.items():
                if field in summary:
                    available_counts.append(field)
                    declared = summary[field]
                    if isinstance(declared, bool) or not isinstance(declared, int) or declared != value:
                        issues.append(_fail("ENV_LABEL_METRIC_COUNT_MISMATCH", label=label, field=field,
                                            expected=value, actual=declared if isinstance(declared, int) else None,
                                            actual_type=type(declared).__name__))
        if not available_counts:
            issues.append(_blocked("ENV_LABEL_METRIC_COUNTER_NOT_PERSISTED", label=label))
        if not metrics:
            issues.append(_blocked("ENV_LABEL_METRIC_SAMPLE_MISSING", label=label))
        return _result("ENV-CLOSURE-LABEL", len(metrics) + len(routes), issues,
                       label=label, checked_metric_count_fields=available_counts,
                       metric_count=len(metrics), route_count=len(routes))

    @staticmethod
    def check_admission_branch(snapshot: CalculationAuditSnapshot, label: str, branch: str) -> CalculationCheckResult:
        """Audit one label/truth-table branch using full TS/CS pair identities.

        Invalid label/branch raises ValueError. Missing pair/config evidence is
        blocked, contradictions fail. Any-valid governs dimension inclusion,
        not the existence of a route: score thresholds or other publication
        gates may still reject a valid pair. Return final-result checks only.
        """
        _validate_label(label)
        if branch not in ADMISSION_BRANCHES:
            raise ValueError("unknown admission branch")
        metrics = tuple(m for m in snapshot.evaluation_metrics if m.label_code == label)
        pairs, pair_issues = _metric_pairs(metrics)
        issues = list(pair_issues)
        for metric in metrics:
            for field, expected in (("eval_batch_id", snapshot.batch.id), ("market_scope", snapshot.batch.market_scope),
                                    ("label_kind", snapshot.batch.label_kind), ("scoring_version", snapshot.batch.score_rule_version)):
                if getattr(metric, field) != expected:
                    issues.append(_fail("ENV_ADMISSION_METRIC_PARTITION_MISMATCH", metric_id=metric.id, field=field))
        checked = 0
        for pair in pairs.values():
            if set(pair) != set(_SCOPES):
                issues.append(_blocked("ENV_ADMISSION_PAIR_INCOMPLETE", label=label))
                continue
            valid = {scope for scope in _SCOPES if pair[scope].metric_status == "success" and pair[scope].is_valid is True}
            current = "both" if len(valid) == 2 else "ts_only" if valid == {"time_series"} else "cs_only" if valid else "neither"
            if current != branch:
                continue
            checked += 1
            ids = {metric.id for metric in pair.values()}
            for metric in pair.values():
                eligibility = metric.route_eligibility
                if not isinstance(eligibility, Mapping):
                    issues.append(_blocked("ENV_ADMISSION_EVIDENCE_MISSING", metric_id=metric.id))
                    continue
                _check_scope_evidence(eligibility, valid, issues, metric_id=metric.id)
                if not isinstance(eligibility.get("is_eligible"), bool):
                    issues.append(_blocked("ENV_ADMISSION_ELIGIBILITY_RESULT_MISSING", metric_id=metric.id))
                elif eligibility["is_eligible"] is True and not valid:
                    issues.append(_fail("ENV_ADMISSION_NEITHER_MARKED_ELIGIBLE", metric_id=metric.id))
            matched_routes = [route for route in snapshot.routes if route.metric_id in ids]
            for route in matched_routes:
                result = Factor4FinalResultService.check_route_identity(replace(snapshot, routes=(route,)))
                issues.extend(f for f in result.findings if f.status == "FAIL")
                _check_scope_evidence(route.evidence, valid, issues, route_id=route.id)
                primary = next(metric for metric in pair.values() if metric.id == route.metric_id)
                same_identity = all(getattr(primary, field) == getattr(route, field) for field in (
                    "eval_batch_id", "market_scope", "label_kind", "label_code", "factor_ref",
                    "factor_type", "factor_id", "factor_version",
                )) and primary.scoring_version == route.score_rule_version
                if (same_identity and route.is_eligible and isinstance(primary.route_eligibility, Mapping)
                        and primary.route_eligibility.get("is_eligible") is False):
                    issues.append(_fail("ENV_ADMISSION_ELIGIBLE_ROUTE_REJECTED_BY_PRIMARY_METRIC",
                                        route_id=route.id, metric_id=primary.id))
                scores, confidences = _check_route_metric_values(route, pair, valid, issues)
                if not valid:
                    if route.is_eligible:
                        issues.append(_fail("ENV_ADMISSION_NEITHER_HAS_ELIGIBLE_ROUTE", route_id=route.id))
                    continue
                _check_weights(snapshot, route, valid, scores, confidences, issues)
            if valid and not matched_routes:
                issues.append(_blocked("ENV_ADMISSION_BRANCH_HAS_NO_ROUTE_FOR_WEIGHT_CHECK", label=label, branch=branch))
        if not checked:
            issues.append(_blocked("ENV_ADMISSION_BRANCH_SAMPLE_MISSING", label=label, branch=branch))
        return _result("ENV-CLOSURE-ADMISSION", checked, issues, label=label, branch=branch)


def _check_scope_evidence(payload: Mapping[str, Any], valid: set[str], issues: list[CalculationIssue], **identity: Any) -> None:
    if payload.get("admission_mode") is None:
        issues.append(_blocked("ENV_ADMISSION_MODE_MISSING", **identity))
    elif payload["admission_mode"] != "any_valid_scope":
        issues.append(_fail("ENV_ADMISSION_MODE_MISMATCH", **identity))
    for field, expected in (("valid_scopes", valid), ("invalid_scopes", set(_SCOPES) - valid)):
        value = payload.get(field)
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            issues.append(_blocked("ENV_ADMISSION_SCOPES_MISSING", field=field, **identity))
        elif set(value) != expected or len(value) != len(set(value)):
            issues.append(_fail("ENV_ADMISSION_SCOPES_MISMATCH", field=field, **identity))


def _check_route_metric_values(route: PublishedRoute, pair: Mapping[str, EvaluationMetric], valid: set[str],
                               issues: list[CalculationIssue]) -> tuple[dict[str, Decimal | None], dict[str, Decimal | None]]:
    expected_ids = {scope: pair[scope].id for scope in valid}
    payload = route.evidence
    if not isinstance(payload.get("metric_ids"), Mapping):
        issues.append(_blocked("ENV_ADMISSION_METRIC_IDS_MISSING", route_id=route.id))
    elif payload["metric_ids"] != expected_ids:
        issues.append(_fail("ENV_ADMISSION_METRIC_IDS_MISMATCH", route_id=route.id))
    scores = {scope: _decimal(getattr(pair[scope], f"{scope}_score")) for scope in valid}
    confidences = {scope: _decimal(pair[scope].confidence) for scope in valid}
    for scope in _SCOPES:
        raw_score = getattr(route, f"{scope}_score")
        actual = _decimal(raw_score)
        if scope not in valid and raw_score is not None:
            issues.append(_fail("ENV_ADMISSION_INVALID_SCOPE_SCORE_RETAINED", route_id=route.id, scope=scope))
        elif scope in valid and scores[scope] is not None and actual != scores[scope]:
            issues.append(_fail("ENV_ADMISSION_SCOPE_SCORE_MISMATCH", route_id=route.id, scope=scope))
    return scores, confidences


def _check_weights(snapshot: CalculationAuditSnapshot, route: PublishedRoute, valid: set[str],
                   scores: Mapping[str, Decimal | None], confidences: Mapping[str, Decimal | None],
                   issues: list[CalculationIssue]) -> None:
    if snapshot.batch.score_rule_version != "env-score-v1":
        issues.append(_blocked_doc("ENV_ADMISSION_SCORE_VERSION_UNSUPPORTED", route_id=route.id))
        return
    weights, _path, _error = _configured_route_profile_weights(snapshot.batch)
    if weights is None:
        issues.append(_blocked("ENV_ADMISSION_FROZEN_WEIGHTS_MISSING", route_id=route.id))
        return
    denominator = sum(weights[scope] for scope in valid)
    if denominator <= 0:
        issues.append(_blocked("ENV_ADMISSION_VALID_WEIGHT_SUM_ZERO", route_id=route.id))
        return
    effective = {scope: weights[scope] / denominator if scope in valid else Decimal(0) for scope in _SCOPES}
    payload = route.evidence
    for field, expected in (("configured_profile_weights", weights), ("effective_profile_weights", effective)):
        actual = payload.get(field)
        if not isinstance(actual, Mapping):
            issues.append(_blocked("ENV_ADMISSION_WEIGHT_EVIDENCE_MISSING", route_id=route.id, field=field))
            continue
        for scope in _SCOPES:
            value = _decimal(actual.get(scope))
            if value is None:
                issues.append(_blocked("ENV_ADMISSION_WEIGHT_VALUE_MISSING", route_id=route.id, field=field, scope=scope))
            elif abs(value - expected[scope]) > Decimal("0.000001"):
                issues.append(_fail("ENV_ADMISSION_WEIGHT_MISMATCH", route_id=route.id, field=field, scope=scope))
    if any(value is None for value in (*scores.values(), *confidences.values())):
        issues.append(_blocked("ENV_ADMISSION_SCORE_INPUT_MISSING", route_id=route.id))
        return
    base = sum(scores[scope] * effective[scope] for scope in valid)
    confidence = sum(confidences[scope] * effective[scope] for scope in valid)
    minimum = _decimal(snapshot.batch.evaluation_config.get("minimum_route_score"))
    if minimum is None:
        issues.append(_blocked("ENV_ADMISSION_MINIMUM_ROUTE_SCORE_MISSING", route_id=route.id))
    elif route.is_eligible and base * confidence < minimum:
        issues.append(_fail("ENV_ADMISSION_ELIGIBLE_BELOW_MINIMUM_SCORE", route_id=route.id))
    for field, expected, scale in (("routing_score", base * confidence, "0.000001"), ("confidence", confidence, "0.000000001")):
        actual = _decimal(getattr(route, field))
        if actual is None:
            issues.append(_blocked("ENV_ADMISSION_FINAL_SCORE_MISSING", route_id=route.id, field=field))
        elif actual != expected.quantize(Decimal(scale), rounding=ROUND_HALF_UP):
            issues.append(_fail("ENV_ADMISSION_FINAL_SCORE_MISMATCH", route_id=route.id, field=field))


def _day(value: object) -> date | None:
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else value
        return parsed if isinstance(parsed, date) and not isinstance(parsed, datetime) else None
    except ValueError:
        return None


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value)) if value is not None and not isinstance(value, bool) else None
        return result if result is not None and result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _validate_label(label: str) -> None:
    if label not in ENVIRONMENT_LABELS:
        raise ValueError("unknown environment label")


def _fail(code: str, **evidence: Any) -> CalculationIssue:
    return CalculationIssue("FAIL", code, code, evidence=evidence)


def _blocked(code: str, **evidence: Any) -> CalculationIssue:
    return CalculationIssue("BLOCKED_DATA_PRECONDITION", code, code, evidence=evidence)


def _blocked_doc(code: str, **evidence: Any) -> CalculationIssue:
    return CalculationIssue("BLOCKED_DOC", code, code, evidence=evidence)


def _result(case_id: str, checked: int, issues: Sequence[CalculationIssue], **evidence: Any) -> CalculationCheckResult:
    status = ("FAIL" if any(item.status == "FAIL" for item in issues)
              else "BLOCKED_DATA_PRECONDITION" if any(item.status == "BLOCKED_DATA_PRECONDITION" for item in issues) or not checked
              else "BLOCKED_DOC" if issues else "PASS")
    return CalculationCheckResult(case_id, case_id, status, case_id, checked, tuple(issues), evidence)
