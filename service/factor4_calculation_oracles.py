"""Deterministic, independent oracles for Factor 4.0 fixture validation.

The production calculation service only audits evidence that already exists in
the database.  The helpers in this module provide small, implementation-
independent calculations for the sub-cases that need a controlled fixture
(time-series/cross-sectional grouping, point-in-time visibility, fold and
cost arithmetic, ranking, parent aggregation, and canonical snapshot hashes).
They deliberately do not call an API or database and must not be interpreted
as a product PASS without comparing their output with a real run.
"""

from __future__ import annotations

import hashlib
import json
from math import ceil
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal


@dataclass(frozen=True)
class Factor4Sample:
    """One timestamped factor observation and its aligned forward return.

    ``asset`` identifies the instrument, ``timestamp`` identifies the factor
    observation time, and both numeric fields are expected to be finite
    ``Decimal`` values.  Construction intentionally performs no coercion so a
    fixture can expose malformed input explicitly to its caller.
    """

    asset: str
    timestamp: datetime
    factor: Decimal
    forward_return: Decimal


def time_series_groups(
    rows: Iterable[Factor4Sample],
) -> dict[str, tuple[Factor4Sample, ...]]:
    """Group samples by asset and sort each series chronologically.

    Parameters
    ----------
    rows:
        Any finite iterable of :class:`Factor4Sample` objects.

    Returns
    -------
    dict[str, tuple[Factor4Sample, ...]]
        A newly allocated mapping whose values are immutable, timestamp-sorted
        tuples.  Input rows are not mutated.

    Raises
    ------
    TypeError
        If an item is not a ``Factor4Sample``.
    ValueError
        If one asset mixes timezone-aware and naive timestamps, because their
        chronological order is undefined without a contract timezone.
    """

    groups: dict[str, list[Factor4Sample]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, Factor4Sample):
            raise TypeError("rows must contain Factor4Sample values")
        groups[row.asset].append(row)
    result: dict[str, tuple[Factor4Sample, ...]] = {}
    for asset, values in groups.items():
        _ensure_timestamp_style([row.timestamp for row in values])
        result[asset] = tuple(sorted(values, key=lambda row: row.timestamp))
    return result


def cross_sectional_slice(
    rows: Iterable[Factor4Sample],
    timestamp: datetime,
) -> tuple[Factor4Sample, ...]:
    """Return samples observed at exactly one timestamp in asset order.

    The comparison is exact; no date truncation or timezone conversion is
    guessed.  A caller that wants calendar-day grouping must normalize its
    fixture timestamps before invoking this oracle.

    Raises ``TypeError`` for non-``Factor4Sample`` rows and ``ValueError`` when
    the input mixes naive and aware timestamps with the query timestamp.
    """

    if not isinstance(timestamp, datetime):
        raise TypeError("timestamp must be a datetime")
    materialized = tuple(rows)
    for row in materialized:
        if not isinstance(row, Factor4Sample):
            raise TypeError("rows must contain Factor4Sample values")
    _ensure_timestamp_style(
        [row.timestamp for row in materialized],
        reference=timestamp,
    )
    return tuple(
        sorted(
            (row for row in materialized if row.timestamp == timestamp),
            key=lambda row: row.asset,
        )
    )


def forward_returns(
    prices: Sequence[Decimal],
    horizon: int,
) -> tuple[Decimal, ...]:
    """Compute exact simple forward returns for complete horizons only.

    The value at index ``t`` is ``prices[t + horizon] / prices[t] - 1``.  The
    final ``horizon`` prices are omitted because a complete future interval is
    unavailable.  ``horizon`` must be a positive integer; zero prices and
    non-finite/non-Decimal inputs are rejected rather than silently producing
    an invalid oracle result.
    """

    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError("horizon must be a positive integer")
    values = tuple(prices)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ValueError("prices must contain finite Decimal values")
    if any(value == 0 for value in values[:-horizon] if values):
        raise ValueError("prices used as return denominators must be non-zero")
    return tuple(
        values[index + horizon] / values[index] - Decimal(1)
        for index in range(max(0, len(values) - horizon))
    )


def rolling_mean(
    values: Sequence[Decimal],
    window: int,
    *,
    min_periods: int | None = None,
) -> tuple[Decimal | None, ...]:
    """Compute a finite rolling mean and expose warm-up rows as ``None``.

    This is an independent fixture oracle for checking persisted formula
    evidence.  It deliberately does not infer a product default: callers must
    provide ``window`` and may optionally provide ``min_periods``.  Until the
    requested minimum number of observations is available the result is
    ``None``; once available, only the finite trailing window is used.
    ``ValueError`` is raised for invalid windows, non-finite values or a
    ``min_periods`` outside ``1..window``.
    """

    _validate_decimal_series(values, "values")
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError("window must be a positive integer")
    minimum = window if min_periods is None else min_periods
    if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= window:
        raise ValueError("min_periods must be an integer in 1..window")
    materialized = tuple(values)
    result: list[Decimal | None] = []
    for index in range(len(materialized)):
        start = max(0, index - window + 1)
        sample = materialized[start : index + 1]
        result.append(sum(sample, Decimal(0)) / Decimal(len(sample)) if len(sample) >= minimum else None)
    return tuple(result)


def diff_series(values: Sequence[Decimal], periods: int = 1) -> tuple[Decimal | None, ...]:
    """Return finite differences with ``None`` for the warm-up prefix."""

    _validate_decimal_series(values, "values")
    if isinstance(periods, bool) or not isinstance(periods, int) or periods < 1:
        raise ValueError("periods must be a positive integer")
    materialized = tuple(values)
    return tuple(
        None if index < periods else materialized[index] - materialized[index - periods]
        for index in range(len(materialized))
    )


def pct_change_series(values: Sequence[Decimal], periods: int = 1) -> tuple[Decimal | None, ...]:
    """Return finite percentage changes with explicit zero-denominator failure."""

    _validate_decimal_series(values, "values")
    if isinstance(periods, bool) or not isinstance(periods, int) or periods < 1:
        raise ValueError("periods must be a positive integer")
    materialized = tuple(values)
    result: list[Decimal | None] = []
    for index, value in enumerate(materialized):
        if index < periods:
            result.append(None)
            continue
        denominator = materialized[index - periods]
        if denominator == 0:
            raise ValueError("percentage-change denominator cannot be zero")
        result.append(value / denominator - Decimal(1))
    return tuple(result)


def rolling_vwap(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
    volume: Sequence[Decimal],
    window: int,
    *,
    min_periods: int | None = None,
) -> tuple[Decimal | None, ...]:
    """Compute finite rolling typical-price VWAP, never cumulative VWAP.

    The numerator is ``sum(((high + low + close) / 3) * volume)`` over the
    trailing ``window`` bars and the denominator is the matching volume sum.
    Warm-up rows and zero-volume windows return ``None``. min_periods defaults
    to the full window; a caller must explicitly select partial-window behavior.
    Invalid windows, negative volume, misaligned or nonfinite inputs raise ValueError.
    This offline convention is not evidence of the product's warm-up contract.
    """

    sequences = (high, low, close, volume)
    if len({len(series) for series in sequences}) != 1:
        raise ValueError("VWAP input series must be aligned")
    for name, series in zip(("high", "low", "close", "volume"), sequences, strict=True):
        _validate_decimal_series(series, name)
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError("window must be a positive integer")
    minimum = window if min_periods is None else min_periods
    if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= window:
        raise ValueError("min_periods must be an integer in 1..window")
    if any(value < 0 for value in volume):
        raise ValueError("volume must be nonnegative")
    result: list[Decimal | None] = []
    for index in range(len(close)):
        start = max(0, index - window + 1)
        numerator = Decimal(0)
        denominator = Decimal(0)
        for position in range(start, index + 1):
            typical = (high[position] + low[position] + close[position]) / Decimal(3)
            numerator += typical * volume[position]
            denominator += volume[position]
        result.append(None if index - start + 1 < minimum or denominator == 0 else numerator / denominator)
    return tuple(result)


def _validate_decimal_series(values: Sequence[Decimal], name: str) -> None:
    """Validate one finite Decimal sequence used by an offline oracle."""

    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ValueError(f"{name} must contain finite Decimal values")


def contiguous_segments(
    timestamps: Sequence[datetime],
    expected_step: timedelta,
) -> tuple[tuple[datetime, ...], ...]:
    """Split a timeline whenever the expected bar interval is absent.

    Timestamps are sorted before segmentation.  Duplicate timestamps and any
    interval other than ``expected_step`` start a new segment.  The step must
    be positive and all timestamps must use the same aware/naive style.
    """

    if expected_step <= timedelta(0):
        raise ValueError("expected_step must be positive")
    ordered = tuple(timestamps)
    if any(not isinstance(value, datetime) for value in ordered):
        raise TypeError("timestamps must contain datetime values")
    _ensure_timestamp_style(ordered)
    if not ordered:
        return ()
    sorted_values = tuple(sorted(ordered))
    segments: list[list[datetime]] = [[sorted_values[0]]]
    for timestamp in sorted_values[1:]:
        if timestamp - segments[-1][-1] != expected_step:
            segments.append([])
        segments[-1].append(timestamp)
    return tuple(tuple(segment) for segment in segments)


def visible_revision(
    revisions: Sequence[tuple[int, datetime]],
    as_of: datetime,
) -> int | None:
    """Select the highest revision available at or before ``as_of``.

    Availability comparison is inclusive (`available_at <= as_of`).  Revision
    IDs must be positive integers and timestamps must share the same timezone
    style as ``as_of``.  ``None`` means no revision was visible at the query
    time.
    """

    if not isinstance(as_of, datetime):
        raise TypeError("as_of must be a datetime")
    normalized: list[tuple[int, datetime]] = []
    for revision, available_at in revisions:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("revision IDs must be positive integers")
        if not isinstance(available_at, datetime):
            raise TypeError("revision availability must be datetime values")
        _ensure_timestamp_style((available_at,), reference=as_of)
        normalized.append((revision, available_at))
    visible = [revision for revision, available_at in normalized if available_at <= as_of]
    return max(visible) if visible else None


def fold_aggregate(
    values: Sequence[Decimal],
    weights: Sequence[Decimal],
) -> Decimal:
    """Return a weighted Decimal mean for aligned fold metrics.

    Both sequences must be non-empty, equally sized, finite Decimal values,
    and the total weight must be strictly positive.  Individual negative
    weights are rejected because they do not represent a valid fold/sample
    weighting contract.
    """

    if len(values) != len(weights) or not values:
        raise ValueError("values and weights must be non-empty and aligned")
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ValueError("values must contain finite Decimal values")
    if any(not isinstance(weight, Decimal) or not weight.is_finite() for weight in weights):
        raise ValueError("weights must contain finite Decimal values")
    if any(weight < 0 for weight in weights):
        raise ValueError("weights cannot be negative")
    total_weight = sum(weights, Decimal(0))
    if total_weight <= 0:
        raise ValueError("weights must have a positive total")
    return sum(
        (value * weight for value, weight in zip(values, weights, strict=True)),
        Decimal(0),
    ) / total_weight


def net_returns(
    gross: Sequence[Decimal],
    turnover: Sequence[Decimal],
    fee: Decimal,
    slippage: Decimal,
) -> tuple[Decimal, ...]:
    """Apply per-period fee and slippage to gross returns using turnover.

    Net return is ``gross - turnover * (fee + slippage)`` for each aligned
    period.  Costs and turnover must be finite, non-negative Decimals; length
    mismatches raise ``ValueError`` instead of truncating with ``zip``.
    """

    if len(gross) != len(turnover):
        raise ValueError("gross and turnover must be aligned")
    values = tuple(gross)
    traded = tuple(turnover)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ValueError("gross must contain finite Decimal values")
    if any(not isinstance(value, Decimal) or not value.is_finite() or value < 0 for value in traded):
        raise ValueError("turnover must contain finite non-negative Decimal values")
    for name, value in (("fee", fee), ("slippage", slippage)):
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            raise ValueError(f"{name} must be a finite non-negative Decimal")
    cost = fee + slippage
    return tuple(
        value - traded_value * cost
        for value, traded_value in zip(values, traded, strict=True)
    )


def partition_ranks(
    rows: Sequence[tuple[str, str, Decimal, str]],
    *,
    tie_breaker: Literal["factor_ref"] = "factor_ref",
) -> dict[tuple[str, str], tuple[tuple[str, int], ...]]:
    """Rank fixture rows independently by scope and environment.

    ``rows`` contain ``(market_scope, environment, score, factor_ref)``.  A
    separate partition is never allowed to influence another one.  Scores are
    compared as Decimals in descending order; the optional deterministic
    ``factor_ref`` tie-breaker is a fixture convenience and does not assert a
    production tie-breaker contract.
    """

    if tie_breaker != "factor_ref":
        raise ValueError("unsupported tie_breaker")
    partitions: dict[tuple[str, str], list[tuple[str, Decimal]]] = defaultdict(list)
    for scope, environment, score, factor_ref in rows:
        if not isinstance(score, Decimal) or not score.is_finite():
            raise ValueError("ranking scores must be finite Decimal values")
        partitions[(scope, environment)].append((factor_ref, score))
    return {
        key: tuple(
            (factor_ref, rank)
            for rank, (factor_ref, _score) in enumerate(
                sorted(values, key=lambda item: (-item[1], item[0])),
                start=1,
            )
        )
        for key, values in partitions.items()
    }


def parent_value(
    child_values: Mapping[str, Decimal],
    relation_snapshot: Sequence[tuple[str, Decimal]],
) -> Decimal:
    """Compute a normalized weighted parent value from frozen child members.

    Every relation child must exist in ``child_values``.  The relation order is
    retained for diagnostics but does not affect the weighted result.
    """

    selected = [(child_values[child], weight) for child, weight in relation_snapshot]
    return fold_aggregate(
        [value for value, _weight in selected],
        [weight for _value, weight in selected],
    )


def snapshot_hash(payload: object) -> str:
    """Hash a canonical JSON representation with SHA-256.

    Mapping keys are sorted and separators are fixed so equivalent JSON
    structures have stable identities.  Non-JSON scalar objects such as
    ``Decimal`` are rendered through ``str`` rather than using a process-
    dependent representation.  ``TypeError``/``ValueError`` from unsupported
    cyclic structures are propagated to expose a malformed fixture.
    """

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def threshold_is_met(
    score: Decimal,
    threshold: Decimal,
    *,
    inclusive: bool = True,
) -> bool:
    """Evaluate a versioned Decimal threshold without float coercion.

    ``inclusive=True`` applies ``>=``; ``False`` applies strict ``>``.  Both
    operands must be finite Decimals, otherwise ``ValueError`` is raised.
    """

    if (
        not isinstance(score, Decimal)
        or not score.is_finite()
        or not isinstance(threshold, Decimal)
        or not threshold.is_finite()
    ):
        raise ValueError("score and threshold must be finite Decimal values")
    return score >= threshold if inclusive else score > threshold


def time_series_positions(
    factors: Mapping[str, Decimal | None],
    training_mean: Decimal | Mapping[str, Decimal],
    training_std: Decimal | Mapping[str, Decimal],
    *,
    direction: int,
    frozen_asset_count: int,
) -> dict[str, Decimal]:
    """Calculate v2 TS positions with clipping and zero-filled missing assets."""

    if direction not in (-1, 1) or frozen_asset_count < 1:
        raise ValueError("direction must be +/-1 and asset count must be positive")
    for parameter in (training_mean, training_std):
        values = parameter.values() if isinstance(parameter, Mapping) else (parameter,)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise ValueError("training parameters must be finite Decimals")
    if isinstance(training_std, Decimal) and training_std <= 0:
        raise ValueError("training_std must be positive")
    result: dict[str, Decimal] = {}
    for asset, value in factors.items():
        if value is None:
            result[asset] = Decimal(0)
            continue
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("factor values must be finite Decimals or None")
        mean = training_mean[asset] if isinstance(training_mean, Mapping) else training_mean
        std = training_std[asset] if isinstance(training_std, Mapping) else training_std
        if not isinstance(mean, Decimal) or not isinstance(std, Decimal) or not mean.is_finite() or not std.is_finite() or std <= 0:
            raise ValueError("training parameters must be finite per-asset Decimals")
        z = (value - mean) / std
        z = min(max(z, Decimal("-3")), Decimal("3"))
        result[asset] = z / Decimal(frozen_asset_count) / Decimal(3) * direction
    return result


def cross_sectional_positions(
    factors: Mapping[str, Decimal],
    *,
    direction: int = 1,
) -> dict[str, Decimal] | None:
    """Build v2 CS positions from stable ascending ranks and 20% tails."""

    if direction not in (-1, 1):
        raise ValueError("direction must be +/-1")
    if len(factors) < 5 or any(not isinstance(v, Decimal) or not v.is_finite() for v in factors.values()):
        raise ValueError("CS requires at least five finite Decimal factors")
    if len(set(factors.values())) < 2:
        return None
    count = ceil(len(factors) * 0.2)
    ordered = sorted(factors, key=factors.__getitem__)
    weight = Decimal(1) / Decimal(count)
    result = {asset: Decimal(0) for asset in factors}
    for asset in ordered[:count]:
        result[asset] = -weight * direction
    for asset in ordered[-count:]:
        result[asset] = weight * direction
    return result


def turnover_series(weights: Sequence[Mapping[str, Decimal]]) -> tuple[Decimal, ...]:
    """Return full-grid turnover, including initial open and every exit/rebalance."""

    previous: dict[str, Decimal] = {}
    result: list[Decimal] = []
    for current in weights:
        assets = set(previous) | set(current)
        result.append(sum((abs(current.get(a, Decimal(0)) - previous.get(a, Decimal(0))) for a in assets), Decimal(0)))
        previous = dict(current)
    return tuple(result)


def equity_curve(net: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Compound net returns while clipping bankruptcy periods at zero."""

    value = Decimal(1)
    result: list[Decimal] = []
    for item in net:
        if not isinstance(item, Decimal) or not item.is_finite():
            raise ValueError("net returns must be finite Decimals")
        value *= max(Decimal(0), Decimal(1) + item)
        result.append(value)
    return tuple(result)


def max_drawdown(equity: Sequence[Decimal]) -> Decimal:
    """Return maximum drawdown magnitude including initial capital of one."""

    peak = Decimal(1)
    worst = Decimal(0)
    for value in equity:
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            raise ValueError("equity values must be finite non-negative Decimals")
        peak = max(peak, value)
        if peak:
            worst = max(worst, (peak - value) / peak)
    return worst


def sharpe_ratio(returns: Sequence[Decimal], annualization_factor: Decimal) -> Decimal:
    """Calculate sample-standard-deviation Sharpe including zero-return bars."""

    if len(returns) < 2 or annualization_factor <= 0:
        raise ValueError("at least two returns and positive annualization are required")
    if any(not isinstance(v, Decimal) or not v.is_finite() for v in returns):
        raise ValueError("returns must be finite Decimals")
    mean = sum(returns, Decimal(0)) / Decimal(len(returns))
    variance = sum((v - mean) ** 2 for v in returns) / Decimal(len(returns) - 1)
    if variance == 0:
        return Decimal(0)
    return mean / variance.sqrt() * annualization_factor.sqrt()


def daily_win_rate(days: Sequence[tuple[Decimal, bool]]) -> Decimal:
    """Return positive-return share over active days only."""

    active = [value for value, is_active in days if is_active]
    if not active:
        return Decimal(0)
    return Decimal(sum(value > 0 for value in active)) / Decimal(len(active))


def strictly_monotonic(values: Sequence[Decimal]) -> bool:
    """Return true only for strictly increasing or strictly decreasing values."""

    if len(values) < 2:
        return False
    deltas = [right - left for left, right in zip(values, values[1:])]
    return all(delta > 0 for delta in deltas) or all(delta < 0 for delta in deltas)


@dataclass(frozen=True)
class EnvironmentAdmissionThresholds:
    """Frozen env-score-v1 admission thresholds used by an independent oracle.

    Defaults are the documented rule defaults; live callers must construct
    this value from the batch's frozen configuration when it overrides them.
    Thresholds are ratios rather than percentages. Invalid values are rejected
    when :func:`environment_admission_reasons` evaluates the rule.
    """

    coverage: Decimal = Decimal("0.70")
    effective_samples: Decimal = Decimal("30")
    directed_t_stat: Decimal = Decimal("1.96")
    oos_retention: Decimal = Decimal("0.50")
    oos_sign_consistency: Decimal = Decimal("0.60")
    valid_oos_folds: int = 3


def linear_score(value: Decimal | None, lower: Decimal, upper: Decimal) -> Decimal:
    """Return ``100 * clip((value-lower)/(upper-lower), 0, 1)``.

    ``None`` has the documented score of zero. Non-finite/non-Decimal values
    or an empty/reversed interval raise ``ValueError``. No persistence rounding
    is imposed, because the document specifies precision but not a rounding mode.
    """

    lo = _finite_decimal(lower, "lower")
    hi = _finite_decimal(upper, "upper")
    if hi <= lo:
        raise ValueError("upper must be greater than lower")
    if value is None:
        return Decimal(0)
    return Decimal(100) * _clip((_finite_decimal(value, "value") - lo) / (hi - lo))


def environment_score_v1(
    metrics: Mapping[str, Decimal | None],
    *,
    minimum_t_stat: Decimal = Decimal("1.96"),
) -> dict[str, Decimal]:
    """Independently calculate the documented env-score-v1 components.

    Input keys are directed_mean_rank_ic/directed_mean_ic,
    directed_rank_icir/directed_icir, directed_t_stat, coverage_rate,
    effective_sample_size, oos_retention, oos_sign_consistency, sharpe,
    net_return, max_drawdown and turnover_rate. Rank metrics fall back only
    when absent, including ``None``; zero is a real value and must not fall back.
    Returns unrounded A/B/C/D/E, penalty, confidence and metric_score values.
    Non-finite numeric input raises ``ValueError``. This is an oracle, not a
    server result or a product PASS.
    """

    def value(name: str) -> Decimal:
        raw = metrics.get(name)
        return Decimal(0) if raw is None else _finite_decimal(raw, name)

    def preferred(primary: str, fallback: str) -> Decimal | None:
        raw = metrics.get(primary)
        return metrics.get(fallback) if raw is None else raw

    coverage = _clip(value("coverage_rate"))
    samples = max(value("effective_sample_size"), Decimal(0))
    consistency = _clip(value("oos_sign_consistency"))
    retention = _clip(value("oos_retention"))
    strength = (
        Decimal("0.6") * linear_score(preferred("directed_mean_rank_ic", "directed_mean_ic"), Decimal("0.01"), Decimal("0.05"))
        + Decimal("0.4") * linear_score(preferred("directed_rank_icir", "directed_icir"), Decimal("0.35"), Decimal("0.90"))
    )
    stability = Decimal("0.7") * 100 * consistency + Decimal("0.3") * linear_score(
        metrics.get("directed_t_stat"), minimum_t_stat, Decimal("5.0")
    )
    confidence = coverage * (samples / (samples + 30)).sqrt() if samples else Decimal(0)
    oos = Decimal(100) * retention
    confidence_score = Decimal(100) * confidence
    economics = Decimal("0.6") * linear_score(metrics.get("sharpe"), Decimal(0), Decimal(2)) + Decimal("0.4") * linear_score(
        metrics.get("net_return"), Decimal(0), Decimal("0.10")
    )
    penalty = Decimal(50) * max(abs(value("max_drawdown")) - Decimal("0.20"), Decimal(0)) + Decimal(20) * max(
        value("turnover_rate") - Decimal("0.50"), Decimal(0)
    )
    total = Decimal("0.30") * strength + Decimal("0.25") * stability + Decimal("0.20") * oos + Decimal("0.15") * confidence_score + Decimal("0.10") * economics - penalty
    return {
        "strength": strength,
        "stability": stability,
        "oos": oos,
        "confidence_score": confidence_score,
        "economics": economics,
        "penalty": penalty,
        "confidence": confidence,
        "metric_score": min(max(total, Decimal(0)), Decimal(100)),
    }


def environment_admission_reasons(
    metrics: Mapping[str, Decimal | None],
    *,
    valid_oos_folds: int | None,
    thresholds: EnvironmentAdmissionThresholds = EnvironmentAdmissionThresholds(),
) -> tuple[str, ...]:
    """Return every failed env-score-v1 admission rule in stable order.

    Numeric inputs use the same field names as :func:`environment_score_v1`.
    Missing coverage/sample/OOS fields are treated as zero. Missing significance
    always rejects, and net_return must be present and strictly positive.
    ``valid_oos_folds`` is compared with the already resolved frozen fold
    threshold. Invalid/non-finite inputs and invalid thresholds raise
    ``ValueError`` instead of yielding a misleading eligibility decision.
    """

    if (
        isinstance(thresholds.valid_oos_folds, bool)
        or not isinstance(thresholds.valid_oos_folds, int)
        or thresholds.valid_oos_folds < 0
    ):
        raise ValueError("valid_oos_folds threshold must be a non-negative integer")
    if valid_oos_folds is not None and (isinstance(valid_oos_folds, bool) or not isinstance(valid_oos_folds, int) or valid_oos_folds < 0):
        raise ValueError("valid_oos_folds must be a non-negative integer or None")
    rules = (
        ("coverage_rate", thresholds.coverage, "COVERAGE_BELOW_THRESHOLD"),
        ("effective_sample_size", thresholds.effective_samples, "EFFECTIVE_SAMPLE_SIZE_BELOW_THRESHOLD"),
        ("directed_t_stat", thresholds.directed_t_stat, "HAC_SIGNIFICANCE_BELOW_THRESHOLD"),
        ("oos_retention", thresholds.oos_retention, "OOS_RETENTION_BELOW_THRESHOLD"),
        ("oos_sign_consistency", thresholds.oos_sign_consistency, "OOS_SIGN_CONSISTENCY_BELOW_THRESHOLD"),
    )
    reasons: list[str] = []
    for name, threshold, reason in rules:
        actual = metrics.get(name)
        expected = _finite_decimal(threshold, name + " threshold")
        if expected < 0:
            raise ValueError("admission thresholds must be non-negative")
        if (name == "directed_t_stat" and actual is None) or (Decimal(0) if actual is None else _finite_decimal(actual, name)) < expected:
            reasons.append(reason)
    if (valid_oos_folds or 0) < thresholds.valid_oos_folds:
        reasons.append("OOS_FOLDS_INCOMPLETE")
    net_return = metrics.get("net_return")
    if net_return is None or _finite_decimal(net_return, "net_return") <= 0:
        reasons.append("NET_RETURN_NOT_POSITIVE")
    return tuple(reasons)


def _clip(value: Decimal) -> Decimal:
    return min(max(value, Decimal(0)), Decimal(1))


def _finite_decimal(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return value


def _ensure_timestamp_style(
    values: Sequence[datetime],
    *,
    reference: datetime | None = None,
) -> None:
    """Raise a clear error when aware and naive datetimes are mixed."""

    styles = {value.tzinfo is not None for value in values}
    if reference is not None:
        styles.add(reference.tzinfo is not None)
    if len(styles) > 1:
        raise ValueError("timestamps must use one timezone style")


__all__ = [
    "Factor4Sample",
    "EnvironmentAdmissionThresholds",
    "contiguous_segments",
    "cross_sectional_slice",
    "fold_aggregate",
    "environment_admission_reasons",
    "environment_score_v1",
    "forward_returns",
    "net_returns",
    "linear_score",
    "parent_value",
    "partition_ranks",
    "snapshot_hash",
    "threshold_is_met",
    "time_series_positions",
    "cross_sectional_positions",
    "turnover_series",
    "equity_curve",
    "max_drawdown",
    "sharpe_ratio",
    "daily_win_rate",
    "strictly_monotonic",
    "time_series_groups",
    "visible_revision",
]
