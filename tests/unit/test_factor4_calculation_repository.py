"""Offline tests for the Factor 4.0 calculation-audit repository."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from db.client import ExecutionResult
from db.factor4_calculation_repository import (
    ActivePublishedPartition,
    CalculationRepositoryError,
    EnvironmentDailyRecord,
    Factor4CalculationRepository,
    _safe_metric_formula_link_values,
)


pytestmark = pytest.mark.unit


class StubTransaction:
    """Return ordered query responses and record every SQL operation."""

    def __init__(
        self,
        *,
        one_responses: list[dict[str, Any] | None],
        all_responses: list[list[dict[str, Any]]],
    ) -> None:
        """Store the predefined responses for one repository transaction."""

        self.one_responses = list(one_responses)
        self.all_responses = list(all_responses)
        self.operations: list[tuple[str, str, tuple[Any, ...] | None]] = []

    def execute(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> ExecutionResult:
        """Record one transaction-control statement."""

        self.operations.append(("execute", query, parameters))
        return ExecutionResult(rowcount=0, lastrowid=None)

    def fetch_one(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> dict[str, Any] | None:
        """Record a single-row query and consume its predefined response."""

        self.operations.append(("fetch_one", query, parameters))
        if not self.one_responses:
            raise AssertionError("no single-row response remains")
        return self.one_responses.pop(0)

    def fetch_all(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Record a multi-row query and consume its predefined response."""

        self.operations.append(("fetch_all", query, parameters))
        if not self.all_responses:
            raise AssertionError("no multi-row response remains")
        return self.all_responses.pop(0)


class StubDatabaseClient:
    """Expose a public transaction API backed by one stub transaction."""

    def __init__(self, transaction: StubTransaction) -> None:
        """Store the transaction returned to the repository."""

        self.transaction_stub = transaction
        self.transaction_count = 0

    @contextmanager
    def transaction(self) -> Iterator[StubTransaction]:
        """Yield the configured transaction and count context entries."""

        self.transaction_count += 1
        yield self.transaction_stub


def _batch_row() -> dict[str, Any]:
    """Build a complete active-published batch row."""

    return {
        "id": 6,
        "batch_uid": "batch-uid",
        "market_scope": "all",
        "label_kind": "fact",
        "route_profile_key": "default",
        "start_date": date(2024, 9, 2),
        "end_date": date(2026, 9, 1),
        "as_of_time": datetime(2026, 9, 2, 1, 17),
        "published_at": datetime(2026, 9, 2, 14, 20),
        "publication_uid": "publication-uid",
        "publish_version": "publish-v1",
        "evaluation_config_version": "env-eval-v1",
        "score_rule_version": "env-score-v1",
        "code_version": "git-sha",
        "status": "success",
        "publish_status": "published",
        "is_active": 1,
        "expected_metric_count": 12,
        "completed_metric_count": 10,
        "insufficient_metric_count": 2,
        "failed_metric_count": 0,
        "factor_set_snapshot_hash": "factor-hash",
        "environment_snapshot_hash": "environment-hash",
        "release_manifest_hash": None,
        "factor_set_snapshot": (
            '{"factor_count": 1, "members": ['
            '{"factor_ref": "sub_factor:10", "factor_type": "sub_factor", '
            '"factor_id": 10, '
            '"factor_version": "updated_at:2026-08-01T00:00:00Z"}]}'
        ),
        "evaluation_config": '{"minimum_route_score": 60}',
        "environment_status": '{"WIDE_RANGE": {"status": "success"}}',
    }


def test_list_active_published_partitions_discovers_all_profiles_read_only() -> None:
    """Discovery returns every active profile and uses no write SQL."""

    rows = [
        {
            "id": 7,
            "market_scope": "all",
            "route_profile_key": "qa_six_labels_20260905",
            "publication_uid": "pub-7",
            "publish_version": "v7",
            "published_at": datetime(2026, 9, 5, 2, 0),
        },
        {
            "id": 6,
            "market_scope": "all",
            "route_profile_key": "default",
            "publication_uid": "pub-6",
            "publish_version": "v6",
            "published_at": datetime(2026, 9, 4, 2, 0),
        },
    ]
    transaction = StubTransaction(one_responses=[], all_responses=[rows])
    repository = Factor4CalculationRepository(StubDatabaseClient(transaction))

    result = repository.list_active_published_partitions()

    assert result == (
        ActivePublishedPartition(
            id=7,
            market_scope="all",
            route_profile_key="qa_six_labels_20260905",
            publication_uid="pub-7",
            publish_version="v7",
            published_at=datetime(2026, 9, 5, 2, 0),
        ),
        ActivePublishedPartition(
            id=6,
            market_scope="all",
            route_profile_key="default",
            publication_uid="pub-6",
            publish_version="v6",
            published_at=datetime(2026, 9, 4, 2, 0),
        ),
    )
    assert transaction.operations[0][0] == "execute"
    assert all(op[0] != "execute" or "INSERT" not in op[1].upper() for op in transaction.operations)


def test_list_active_published_partitions_rejects_malformed_row() -> None:
    """Malformed discovery identity fails closed instead of selecting a profile."""

    transaction = StubTransaction(
        one_responses=[],
        all_responses=[
            [{
                "id": 7,
                "market_scope": "all",
                "route_profile_key": "default",
                "publication_uid": "pub-7",
                "publish_version": "v7",
                "published_at": "not-a-datetime",
            }],
        ],
    )
    repository = Factor4CalculationRepository(StubDatabaseClient(transaction))

    with pytest.raises(CalculationRepositoryError, match="published_at"):
        repository.list_active_published_partitions()


def _definition_row() -> dict[str, Any]:
    """Build a current catalog row joined to the frozen identity."""

    return {
        "factor_ref": "sub_factor:10",
        "factor_type": "sub_factor",
        "factor_id": 10,
        "batch_factor_version": "sha256:def",
        "serial_number": "SF10",
        "name": "factor-ten",
        "window": "24H",
        "factor_bar_interval": "1h",
        "formula_summary": "rolling mean",
        "definition_updated_at": datetime(2026, 8, 1),
    }


def _identity_row() -> dict[str, Any]:
    """Build one metric membership identity row.

    The table-level ``factor_version`` is the executable formula version;
    membership reconciliation uses the definition/catalog version carried in
    ``metric_identity.definition_factor_version``.
    """

    return {
        "factor_ref": "sub_factor:10",
        "factor_type": "sub_factor",
        "factor_id": 10,
        "factor_version": "sha256:def",
        "definition_factor_version": "updated_at:2026-08-01T00:00:00Z",
    }


def _detail_row() -> dict[str, Any]:
    """Build one formula-bearing factor detail row."""

    return {
        "id": 100,
        "factor_ref": "sub_factor:10",
        "factor_type": "sub_factor",
        "factor_id": 10,
        "batch_factor_version": "sha256:def",
        "is_sub_factor_id": 1,
        "serial_number": "SF10",
        "name": "factor-ten",
        "status": 2,
        "calc_logic": "mean(close, window)",
        "params": '{"window": 24}',
        "data_source_metadata": '{"fields": ["close"]}',
        "updated_at": datetime(2026, 8, 1),
    }


def _formula_row() -> dict[str, Any]:
    """Build one completed-run immutable formula evidence row."""

    return {
        "id": 101,
        "run_id": "run-1",
        "factor_ref": "sub_factor:10",
        "factor_type": "sub_factor",
        "factor_id": 10,
        "factor_version": "sha256:def",
        "is_sub_factor_id": 1,
        "calculation_mode": "direct",
        "factor_bar_interval": "1h",
        "factor_window_bars": "24H",
        "return_bar_interval": "1h",
        "forward_return_bars": 1,
        "formula_version": "python-ast-v1:abc",
        "formula_hash": "abc",
        "hash_algorithm": "sha256",
        "normalization_version": "python-ast-v1",
        "expression": "mean(close, window)",
        "required_fields": '["close"]',
        "lookback_json": "24",
        "lag_json": None,
        "missing_policy": "drop",
        "output_unit": None,
        "metadata_complete": 1,
        "metadata_warnings": "[]",
        "source_detail_id": 100,
        "recorded_at": datetime(2026, 8, 2, 1),
        "run_status": "completed",
        "run_completed_at": datetime(2026, 8, 2, 2),
        "evidence_recency": 1,
    }


def _evaluation_metric_row() -> dict[str, Any]:
    """Build one successful environment TS metric row."""

    row: dict[str, Any] = {
        "id": 301,
        "eval_batch_id": 6,
        "factor_ref": "sub_factor:10",
        "factor_type": "sub_factor",
        "factor_id": 10,
        "factor_version": "sha256:def",
        "market_scope": "all",
        "label_kind": "fact",
        "label_code": "WIDE_RANGE",
        "evaluation_type": "time_series",
        "interval": "1h",
        "return_bar_interval": "1h",
        "forward_return_bars": 1,
        "window_scope": "trailing_730d",
        "sample_start_date": date(2024, 9, 2),
        "sample_end_date": date(2026, 9, 1),
        "mean_ic": "-0.04",
        "mean_rank_ic": "-0.03",
        "icir": "-0.8",
        "rank_icir": "-0.6",
        "metric_status": "success",
        "is_valid": 1,
        "scoring_version": "env-score-v1",
        "metric_pair_identity_hash": "a" * 64,
        "route_admission_mode": "any_valid_scope",
        "route_valid_scopes": '["time_series"]',
        "route_invalid_scopes": '["cross_sectional"]',
        "route_is_eligible": "true",
        "route_eligibility_routing_score": "80.1",
        "route_minimum_route_score": "60",
        "time_series_score": "85.5",
        "cross_sectional_score": None,
        "routing_score": "80.1",
        "confidence": "0.9",
        "error_code": None,
        "error_message": None,
    }
    return row


def _routing_metric_evidence_row() -> dict[str, Any]:
    """Build compact calculation evidence for one scored environment metric."""

    return {
        "id": 301,
        "metric_identity": '{"factor_window_bars": "24H"}',
        "score_components": '{"strength": 80}',
        "direction": '{"predictive_direction": -1}',
        "oos": '{"retention": 0.8}',
        "aggregation": '{"calculation_mode": "direct"}',
        "directed_mean_ic": "0.04",
        "directed_mean_rank_ic": "0.03",
        "directed_icir": "0.8",
        "directed_rank_icir": "0.6",
    }


def _route_row() -> dict[str, Any]:
    """Build one published route row with tie-breaking evidence."""

    return {
        "id": 401,
        "publication_uid": "publication-uid",
        "eval_batch_id": 6,
        "metric_id": 301,
        "market_scope": "all",
        "route_profile_key": "default",
        "environment_date": date(2026, 9, 1),
        "label_kind": "fact",
        "label_code": "WIDE_RANGE",
        "as_of_time": datetime(2026, 9, 2, 1, 17),
        "factor_ref": "sub_factor:10",
        "factor_type": "sub_factor",
        "factor_id": 10,
        "factor_version": "sha256:def",
        "rank_no": 1,
        "routing_score": "80.1",
        "confidence": "0.9",
        "time_series_score": "85.5",
        "cross_sectional_score": None,
        "is_eligible": 1,
        "reject_reason_code": None,
        "evidence": '{"metric_ids": {"time_series": 301}}',
        "score_rule_version": "env-score-v1",
        "publish_version": "publish-v1",
        "is_active": 1,
    }


def _route_snapshot_batch_row() -> dict[str, Any]:
    """Build the publication identity used by the lightweight route read."""

    return {
        "id": 6,
        "publication_uid": "publication-uid",
        "publish_version": "publish-v1",
        "market_scope": "all",
        "route_profile_key": "default",
    }


def _route_ranking_row() -> dict[str, Any]:
    """Build one active route row without heavyweight evidence JSON."""

    route = _route_row()
    names = (
        "id",
        "metric_id",
        "environment_date",
        "label_kind",
        "label_code",
        "as_of_time",
        "factor_ref",
        "factor_type",
        "factor_id",
        "factor_version",
        "rank_no",
        "routing_score",
        "confidence",
        "time_series_score",
        "cross_sectional_score",
        "score_rule_version",
    )
    return {name: route[name] for name in names}


def _happy_transaction() -> StubTransaction:
    """Build a transaction that returns one row for every evidence family."""

    return StubTransaction(
        one_responses=[{"captured_at": datetime(2026, 9, 4, 12)}],
        all_responses=[
            [_batch_row()],
            [_identity_row()],
            [_definition_row()],
            [_detail_row()],
            [_evaluation_metric_row()],
            [_routing_metric_evidence_row()],
            [_formula_row()],
            [_route_row()],
        ],
    )


def test_read_calculation_snapshot_returns_typed_atomic_evidence() -> None:
    """The public read should normalize every evidence family in one transaction."""

    transaction = _happy_transaction()
    client = StubDatabaseClient(transaction)

    snapshot = Factor4CalculationRepository(client).read_calculation_snapshot()

    assert client.transaction_count == 1
    assert snapshot.batch.id == 6
    assert snapshot.batch.factor_set_snapshot["factor_count"] == 1
    assert snapshot.membership_differences.missing_from_metrics == ()
    assert snapshot.membership_differences.unexpected_in_metrics == ()
    assert snapshot.membership_differences.missing_definition_versions == ()
    assert snapshot.definitions[0].batch_factor_version == (
        "updated_at:2026-08-01T00:00:00Z"
    )
    assert snapshot.formula_evidence[0].batch_factor_version == (
        "updated_at:2026-08-01T00:00:00Z"
    )
    assert snapshot.evaluation_metrics[0].factor_version == "sha256:def"
    assert snapshot.routes[0].factor_version == "sha256:def"
    assert snapshot.details[0].params == {"window": 24}
    assert snapshot.formula_evidence[0].lookback == 24
    assert snapshot.formula_evidence[0].required_fields == ("close",)
    assert snapshot.evaluation_metrics[0].metric_payload["score_components"] == {
        "strength": 80
    }
    assert snapshot.evaluation_metrics[0].metric_payload["oos"] == {
        "retention": 0.8
    }
    assert snapshot.evaluation_metrics[0].routing_score == Decimal("80.1")
    assert snapshot.evaluation_metrics[0].sample_start_date == date(2024, 9, 2)
    assert snapshot.evaluation_metrics[0].mean_ic == Decimal("-0.04")
    assert snapshot.evaluation_metrics[0].rank_icir == Decimal("-0.6")
    assert snapshot.evaluation_metrics[0].metric_pair_identity_hash == "a" * 64
    assert snapshot.evaluation_metrics[0].metric_payload["directed_mean_ic"] == (
        Decimal("0.04")
    )
    assert snapshot.evaluation_metrics[0].metric_payload["directed_rank_icir"] == (
        Decimal("0.6")
    )
    assert snapshot.evaluation_metrics[0].route_eligibility == {
        "admission_mode": "any_valid_scope",
        "valid_scopes": ["time_series"],
        "invalid_scopes": ["cross_sectional"],
        "is_eligible": True,
        "routing_score": Decimal("80.1"),
        "minimum_route_score": Decimal("60"),
    }
    assert snapshot.routes[0].route_profile_key == "default"
    assert snapshot.routes[0].evidence == {"metric_ids": {"time_series": 301}}

    control_sql = [
        query
        for operation, query, _ in transaction.operations
        if operation == "execute"
    ]
    assert control_sql == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
        "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY",
    ]
    batch_query = transaction.operations[3]
    assert batch_query[0] == "fetch_all"
    assert batch_query[2] == ("all", "default")
    route_query = transaction.operations[-1]
    assert route_query[2] == (6, "publication-uid", "publish-v1")
    assert "route.is_active = 1" in route_query[1]

    metric_queries = [
        query
        for operation, query, _ in transaction.operations
        if operation == "fetch_all"
        and "FROM market_environment_factor_metric" in query
    ]
    compact_query = next(query for query in metric_queries if "route_eligibility" in query)
    evidence_query = next(query for query in metric_queries if "score_components" in query)
    assert "sample_start_date" in compact_query
    assert "mean_rank_ic" in compact_query
    assert "SHA2(" in compact_query
    assert "AS metric_pair_identity_hash" in compact_query
    membership_query = next(
        query
        for operation, query, _ in transaction.operations
        if operation == "fetch_all"
        and "definition_factor_version" in query
    )
    assert "$.metric_identity.definition_factor_version" in membership_query
    assert "'$.route_eligibility'" not in compact_query
    assert "AS metric_payload" not in compact_query
    assert "routing_score IS NOT NULL" not in evidence_query
    assert "AS metric_payload" in evidence_query
    assert "'$.directed_mean_ic'" in evidence_query
    assert "'$.directed_rank_icir'" in evidence_query
    formula_query = next(
        query
        for operation, query, _ in transaction.operations
        if operation == "fetch_all"
        and "factor_ic_run_formula_evidence" in query
    )
    assert "formula_factor_version" in formula_query
    assert "ROW_NUMBER" in formula_query

    mutation = re.compile(r"\b(?:INSERT|UPDATE|DELETE|REPLACE|TRUNCATE|DROP|ALTER)\b", re.I)
    assert all(not mutation.search(query) for _, query, _ in transaction.operations)


def test_metric_payload_preserves_unscored_rejections_and_explicit_null() -> None:
    """Unscored metrics retain null/absent distinctions and rejection evidence."""

    row = _evaluation_metric_row()
    row["routing_score"] = None
    row["is_valid"] = 0
    payload = {
        "metric_identity": {"factor_window_bars": "24H"},
        "directed_mean_rank_ic": None,
        "reject_reasons": ["NET_RETURN_NOT_POSITIVE"],
        "is_valid": False,
        "oos": {"valid_fold_count": 0, "retention": None},
        "route_eligibility": {"is_eligible": False, "routing_score": None},
    }
    transaction = StubTransaction(one_responses=[], all_responses=[
        [row], [{"id": row["id"], "metric_payload": payload}],
    ])

    stored = Factor4CalculationRepository._read_evaluation_metric_rows(transaction, 6)[0]

    assert stored["metric_payload"] == payload
    assert stored["metric_payload"]["directed_mean_rank_ic"] is None
    assert "directed_mean_ic" not in stored["metric_payload"]
    assert stored["route_eligibility"]["routing_score"] is None


def test_read_calculation_snapshot_reports_metric_membership_drift() -> None:
    """Metric membership drift should remain structured business evidence."""

    metric_identity = _identity_row()
    metric_identity["factor_ref"] = "sub_factor:11"
    metric_identity["factor_id"] = 11
    transaction = _happy_transaction()
    transaction.all_responses[1] = [metric_identity]

    snapshot = Factor4CalculationRepository(
        StubDatabaseClient(transaction)
    ).read_calculation_snapshot()

    assert [
        identity.factor_id
        for identity in snapshot.membership_differences.missing_from_metrics
    ] == [10]
    assert [
        identity.factor_id
        for identity in snapshot.membership_differences.unexpected_in_metrics
    ] == [11]


def test_read_calculation_snapshot_blocks_missing_definition_membership_version() -> None:
    """Missing payload definition versions remain explicit data preconditions."""

    identity = _identity_row()
    identity.pop("definition_factor_version")
    transaction = _happy_transaction()
    transaction.all_responses[1] = [identity]

    snapshot = Factor4CalculationRepository(
        StubDatabaseClient(transaction)
    ).read_calculation_snapshot()

    assert snapshot.membership_differences.missing_from_metrics == ()
    assert snapshot.membership_differences.unexpected_in_metrics == ()
    assert len(snapshot.membership_differences.missing_definition_versions) == 1
    issue = snapshot.membership_differences.missing_definition_versions[0]
    assert issue.factor_ref == "sub_factor:10"
    assert issue.reason == "definition_factor_version_missing_or_invalid"


def test_read_calculation_snapshot_requires_evidence_for_scored_metrics() -> None:
    """A scored metric without its compact calculation evidence must fail closed."""

    transaction = _happy_transaction()
    transaction.all_responses[5] = []

    with pytest.raises(CalculationRepositoryError, match="missing routing evidence"):
        Factor4CalculationRepository(
            StubDatabaseClient(transaction)
        ).read_calculation_snapshot()


def test_read_evaluation_metric_rows_rejects_duplicate_metric_ids() -> None:
    """Duplicate primary-key rows must not be admitted into one calculation snapshot."""

    first = _evaluation_metric_row()
    duplicate = dict(first)
    duplicate["evaluation_type"] = "cross_sectional"
    transaction = StubTransaction(
        one_responses=[],
        all_responses=[
            [first, duplicate],
            [_routing_metric_evidence_row()],
        ],
    )

    with pytest.raises(
        CalculationRepositoryError,
        match="duplicate metric id",
    ):
        Factor4CalculationRepository._read_evaluation_metric_rows(transaction, 6)


def test_unscored_metric_keeps_pair_hash_without_full_identity() -> None:
    """Unscored TS/CS rows should remain pairable without heavyweight identity."""

    metric = _evaluation_metric_row()
    metric["routing_score"] = None
    transaction = _happy_transaction()
    transaction.all_responses[4] = [metric]
    transaction.all_responses[5] = []
    transaction.all_responses[7] = []

    snapshot = Factor4CalculationRepository(
        StubDatabaseClient(transaction)
    ).read_calculation_snapshot()

    stored = snapshot.evaluation_metrics[0]
    assert stored.metric_pair_identity_hash == "a" * 64
    assert stored.metric_identity is None
    assert stored.metric_payload == {
        "route_eligibility": stored.route_eligibility,
    }


def test_unscored_metric_preserves_metric_identity_from_compact_query() -> None:
    """Formula identity must survive even when a metric has no routing score."""

    metric = _evaluation_metric_row()
    metric["routing_score"] = None
    metric["metric_identity"] = (
        '{"factor_version":"sha256:exec",'
        '"definition_factor_version":"updated_at:2026-08-01T00:00:00Z",'
        '"factor_window_bars":"24H"}'
    )
    transaction = _happy_transaction()
    transaction.all_responses[4] = [metric]
    transaction.all_responses[5] = []
    transaction.all_responses[7] = []

    snapshot = Factor4CalculationRepository(
        StubDatabaseClient(transaction)
    ).read_calculation_snapshot()

    stored = snapshot.evaluation_metrics[0]
    assert stored.metric_identity == {
        "factor_version": "sha256:exec",
        "definition_factor_version": "updated_at:2026-08-01T00:00:00Z",
        "factor_window_bars": "24H",
    }
    assert stored.metric_payload is not None
    assert stored.metric_payload["metric_identity"] == stored.metric_identity


def test_formula_evidence_query_adds_metric_linked_historical_rows() -> None:
    """An exact old run must be fetched in addition to the latest context row."""

    latest = _formula_row()
    linked = dict(latest)
    linked.update(
        {
            "id": 99,
            "run_id": "run-old",
            "formula_hash": "hash-old",
            "formula_version": "version-old",
        }
    )
    metric = SimpleNamespace(
        factor_id=10,
        factor_type="sub_factor",
        metric_identity={
            "run_id": "run-old",
            "formula_hash": "hash-old",
            "formula_version": "version-old",
        },
    )
    transaction = StubTransaction(
        one_responses=[],
        all_responses=[[latest], [linked]],
    )
    identities = {
        (True, 10): {
            "factor_ref": "sub_factor:10",
            "factor_type": "sub_factor",
            "factor_id": 10,
            "batch_factor_version": "updated_at:2026-08-01T00:00:00Z",
        }
    }

    rows = Factor4CalculationRepository._read_formula_evidence_rows(
        transaction,
        identities,
        (metric,),
    )

    assert {row["id"] for row in rows} == {99, 101}
    linked_query = transaction.operations[1][1]
    assert "e.run_id = %s" in linked_query
    assert "e.formula_hash = %s" in linked_query
    assert "e.formula_version = %s" in linked_query


def test_read_published_route_snapshot_returns_only_active_ranking_fields() -> None:
    """Repeat reads should use a lightweight atomic active-route snapshot."""

    transaction = StubTransaction(
        one_responses=[{"captured_at": datetime(2026, 9, 4, 13)}],
        all_responses=[[_route_snapshot_batch_row()], [_route_ranking_row()]],
    )

    snapshot = Factor4CalculationRepository(
        StubDatabaseClient(transaction)
    ).read_published_route_snapshot()

    assert snapshot.batch_id == 6
    assert snapshot.publication_uid == "publication-uid"
    assert snapshot.publish_version == "publish-v1"
    assert snapshot.routes[0].factor_ref == "sub_factor:10"
    assert snapshot.routes[0].routing_score == Decimal("80.1")

    batch_query = transaction.operations[3]
    route_query = transaction.operations[4]
    assert batch_query[2] == ("all", "default")
    assert route_query[2] == (6, "publication-uid", "publish-v1")
    assert "is_active = 1" in route_query[1]
    assert "is_eligible = 1" in route_query[1]
    assert "evidence" not in route_query[1]


@pytest.mark.parametrize("batch_rows", [[], [_batch_row(), _batch_row()]])
def test_read_calculation_snapshot_requires_one_active_published_batch(
    batch_rows: list[dict[str, Any]],
) -> None:
    """Missing or ambiguous latest batches should stop all downstream reads."""

    transaction = StubTransaction(
        one_responses=[{"captured_at": datetime(2026, 9, 4, 12)}],
        all_responses=[batch_rows],
    )

    with pytest.raises(CalculationRepositoryError, match="latest_published_batch"):
        Factor4CalculationRepository(StubDatabaseClient(transaction)).read_calculation_snapshot()

    assert len(transaction.operations) == 4


def test_read_calculation_snapshot_reports_invalid_json_without_leaking_value() -> None:
    """Malformed required JSON should produce a field-level fail-closed error."""

    batch = _batch_row()
    batch["evaluation_config"] = "not-json-secret-value"
    transaction = StubTransaction(
        one_responses=[{"captured_at": datetime(2026, 9, 4, 12)}],
        all_responses=[[batch]],
    )

    with pytest.raises(CalculationRepositoryError) as captured:
        Factor4CalculationRepository(StubDatabaseClient(transaction)).read_calculation_snapshot()

    assert captured.value.stage == "latest_published_batch"
    assert "evaluation_config" in str(captured.value)
    assert "not-json-secret-value" not in str(captured.value)


def test_read_calculation_snapshot_preserves_optional_environment_snapshot() -> None:
    """CALC-513 can inspect the frozen environment members when the column exists."""

    batch = _batch_row()
    batch["environment_snapshot"] = {
        "members": [{"environment_date": "2026-09-01"}],
        "missing_dates": [],
        "as_of_time": "2026-09-02T01:17:00",
    }
    # Exercise the row conversion directly so this test stays independent of
    # the many subsequent queries in a complete snapshot read.
    parsed = Factor4CalculationRepository._to_batch(batch, "unit")

    assert parsed.environment_snapshot == batch["environment_snapshot"]


def test_read_calculation_snapshot_reads_referenced_daily_rows_by_bound_ids() -> None:
    """The calculation snapshot includes only daily rows named by the frozen JSON."""

    batch = _batch_row()
    batch["environment_snapshot"] = {
        "members": [{"daily_id": 17, "environment_date": "2026-09-01"}],
        "missing_dates": [],
        "as_of_time": "2026-09-02T01:17:00",
    }
    daily = {
        "id": 17,
        "environment_date": date(2026, 9, 1),
        "label_kind": "fact",
        "label_code": "WIDE_RANGE",
        "revision": 1,
        "is_current": 1,
        "available_at": datetime(2026, 9, 1, 12),
        "schema_version": "market-env-v1",
    }
    transaction = StubTransaction(
        one_responses=[{"captured_at": datetime(2026, 9, 4, 12)}],
        all_responses=[
            [batch],
            [daily],
            [_identity_row()],
            [_definition_row()],
            [_detail_row()],
            [_evaluation_metric_row()],
            [_routing_metric_evidence_row()],
            [_formula_row()],
            [_route_row()],
        ],
    )

    snapshot = Factor4CalculationRepository(
        StubDatabaseClient(transaction)
    ).read_calculation_snapshot()

    assert snapshot.environment_daily == (
        EnvironmentDailyRecord(
            id=17,
            environment_date=date(2026, 9, 1),
            label_kind="fact",
            label_code="WIDE_RANGE",
            revision=1,
            is_current=True,
            available_at=datetime(2026, 9, 1, 12),
            schema_version="market-env-v1",
        ),
    )
    daily_operation = next(
        operation
        for operation in transaction.operations
        if operation[0] == "fetch_all" and "market_environment_daily" in operation[1]
    )
    assert daily_operation[2] == (17,)
    assert "IN (%s)" in daily_operation[1]


def test_read_calculation_snapshot_loads_history_only_for_versioned_members() -> None:
    """Explicit member revisions request a narrow all-revision PIT projection."""

    batch = _batch_row()
    batch["environment_snapshot"] = {
        "members": [
            {
                "daily_id": 17,
                "environment_date": "2026-09-01",
                "label_kind": "fact",
                "revision": 2,
            }
        ],
        "missing_dates": [],
        "as_of_time": "2026-09-02T01:17:00",
    }
    selected = {
        "id": 17,
        "environment_date": date(2026, 9, 1),
        "label_kind": "fact",
        "label_code": "WIDE_RANGE",
        "revision": 2,
        "is_current": 1,
        "available_at": datetime(2026, 9, 1, 12),
        "schema_version": "market-env-v1",
    }
    history = [
        {**selected, "revision": 1, "is_current": 0},
        selected,
    ]
    transaction = StubTransaction(
        one_responses=[{"captured_at": datetime(2026, 9, 4, 12)}],
        all_responses=[
            [batch],
            [selected],
            history,
            [_identity_row()],
            [_definition_row()],
            [_detail_row()],
            [_evaluation_metric_row()],
            [_routing_metric_evidence_row()],
            [_formula_row()],
            [_route_row()],
        ],
    )

    snapshot = Factor4CalculationRepository(
        StubDatabaseClient(transaction)
    ).read_calculation_snapshot()

    assert snapshot.environment_daily_history_loaded is True
    assert tuple(row.revision for row in snapshot.environment_daily_history) == (1, 2)
    history_operation = next(
        operation
        for operation in transaction.operations
        if operation[0] == "fetch_all" and "ORDER BY environment_date" in operation[1]
    )
    assert "label_kind = %s" in history_operation[1]
    assert "environment_date IN (%s)" in history_operation[1]
    assert history_operation[2] == ("fact", date(2026, 9, 1))


def test_read_calculation_snapshot_does_not_query_history_without_revision() -> None:
    """Legacy daily_id-only snapshots retain the lightweight read path."""

    batch = _batch_row()
    batch["environment_snapshot"] = {
        "members": [{"daily_id": 17, "environment_date": "2026-09-01"}],
        "missing_dates": [],
        "as_of_time": "2026-09-02T01:17:00",
    }
    daily = {
        "id": 17,
        "environment_date": date(2026, 9, 1),
        "label_kind": "fact",
        "label_code": "WIDE_RANGE",
        "revision": 1,
        "is_current": 1,
        "available_at": datetime(2026, 9, 1, 12),
        "schema_version": "market-env-v1",
    }
    transaction = StubTransaction(
        one_responses=[{"captured_at": datetime(2026, 9, 4, 12)}],
        all_responses=[
            [batch],
            [daily],
            [_identity_row()],
            [_definition_row()],
            [_detail_row()],
            [_evaluation_metric_row()],
            [_routing_metric_evidence_row()],
            [_formula_row()],
            [_route_row()],
        ],
    )

    snapshot = Factor4CalculationRepository(
        StubDatabaseClient(transaction)
    ).read_calculation_snapshot()

    assert snapshot.environment_daily_history_loaded is False
    assert snapshot.environment_daily_history == ()
    assert not any(
        operation[0] == "fetch_all" and "ORDER BY environment_date" in operation[1]
        for operation in transaction.operations
    )


def test_environment_history_query_groups_exact_keys_with_bound_parameters() -> None:
    """Several label kinds do not create an unintended date/kind cross product."""

    transaction = StubTransaction(one_responses=[], all_responses=[[]])
    keys = (
        (date(2026, 9, 1), "fact"),
        (date(2026, 9, 2), "fact"),
        (date(2026, 9, 3), "forecast"),
    )

    rows = Factor4CalculationRepository._read_environment_daily_history_rows(
        transaction,
        keys,
    )

    assert rows == []
    operation = transaction.operations[0]
    assert operation[0] == "fetch_all"
    assert operation[1].count("label_kind = %s") == 2
    assert operation[1].count("environment_date IN") == 2
    assert operation[2] == (
        "fact",
        date(2026, 9, 1),
        date(2026, 9, 2),
        "forecast",
        date(2026, 9, 3),
    )


@pytest.mark.parametrize("history_present", [False, True])
def test_full_calendar_history_is_read_even_without_any_frozen_members(history_present: bool) -> None:
    """An empty frozen declaration cannot hide daily rows from the independent read."""
    transaction = _happy_transaction()
    batch = transaction.all_responses[0][0]
    batch["environment_snapshot"] = {"members": [], "missing_dates": ["2026-09-01"]}
    history = [{"id": 17, "environment_date": date(2026, 9, 1), "label_kind": "fact",
                "label_code": "CHOPPY_UP", "label_status": "ready", "revision": 1,
                "is_current": 0, "available_at": datetime(2026, 9, 1), "schema_version": "v1"}] if history_present else []
    transaction.all_responses.insert(1, history)
    snapshot = Factor4CalculationRepository(StubDatabaseClient(transaction)).read_calculation_snapshot(
        include_full_environment_history=True,
    )
    assert snapshot.environment_daily_history_loaded is True
    assert snapshot.environment_daily_history_range == (batch["start_date"], batch["end_date"], batch["label_kind"])
    assert len(snapshot.environment_daily_history) == int(history_present)
    assert snapshot.environment_daily == ()
    operations = [operation for operation in transaction.operations if "FROM market_environment_daily" in operation[1]]
    assert len(operations) == 1
    query, parameters = operations[0][1:]
    assert parameters == ("fact", date(2024, 9, 2), date(2026, 9, 1))
    where = query.split("WHERE", 1)[1]
    assert "environment_date >= %s AND environment_date <= %s" in where
    assert "is_current" not in where and "available_at" not in where and " IN " not in where
    assert "label_status" in query
    if history_present:
        assert snapshot.environment_daily_history[0].label_status == "ready"


def test_full_range_history_replaces_member_history_query_without_changing_selected_rows() -> None:
    """Opt-in performs one full history query while retaining exact member-ID evidence."""
    transaction = _happy_transaction()
    batch = transaction.all_responses[0][0]
    batch["environment_snapshot"] = {"members": [{"daily_id": 17, "environment_date": "2026-09-01", "revision": 1}]}
    selected = {"id": 17, "environment_date": date(2026, 9, 1), "label_kind": "fact",
                "label_code": "WIDE_RANGE", "revision": 1, "is_current": 1,
                "available_at": datetime(2026, 9, 1), "schema_version": "v1"}
    omitted = {**selected, "id": 18, "environment_date": date(2026, 8, 31), "label_status": "ready"}
    transaction.all_responses[1:1] = [[selected], [{**selected, "label_status": "ready"}, omitted]]
    snapshot = Factor4CalculationRepository(StubDatabaseClient(transaction)).read_calculation_snapshot(
        include_full_environment_history=True,
    )
    assert [row.id for row in snapshot.environment_daily] == [17]
    assert [row.id for row in snapshot.environment_daily_history] == [17, 18]
    history_queries = [query for _operation, query, _parameters in transaction.operations if "ORDER BY environment_date" in query]
    assert len(history_queries) == 1
    assert "environment_date IN" not in history_queries[0]


@pytest.mark.parametrize("value", [1, None, "true"])
def test_full_history_flag_rejects_nonboolean_before_database_access(value: object) -> None:
    """Only an explicit boolean may widen the history query."""
    transaction = _happy_transaction()
    client = StubDatabaseClient(transaction)
    with pytest.raises(ValueError, match="must be a boolean"):
        Factor4CalculationRepository(client).read_calculation_snapshot(include_full_environment_history=value)
    assert client.transaction_count == 0


def test_full_range_query_binds_kind_instead_of_interpolating_it() -> None:
    """Query syntax is independent of values, including unusual kind strings."""
    transaction = StubTransaction(one_responses=[], all_responses=[[]])
    kind = "fact' OR 1=1 --"
    Factor4CalculationRepository._read_environment_daily_range_history_rows(transaction, date(2026, 1, 1), date(2026, 1, 2), kind)
    query, parameters = transaction.operations[0][1:]
    assert kind not in query
    assert parameters == (kind, date(2026, 1, 1), date(2026, 1, 2))


def test_environment_daily_nullable_label_code_is_preserved() -> None:
    """not_ready/invalid daily rows may have a NULL label_code in MySQL."""

    row = {
        "id": 17,
        "environment_date": date(2026, 9, 1),
        "label_kind": "fact",
        "label_code": None,
        "revision": 1,
        "is_current": 0,
        "available_at": datetime(2026, 9, 1, 12),
        "schema_version": "market-env-v1",
    }

    parsed = Factor4CalculationRepository._to_environment_daily(row, "unit")

    assert parsed.label_code is None


def test_batch_selection_sorts_without_large_json_payload() -> None:
    """Batch identity ordering must not filesort the environment snapshot JSON."""

    transaction = StubTransaction(
        one_responses=[{"captured_at": datetime(2026, 9, 4, 12)}],
        all_responses=[
            [{"id": 6}],
            [_batch_row()],
            [_identity_row()],
            [_definition_row()],
            [_detail_row()],
            [_evaluation_metric_row()],
            [_routing_metric_evidence_row()],
            [_formula_row()],
            [_route_row()],
        ],
    )

    Factor4CalculationRepository(StubDatabaseClient(transaction)).read_calculation_snapshot()

    candidate_query = next(
        operation[1]
        for operation in transaction.operations
        if operation[0] == "fetch_all" and "LIMIT 2" in operation[1]
    )
    assert "environment_snapshot" not in candidate_query


def test_read_calculation_snapshot_rejects_blank_partition_selectors() -> None:
    """Blank selectors should fail before a database transaction is opened."""

    transaction = StubTransaction(one_responses=[], all_responses=[])
    client = StubDatabaseClient(transaction)

    with pytest.raises(ValueError, match="must be non-empty"):
        Factor4CalculationRepository(client).read_calculation_snapshot(" ", "default")

    assert client.transaction_count == 0


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (7, 7),
        (7.0, 7),
        (Decimal("7.00"), 7),
        ("  +007  ", 7),
        (-3.0, -3),
        (Decimal("-3E+2"), -300),
    ],
)
def test_required_int_accepts_only_integral_representations(
    raw_value: Any,
    expected: int,
) -> None:
    """Integral DB/JSON representations normalize without lossy truncation."""

    assert Factor4CalculationRepository._required_int(
        {"value": raw_value},
        "value",
        "unit",
    ) == expected


def test_optional_int_accepts_null() -> None:
    """Nullable integer columns preserve an explicit null value."""

    assert Factor4CalculationRepository._optional_int(
        {"value": None},
        "value",
        "unit",
    ) is None


@pytest.mark.parametrize(
    "raw_value",
    [
        True,
        False,
        1.25,
        Decimal("1.25"),
        Decimal("NaN"),
        Decimal("Infinity"),
        float("nan"),
        float("inf"),
        float("-inf"),
        "1.0",
        "nan",
        "infinity",
        b"7",
        object(),
    ],
)
def test_required_int_rejects_non_integral_or_non_finite_values(raw_value: Any) -> None:
    """Invalid integer representations fail closed instead of being truncated."""

    with pytest.raises(CalculationRepositoryError, match="column 'value' must be an integer"):
        Factor4CalculationRepository._required_int({"value": raw_value}, "value", "unit")


@pytest.mark.parametrize("raw_value", [1.25, Decimal("1.25"), float("nan"), float("inf")])
def test_optional_int_rejects_non_integral_or_non_finite_values(raw_value: Any) -> None:
    """Optional integers apply the same strict validation when non-null."""

    with pytest.raises(CalculationRepositoryError, match="column 'value' must be an integer"):
        Factor4CalculationRepository._optional_int({"value": raw_value}, "value", "unit")


@pytest.mark.parametrize(
    "raw_value",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        Decimal("NaN"),
        Decimal("Infinity"),
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_required_decimal_rejects_non_finite_values(raw_value: Any) -> None:
    """Non-finite numeric values must fail before route quantization can crash."""

    with pytest.raises(
        CalculationRepositoryError,
        match="column 'value' must be a finite decimal",
    ):
        Factor4CalculationRepository._required_decimal(
            {"value": raw_value},
            "value",
            "unit",
        )


def test_formula_link_values_normalize_nested_ids_and_reject_conflicts() -> None:
    """SQL link predicates use canonical scalar values only."""

    valid = _safe_metric_formula_link_values(
        {
            "formula_identity": {
                "formula_evidence_id": "001",
                "run_id": "run-1",
                "formula_hash": "hash-1",
                "formula_version": "version-1",
            }
        }
    )
    assert valid == {
        "formula_evidence_id": 1,
        "run_id": "run-1",
        "formula_hash": "hash-1",
        "formula_version": "version-1",
    }

    conflicted = _safe_metric_formula_link_values(
        {
            "formula_hash": "hash-top",
            "formula": {"formula_hash": "hash-nested"},
            "run_id": ["run-1"],
            "formula_evidence_id": 1.5,
        }
    )
    assert conflicted == {}


@pytest.mark.parametrize("raw_factor_id", [1.9, "1.9", Decimal("1.9")])
def test_definition_rows_reject_non_integral_factor_id(raw_factor_id: Any) -> None:
    """Catalog factor IDs must not be silently truncated during enrichment."""

    transaction = StubTransaction(
        one_responses=[],
        all_responses=[
            [
                {
                    "factor_id": raw_factor_id,
                    "serial_number": "SF10",
                    "name": "factor-ten",
                    "window": "24H",
                    "factor_bar_interval": "1h",
                    "formula_summary": "rolling mean",
                    "definition_updated_at": datetime(2026, 8, 1),
                }
            ]
        ],
    )
    identities = {
        (True, 10): {
            "factor_ref": "sub_factor:10",
            "factor_type": "sub_factor",
            "factor_id": 10,
            "batch_factor_version": "updated_at:2026-08-01T00:00:00Z",
        }
    }

    with pytest.raises(
        CalculationRepositoryError,
        match="column 'factor_id' must be an integer",
    ):
        Factor4CalculationRepository._read_definition_rows(
            transaction,
            identities,
        )


def test_required_date_rejects_datetime_subclass() -> None:
    """Calendar-day fields must not accept timestamp values."""

    with pytest.raises(
        CalculationRepositoryError,
        match="column 'value' must be a date",
    ):
        Factor4CalculationRepository._required_date(
            {"value": datetime(2026, 9, 4, 12)},
            "value",
            "unit",
        )
