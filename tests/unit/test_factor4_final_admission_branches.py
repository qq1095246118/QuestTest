"""Offline negative controls for persisted admission/score branches, never live evidence."""

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_EVEN
from types import SimpleNamespace

import pytest

from service.factor4_calculation_oracles import EnvironmentAdmissionThresholds, environment_admission_reasons, environment_score_v1
from service.factor4_scoring_service import ADMISSION_FIELDS, SCORE_INPUTS, Factor4ScoringService
from tests.cases.factor4 import test_scoring_environment_matrix as cases

pytestmark = pytest.mark.unit
CONFIG = {"oos_fold_count": 4, "min_oos_valid_folds": 3}


def _payload() -> dict[str, object]:
    return {"directed_mean_rank_ic": Decimal("0.03"), "directed_rank_icir": Decimal("0.625"),
            "directed_t_stat": Decimal("3.48"), "coverage_rate": Decimal("0.80"),
            "effective_sample_size": Decimal("70"), "oos_retention": Decimal("0.75"),
            "oos_sign_consistency": Decimal("0.80"), "sharpe": Decimal("1"), "net_return": Decimal("0.05"),
            "max_drawdown": Decimal("-0.25"), "turnover_rate": Decimal("0.60"),
            "oos": {"valid_fold_count": 3, "required_fold_count": 3, "requested_fold_count": 4},
            "metric_identity": {"evaluation_config_version": "env-eval-v1"}}


def _metric(payload: dict[str, object] | None = None, **changes: object) -> SimpleNamespace:
    payload = deepcopy(payload if payload is not None else _payload())
    values, _ = Factor4ScoringService._input_values(payload)
    score = environment_score_v1(values)
    reasons = environment_admission_reasons(values, valid_oos_folds=payload["oos"].get("valid_fold_count"))
    payload.update(metric_status="success", is_valid=not reasons, reject_reasons=list(reasons),
                   metric_score=score["metric_score"].quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN),
                   confidence=score["confidence"].quantize(Decimal("0.000000001"), rounding=ROUND_HALF_EVEN),
                   score_components={key: value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
                                     for key, value in score.items() if key not in {"confidence", "metric_score"}})
    metric = SimpleNamespace(id=42, label_code="CHOPPY_UP", evaluation_type="time_series",
        scoring_version="env-score-v1", metric_status="success", metric_payload=payload,
        is_valid=payload["is_valid"], confidence=payload["confidence"],
        time_series_score=payload["metric_score"], cross_sectional_score=None,
        score_components=payload["score_components"])
    for name, value in changes.items():
        setattr(metric, name, value)
    return metric


def _audit(metric: SimpleNamespace, config: dict[str, object] | None = None):
    service = Factor4ScoringService()
    config = config if config is not None else CONFIG
    return service.audit_admission(metric, thresholds=service.thresholds_from_batch_config(config),
                                   evaluation_config_version="env-eval-v1", evaluation_config=config)


def _snapshot(*metrics: SimpleNamespace, config: dict[str, object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(batch=SimpleNamespace(evaluation_config=config or CONFIG,
                          evaluation_config_version="env-eval-v1"), evaluation_metrics=metrics)


@pytest.mark.parametrize("field,threshold,code", [
    ("coverage_rate", "0.70", "COVERAGE_BELOW_THRESHOLD"),
    ("effective_sample_size", "30", "EFFECTIVE_SAMPLE_SIZE_BELOW_THRESHOLD"),
    ("directed_t_stat", "1.96", "HAC_SIGNIFICANCE_BELOW_THRESHOLD"),
    ("oos_retention", "0.50", "OOS_RETENTION_BELOW_THRESHOLD"),
    ("oos_sign_consistency", "0.60", "OOS_SIGN_CONSISTENCY_BELOW_THRESHOLD"),
    ("valid_oos_folds", "3", "OOS_FOLDS_INCOMPLETE"),
    ("net_return", "0", "NET_RETURN_NOT_POSITIVE"),
])
@pytest.mark.parametrize("side", [-1, 0, 1])
def test_all_admission_threshold_sides_compare_status_and_reason(field: str, threshold: str, code: str, side: int) -> None:
    payload = _payload()
    value = Decimal(threshold) + side * (1 if field == "valid_oos_folds" else Decimal("0.0001"))
    if field == "valid_oos_folds":
        payload["oos"]["valid_fold_count"] = int(value)
    else:
        payload[field] = value
    metric = _metric(payload)
    result = _audit(metric)
    rejected = side < 0 or (field == "net_return" and side == 0)
    assert result.status == "PASS"
    assert (code in result.expected_reasons) == rejected
    assert result.expected_valid == (not rejected)
    metric.metric_payload["reject_reasons"] = [] if rejected else [code]
    assert "reject_reasons:" + code in _audit(metric).mismatches


def test_all_reject_reasons_are_compared_without_first_failure_shortcut() -> None:
    payload = _payload()
    payload.update({name: Decimal(0) for name in ADMISSION_FIELDS if name != "valid_oos_folds"})
    payload["oos"]["valid_fold_count"] = 0
    metric = _metric(payload)
    assert len(_audit(metric).expected_reasons) == 7
    metric.metric_payload["reject_reasons"] = metric.metric_payload["reject_reasons"][:1]
    metric.is_valid = True
    result = _audit(metric)
    assert result.status == "FAIL" and len([item for item in result.mismatches if item.startswith("reject_reasons:")]) == 6
    assert "is_valid" in result.mismatches


@pytest.mark.parametrize("field", ["coverage_rate", "effective_sample_size", "directed_t_stat", "oos_retention", "oos_sign_consistency", "net_return"])
def test_explicit_null_admission_value_rejects_but_unprovided_key_blocks(field: str) -> None:
    payload = _payload()
    payload[field] = None
    metric = _metric(payload)
    assert _audit(metric).status == "PASS" and _audit(metric).expected_valid is False
    del metric.metric_payload[field]
    result = _audit(metric)
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(field in reason for reason in result.blocked_reasons)


def test_missing_significance_never_passes_even_with_frozen_zero_threshold() -> None:
    thresholds = EnvironmentAdmissionThresholds(directed_t_stat=Decimal(0))
    assert "HAC_SIGNIFICANCE_BELOW_THRESHOLD" in environment_admission_reasons(
        {"directed_t_stat": None, "net_return": Decimal(1)}, valid_oos_folds=3, thresholds=thresholds)


@pytest.mark.parametrize("mutation", ["missing", "null", "duplicate", "unknown", "malformed"])
def test_reject_evidence_errors_and_unknown_codes_do_not_pass(mutation: str) -> None:
    metric = _metric()
    if mutation == "missing":
        del metric.metric_payload["reject_reasons"]
    else:
        metric.metric_payload["reject_reasons"] = {"null": None, "duplicate": ["NET_RETURN_NOT_POSITIVE"] * 2,
            "unknown": ["UNDECLARED_RULE"], "malformed": [1]}[mutation]
    result = _audit(metric)
    assert result.status == ("BLOCKED_DATA_PRECONDITION" if mutation in {"missing", "unknown"} else "FAIL")


def test_known_failure_survives_unknown_rule_and_missing_another_input() -> None:
    metric = _metric()
    del metric.metric_payload["coverage_rate"]
    metric.metric_payload["reject_reasons"] = ["NET_RETURN_NOT_POSITIVE", "UNDECLARED_RULE"]
    result = _audit(metric)
    assert result.status == "FAIL" and result.blocked_reasons
    assert "reject_reasons:NET_RETURN_NOT_POSITIVE" in result.mismatches


def test_non_success_null_outputs_and_v2_are_not_scored_as_v1_success() -> None:
    metric = _metric(metric_status="insufficient_sample", is_valid=None, confidence=None, time_series_score=None)
    metric.metric_payload.update(metric_status="insufficient_sample", is_valid=None, metric_score=None, confidence=None)
    assert _audit(metric).status == "PASS"
    assert Factor4ScoringService().audit_metric(metric).status == "PASS"
    metric.is_valid = False
    metric.metric_payload["metric_score"] = 0
    assert "non_success.is_valid_must_be_null" in _audit(metric).mismatches
    assert Factor4ScoringService().audit_metric(metric).status == "FAIL"
    metric.scoring_version = "env-score-v2"
    assert _audit(metric).status == "BLOCKED_DATA_PRECONDITION"


@pytest.mark.parametrize("flag", [0, 1, None, "true"])
def test_repository_typed_success_validity_requires_actual_boolean(flag: object) -> None:
    """Typed DB projections cannot pass malformed boolean values through equality coercion."""
    metric = _metric(is_valid=flag)
    assert "success.is_valid_must_be_boolean" in _audit(metric).mismatches


def test_unknown_null_metric_status_is_blocked_not_treated_as_known_failure_state() -> None:
    """Only documented non-success final statuses can satisfy null-output checks."""
    metric = _metric(metric_status="new_status", is_valid=None, confidence=None, time_series_score=None)
    metric.metric_payload.update(metric_status="new_status", is_valid=None, metric_score=None, confidence=None)
    assert _audit(metric).status == "BLOCKED_DATA_PRECONDITION"
    assert Factor4ScoringService().audit_metric(metric).status == "BLOCKED_DATA_PRECONDITION"


@pytest.mark.parametrize("required,requested", [(2, 4), (4, 2), (None, 4)])
def test_unresolved_fold_threshold_does_not_invent_min_max_rule(required: object, requested: int) -> None:
    metric = _metric()
    metric.metric_payload["oos"].update(required_fold_count=required, requested_fold_count=requested)
    result = _audit(metric)
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert not result.mismatches


def test_actual_fold_count_never_replaced_by_required_threshold() -> None:
    metric = _metric()
    metric.metric_payload["oos"]["valid_fold_count"] = 2
    result = _audit(metric)
    assert result.status == "FAIL"
    assert "reject_reasons:OOS_FOLDS_INCOMPLETE" in result.mismatches
    assert result.expected_valid is False


@pytest.mark.parametrize("field", SCORE_INPUTS)
def test_saved_null_score_inputs_use_zero_or_null_linear_mapping(field: str) -> None:
    payload = _payload()
    payload[field] = None
    metric = _metric(payload)
    result = Factor4ScoringService().audit_metric(metric)
    assert result.status == "PASS", (result.mismatches, result.blocked_reasons)
    assert result.input_states[field] == "null"
    del metric.metric_payload[field]
    assert Factor4ScoringService().audit_metric(metric).status == "BLOCKED_DATA_PRECONDITION"


@pytest.mark.parametrize("rank,fallback", [("directed_mean_rank_ic", "directed_mean_ic"), ("directed_rank_icir", "directed_icir")])
@pytest.mark.parametrize("primary", ["null", "unprovided", "zero"])
def test_rank_fallback_and_zero_branches_have_real_persisted_comparisons(rank: str, fallback: str, primary: str) -> None:
    payload = _payload()
    payload[fallback] = payload[rank]
    if primary == "unprovided":
        del payload[rank]
    else:
        payload[rank] = None if primary == "null" else Decimal(0)
    metric = _metric(payload)
    result = Factor4ScoringService().natural_branch_coverage((_snapshot(metric),), "time_series", "rank_fallback")
    assert rank + (":zero" if primary == "zero" else ":fallback_" + primary) in result.observed
    assert all(audit.status == "PASS" for audit in result.metrics)
    metric.time_series_score += Decimal(1)
    assert Factor4ScoringService().natural_branch_coverage((_snapshot(metric),), "time_series", "rank_fallback").metrics[0].status == "FAIL"


def test_natural_branch_discovery_does_not_approximate_equal_or_mutate_metrics() -> None:
    metric = _metric()
    before = deepcopy(metric.__dict__)
    result = Factor4ScoringService().natural_branch_coverage((_snapshot(metric),), "time_series", "admission_boundaries")
    assert "coverage_rate:above" in result.observed and "coverage_rate:equal" in result.missing
    assert metric.__dict__ == before
    assert len(result.missing) > 0


@pytest.mark.parametrize("field,lower,upper", [
    ("directed_mean_rank_ic", "0.01", "0.05"), ("directed_rank_icir", "0.35", "0.90"),
    ("directed_t_stat", "1.96", "5"), ("sharpe", "0", "2"), ("net_return", "0", "0.10"),
])
@pytest.mark.parametrize("side", ["below", "lower", "between", "upper", "above"])
def test_every_linear_mapping_branch_is_classified_and_checked(field: str, lower: str, upper: str, side: str) -> None:
    """Classify exact persisted endpoints, then prove a wrong saved component fails."""
    lo, hi = Decimal(lower), Decimal(upper)
    value = {"below": lo - Decimal("0.0001"), "lower": lo, "between": (lo + hi) / 2,
             "upper": hi, "above": hi + Decimal("0.0001")}[side]
    metric = _metric({**_payload(), field: value})
    service = Factor4ScoringService()
    result = service.natural_branch_coverage((_snapshot(metric),), "time_series", "clip_endpoints")
    assert field + ":" + side in result.observed
    assert result.metrics[0].status == "PASS"
    metric.time_series_score += 1
    assert service.natural_branch_coverage((_snapshot(metric),), "time_series", "clip_endpoints").metrics[0].status == "FAIL"


@pytest.mark.parametrize("field,threshold", [("max_drawdown", "0.20"), ("turnover_rate", "0.50")])
@pytest.mark.parametrize("side", [-1, 0, 1])
def test_penalty_activation_uses_exact_threshold_and_drawdown_magnitude(field: str, threshold: str, side: int) -> None:
    """Zero penalty at equality and activation above the bound remain auditable."""
    value = Decimal(threshold) + side * Decimal("0.0001")
    metric = _metric({**_payload(), field: -value if field == "max_drawdown" else value})
    result = Factor4ScoringService().natural_branch_coverage((_snapshot(metric),), "time_series", "clip_endpoints")
    assert field + ":" + { -1: "below", 0: "equal", 1: "above"}[side] in result.observed
    assert result.metrics[0].status == "PASS"


@pytest.mark.parametrize("samples,branch", [(0, "zero"), (30, "30"), (10**12, "above_30")])
def test_confidence_sample_zero_threshold_and_large_n_are_distinct(samples: int, branch: str) -> None:
    """Natural coverage records n=0/n=30/n>30 without replacing a missing sample."""
    metric = _metric({**_payload(), "effective_sample_size": Decimal(samples)})
    result = Factor4ScoringService().natural_branch_coverage((_snapshot(metric),), "time_series", "clip_endpoints")
    assert "effective_sample_size:" + branch in result.observed
    assert result.metrics[0].status == "PASS"


def test_natural_ratio_coverage_does_not_require_out_of_domain_business_results() -> None:
    """Coverage and sign consistency outside [0,1] belong to offline anomaly vectors only."""
    result = Factor4ScoringService().natural_branch_coverage((_snapshot(_metric()),), "time_series", "clip_endpoints")
    needed = set(result.observed) | set(result.missing)
    for field in ("coverage_rate", "oos_sign_consistency"):
        assert {field + ":lower", field + ":between", field + ":upper"} <= needed
        assert field + ":below" not in needed and field + ":above" not in needed


@pytest.mark.parametrize("mutation", ["none", "seventh_decimal", "wrong_nearest", "tenth_confidence_decimal"])
def test_persisted_rounding_checks_scale_and_unique_nearest_value(mutation: str) -> None:
    metric = _metric()
    if mutation == "seventh_decimal":
        metric.time_series_score += Decimal("0.0000001")
    elif mutation == "wrong_nearest":
        metric.time_series_score += Decimal("0.000001")
    elif mutation == "tenth_confidence_decimal":
        metric.confidence += Decimal("0.0000000001")
    result = Factor4ScoringService().natural_branch_coverage((_snapshot(metric),), "time_series", "rounding")
    assert not result.missing
    assert (result.metrics[0].status == "FAIL") == (mutation != "none")


def test_exact_rounding_midpoint_is_document_block_not_assumed_half_even() -> None:
    metric = _metric()
    audit = Factor4ScoringService().audit_metric(metric)
    audit.expected_components["metric_score"] = metric.time_series_score + Decimal("0.0000005")
    result, _ = Factor4ScoringService()._audit_precision(metric, audit)
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any("midpoint" in reason for reason in result.blocked_reasons)


def test_business_case_failure_wins_over_missing_natural_boundaries() -> None:
    metric = _metric()
    metric.time_series_score += 1
    with pytest.raises(AssertionError):
        cases.test_natural_final_metrics_cover_admission_and_scoring_branches((_snapshot(metric),), "time_series", "clip_endpoints")
    with pytest.raises(pytest.skip.Exception, match="missing_natural_branches"):
        cases.test_natural_final_metrics_cover_admission_and_scoring_branches((_snapshot(_metric()),), "time_series", "clip_endpoints")


def test_admission_case_checks_all_partitions_before_block_handling() -> None:
    missing, invalid = _metric(), _metric()
    del missing.metric_payload["coverage_rate"]
    invalid.is_valid = False
    with pytest.raises(AssertionError):
        cases.test_final_metric_admission_matches_all_frozen_reject_rules((_snapshot(missing), _snapshot(invalid)), "time_series")
