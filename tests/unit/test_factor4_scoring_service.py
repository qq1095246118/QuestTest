"""Unit coverage for the persisted env-score-v1 audit service."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from service.factor4_scoring_service import Factor4ScoringService


pytestmark = pytest.mark.unit


def _metric(payload: dict[str, object], *, version: str = "env-score-v1") -> SimpleNamespace:
    """Build the minimal metric projection consumed by the scoring service."""

    return SimpleNamespace(
        id=42,
        label_code="WIDE_RANGE",
        scoring_version=version,
        metric_payload=payload,
        score_components={"metric_score": Decimal("57.500000")},
        evaluation_type="time_series",
        time_series_score=Decimal("57.500000"),
        cross_sectional_score=None,
        confidence=Decimal("0.8") * Decimal("0.7").sqrt(),
    )


def _complete_payload() -> dict[str, object]:
    """Return a complete hand-calculable env-score-v1 payload."""

    return {
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


def test_audit_metric_passes_when_persisted_components_match_oracle() -> None:
    """A complete payload and exact persisted components produce PASS."""

    from service.factor4_calculation_oracles import environment_score_v1

    payload = _complete_payload()
    score = environment_score_v1(payload)["metric_score"]
    metric = _metric(payload)
    metric.score_components = {"metric_score": score}  # type: ignore[misc]
    metric.time_series_score = score  # type: ignore[misc]

    result = Factor4ScoringService().audit_metric(metric, valid_oos_folds=3)

    assert result.status == "PASS"
    assert result.mismatches == ()


def test_audit_metric_fails_on_score_mismatch() -> None:
    """A numeric mismatch is a FAIL, not a data block."""

    result = Factor4ScoringService().audit_metric(_metric(_complete_payload()), valid_oos_folds=3)

    assert result.status == "FAIL"
    assert "score_components.metric_score" in result.mismatches
    assert "time_series_score" in result.mismatches


@pytest.mark.parametrize("version", ["env-score-v2", "unknown"])
def test_audit_metric_blocks_non_v1_versions(version: str) -> None:
    """v2 and unknown versions cannot be mistaken for a scoring pass."""

    result = Factor4ScoringService().audit_metric(_metric(_complete_payload(), version=version))

    assert result.status == "BLOCKED_DATA_PRECONDITION"


def test_audit_metric_blocks_missing_inputs() -> None:
    """Missing persisted inputs are reported as blocked evidence."""

    payload = _complete_payload()
    del payload["coverage_rate"]

    result = Factor4ScoringService().audit_metric(_metric(payload), valid_oos_folds=3)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert "coverage_rate" in result.summary


def test_thresholds_are_loaded_from_frozen_batch_configuration() -> None:
    """Batch min_* values override defaults without changing formula constants."""

    thresholds = Factor4ScoringService.thresholds_from_batch_config(
        {
            "min_coverage_rate": "0.80",
            "min_effective_sample_size": 40,
            "min_directed_t_stat": "2.5",
            "min_oos_retention": "0.7",
            "min_sign_consistency": "0.75",
            "min_oos_valid_folds": 4,
        }
    )
    assert thresholds.coverage == Decimal("0.80")
    assert thresholds.effective_samples == Decimal("40")
    assert thresholds.directed_t_stat == Decimal("2.5")
    assert thresholds.valid_oos_folds == 4
