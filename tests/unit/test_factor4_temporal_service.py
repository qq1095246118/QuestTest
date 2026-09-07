"""Offline counterexamples for final temporal evidence; no original calculation inputs."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from db.factor4_lifecycle_repository import LifecycleSnapshot
from db.factor4_read_repository import DailyReadSnapshot
from service.factor4_temporal_service import Factor4TemporalService, calendar_segments

pytestmark = pytest.mark.unit


def _data() -> tuple[LifecycleSnapshot, DailyReadSnapshot]:
    member = {"daily_id": 1, "environment_date": "2026-09-01", "label_code": "CHOPPY_UP",
              "revision": 1, "schema_version": "e1", "available_at": "2026-09-01T00:00:00Z"}
    snapshot = {"as_of_time": "2026-09-03T00:00:00Z", "members": [member],
                "partitions": {"CHOPPY_UP": {"dates": ["2026-09-01"], "day_count": 1,
                                             "segments": calendar_segments(["2026-09-01"])}}}
    batch = {"id": 1, "status": "success", "environment_snapshot": snapshot, "factor_set_snapshot": {"members": []},
             "as_of_time": datetime(2026, 9, 3, 8), "label_kind": "fact", "start_date": "2026-09-01", "end_date": "2026-09-02"}
    metric = {"id": 2, "eval_batch_id": 1, "label_code": "CHOPPY_UP", "label_kind": "fact", "metric_status": "success",
              "metric_payload": {"sample_day_count": 1, "oos": {"folds": [{"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}]},
                                 "direction": {"direction_frozen_at": "2026-09-01T00:00:00Z"}}}
    daily = {**member, "id": 1, "label_kind": "fact", "is_current": 0}
    return LifecycleSnapshot((batch,), (metric,), (), {}), DailyReadSnapshot(datetime(2026, 9, 3, tzinfo=timezone.utc), (daily,))


def test_calendar_segments_preserve_disjoint_dates_and_reject_duplicates() -> None:
    assert calendar_segments(["2026-09-01", "2026-09-02", "2026-09-05"]) == [
        {"start_date": "2026-09-01", "end_date": "2026-09-02", "day_count": 2},
        {"start_date": "2026-09-05", "end_date": "2026-09-05", "day_count": 1}]
    with pytest.raises(ValueError):
        calendar_segments(["2026-09-01", "2026-09-01"])


def test_temporal_final_evidence_uses_historical_revision_without_current_flag() -> None:
    snapshot, daily = _data()
    service = Factor4TemporalService()
    assert not service.check_environment_partitions(snapshot, daily, label="CHOPPY_UP").issues
    assert not service.check_result_time_boundaries(snapshot).issues


@pytest.mark.parametrize("field,value", [("revision", 2), ("schema_version", "e2"), ("label_code", "CHOPPY_DOWN"),
                                        ("available_at", "2026-09-04T00:00:00Z")])
def test_snapshot_revision_and_time_mutations_fail(field: str, value: object) -> None:
    snapshot, daily = _data()
    daily.rows[0][field] = value
    assert Factor4TemporalService().check_environment_partitions(snapshot, daily, label="CHOPPY_UP").issues


def test_missing_metric_sample_day_count_is_not_silently_treated_as_zero() -> None:
    snapshot, daily = _data()
    snapshot.metrics[0]["metric_payload"]["sample_day_count"] = None
    assert Factor4TemporalService().check_environment_partitions(snapshot, daily, label="CHOPPY_UP").issues


def test_temporal_oos_detects_future_end_and_post_training_direction_freeze() -> None:
    snapshot, _ = _data()
    snapshot.metrics[0]["metric_payload"]["oos"]["folds"][0]["end"] = "2026-09-04T00:00:00Z"
    snapshot.metrics[0]["metric_payload"]["direction"]["direction_frozen_at"] = "2026-09-02T00:00:00Z"
    check = Factor4TemporalService().check_result_time_boundaries(snapshot)
    assert "metric:id=2:oos_fold_boundary" in check.issues
    assert "metric:id=2:direction_frozen_at" in check.issues


def test_temporal_nonterminal_metric_is_not_calculation_evidence() -> None:
    snapshot, _ = _data()
    snapshot.batches[0]["status"] = "cancelled"
    from service.factor4_read_service import ReadPrecondition
    with pytest.raises(ReadPrecondition):
        Factor4TemporalService().check_result_time_boundaries(snapshot)


def test_no_metrics_does_not_hide_future_frozen_definition_failure() -> None:
    snapshot, _ = _data()
    snapshot.batches[0]["factor_set_snapshot"]["members"] = [{"updated_at": "2026-09-04T00:00:00Z"}]
    result = Factor4TemporalService().check_result_time_boundaries(replace(snapshot, metrics=()))
    assert result.checked_count > 0
    assert "batch:id=1:future_factor_definition" in result.issues


def test_only_unsuccessful_metrics_cannot_pass_oos_validation() -> None:
    from service.factor4_read_service import ReadPrecondition
    snapshot, _ = _data()
    snapshot.metrics[0]["metric_status"] = "insufficient_data"
    with pytest.raises(ReadPrecondition, match="no successful terminal metrics"):
        Factor4TemporalService().check_result_time_boundaries(snapshot)
