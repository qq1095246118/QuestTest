"""最终结果验收 Service 的离线契约测试。"""

from dataclasses import replace
import pytest

from db.factor4_calculation_repository import PublishedRouteSnapshot, RouteRankingEntry
from service.factor4_result_service import Factor4FinalResultService
from tests.unit.test_factor4_calculation_service import _batch, _metric, _route, _snapshot


pytestmark = pytest.mark.unit


def test_route_identity_accepts_same_batch_final_result() -> None:
    snapshot = _snapshot(
        batch=_batch(environment_status={"WIDE_RANGE": {"status": "success", "route_count": 1}}),
        metrics=(_metric(11, "sub_factor:10", "time_series"),),
        routes=(_route(1, "sub_factor:10", 11, 1, "80"),),
    )
    result = Factor4FinalResultService.check_route_identity(snapshot)
    assert result.status == "PASS"
    assert result.checked_count == 1


def test_route_identity_detects_cross_batch_metric() -> None:
    snapshot = _snapshot(
        metrics=(_metric(11, "sub_factor:10", "time_series"),),
        routes=(replace(_route(1, "sub_factor:10", 11, 1, "80"), metric_id=999),),
    )
    result = Factor4FinalResultService.check_route_identity(snapshot)
    assert result.status == "FAIL"
    assert any(item.code == "RESULT_ROUTE_METRIC_NOT_UNIQUE" for item in result.findings)


def test_environment_summary_detects_published_route_count_bug() -> None:
    snapshot = _snapshot(
        batch=_batch(environment_status={"WIDE_RANGE": {"status": "success", "route_count": 0}}),
        metrics=(_metric(11, "sub_factor:10", "time_series"),),
        routes=(_route(1, "sub_factor:10", 11, 1, "80"),),
    )
    result = Factor4FinalResultService.check_environment_summary(snapshot, "WIDE_RANGE")
    assert result.status == "FAIL"
    finding = next(item for item in result.findings if item.code == "ROUTE_ENVIRONMENT_ROUTE_COUNT_MISMATCH")
    assert finding.evidence["declared_route_count"] == 0
    assert finding.evidence["actual_route_count"] == 1


def test_value_domain_rejects_non_finite_or_out_of_range_score() -> None:
    metric = _metric(11, "sub_factor:10", "time_series", score="101")
    snapshot = _snapshot(metrics=(metric,), routes=())
    result = Factor4FinalResultService.check_value_domains(snapshot)
    assert result.status == "FAIL"
    assert any(item.code == "RESULT_METRIC_VALUE_OUT_OF_RANGE" for item in result.findings)


def test_ranking_checks_final_results_without_raw_data() -> None:
    snapshot = _snapshot(
        metrics=(_metric(11, "sub_factor:10", "time_series"),),
        routes=(_route(1, "sub_factor:10", 11, 1, "80"),),
    )
    route = snapshot.routes[0]
    repeated = PublishedRouteSnapshot(
        captured_at=snapshot.captured_at,
        batch_id=6,
        publication_uid="publication-6",
        publish_version="publish-v1",
        market_scope="all",
        route_profile_key="default",
        routes=(RouteRankingEntry(
            id=route.id, metric_id=route.metric_id, environment_date=route.environment_date,
            label_kind=route.label_kind, label_code=route.label_code, as_of_time=route.as_of_time,
            factor_ref=route.factor_ref, factor_type=route.factor_type, factor_id=route.factor_id,
            factor_version=route.factor_version, rank_no=route.rank_no, routing_score=route.routing_score,
            confidence=route.confidence, time_series_score=route.time_series_score,
            cross_sectional_score=route.cross_sectional_score, score_rule_version=route.score_rule_version,
        ),),
    )
    from service.factor4_calculation_service import Factor4CalculationService
    result = Factor4CalculationService.check_final_result_ranking(snapshot, repeated)
    assert result.status == "PASS"
    assert result.evidence["repeated_calculation"] is False


def test_route_evidence_checks_only_same_record_columns() -> None:
    base = _route(1, "sub_factor:10", 11, 1, "80", evidence={
        "admission_mode": "any_valid_scope",
        "valid_scopes": ["time_series"],
        "invalid_scopes": ["cross_sectional"],
        "routing_score": "79",
        "confidence": "0.9",
    })
    snapshot = _snapshot(metrics=(_metric(11, "sub_factor:10", "time_series"),), routes=(base,))
    result = Factor4FinalResultService.check_route_evidence_consistency(snapshot)
    assert result.status == "FAIL"
    assert any(item.code == "RESULT_ROUTE_EVIDENCE_VALUE_MISMATCH" for item in result.findings)


def test_partition_isolation_rejects_route_from_other_publication() -> None:
    base = _route(1, "sub_factor:10", 11, 1, "80")
    snapshot = _snapshot(
        metrics=(_metric(11, "sub_factor:10", "time_series"),),
        routes=(replace(base, publication_uid="old-publication"),),
    )
    result = Factor4FinalResultService.check_partition_isolation(snapshot)
    assert result.status == "FAIL"
    assert any(item.code == "RESULT_PARTITION_LEAK" for item in result.findings)


def test_environment_matrix_rejects_undeclared_route_label() -> None:
    base = _route(1, "sub_factor:10", 11, 1, "80", label_code="UNILATERAL_UP")
    snapshot = _snapshot(
        metrics=(_metric(11, "sub_factor:10", "time_series", label_code="UNILATERAL_UP"),),
        routes=(base,),
    )
    result = Factor4FinalResultService.check_environment_matrix(snapshot)
    assert result.status == "FAIL"
    assert any(item.code == "RESULT_ENVIRONMENT_LABEL_UNDECLARED" for item in result.findings)
