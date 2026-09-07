"""Factor 4.0 scoring-contract audit helpers.

This module deliberately keeps the product calculation implementation out of
the oracle.  It only extracts persisted metric evidence, recomputes the
documented ``env-score-v1`` formula and reports missing evidence separately
from an actual mismatch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Literal

from db.factor4_calculation_repository import CalculationAuditSnapshot, EvaluationMetric
from service.factor4_calculation_oracles import (
    EnvironmentAdmissionThresholds,
    environment_admission_reasons,
    environment_score_v1,
)

AuditStatus = Literal["PASS", "FAIL", "BLOCKED_DATA_PRECONDITION"]


@dataclass(frozen=True)
class ScoringAuditResult:
    """One metric audit; checked_count counts saved fields checked against a copy or oracle."""

    status: AuditStatus
    metric_id: int
    label_code: str
    summary: str
    expected_components: Mapping[str, Decimal]
    actual_components: Mapping[str, Decimal]
    mismatches: tuple[str, ...] = ()
    admission_reasons: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    checked_count: int = 0
    valid_oos_folds: int | None = None
    input_states: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AdmissionAuditResult:
    """One saved metric's admission checks; unknown reason semantics remain blocked."""

    status: str
    metric_id: int
    label_code: str
    expected_valid: bool | None
    expected_reasons: tuple[str, ...]
    actual_reasons: tuple[str, ...]
    mismatches: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    checked_count: int = 0
    branches: tuple[str, ...] = ()


ADMISSION_FIELDS = (
    "coverage_rate", "effective_sample_size", "directed_t_stat", "oos_retention",
    "oos_sign_consistency", "valid_oos_folds", "net_return",
)
SCORE_INPUTS = (
    "directed_mean_rank_ic", "directed_rank_icir", "directed_t_stat", "coverage_rate",
    "effective_sample_size", "oos_retention", "oos_sign_consistency", "sharpe",
    "net_return", "max_drawdown", "turnover_rate",
)
_ADMISSION_CODES = environment_admission_reasons({}, valid_oos_folds=None)


@dataclass(frozen=True)
class ScoringCoverageResult:
    """Natural-result branch coverage; offline vectors do not satisfy live sample requirements."""

    group: str
    observed: tuple[str, ...]
    missing: tuple[str, ...]
    metrics: tuple[ScoringAuditResult | AdmissionAuditResult, ...]


class Factor4ScoringService:
    """Recompute and audit persisted Factor 4.0 environment scores."""

    @staticmethod
    def thresholds_from_batch_config(
        config: Mapping[str, object],
    ) -> EnvironmentAdmissionThresholds:
        """Build admission thresholds from a frozen batch configuration.

        The batch uses ``min_*`` names while the oracle uses canonical names.
        Missing keys retain the documented defaults; malformed or non-finite
        values raise ``ValueError`` rather than silently changing eligibility.
        """

        def decimal(name: str, default: Decimal) -> Decimal:
            value = config.get(name, default)
            try:
                parsed = value if isinstance(value, Decimal) else Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                raise ValueError(f"{name} must be numeric") from None
            if not parsed.is_finite() or parsed < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
            return parsed

        fold_value = config.get("min_oos_valid_folds", 3)
        if isinstance(fold_value, bool) or not isinstance(fold_value, int) or fold_value < 0:
            raise ValueError("min_oos_valid_folds must be a non-negative integer")
        return EnvironmentAdmissionThresholds(
            coverage=decimal("min_coverage_rate", Decimal("0.70")),
            effective_samples=decimal("min_effective_sample_size", Decimal("30")),
            directed_t_stat=decimal("min_directed_t_stat", Decimal("1.96")),
            oos_retention=decimal("min_oos_retention", Decimal("0.50")),
            oos_sign_consistency=decimal("min_sign_consistency", Decimal("0.60")),
            valid_oos_folds=fold_value,
        )

    def audit_metric(
        self,
        metric: EvaluationMetric,
        *,
        valid_oos_folds: int | None = None,
        thresholds: EnvironmentAdmissionThresholds = EnvironmentAdmissionThresholds(),
        tolerance: Decimal = Decimal("0.000001"),
    ) -> ScoringAuditResult:
        """Audit one metric's score components and admission evidence.

        ``metric`` is a read-only repository projection.  ``BLOCKED_DATA_PRECONDITION``
        is returned when the persisted payload does not expose the formula
        inputs or when scoring version ``v2`` is encountered (v2 intentionally
        computes metrics but does not score or publish them). ``FAIL`` means
        saved copies contradict each other, a saved score is malformed, or a
        score disagrees with the independent oracle. Missing inputs do not hide
        known copy differences. Malformed numeric inputs are failures on this
        metric; invalid tolerance or an explicit invalid fold-count argument raises
        ``ValueError`` before a misleading result can be returned.
        """

        if not isinstance(tolerance, Decimal) or tolerance < 0 or not tolerance.is_finite():
            raise ValueError("tolerance must be a finite non-negative Decimal")
        actual_payload = self._payload(metric)
        if metric.scoring_version == "env-score-v2":
            return self._blocked(metric, "env-score-v2 does not publish scores")
        if metric.scoring_version != "env-score-v1":
            return self._blocked(metric, "unsupported scoring version")

        if getattr(metric, "metric_status", "success") != "success":
            return self._audit_non_success_score(metric, actual_payload)

        actual, targets, malformed, absent = self._actual_components(metric)
        mismatches = list(malformed)
        blocked = list(absent)
        checked_fields: set[str] = set(malformed)
        # Compare persisted copies before requiring the inputs for the oracle.
        # A missing input must not hide a known column/payload contradiction.
        for target in set(targets.values()):
            copies = [(name, actual[name]) for name in actual if targets[name] == target]
            if len(copies) > 1:
                checked_fields.update(name for name, _ in copies)
            allowed = min(tolerance, Decimal("0.000000001")) if target == "confidence" else tolerance
            if copies and any(abs(value - copies[0][1]) > allowed for _, value in copies[1:]):
                mismatches.extend(name for name, _ in copies)
        try:
            values, input_states = self._input_values(actual_payload)
        except ValueError as error:
            mismatches.append(str(error))
            values, input_states = {}, {name: "unprovided" for name in SCORE_INPUTS}
        missing = tuple(name for name in SCORE_INPUTS if input_states[name] == "unprovided")
        if missing:
            blocked.append("missing score inputs: " + ", ".join(missing))
        expected = environment_score_v1(values, minimum_t_stat=thresholds.directed_t_stat) if not missing else {}
        for name, actual_value in actual.items():
            target = targets[name]
            if target not in expected:
                continue
            checked_fields.add(name)
            allowed = min(tolerance, Decimal("0.000000001")) if target == "confidence" else tolerance
            if abs(actual_value - expected[target]) > allowed:
                mismatches.append(name)
        persisted_folds, fold_error = self._persisted_valid_folds(actual_payload)
        if valid_oos_folds is None:
            valid_oos_folds = persisted_folds
        elif isinstance(valid_oos_folds, bool) or not isinstance(valid_oos_folds, int) or valid_oos_folds < 0:
            raise ValueError("valid_oos_folds must be a non-negative integer")
        elif persisted_folds is not None and persisted_folds != valid_oos_folds:
            mismatches.append("oos.valid_fold_count")
        if valid_oos_folds is None or fold_error:
            blocked.append(fold_error or "persisted oos.valid_fold_count is missing")
        reasons = environment_admission_reasons(values, valid_oos_folds=valid_oos_folds, thresholds=thresholds) if not missing and valid_oos_folds is not None and not fold_error else ()
        status: AuditStatus = "FAIL" if mismatches else "BLOCKED_DATA_PRECONDITION" if blocked else "PASS"
        return ScoringAuditResult(
            status, metric.id, metric.label_code,
            "persisted score values disagree" if mismatches else "; ".join(blocked) if blocked else "persisted score matches oracle",
            expected, actual, tuple(sorted(set(mismatches))), reasons,
            tuple(dict.fromkeys(blocked)), len(checked_fields), valid_oos_folds, input_states,
        )

    def audit_snapshot_scores(
        self,
        snapshot: CalculationAuditSnapshot,
        *,
        thresholds: EnvironmentAdmissionThresholds | None = None,
    ) -> tuple[ScoringAuditResult, ...]:
        """Audit all saved metrics using frozen thresholds and each metric's actual folds.

        Missing final evidence remains blocked per metric; no batch minimum is used
        as a measured fold count. Invalid frozen configuration raises ValueError.
        """

        if thresholds is None:
            thresholds = self.thresholds_from_batch_config(snapshot.batch.evaluation_config)
        return tuple(
            self.audit_metric(
                metric,
                thresholds=thresholds,
            )
            for metric in snapshot.evaluation_metrics
        )

    def audit_admission(
        self, metric: EvaluationMetric, *, thresholds: EnvironmentAdmissionThresholds,
        evaluation_config_version: str, evaluation_config: Mapping[str, object],
    ) -> AdmissionAuditResult:
        """Compare saved env-eval-v1/env-score-v1 status and every documented reject rule.

        Only persisted final inputs are consumed. Explicit null follows the v1 zero/
        rejection rules; unavailable projection keys block only their own comparisons.
        Unknown versions/reasons and unresolved OOS threshold binding remain blocked.
        Numeric input errors are returned as failures; no database or network is used.
        """
        payload = self._payload(metric)
        issues: list[str] = []
        blocked: list[str] = []
        branches: list[str] = []
        actual: tuple[str, ...] = ()
        expected: tuple[str, ...] = ()
        expected_valid = None
        checked = 0
        if metric.scoring_version != "env-score-v1" or evaluation_config_version != "env-eval-v1":
            blocked.append("unsupported admission rule version")
        else:
            for name, value in (("is_valid", metric.is_valid), ("metric_status", metric.metric_status),
                                ("scoring_version", metric.scoring_version)):
                if name in payload:
                    checked += 1
                    if payload[name] != value:
                        issues.append("metric_payload." + name)
            identity = payload.get("metric_identity")
            if isinstance(identity, Mapping) and "evaluation_config_version" in identity:
                checked += 1
                if identity["evaluation_config_version"] != evaluation_config_version:
                    issues.append("metric_identity.evaluation_config_version")
            if metric.metric_status != "success":
                if metric.metric_status not in {"insufficient_sample", "failed"}:
                    blocked.append("unsupported non-success metric status")
                checked += 1
                if metric.is_valid is not None:
                    issues.append("non_success.is_valid_must_be_null")
                branches.append("non_success")
            else:
                raw_reasons = payload.get("reject_reasons")
                if "reject_reasons" not in payload:
                    blocked.append("metric_payload.reject_reasons is unprovided")
                elif not isinstance(raw_reasons, list) or any(not isinstance(reason, str) for reason in raw_reasons):
                    issues.append("metric_payload.reject_reasons must be an array of codes")
                else:
                    actual = tuple(raw_reasons)
                    checked += 1
                    if len(set(actual)) != len(actual):
                        issues.append("metric_payload.reject_reasons contains duplicates")
                    unknown = set(actual) - set(_ADMISSION_CODES)
                    if unknown:
                        blocked.append("undocumented reject codes: " + ",".join(sorted(unknown)))
                try:
                    values, states = self._input_values(payload)
                except ValueError as error:
                    issues.append(str(error))
                    values, states = {}, {name: "unprovided" for name in SCORE_INPUTS}
                folds, fold_error = self._persisted_valid_folds(payload)
                resolved, fold_blocks = self._resolved_fold_threshold(payload, evaluation_config, thresholds)
                blocked.extend(fold_blocks)
                if fold_error:
                    issues.append(fold_error)
                if folds is None:
                    blocked.append("actual valid OOS fold count is unavailable")
                fields = ADMISSION_FIELDS[:5] + ("net_return",)
                known = {name for name in fields if states.get(name) != "unprovided"}
                blocked.extend("admission input is unprovided: " + name for name in fields if name not in known)
                known_codes = {_ADMISSION_CODES[ADMISSION_FIELDS.index(name)] for name in known}
                if folds is not None and resolved is not None:
                    known_codes.add("OOS_FOLDS_INCOMPLETE")
                resolved_thresholds = replace(thresholds, valid_oos_folds=resolved or thresholds.valid_oos_folds)
                all_reasons = environment_admission_reasons(values, valid_oos_folds=folds, thresholds=resolved_thresholds)
                expected = tuple(reason for reason in all_reasons if reason in known_codes)
                if isinstance(raw_reasons, list) and all(isinstance(reason, str) for reason in raw_reasons):
                    for reason in sorted(known_codes):
                        checked += 1
                        if (reason in actual) != (reason in expected):
                            issues.append("reject_reasons:" + reason)
                checked += 1
                if not isinstance(metric.is_valid, bool):
                    issues.append("success.is_valid_must_be_boolean")
                if expected:
                    expected_valid = False
                elif len(known_codes) == len(_ADMISSION_CODES) and not blocked:
                    expected_valid = True
                if expected_valid is not None and metric.is_valid != expected_valid:
                    issues.append("is_valid")
                for name, threshold in zip(ADMISSION_FIELDS, (
                    thresholds.coverage, thresholds.effective_samples, thresholds.directed_t_stat,
                    thresholds.oos_retention, thresholds.oos_sign_consistency, resolved, Decimal(0),
                )):
                    value = folds if name == "valid_oos_folds" else values.get(name)
                    if value is not None and threshold is not None:
                        branches.append(name + ":" + ("below" if value < threshold else "above" if value > threshold else "equal"))
                if expected_valid is not None:
                    branches.append("valid" if expected_valid else "invalid")
                if len(expected) > 1:
                    branches.append("multiple_reject_reasons")
        return AdmissionAuditResult(
            "FAIL" if issues else "BLOCKED_DATA_PRECONDITION" if blocked else "PASS",
            metric.id, metric.label_code, expected_valid, expected, actual,
            tuple(issues), tuple(dict.fromkeys(blocked)), checked, tuple(branches),
        )

    def audit_snapshot_admission(self, snapshot: CalculationAuditSnapshot) -> tuple[AdmissionAuditResult, ...]:
        """Audit every saved metric, including invalid/unscored rows; config errors propagate."""
        thresholds = self.thresholds_from_batch_config(snapshot.batch.evaluation_config)
        return tuple(self.audit_admission(
            metric, thresholds=thresholds, evaluation_config_version=snapshot.batch.evaluation_config_version,
            evaluation_config=snapshot.batch.evaluation_config,
        ) for metric in snapshot.evaluation_metrics)

    def natural_branch_coverage(
        self, snapshots: tuple[CalculationAuditSnapshot, ...], scope: str, group: str,
    ) -> ScoringCoverageResult:
        """Audit naturally occurring null/fallback/clip/precision/admission boundary branches.

        No values are modified and no exact boundary is approximated by a nearby value.
        Returns observed/missing branch keys and every selected metric's actual audit;
        missing branches cannot hide selected metric failures. Unknown group/scope
        raises ValueError. Original evidence/configuration errors propagate.
        """
        if scope not in {"time_series", "cross_sectional"}:
            raise ValueError("branch coverage requires time_series/cross_sectional")
        groups = {"admission_boundaries", "null_inputs", "rank_fallback", "clip_endpoints", "rounding"}
        if group not in groups:
            raise ValueError("unknown scoring branch group")
        needed: set[str] = set()
        if group == "admission_boundaries":
            needed = {name + ":" + side for name in ADMISSION_FIELDS for side in ("below", "equal", "above")}
            needed.update(("valid", "invalid", "multiple_reject_reasons"))
        elif group == "null_inputs":
            needed = {name + ":null" for name in SCORE_INPUTS}
        elif group == "rank_fallback":
            needed = {name + ":" + state for name in SCORE_INPUTS[:2] for state in ("fallback_null", "fallback_unprovided", "zero")}
        elif group == "rounding":
            needed = {"score_6_places", "confidence_9_places"}
        audits: list[ScoringAuditResult | AdmissionAuditResult] = []
        observed: set[str] = set()
        for snapshot in snapshots:
            thresholds = self.thresholds_from_batch_config(snapshot.batch.evaluation_config)
            clip_intervals = {
                "directed_mean_rank_ic": (Decimal("0.01"), Decimal("0.05")),
                "directed_rank_icir": (Decimal("0.35"), Decimal("0.90")),
                "directed_t_stat": (thresholds.directed_t_stat, Decimal(5)),
                "sharpe": (Decimal(0), Decimal(2)), "net_return": (Decimal(0), Decimal("0.10")),
                "coverage_rate": (Decimal(0), Decimal(1)), "oos_retention": (Decimal(0), Decimal(1)),
                "oos_sign_consistency": (Decimal(0), Decimal(1)),
            }
            if group == "clip_endpoints":
                for name in clip_intervals:
                    sides = ("lower", "between", "upper") if name in {"coverage_rate", "oos_sign_consistency"} else ("below", "lower", "between", "upper", "above")
                    needed.update(name + ":" + side for side in sides)
                needed.update(name + ":" + side for name in ("max_drawdown", "turnover_rate") for side in ("below", "equal", "above"))
                needed.update(("effective_sample_size:zero", "effective_sample_size:30", "effective_sample_size:above_30"))
            for metric in snapshot.evaluation_metrics:
                if metric.evaluation_type != scope or metric.scoring_version != "env-score-v1":
                    continue
                if group == "admission_boundaries":
                    audit = self.audit_admission(metric, thresholds=thresholds,
                        evaluation_config_version=snapshot.batch.evaluation_config_version,
                        evaluation_config=snapshot.batch.evaluation_config)
                    if audit.branches:
                        audits.append(audit)
                        observed.update(set(audit.branches) & needed)
                    continue
                if metric.metric_status != "success":
                    continue
                payload = self._payload(metric)
                try:
                    values, states = self._input_values(payload)
                except ValueError:
                    audits.append(self.audit_metric(metric, thresholds=thresholds))
                    continue
                matched: set[str] = set()
                if group == "null_inputs":
                    matched = {name + ":null" for name in SCORE_INPUTS if states[name] == "null"}
                elif group == "rank_fallback":
                    for name in SCORE_INPUTS[:2]:
                        if states[name] == "fallback":
                            matched.add(name + (":fallback_null" if name in payload else ":fallback_unprovided"))
                        elif values.get(name) == 0:
                            matched.add(name + ":zero")
                elif group == "clip_endpoints":
                    for name, (lower, upper) in clip_intervals.items():
                        value = values.get(name)
                        if value is not None:
                            side = "below" if value < lower else "lower" if value == lower else "between" if value < upper else "upper" if value == upper else "above"
                            matched.add(name + ":" + side)
                    for name, threshold in (("max_drawdown", Decimal("0.20")), ("turnover_rate", Decimal("0.50"))):
                        value = values.get(name)
                        if value is not None:
                            value = abs(value) if name == "max_drawdown" else value
                            matched.add(name + ":" + ("below" if value < threshold else "above" if value > threshold else "equal"))
                    samples = values.get("effective_sample_size")
                    if samples is not None and (samples == 0 or samples >= 30):
                        matched.add("effective_sample_size:" + ("zero" if samples == 0 else "30" if samples == 30 else "above_30"))
                elif group == "rounding":
                    matched = set(needed)
                if matched:
                    audit = self.audit_metric(metric, thresholds=thresholds)
                    if group == "rounding":
                        audit, matched = self._audit_precision(metric, audit)
                    audits.append(audit)
                    observed.update(matched & needed)
        return ScoringCoverageResult(group, tuple(sorted(observed)), tuple(sorted(needed - observed)), tuple(audits))

    def _audit_precision(self, metric: EvaluationMetric, audit: ScoringAuditResult) -> tuple[ScoringAuditResult, set[str]]:
        """Validate documented decimal places and unique nearest rounding; midpoint policy blocks."""
        actual, targets, _, _ = self._actual_components(metric)
        issues, blocked = list(audit.mismatches), list(audit.blocked_reasons)
        observed: set[str] = set()
        for path, value in actual.items():
            target = targets[path]
            quantum = Decimal("0.000000001") if target == "confidence" else Decimal("0.000001")
            if value.quantize(quantum) != value:
                issues.append(path + ":decimal_places")
            expected = audit.expected_components.get(target)
            if expected is None:
                continue
            rounded = expected.quantize(quantum, rounding=ROUND_HALF_EVEN)
            if abs(expected - rounded) == quantum / 2:
                blocked.append("BLOCKED_DOC: exact midpoint rounding mode is unspecified: " + path)
            elif rounded != value:
                issues.append(path + ":nearest_rounding")
            if expected != expected.to_integral_value():
                observed.add("confidence_9_places" if target == "confidence" else "score_6_places")
        return replace(audit,
            status="FAIL" if issues else "BLOCKED_DATA_PRECONDITION" if blocked else "PASS",
            mismatches=tuple(dict.fromkeys(issues)), blocked_reasons=tuple(dict.fromkeys(blocked)),
            checked_count=max(audit.checked_count, len(actual))), observed

    @staticmethod
    def _resolved_fold_threshold(
        payload: Mapping[str, object], config: Mapping[str, object], thresholds: EnvironmentAdmissionThresholds,
    ) -> tuple[int | None, tuple[str, ...]]:
        """Use explicit resolved OOS evidence only; do not invent min/max parsing semantics."""
        oos = payload.get("oos")
        if not isinstance(oos, Mapping):
            return None, ("resolved OOS fold evidence is unavailable",)
        required, requested = oos.get("required_fold_count"), oos.get("requested_fold_count")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (required, requested)):
            return None, ("resolved/requested OOS fold count is unavailable",)
        frozen_requested = config.get("oos_fold_count")
        if frozen_requested is None or requested != frozen_requested:
            return None, ("requested OOS folds cannot bind to frozen oos_fold_count",)
        if required != thresholds.valid_oos_folds or required > requested:
            return None, ("BLOCKED_DOC: requested/minimum OOS fold resolution is unspecified",)
        return required, ()

    @staticmethod
    def _audit_non_success_score(metric: EvaluationMetric, payload: Mapping[str, object]) -> ScoringAuditResult:
        """Non-success v1 rows retain null score/confidence, without fabricating numeric inputs."""
        values = {"confidence": getattr(metric, "confidence", None),
                  metric.evaluation_type + "_score": getattr(metric, metric.evaluation_type + "_score", None)}
        for name in ("metric_score", "confidence"):
            if name in payload:
                values["metric_payload." + name] = payload[name]
        for path, components in (("score_components", metric.score_components),
                                 ("metric_payload.score_components", payload.get("score_components"))):
            if isinstance(components, Mapping) and "metric_score" in components:
                values[path + ".metric_score"] = components["metric_score"]
        issues = tuple(name for name, value in values.items() if value is not None)
        blocked = () if metric.metric_status in {"insufficient_sample", "failed"} else ("unsupported non-success metric status",)
        return ScoringAuditResult("FAIL" if issues else "BLOCKED_DATA_PRECONDITION" if blocked else "PASS", metric.id, metric.label_code,
                                  "non-success score/confidence must be null", {}, {}, issues,
                                  blocked_reasons=blocked, checked_count=len(values), input_states={"metric_status": "non_success"})

    @staticmethod
    def _payload(metric: EvaluationMetric) -> dict[str, object]:
        """Merge metric payload and score-component JSON without mutating either."""

        payload: dict[str, object] = {}
        if isinstance(metric.metric_payload, Mapping):
            payload.update(metric.metric_payload)
        if isinstance(metric.score_components, Mapping):
            payload.update({key: value for key, value in metric.score_components.items() if key not in payload})
        for name in (
            "coverage_rate",
            "effective_sample_size",
            "directed_t_stat",
            "oos_retention",
            "sharpe",
            "max_drawdown",
            "turnover_rate",
            "net_return",
        ):
            value = getattr(metric, name, None)
            derived = name in {"effective_sample_size", "oos_retention", "directed_t_stat"}
            if hasattr(metric, name) and (not derived or value is not None):
                payload.setdefault(name, value)
        return payload

    @classmethod
    def _input_values(cls, payload: Mapping[str, object]) -> tuple[dict[str, Decimal | None], dict[str, str]]:
        """Preserve known null versus unavailable projection; rank zero never falls back."""

        aliases = {
            "directed_mean_rank_ic": ("directed_mean_rank_ic", "directed_mean_ic"),
            "directed_rank_icir": ("directed_rank_icir", "directed_icir"),
            "directed_t_stat": ("directed_t_stat",),
            "coverage_rate": ("coverage_rate", "coverage"),
            "effective_sample_size": ("effective_sample_size", "ess"),
            "oos_retention": ("oos_retention",),
            "oos_sign_consistency": ("oos_sign_consistency",),
            "sharpe": ("sharpe", "net_sharpe"),
            "net_return": ("net_return",),
            "max_drawdown": ("max_drawdown",),
            "turnover_rate": ("turnover_rate",),
        }
        nested_oos = payload.get("oos")
        if isinstance(nested_oos, Mapping):
            payload = {**payload}
            for target, source in (("oos_retention", "retention"), ("oos_sign_consistency", "sign_consistency")):
                if source in nested_oos:
                    payload.setdefault(target, nested_oos[source])
        result: dict[str, Decimal | None] = {}
        states: dict[str, str] = {}
        for canonical, names in aliases.items():
            states[canonical] = "unprovided"
            for name in names:
                if name not in payload:
                    continue
                value = payload[name]
                if value is None:
                    result[canonical] = None
                    states[canonical] = "null"
                    continue
                try:
                    if isinstance(value, bool):
                        raise ValueError("boolean metric")
                    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
                except (InvalidOperation, TypeError, ValueError):
                    raise ValueError(f"{name} must be numeric") from None
                if not decimal_value.is_finite():
                    raise ValueError(f"{name} must be finite")
                result[canonical] = decimal_value
                states[canonical] = "fallback" if name != canonical and canonical.startswith("directed_") else "value"
                break
        return result, states

    @staticmethod
    def _actual_components(metric: EvaluationMetric) -> tuple[dict[str, Decimal], dict[str, str], tuple[str, ...], tuple[str, ...]]:
        """Keep every explicit score copy under its own source path; never overwrite."""
        result: dict[str, Decimal] = {}
        targets: dict[str, str] = {}
        malformed, absent = [], []

        def add(path: str, target: str, value: object) -> None:
            targets[path] = target
            if value is None:
                absent.append("persisted score is missing: " + path)
                return
            try:
                if isinstance(value, bool):
                    raise ValueError("boolean score")
                parsed = value if isinstance(value, Decimal) else Decimal(str(value))
                if not parsed.is_finite():
                    raise ValueError("non-finite score")
            except (InvalidOperation, TypeError, ValueError):
                malformed.append(path)
                return
            result[path] = parsed

        aliases = {"strength": "strength", "stability": "stability", "oos": "oos",
                   "confidence": "confidence_score", "confidence_score": "confidence_score",
                   "economic": "economics", "economics": "economics", "penalty": "penalty",
                   "score_penalty": "penalty", "metric_score": "metric_score"}
        payload = metric.metric_payload if isinstance(metric.metric_payload, Mapping) else {}
        for prefix, components in (("score_components", metric.score_components),
                                   ("metric_payload.score_components", payload.get("score_components"))):
            if isinstance(components, Mapping):
                for key, value in components.items():
                    if key in aliases:
                        add(prefix + "." + key, aliases[key], value)
        if metric.evaluation_type not in {"time_series", "cross_sectional"}:
            absent.append("unsupported evaluation_type")
        else:
            scope_field = metric.evaluation_type + "_score"
            add(scope_field, "metric_score", getattr(metric, scope_field, None))
            if scope_field in payload:
                add("metric_payload." + scope_field, "metric_score", payload[scope_field])
        add("confidence", "confidence", getattr(metric, "confidence", None))
        for key, target in (("metric_score", "metric_score"), ("confidence", "confidence"), ("score_penalty", "penalty")):
            if key in payload:
                add("metric_payload." + key, target, payload[key])
        return result, targets, tuple(malformed), tuple(absent)

    @staticmethod
    def _persisted_valid_folds(payload: Mapping[str, object]) -> tuple[int | None, str | None]:
        """Read measured OOS count; absent/malformed evidence cannot become zero or a minimum."""
        oos = payload.get("oos")
        if not isinstance(oos, Mapping) or "valid_fold_count" not in oos:
            return None, None
        count = oos["valid_fold_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None, "persisted oos.valid_fold_count is invalid"
        folds = oos.get("folds")
        if isinstance(folds, list) and count > len(folds):
            return None, "persisted oos.valid_fold_count exceeds recorded folds"
        return count, None

    @staticmethod
    def _blocked(metric: EvaluationMetric, summary: str) -> ScoringAuditResult:
        """Build a structured blocked result without exposing payload contents."""

        return ScoringAuditResult(
            "BLOCKED_DATA_PRECONDITION", metric.id, metric.label_code, summary, {}, {},
            blocked_reasons=(summary,),
        )


__all__ = ["AdmissionAuditResult", "Factor4ScoringService", "ScoringAuditResult", "ScoringCoverageResult"]
