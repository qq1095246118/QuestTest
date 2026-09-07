"""Recompute published economic/OOS score components from final metric columns.

This deliberately does not replay returns, positions, fees or OOS folds. The
current calculation repository strips fold rows and exposes no gross-return
or per-bar grid; those scenarios cannot be proved by these checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import json
from typing import Literal

from db.factor4_calculation_repository import CalculationAuditSnapshot
from db.factor4_lifecycle_repository import LifecycleSnapshot
from service.factor4_calculation_service import _snapshot_datetime


Component = Literal["economics", "penalty", "oos"]


@dataclass(frozen=True)
class EconomicsClosureResult:
    """Return compared metrics, finite diagnostics and missing evidence separately."""

    checked_count: int
    issues: tuple[str, ...]
    blocked: tuple[str, ...]


class Factor4EconomicsClosureService:
    """Independently check E/P/OOS components without unrelated IC prerequisites."""

    def check_oos_fold_configuration(self, snapshot: LifecycleSnapshot, scope: str) -> EconomicsClosureResult:
        """Check real fold count/duration and signed fold metrics against frozen config.

        ``snapshot`` is the lifecycle repository's persisted OOS projection;
        ``scope`` is time_series or cross_sectional. Recognized fields are the
        published oos_fold_count/oos_fold_days/min_oos_valid_folds configuration,
        requested/required/valid_fold_count and fold start/end/mean_ic/rank_ic
        fields. Missing evidence is blocked; malformed or inconsistent exposed
        values fail. OOS aggregate retention cannot be recomputed without its
        specified weighting/selection contract and is not claimed by this check.
        Invalid scope raises ``ValueError``; no I/O or assertions are performed.
        """

        if scope not in {"time_series", "cross_sectional"}:
            raise ValueError("unsupported OOS scope")
        batches = {b["id"]: b for b in snapshot.batches if b.get("status") in {"success", "completed"}}
        checked = 0
        issues: list[str] = []
        blocked: list[str] = []
        for metric in snapshot.metrics:
            if metric.get("evaluation_type") != scope or metric.get("metric_status") != "success" or metric.get("eval_batch_id") not in batches:
                continue
            prefix = f"metric={metric['id']}:oos:"
            batch = batches[metric["eval_batch_id"]]
            config = _object(batch.get("evaluation_config"))
            payload = _object(metric.get("metric_payload"))
            if config is None or payload is None:
                issues.append(prefix + "invalid_config_or_payload")
                continue
            oos = payload.get("oos")
            if oos is None:
                blocked.append(prefix + "missing_oos")
                continue
            if not isinstance(oos, Mapping) or not isinstance(oos.get("folds"), list):
                issues.append(prefix + "invalid_oos_folds")
                continue
            folds = oos["folds"]
            if not folds:
                blocked.append(prefix + "empty_oos_folds")
                continue
            checked += 1
            counts: dict[str, int] = {}
            for name in ("oos_fold_count", "oos_fold_days", "min_oos_valid_folds"):
                if name not in config:
                    blocked.append(prefix + "missing_config_" + name)
                elif not _positive_int(config[name]):
                    issues.append(prefix + "invalid_config_" + name)
                else:
                    counts[name] = config[name]
            if "oos_fold_count" in counts and len(folds) != counts["oos_fold_count"]:
                issues.append(prefix + "fold_count_differs_from_frozen_config")
            for name, config_name in (("requested_fold_count", "oos_fold_count"), ("required_fold_count", "min_oos_valid_folds")):
                if name not in oos:
                    blocked.append(prefix + "missing_" + name)
                elif not _positive_int(oos[name]):
                    issues.append(prefix + "invalid_" + name)
                elif config_name in counts and oos[name] != counts[config_name]:
                    issues.append(prefix + name + "_differs_from_frozen_config")
            valid = oos.get("valid_fold_count")
            if "valid_fold_count" not in oos:
                blocked.append(prefix + "missing_valid_fold_count")
            elif isinstance(valid, bool) or not isinstance(valid, int) or valid < 0 or valid > len(folds):
                issues.append(prefix + "invalid_valid_fold_count")
            direction_data = payload.get("direction")
            direction = direction_data.get("predictive_direction") if isinstance(direction_data, Mapping) else None
            if direction is None:
                blocked.append(prefix + "missing_predictive_direction")
            elif isinstance(direction, bool) or direction not in (-1, 1):
                issues.append(prefix + "invalid_predictive_direction")
                direction = None
            seen: set[tuple[object, object]] = set()
            previous_end = None
            for index, fold in enumerate(folds):
                fp = prefix + f"fold={index}:"
                if not isinstance(fold, Mapping):
                    issues.append(fp + "invalid_fold")
                    continue
                start, end = _snapshot_datetime(fold.get("start")), _snapshot_datetime(fold.get("end"))
                if start is None or end is None:
                    issues.append(fp + "invalid_fold_time")
                elif start.tzinfo is None or end.tzinfo is None:
                    blocked.append("BLOCKED_DOC:" + fp + "fold_timezone_undefined")
                    previous_end = None
                else:
                    identity = (start, end)
                    if identity in seen:
                        issues.append(fp + "duplicate_fold")
                    seen.add(identity)
                    if start >= end or (previous_end is not None and start != previous_end):
                        issues.append(fp + "noncontiguous_or_reversed_fold")
                    if "oos_fold_days" in counts and end - start != timedelta(days=counts["oos_fold_days"]):
                        issues.append(fp + "duration_differs_from_frozen_config")
                    previous_end = end
                for raw_name, directed_name in (("mean_ic", "directed_mean_ic"), ("mean_rank_ic", "directed_mean_rank_ic")):
                    if raw_name not in fold or directed_name not in fold:
                        blocked.append(fp + "missing_" + directed_name)
                        continue
                    raw, directed = _decimal(fold[raw_name]), _decimal(fold[directed_name])
                    if fold[raw_name] is None and fold[directed_name] is None:
                        continue  # Published invalid folds may carry explicit null pairs.
                    if raw is None or directed is None:
                        issues.append(fp + "invalid_" + directed_name)
                    elif direction is not None and abs(raw * direction - directed) > Decimal("0.000000000001"):
                        issues.append(fp + "direction_mismatch_" + directed_name)
        if not checked:
            blocked.append(scope + ":no_comparable_oos_folds")
        return EconomicsClosureResult(checked, tuple(dict.fromkeys(issues)), tuple(dict.fromkeys(blocked)))

    def check_component(
        self, snapshot: CalculationAuditSnapshot, component: Component,
        *, tolerance: Decimal = Decimal("0.000001"),
    ) -> EconomicsClosureResult:
        """Compare a persisted v1 component to its documented arithmetic.

        ``snapshot`` is the real read-only repository projection. ``component``
        selects economics E, excess drawdown/turnover penalty P, or OOS retention
        contribution. ``tolerance`` uses the existing scoring auditor's six-place
        component tolerance; invalid names/tolerance raise ``ValueError``. Missing
        inputs/components remain blocked, while malformed or contradictory
        explicitly exposed values remain failures even if other evidence is absent.
        This method performs no I/O, writes or pytest assertions.
        """

        if component not in {"economics", "penalty", "oos"}:
            raise ValueError("unsupported economic component")
        if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
            raise ValueError("tolerance must be a finite nonnegative Decimal")
        fields = {"economics": ("sharpe", "net_return"),
                  "penalty": ("max_drawdown", "turnover_rate"),
                  "oos": ("oos_retention",)}[component]
        component_names = {"economics": ("economic", "economics"),
                           "penalty": ("penalty", "score_penalty"),
                           "oos": ("oos",)}[component]
        issues: list[str] = []
        blocked: list[str] = []
        checked = 0
        for metric in snapshot.evaluation_metrics:
            if metric.metric_status != "success":
                continue
            prefix = f"metric={metric.id}:{component}:"
            if metric.scoring_version != "env-score-v1":
                blocked.append(prefix + "unsupported_scoring_version")
                continue
            values: dict[str, Decimal] = {}
            for name in fields:
                raw = getattr(metric, name)
                if raw is None:
                    blocked.append(prefix + "missing_" + name)
                else:
                    value = _decimal(raw)
                    if value is None:
                        issues.append(prefix + "invalid_" + name)
                    else:
                        values[name] = value
                        if name == "turnover_rate" and value < 0:
                            issues.append(prefix + "negative_turnover_rate")
            sources: list[tuple[str, object]] = []
            if isinstance(metric.score_components, Mapping):
                sources.extend(("score_components." + key, metric.score_components[key])
                               for key in component_names if key in metric.score_components)
            elif metric.score_components is not None:
                issues.append(prefix + "invalid_score_components")
            if component == "penalty" and isinstance(metric.metric_payload, Mapping) and "score_penalty" in metric.metric_payload:
                sources.append(("metric_payload.score_penalty", metric.metric_payload["score_penalty"]))
            if not sources:
                blocked.append(prefix + "component_not_exposed")
            actual_values: list[tuple[str, Decimal]] = []
            for path, raw in sources:
                actual = _decimal(raw)
                if actual is None:
                    issues.append(prefix + "invalid_" + path)
                else:
                    actual_values.append((path, actual))
            # A duplicate exposed alias is not silently overridden by another.
            if actual_values and any(abs(actual - actual_values[0][1]) > tolerance for _, actual in actual_values[1:]):
                issues.append(prefix + "component_projections_disagree")
            if len(values) != len(fields) or not actual_values:
                continue
            expected = _expected(component, values)
            checked += 1
            for path, actual in actual_values:
                if abs(actual - expected) > tolerance:
                    issues.append(prefix + "recalculation_mismatch:" + path)
        if not snapshot.evaluation_metrics or not any(metric.metric_status == "success" for metric in snapshot.evaluation_metrics):
            blocked.append(component + ":no_successful_metrics")
        return EconomicsClosureResult(checked, tuple(dict.fromkeys(issues)), tuple(dict.fromkeys(blocked)))


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None or not isinstance(value, (Decimal, int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _object(value: object) -> Mapping[str, object] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, Mapping) else None


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _clip(value: Decimal) -> Decimal:
    return min(max(value, Decimal(0)), Decimal(1))


def _expected(component: Component, values: Mapping[str, Decimal]) -> Decimal:
    if component == "economics":
        return Decimal(60) * _clip(values["sharpe"] / Decimal(2)) + Decimal(40) * _clip(values["net_return"] / Decimal("0.10"))
    if component == "penalty":
        return Decimal(50) * max(abs(values["max_drawdown"]) - Decimal("0.20"), Decimal(0)) + Decimal(20) * max(values["turnover_rate"] - Decimal("0.50"), Decimal(0))
    return Decimal(100) * _clip(values["oos_retention"])
