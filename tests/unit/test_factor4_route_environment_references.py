"""Offline tests for the Factor 4.0 CALC-513 reference check."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from db.factor4_calculation_repository import EnvironmentDailyRecord
from service.factor4_calculation_service import Factor4CalculationService, _snapshot_datetime
from tests.unit.test_factor4_calculation_service import (
    _batch,
    _metric,
    _route,
    _service,
    _snapshot,
)


def _coherent_snapshot(
    *,
    label_code: str = "WIDE_RANGE",
    environment_status: dict[str, object] | None = None,
    **route_changes: object,
):
    """Build one minimal route/environment snapshot for the unit cases."""

    batch = _batch(
        factor_set_snapshot={
            "members": [
                {
                    "factor_ref": "sub_factor:10",
                    "factor_version": "updated_at:2026-09-01T00:00:00Z",
                }
            ]
        },
        environment_snapshot={
            "members": [{"daily_id": 1, "environment_date": "2026-09-01"}],
            "missing_dates": [],
            "as_of_time": "2026-09-02T01:17:00",
            "route_environment_date_semantics": "snapshot_member",
        },
        environment_status=environment_status or {label_code: {"status": "success"}},
    )
    route = _route(1, "sub_factor:10", 11, 1, "80", label_code=label_code)
    if route_changes:
        route = replace(route, **route_changes)
    return _snapshot(
        batch=batch,
        metrics=(_metric(11, "sub_factor:10", "time_series", label_code=label_code),),
        routes=(route,),
        environment_daily=(
            EnvironmentDailyRecord(
                id=1,
                environment_date=date(2026, 9, 1),
                label_kind="fact",
                label_code=label_code,
                revision=1,
                is_current=True,
                available_at=datetime(2026, 9, 1, 12),
                schema_version="market-env-v1",
            ),
        ),
    )


def _check(snapshot):
    service, _ = _service(snapshot)
    assert isinstance(service, Factor4CalculationService)
    return service.check_route_environment_references(snapshot)


def test_coherent_route_and_environment_snapshot_passes() -> None:
    result = _check(_coherent_snapshot())

    assert result.case_id == "CALC-513"
    assert result.status == "PASS"
    assert result.checked_count == 1
    assert result.findings == ()
    assert result.evidence["route_environment_date_semantics"] == "snapshot_member"


def test_missing_environment_snapshot_is_data_blocked() -> None:
    snapshot = _coherent_snapshot()
    snapshot = replace(snapshot, batch=replace(snapshot.batch, environment_snapshot=None))

    result = _check(snapshot)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(item.code == "ROUTE_ENVIRONMENT_SNAPSHOT_MISSING" for item in result.findings)


def test_missing_database_daily_evidence_is_data_blocked() -> None:
    """A member declaration alone cannot stand in for the daily DB row."""

    snapshot = replace(_coherent_snapshot(), environment_daily=())

    result = _check(snapshot)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_DAILY_EVIDENCE_MISSING"
        for item in result.findings
    )
    assert not any(
        item.code == "ROUTE_ENVIRONMENT_DAILY_ROW_MISSING"
        for item in result.findings
    )


def test_missing_referenced_database_daily_row_fails() -> None:
    """A partial DB projection proves that an individual reference is broken."""

    snapshot = _coherent_snapshot()
    batch = replace(
        snapshot.batch,
        environment_snapshot={
            **snapshot.batch.environment_snapshot,
            "members": [
                {"daily_id": 1, "environment_date": "2026-09-01"},
                {"daily_id": 2, "environment_date": "2026-09-01"},
            ],
        },
    )
    changed = replace(snapshot, batch=batch)

    result = _check(changed)

    assert result.status == "FAIL"
    finding = next(
        item
        for item in result.findings
        if item.code == "ROUTE_ENVIRONMENT_DAILY_ROW_MISSING"
    )
    assert finding.evidence["daily_id_samples"] == [2]


def test_empty_environment_members_are_data_blocked_when_routes_exist() -> None:
    """An empty frozen environment cannot establish a route reference."""

    snapshot = _coherent_snapshot()
    changed = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_snapshot={
                "members": [],
                "missing_dates": [],
                "as_of_time": "2026-09-02T01:17:00",
                "route_environment_date_semantics": "snapshot_member",
            },
        ),
        environment_daily=(),
    )

    result = _check(changed)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(item.code == "ROUTE_ENVIRONMENT_MEMBERS_EMPTY" for item in result.findings)


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("environment_date", date(2026, 9, 2), "ROUTE_ENVIRONMENT_DAILY_DATE_MISMATCH"),
        ("label_kind", "forecast", "ROUTE_ENVIRONMENT_DAILY_LABEL_KIND_MISMATCH"),
        ("label_code", "UNILATERAL_UP", "ROUTE_ENVIRONMENT_DAILY_LABEL_CODE_MISMATCH"),
        ("revision", 2, "ROUTE_ENVIRONMENT_DAILY_REVISION_MISMATCH"),
        ("schema_version", "market-env-v2", "ROUTE_ENVIRONMENT_DAILY_SCHEMA_VERSION_MISMATCH"),
    ],
)
def test_frozen_member_fields_must_match_database_daily_row(
    field: str,
    value: object,
    expected_code: str,
) -> None:
    """Explicit member identity fields are independently reconciled."""

    snapshot = _coherent_snapshot()
    frozen_member = {
        **snapshot.batch.environment_snapshot["members"][0],
        field: value,
    }
    changed = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_snapshot={
                **snapshot.batch.environment_snapshot,
                "members": [frozen_member],
            },
        ),
    )

    result = _check(changed)

    assert result.status == "FAIL"
    assert any(item.code == expected_code for item in result.findings)


def test_route_label_must_match_the_daily_member_for_snapshot_semantics() -> None:
    """A valid environment-status key is insufficient without daily identity."""

    base = _coherent_snapshot()
    snapshot = replace(
        base,
        routes=(replace(base.routes[0], label_code="UNILATERAL_UP"),),
    )

    result = _check(snapshot)

    assert result.status == "FAIL"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_DAILY_IDENTITY_MISMATCH"
        for item in result.findings
    )


def test_daily_available_at_after_batch_as_of_fails_visibility_check() -> None:
    """A frozen batch cannot reference a daily revision published in the future."""

    snapshot = _coherent_snapshot()
    record = replace(
        snapshot.environment_daily[0],
        available_at=datetime(2026, 9, 3, 0, 0),
    )
    changed = replace(snapshot, environment_daily=(record,))

    result = _check(changed)

    assert result.status == "FAIL"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_DAILY_NOT_VISIBLE_AT_BATCH_AS_OF"
        for item in result.findings
    )


def test_nullable_daily_label_code_is_a_data_precondition_for_routes() -> None:
    snapshot = _coherent_snapshot()
    record = replace(snapshot.environment_daily[0], label_code=None)
    changed = replace(snapshot, environment_daily=(record,))

    result = _check(changed)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_DAILY_LABEL_CODE_MISSING"
        for item in result.findings
    )


def test_undefined_route_date_semantics_is_document_blocked_not_failed() -> None:
    snapshot = _coherent_snapshot()
    snapshot = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_snapshot={
                "members": [{"daily_id": 1, "environment_date": "2020-01-01"}],
                "missing_dates": [],
                "as_of_time": "2026-09-02T01:17:00",
                },
            ),
        environment_daily=(
            EnvironmentDailyRecord(
                id=1,
                environment_date=date(2020, 1, 1),
                label_kind="fact",
                label_code="WIDE_RANGE",
                revision=1,
                is_current=True,
                available_at=datetime(2026, 9, 1, 12),
                schema_version="market-env-v1",
            ),
        ),
    )

    result = _check(snapshot)

    assert result.status == "BLOCKED_DOC"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_DATE_SEMANTICS_UNDEFINED"
        for item in result.findings
    )
    assert not any(item.status == "FAIL" for item in result.findings)


def test_missing_date_explicitly_referenced_by_route_fails() -> None:
    snapshot = _coherent_snapshot()
    snapshot = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_snapshot={
                "members": [{"daily_id": 1, "environment_date": "2026-09-01"}],
                "missing_dates": ["2026-09-01"],
                "as_of_time": "2026-09-02T01:17:00",
                "route_environment_date_semantics": "snapshot_member",
            },
        ),
    )

    result = _check(snapshot)

    assert result.status == "FAIL"
    assert any(item.code == "ROUTE_ENVIRONMENT_DATE_IN_MISSING_SET" for item in result.findings)


def test_route_metric_foreign_key_and_identity_are_checked() -> None:
    snapshot = _coherent_snapshot(metric_id=999)

    result = _check(snapshot)

    assert result.status == "FAIL"
    assert any(item.code == "ROUTE_METRIC_FOREIGN_KEY_MISSING" for item in result.findings)


def test_route_as_of_time_must_match_batch_snapshot_time() -> None:
    snapshot = _coherent_snapshot(as_of_time=datetime(2026, 9, 2, 1, 18))

    result = _check(snapshot)

    assert result.status == "FAIL"
    assert any(
        item.code == "ROUTE_REFERENCE_AS_OF_TIME_MISMATCH"
        for item in result.findings
    )


def test_route_score_rule_version_must_match_batch() -> None:
    snapshot = _coherent_snapshot(score_rule_version="env-score-v2")

    result = _check(snapshot)

    assert result.status == "FAIL"
    finding = next(
        item
        for item in result.findings
        if item.code == "ROUTE_REFERENCE_BATCH_IDENTITY_MISMATCH"
    )
    assert "score_rule_version" in finding.evidence["mismatches"]


def test_publication_effective_route_date_must_match_batch_end_date() -> None:
    snapshot = _coherent_snapshot(environment_date=date(2026, 9, 1))
    changed = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_snapshot={
                **snapshot.batch.environment_snapshot,
                "route_environment_date_semantics": "publication_effective",
            },
        ),
        routes=(replace(snapshot.routes[0], environment_date=date(2026, 8, 31)),),
    )

    result = _check(changed)

    assert result.status == "FAIL"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_DATE_MISMATCH"
        for item in result.findings
    )


@pytest.mark.parametrize(
    ("environment_status", "expected_code"),
    [
        ({"WIDE_RANGE": None}, "ROUTE_ENVIRONMENT_STATUS_ENTRY_INVALID"),
        ({"WIDE_RANGE": {}}, "ROUTE_ENVIRONMENT_STATUS_VALUE_MISSING"),
        ({"WIDE_RANGE": {"status": "success", "route_count": -1}}, "ROUTE_ENVIRONMENT_STATUS_ROUTE_COUNT_INVALID"),
    ],
)
def test_environment_status_entries_are_fail_closed(
    environment_status: dict[str, object],
    expected_code: str,
) -> None:
    snapshot = _coherent_snapshot()
    changed = replace(snapshot, batch=replace(snapshot.batch, environment_status=environment_status))

    result = _check(changed)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(item.code == expected_code for item in result.findings)


def test_environment_status_route_count_matches_active_eligible_routes() -> None:
    snapshot = _coherent_snapshot()
    changed = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_status={"WIDE_RANGE": {"status": "success", "route_count": 0}},
        ),
    )

    result = _check(changed)

    assert result.status == "FAIL"
    finding = next(
        item
        for item in result.findings
        if item.code == "ROUTE_ENVIRONMENT_STATUS_ROUTE_COUNT_MISMATCH"
    )
    assert finding.evidence["expected_route_count"] == 1
    assert finding.evidence["actual_route_count"] == 0


def test_environment_status_route_count_excludes_inactive_routes() -> None:
    snapshot = _coherent_snapshot()
    changed = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_status={"WIDE_RANGE": {"status": "success", "route_count": 0}},
        ),
        routes=(replace(snapshot.routes[0], is_active=False),),
    )

    result = _check(changed)

    assert result.status == "PASS"
    assert not any(
        item.code == "ROUTE_ENVIRONMENT_STATUS_ROUTE_COUNT_MISMATCH"
        for item in result.findings
    )


@pytest.mark.parametrize(
    "label_code",
    (
        "UNILATERAL_UP",
        "CHOPPY_UP",
        "NARROW_RANGE",
        "WIDE_RANGE",
        "UNILATERAL_DOWN",
        "CHOPPY_DOWN",
    ),
)
def test_each_market_environment_label_route_count_is_reconciled(label_code: str) -> None:
    """All six environment labels use the same route-count reconciliation rule."""

    snapshot = _coherent_snapshot(
        label_code=label_code,
        environment_status={label_code: {"status": "success", "route_count": 1}},
    )

    result = _check(snapshot)

    assert result.status == "PASS"
    assert result.checked_count == 1
    assert not any(
        item.code == "ROUTE_ENVIRONMENT_STATUS_ROUTE_COUNT_MISMATCH"
        for item in result.findings
    )


def test_duplicate_route_business_key_fails_even_with_distinct_row_ids() -> None:
    snapshot = _coherent_snapshot()
    second_metric = _metric(12, "sub_factor:10", "time_series")
    second_route = replace(snapshot.routes[0], id=2, metric_id=12, rank_no=2)
    changed = replace(
        snapshot,
        evaluation_metrics=(snapshot.evaluation_metrics[0], second_metric),
        routes=(snapshot.routes[0], second_route),
    )

    result = _check(changed)

    assert result.status == "FAIL"
    assert any(
        item.code == "ROUTE_REFERENCE_BUSINESS_KEY_DUPLICATE"
        for item in result.findings
    )


def test_duplicate_environment_member_business_key_fails() -> None:
    snapshot = _coherent_snapshot()
    member = snapshot.batch.environment_snapshot["members"][0]
    changed = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_snapshot={
                **snapshot.batch.environment_snapshot,
                "members": [member, {**member, "daily_id": 2}],
            },
        ),
    )

    result = _check(changed)

    assert result.status == "FAIL"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_MEMBER_BUSINESS_KEY_DUPLICATE"
        for item in result.findings
    )


def test_route_factor_ref_must_be_in_frozen_factor_snapshot() -> None:
    snapshot = _coherent_snapshot()
    snapshot = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            factor_set_snapshot={
                "members": [
                    {
                        "factor_ref": "sub_factor:other",
                        "factor_version": "updated_at:2026-09-01T00:00:00Z",
                    }
                ]
            },
        ),
    )

    result = _check(snapshot)

    assert result.status == "FAIL"
    assert any(
        item.code == "ROUTE_FACTOR_SNAPSHOT_MEMBER_MISSING"
        for item in result.findings
    )


def test_invalid_snapshot_timestamp_is_data_blocked() -> None:
    snapshot = _coherent_snapshot()
    snapshot = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_snapshot={
                "members": [{"daily_id": 1, "environment_date": "2026-09-01"}],
                "missing_dates": [],
                "as_of_time": "not-a-timestamp",
                "route_environment_date_semantics": "snapshot_member",
            },
        ),
    )

    result = _check(snapshot)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_SNAPSHOT_AS_OF_INVALID"
        for item in result.findings
    )


def test_mixed_snapshot_and_db_timestamp_styles_are_not_guessed() -> None:
    """A JSON UTC suffix cannot be compared to DB-naive time without a contract."""

    snapshot = _coherent_snapshot()
    snapshot = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_snapshot={
                "members": [{"daily_id": 1, "environment_date": "2026-09-01"}],
                "missing_dates": [],
                "as_of_time": "2026-09-02T01:17:00Z",
                "route_environment_date_semantics": "snapshot_member",
            },
        ),
    )

    result = _check(snapshot)

    assert result.status == "BLOCKED_DOC"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_SNAPSHOT_AS_OF_TIMEZONE_UNDEFINED"
        for item in result.findings
    )


def test_snapshot_datetime_rejects_date_only_values() -> None:
    """A calendar date must not be silently interpreted as midnight."""

    assert _snapshot_datetime("2026-09-02") is None
    assert _snapshot_datetime("2026-09-02T01:17:00Z") == datetime(
        2026,
        9,
        2,
        1,
        17,
        tzinfo=timezone.utc,
    )


def test_explicit_null_missing_dates_is_data_blocked() -> None:
    snapshot = _coherent_snapshot()
    changed = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            environment_snapshot={
                **snapshot.batch.environment_snapshot,
                "missing_dates": None,
            },
        ),
    )

    result = _check(changed)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_MISSING_DATES_INVALID"
        for item in result.findings
    )


def test_conflicting_factor_snapshot_versions_are_a_product_failure() -> None:
    snapshot = _coherent_snapshot()
    changed = replace(
        snapshot,
        batch=replace(
            snapshot.batch,
            factor_set_snapshot={
                "members": [
                    {
                        "factor_ref": "sub_factor:10",
                        "factor_version": "version-a",
                    },
                    {
                        "factor_ref": "sub_factor:10",
                        "factor_version": "version-b",
                    },
                ]
            },
        ),
    )

    result = _check(changed)

    assert result.status == "FAIL"
    assert any(
        item.code == "ROUTE_FACTOR_SNAPSHOT_VERSION_CONFLICT"
        for item in result.findings
    )


@pytest.mark.parametrize(
    "members",
    (
        [
            {"unexpected": True},
            {"factor_ref": "sub_factor:10", "factor_version": "version-a"},
            {"factor_ref": "sub_factor:10", "factor_version": "version-b"},
        ],
        [
            {"factor_ref": "sub_factor:10", "factor_version": "version-a"},
            {"factor_ref": "sub_factor:10", "factor_version": "version-b"},
            {"unexpected": True},
        ],
    ),
)
def test_factor_snapshot_conflict_is_not_hidden_by_malformed_member_order(
    members: list[dict[str, object]],
) -> None:
    snapshot = _coherent_snapshot()
    changed = replace(
        snapshot,
        batch=replace(snapshot.batch, factor_set_snapshot={"members": members}),
    )

    result = _check(changed)

    assert result.status == "FAIL"
    finding_codes = {item.code for item in result.findings}
    assert "ROUTE_FACTOR_SNAPSHOT_VERSION_CONFLICT" in finding_codes
    assert "ROUTE_FACTOR_SNAPSHOT_MISSING" in finding_codes


def test_malformed_daily_record_is_blocked_without_attribute_error() -> None:
    snapshot = _coherent_snapshot()
    malformed = SimpleNamespace(
        id=1,
        environment_date="2026-09-01",
        label_kind="fact",
        label_code="WIDE_RANGE",
        revision=1,
        is_current=True,
        available_at=datetime(2026, 9, 1, 12),
        schema_version="market-env-v1",
    )
    changed = replace(snapshot, environment_daily=(malformed,))

    result = _check(changed)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_DAILY_RECORD_INVALID"
        for item in result.findings
    )


@pytest.mark.parametrize(
    ("field", "expected_code"),
    (
        ("environment_daily", "ROUTE_ENVIRONMENT_DAILY_COLLECTION_INVALID"),
        (
            "environment_daily_history",
            "ROUTE_ENVIRONMENT_DAILY_HISTORY_COLLECTION_INVALID",
        ),
    ),
)
def test_malformed_daily_collections_are_blocked_without_type_error(
    field: str,
    expected_code: str,
) -> None:
    snapshot = _coherent_snapshot()
    changed = replace(snapshot, **{field: 123})

    result = _check(changed)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(item.code == expected_code for item in result.findings)


def _versioned_snapshot(
    *,
    selected_revision: int = 1,
    history: tuple[EnvironmentDailyRecord, ...] = (),
    history_loaded: bool = True,
):
    """Build a coherent CALC-513 snapshot with an explicit revision identity."""

    snapshot = _coherent_snapshot()
    member = {
        **snapshot.batch.environment_snapshot["members"][0],
        "revision": selected_revision,
    }
    batch = replace(
        snapshot.batch,
        environment_snapshot={
            **snapshot.batch.environment_snapshot,
            "members": [member],
        },
    )
    selected = replace(snapshot.environment_daily[0], revision=selected_revision)
    return replace(
        snapshot,
        batch=batch,
        environment_daily=(selected,),
        environment_daily_history=history,
        environment_daily_history_loaded=history_loaded,
    )


def test_versioned_member_uses_highest_revision_visible_at_batch_as_of() -> None:
    selected = EnvironmentDailyRecord(
        id=1,
        environment_date=date(2026, 9, 1),
        label_kind="fact",
        label_code="WIDE_RANGE",
        revision=1,
        is_current=False,
        available_at=datetime(2026, 9, 1, 12),
        schema_version="market-env-v1",
    )
    future = replace(
        selected,
        id=2,
        revision=2,
        is_current=True,
        available_at=datetime(2026, 9, 3, 12),
    )
    snapshot = _versioned_snapshot(history=(selected, future))
    snapshot = replace(snapshot, environment_daily=(selected,))

    result = _check(snapshot)

    assert result.status == "PASS"
    assert not any(
        item.code.startswith("ROUTE_ENVIRONMENT_DAILY_PIT_")
        for item in result.findings
    )


@pytest.mark.parametrize("raw_value", ["false", 0, None])
def test_environment_history_loaded_requires_real_boolean(raw_value: object) -> None:
    """Truthiness coercion must not turn an invalid Repository flag into PIT evidence."""

    snapshot = _versioned_snapshot(history_loaded=True)
    snapshot = replace(snapshot, environment_daily_history_loaded=raw_value)

    result = _check(snapshot)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    finding = next(
        item
        for item in result.findings
        if item.code == "ROUTE_ENVIRONMENT_DAILY_HISTORY_LOADED_INVALID"
    )
    assert finding.evidence["actual_type"] == type(raw_value).__name__


def test_versioned_member_selecting_stale_visible_revision_fails() -> None:
    selected = replace(
        _coherent_snapshot().environment_daily[0],
        revision=1,
        is_current=False,
    )
    newer = replace(
        selected,
        id=2,
        revision=2,
        is_current=True,
        available_at=datetime(2026, 9, 1, 18),
    )
    snapshot = _versioned_snapshot(history=(selected, newer))
    snapshot = replace(snapshot, environment_daily=(selected,))

    result = _check(snapshot)

    assert result.status == "FAIL"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_DAILY_PIT_REVISION_MISMATCH"
        for item in result.findings
    )


def test_versioned_member_without_history_is_data_blocked() -> None:
    snapshot = _versioned_snapshot(history=(), history_loaded=False)

    result = _check(snapshot)

    assert result.status == "BLOCKED_DATA_PRECONDITION"
    assert any(
        item.code == "ROUTE_ENVIRONMENT_DAILY_HISTORY_MISSING"
        for item in result.findings
    )


def test_pit_visibility_boundary_is_inclusive_at_available_at() -> None:
    selected = replace(
        _coherent_snapshot().environment_daily[0],
        revision=2,
        is_current=True,
        available_at=datetime(2026, 9, 2, 1, 17),
    )
    old = replace(selected, id=2, revision=1, is_current=False)
    snapshot = _versioned_snapshot(selected_revision=2, history=(old, selected))
    snapshot = replace(snapshot, environment_daily=(selected,))

    result = _check(snapshot)

    assert result.status == "PASS"
