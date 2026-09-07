"""Offline checks for the Factor 4.0 ``env-score-v1`` contract.

These tests deliberately exercise an independent Decimal oracle.  They are
useful as a deterministic reference for a later live calculation audit, but
do not by themselves prove that the service persisted these values correctly.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from service.factor4_calculation_oracles import (
    EnvironmentAdmissionThresholds,
    environment_admission_reasons,
    environment_score_v1,
    linear_score,
)


pytestmark = pytest.mark.unit


def test_linear_score_clips_endpoints_and_preserves_decimal_precision() -> None:
    """The linear mapping returns zero/100 outside the interval and no float noise."""

    assert linear_score(None, Decimal("1"), Decimal("3")) == Decimal("0")
    assert linear_score(Decimal("0"), Decimal("1"), Decimal("3")) == Decimal("0")
    assert linear_score(Decimal("1"), Decimal("1"), Decimal("3")) == Decimal("0")
    assert linear_score(Decimal("2"), Decimal("1"), Decimal("3")) == Decimal("50")
    assert linear_score(Decimal("3"), Decimal("1"), Decimal("3")) == Decimal("100")
    assert linear_score(Decimal("4"), Decimal("1"), Decimal("3")) == Decimal("100")
    assert linear_score(Decimal("1.333333333333333333"), Decimal("1"), Decimal("2")) == Decimal(
        "33.333333333333333300"
    )


@pytest.mark.parametrize(
    ("value", "lower", "upper"),
    [(Decimal("1"), Decimal("1"), Decimal("1")), (Decimal("1"), Decimal("2"), Decimal("1"))],
)
def test_linear_score_rejects_invalid_intervals(value: Decimal, lower: Decimal, upper: Decimal) -> None:
    """A degenerate or reversed scoring interval cannot produce a meaningful score."""

    with pytest.raises(ValueError):
        linear_score(value, lower, upper)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), 1.0, "1"])
def test_linear_score_rejects_non_finite_or_non_decimal_values(bad: object) -> None:
    """The oracle rejects malformed numeric fixtures instead of coercing silently."""

    with pytest.raises(ValueError):
        linear_score(bad, Decimal("0"), Decimal("1"))  # type: ignore[arg-type]


def test_environment_score_v1_matches_documented_components() -> None:
    """A hand-checkable fixture covers A/B/C/D/E, penalty and final clipping."""

    metrics = {
        "directed_mean_rank_ic": Decimal("0.03"),
        "directed_rank_icir": Decimal("0.625"),
        "directed_t_stat": Decimal("3.48"),
        "coverage_rate": Decimal("0.80"),
        "effective_sample_size": Decimal("70"),
        "oos_retention": Decimal("0.75"),
        "oos_sign_consistency": Decimal("0.80"),
        "sharpe": Decimal("1"),
        "net_return": Decimal("0.05"),
        "max_drawdown": Decimal("-0.25"),
        "turnover_rate": Decimal("0.60"),
    }

    result = environment_score_v1(metrics)

    assert result["strength"] == Decimal("50")
    assert result["stability"] == Decimal("71")
    assert result["oos"] == Decimal("75")
    assert result["confidence"] == Decimal("0.8") * (Decimal("0.7")).sqrt()
    assert result["confidence_score"] == Decimal("80") * (Decimal("0.7")).sqrt()
    assert result["economics"] == Decimal("50")
    assert result["penalty"] == Decimal("4.5")
    expected = (
        Decimal("0.30") * Decimal("50")
        + Decimal("0.25") * Decimal("71")
        + Decimal("0.20") * Decimal("75")
        + Decimal("0.15") * result["confidence_score"]
        + Decimal("0.10") * Decimal("50")
        - Decimal("4.5")
    )
    assert result["metric_score"] == expected


def test_score_rank_metric_fallback_is_only_for_missing_or_null() -> None:
    """A real zero rank metric must not be replaced by the legacy IC value."""

    common = {
        "directed_t_stat": Decimal("1.96"),
        "coverage_rate": Decimal("1"),
        "effective_sample_size": Decimal("30"),
        "oos_retention": Decimal("1"),
        "oos_sign_consistency": Decimal("1"),
        "sharpe": Decimal("0"),
        "net_return": Decimal("0.01"),
        "max_drawdown": Decimal("0"),
        "turnover_rate": Decimal("0"),
    }
    fallback = environment_score_v1(
        {**common, "directed_mean_ic": Decimal("0.03"), "directed_icir": Decimal("0.625")}
    )
    explicit_zero = environment_score_v1(
        {
            **common,
            "directed_mean_rank_ic": Decimal("0"),
            "directed_mean_ic": Decimal("0.03"),
            "directed_rank_icir": Decimal("0"),
            "directed_icir": Decimal("0.625"),
        }
    )

    assert fallback["strength"] == Decimal("50")
    assert explicit_zero["strength"] == Decimal("0")


def test_score_v1_missing_optional_values_propagate_as_zero() -> None:
    """Missing metrics score zero while the confidence formula handles ESS zero."""

    result = environment_score_v1({})

    assert result == {
        "strength": Decimal("0"),
        "stability": Decimal("0"),
        "oos": Decimal("0"),
        "confidence_score": Decimal("0"),
        "economics": Decimal("0"),
        "penalty": Decimal("0"),
        "confidence": Decimal("0"),
        "metric_score": Decimal("0"),
    }


def test_score_v1_clips_final_score_and_penalizes_drawdown_by_magnitude() -> None:
    """The published score stays in [0, 100], with MDD penalty sign-agnostic."""

    high = environment_score_v1(
        {
            "directed_mean_rank_ic": Decimal("1"),
            "directed_rank_icir": Decimal("2"),
            "directed_t_stat": Decimal("10"),
            "coverage_rate": Decimal("1"),
            # Large enough that Decimal context rounds confidence to one.
            "effective_sample_size": Decimal("1e1000"),
            "oos_retention": Decimal("1"),
            "oos_sign_consistency": Decimal("1"),
            "sharpe": Decimal("100"),
            "net_return": Decimal("100"),
            "max_drawdown": Decimal("0"),
            "turnover_rate": Decimal("0"),
        }
    )
    negative_mdd = environment_score_v1({"max_drawdown": Decimal("-0.30")})
    positive_mdd = environment_score_v1({"max_drawdown": Decimal("0.30")})
    low = environment_score_v1({"max_drawdown": Decimal("10")})

    assert high["metric_score"] == Decimal("100")
    assert negative_mdd["penalty"] == positive_mdd["penalty"] == Decimal("5")
    assert low["metric_score"] == Decimal("0")


@pytest.mark.parametrize(
    "field",
    [
        "directed_mean_rank_ic",
        "directed_rank_icir",
        "directed_t_stat",
        "coverage_rate",
        "effective_sample_size",
        "oos_retention",
        "oos_sign_consistency",
        "sharpe",
        "net_return",
        "max_drawdown",
        "turnover_rate",
    ],
)
def test_score_v1_rejects_non_finite_metric(field: str) -> None:
    """NaN and infinity cannot leak into persisted score components."""

    with pytest.raises(ValueError):
        environment_score_v1({field: Decimal("NaN")})


def test_admission_reports_all_failures_in_stable_contract_order() -> None:
    """A rejected environment exposes every failed rule, not only the first one."""

    assert environment_admission_reasons({}, valid_oos_folds=None) == (
        "COVERAGE_BELOW_THRESHOLD",
        "EFFECTIVE_SAMPLE_SIZE_BELOW_THRESHOLD",
        "HAC_SIGNIFICANCE_BELOW_THRESHOLD",
        "OOS_RETENTION_BELOW_THRESHOLD",
        "OOS_SIGN_CONSISTENCY_BELOW_THRESHOLD",
        "OOS_FOLDS_INCOMPLETE",
        "NET_RETURN_NOT_POSITIVE",
    )


def test_admission_boundaries_are_inclusive_except_net_return() -> None:
    """Threshold equality admits coverage/significance/folds; net return needs > 0."""

    thresholds = EnvironmentAdmissionThresholds()
    metrics = {
        "coverage_rate": thresholds.coverage,
        "effective_sample_size": thresholds.effective_samples,
        "directed_t_stat": thresholds.directed_t_stat,
        "oos_retention": thresholds.oos_retention,
        "oos_sign_consistency": thresholds.oos_sign_consistency,
        "net_return": Decimal("0"),
    }
    assert environment_admission_reasons(metrics, valid_oos_folds=3, thresholds=thresholds) == (
        "NET_RETURN_NOT_POSITIVE",
    )
    assert environment_admission_reasons(
        {**metrics, "net_return": Decimal("0.000001")}, valid_oos_folds=3, thresholds=thresholds
    ) == ()
    assert "OOS_FOLDS_INCOMPLETE" in environment_admission_reasons(
        {**metrics, "net_return": Decimal("0.1")}, valid_oos_folds=2, thresholds=thresholds
    )


def test_admission_supports_frozen_threshold_overrides() -> None:
    """Live batches may freeze stricter thresholds and fold requirements."""

    thresholds = EnvironmentAdmissionThresholds(
        coverage=Decimal("0.90"), effective_samples=Decimal("50"), valid_oos_folds=4
    )
    metrics = {
        "coverage_rate": Decimal("0.90"),
        "effective_sample_size": Decimal("50"),
        "directed_t_stat": Decimal("1.96"),
        "oos_retention": Decimal("0.50"),
        "oos_sign_consistency": Decimal("0.60"),
        "net_return": Decimal("0.01"),
    }
    assert environment_admission_reasons(metrics, valid_oos_folds=4, thresholds=thresholds) == ()


@pytest.mark.parametrize("bad", [True, -1, 2.5, "3"])
def test_admission_rejects_invalid_fold_counts(bad: object) -> None:
    """Fold counts are cardinalities and must be non-negative integers."""

    with pytest.raises(ValueError):
        environment_admission_reasons({}, valid_oos_folds=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        environment_admission_reasons(
            {},
            valid_oos_folds=0,
            thresholds=EnvironmentAdmissionThresholds(valid_oos_folds=bad),  # type: ignore[arg-type]
        )
