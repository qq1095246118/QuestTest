"""Factor 4.0 calculation-audit data access for the test database."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

from db.client import DatabaseClient, DatabaseTransaction


JsonObject = dict[str, Any]


class CalculationRepositoryError(RuntimeError):
    """Report a fail-closed schema, snapshot, or row-shape problem.

    ``stage`` identifies the semantic read that failed. The exception message
    deliberately omits connection details and bound values; the original
    driver exception remains available through exception chaining.
    """

    def __init__(self, stage: str, message: str) -> None:
        """Initialize a repository error.

        ``stage`` names the failed read and ``message`` contains a
        credential-free diagnostic. No value is returned.
        """

        self.stage = stage
        super().__init__(f"{stage}: {message}")


@dataclass(frozen=True)
class PublishedEvaluationBatch:
    """Describe the exact active, successful, published evaluation batch."""

    id: int
    batch_uid: str
    market_scope: str
    label_kind: str
    route_profile_key: str
    start_date: date
    end_date: date
    as_of_time: datetime
    published_at: datetime
    publication_uid: str
    publish_version: str
    evaluation_config_version: str
    score_rule_version: str
    code_version: str
    status: str
    publish_status: str
    is_active: bool
    expected_metric_count: int
    completed_metric_count: int
    insufficient_metric_count: int
    failed_metric_count: int
    factor_set_snapshot_hash: str
    environment_snapshot_hash: str
    release_manifest_hash: str | None
    factor_set_snapshot: JsonObject
    evaluation_config: JsonObject
    environment_status: JsonObject


@dataclass(frozen=True)
class FactorIdentity:
    """Describe one factor identity exactly as recorded by one source."""

    factor_ref: str
    factor_type: str
    factor_id: int
    factor_version: str


@dataclass(frozen=True)
class FactorMembershipVersionIssue:
    """Describe a metric member whose definition version cannot be trusted.

    ``market_environment_factor_metric.factor_version`` identifies the
    executable formula version.  Membership reconciliation needs the
    definition/catalog version stored in ``metric_identity`` instead.  This
    record keeps the known factor identity and executable version for
    diagnostics when that definition version is missing or contradictory.
    """

    factor_ref: str
    factor_type: str
    factor_id: int
    reason: str
    executable_factor_version: str | None = None


@dataclass(frozen=True)
class FactorMembershipDifferences:
    """Expose bidirectional drift between batch members and metric members."""

    missing_from_metrics: tuple[FactorIdentity, ...]
    unexpected_in_metrics: tuple[FactorIdentity, ...]
    missing_definition_versions: tuple[FactorMembershipVersionIssue, ...] = ()


@dataclass(frozen=True)
class FactorDefinition:
    """Pair a frozen batch identity with current catalog metadata."""

    factor_ref: str
    factor_type: str
    factor_id: int
    batch_factor_version: str
    serial_number: str | None
    name: str | None
    window: str | None
    factor_bar_interval: str | None
    formula_summary: str | None
    definition_updated_at: datetime | None


@dataclass(frozen=True)
class FactorDetail:
    """Expose a current detail row alongside the frozen batch version."""

    id: int
    factor_ref: str
    factor_type: str
    factor_id: int
    batch_factor_version: str
    is_sub_factor_id: bool
    serial_number: str
    name: str
    status: int
    calc_logic: str | None
    params: JsonObject | None
    data_source_metadata: JsonObject | None
    updated_at: datetime | None


@dataclass(frozen=True)
class FormulaEvidence:
    """Expose completed formula evidence alongside the frozen batch version.

    ``batch_factor_version`` is the catalog/definition version copied from the
    frozen batch snapshot.  ``formula_factor_version`` is the executable
    version stored on the evidence row itself.  They intentionally remain
    separate: a published metric can legitimately reference a formula hash
    while belonging to an older catalog definition snapshot.
    """

    id: int
    run_id: str
    factor_ref: str
    factor_type: str
    factor_id: int
    batch_factor_version: str
    is_sub_factor_id: bool
    calculation_mode: str
    factor_bar_interval: str
    factor_window_bars: str
    return_bar_interval: str
    forward_return_bars: int
    formula_version: str
    formula_hash: str
    hash_algorithm: str
    normalization_version: str
    expression: str
    required_fields: tuple[Any, ...]
    lookback: Any | None
    lag: Any | None
    missing_policy: str | None
    output_unit: str | None
    metadata_complete: bool
    metadata_warnings: tuple[Any, ...]
    source_detail_id: int | None
    recorded_at: datetime
    run_status: str
    run_completed_at: datetime
    formula_factor_version: str | None = None


@dataclass(frozen=True)
class EvaluationMetric:
    """Expose one environment-partition TS or CS metric from the batch."""

    id: int
    eval_batch_id: int
    factor_ref: str
    factor_type: str
    factor_id: int
    factor_version: str
    market_scope: str
    label_kind: str
    label_code: str
    evaluation_type: str
    interval: str
    return_bar_interval: str
    forward_return_bars: int
    window_scope: str
    sample_start_date: date
    sample_end_date: date
    mean_ic: Decimal | None
    mean_rank_ic: Decimal | None
    icir: Decimal | None
    rank_icir: Decimal | None
    time_series_score: Decimal | None
    cross_sectional_score: Decimal | None
    routing_score: Decimal | None
    confidence: Decimal | None
    metric_status: str
    is_valid: bool | None
    scoring_version: str
    metric_payload: JsonObject | None
    metric_identity: JsonObject | None
    metric_pair_identity_hash: str | None
    score_components: JsonObject | None
    route_eligibility: JsonObject | None
    direction: JsonObject | None
    aggregation: JsonObject | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class PublishedRoute:
    """Expose one persisted route and its rank/tie evidence."""

    id: int
    publication_uid: str
    eval_batch_id: int
    metric_id: int
    market_scope: str
    route_profile_key: str
    environment_date: date
    label_kind: str
    label_code: str
    as_of_time: datetime
    factor_ref: str
    factor_type: str
    factor_id: int
    factor_version: str
    rank_no: int
    routing_score: Decimal
    confidence: Decimal | None
    time_series_score: Decimal | None
    cross_sectional_score: Decimal | None
    is_eligible: bool
    reject_reason_code: str | None
    evidence: JsonObject
    score_rule_version: str
    publish_version: str
    is_active: bool


@dataclass(frozen=True)
class RouteRankingEntry:
    """Expose the persisted fields that determine one active route's rank."""

    id: int
    metric_id: int
    environment_date: date
    label_kind: str
    label_code: str
    as_of_time: datetime
    factor_ref: str
    factor_type: str
    factor_id: int
    factor_version: str
    rank_no: int
    routing_score: Decimal
    confidence: Decimal | None
    time_series_score: Decimal | None
    cross_sectional_score: Decimal | None
    score_rule_version: str


@dataclass(frozen=True)
class PublishedRouteSnapshot:
    """Hold one lightweight active-publication route ranking snapshot."""

    captured_at: datetime
    batch_id: int
    publication_uid: str
    publish_version: str
    market_scope: str
    route_profile_key: str
    routes: tuple[RouteRankingEntry, ...]


@dataclass(frozen=True)
class CalculationAuditSnapshot:
    """Hold calculation evidence captured in one read-only DB snapshot."""

    captured_at: datetime
    batch: PublishedEvaluationBatch
    membership_differences: FactorMembershipDifferences
    definitions: tuple[FactorDefinition, ...]
    details: tuple[FactorDetail, ...]
    formula_evidence: tuple[FormulaEvidence, ...]
    evaluation_metrics: tuple[EvaluationMetric, ...]
    routes: tuple[PublishedRoute, ...]


class Factor4CalculationRepository:
    """Read coherent Factor 4.0 calculation evidence without judging it."""

    def __init__(self, client: DatabaseClient) -> None:
        """Initialize the repository.

        ``client`` supplies public transaction and parameterized-query APIs.
        No connection is opened and no value is returned.
        """

        self._client = client

    def read_calculation_snapshot(
        self,
        market_scope: str = "all",
        route_profile_key: str = "default",
    ) -> CalculationAuditSnapshot:
        """Read the latest active published calculation evidence atomically.

        ``market_scope`` and ``route_profile_key`` select one publication
        partition and are always bound parameters. The return value contains
        the frozen batch, current definitions/details, latest formula evidence
        per immutable formula identity, batch TS/CS metrics, and published
        routes. Generic IC summary/validity history is intentionally excluded:
        it has no evaluation-batch foreign key and is not an exact batch result.

        ``ValueError`` is raised for blank selectors. A
        ``CalculationRepositoryError`` is raised when no unique active,
        successful, published batch exists, a required row/JSON shape is
        invalid, or a driver/schema query fails. No database writes occur.
        """

        scope, profile = self._normalized_selectors(
            market_scope,
            route_profile_key,
        )

        stage = "begin_read_only_snapshot"
        try:
            with self._client.transaction() as transaction:
                self._begin_read_only_snapshot(transaction)
                stage = "snapshot_identity"
                identity = transaction.fetch_one("SELECT NOW(6) AS captured_at")
                captured_at = self._required_datetime(identity, "captured_at", stage)

                stage = "latest_published_batch"
                batch_rows = transaction.fetch_all(
                    """
                    SELECT
                        id, batch_uid, market_scope, label_kind,
                        route_profile_key, start_date, end_date, as_of_time,
                        published_at, publication_uid, publish_version,
                        evaluation_config_version, score_rule_version,
                        code_version, status, publish_status, is_active,
                        expected_metric_count, completed_metric_count,
                        insufficient_metric_count, failed_metric_count,
                        factor_set_snapshot_hash, environment_snapshot_hash,
                        release_manifest_hash, factor_set_snapshot,
                        evaluation_config, environment_status
                    FROM market_environment_eval_batch
                    WHERE market_scope = %s
                      AND route_profile_key = %s
                      AND status = 'success'
                      AND publish_status = 'published'
                      AND is_active = 1
                    ORDER BY published_at DESC, id DESC
                    LIMIT 2
                    """,
                    (scope, profile),
                )
                if len(batch_rows) != 1:
                    raise CalculationRepositoryError(
                        stage,
                        "expected exactly one active successful published batch "
                        f"but found {len(batch_rows)}",
                    )
                batch = self._to_batch(batch_rows[0], stage)
                batch_id = batch.id

                stage = "batch_factor_identities"
                factor_rows = transaction.fetch_all(
                    """
                    SELECT DISTINCT
                        factor_ref, factor_type, factor_id, factor_version,
                        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                            metric_payload,
                            '$.metric_identity.definition_factor_version'
                        )), 'null') AS definition_factor_version
                    FROM market_environment_factor_metric
                    WHERE eval_batch_id = %s
                    ORDER BY factor_type, factor_id, definition_factor_version,
                             factor_version
                    """,
                    (batch_id,),
                )
                factor_identities = self._snapshot_factor_identity_map(
                    batch.factor_set_snapshot,
                    stage,
                )
                (
                    metric_identities,
                    missing_definition_versions,
                ) = self._metric_membership_identity_map(factor_rows, stage)
                membership_differences = self._membership_differences(
                    factor_identities,
                    metric_identities,
                    missing_definition_versions=missing_definition_versions,
                )

                stage = "factor_definitions"
                definitions = tuple(
                    self._to_definition(row, stage)
                    for row in self._read_definition_rows(
                        transaction, factor_identities
                    )
                )

                stage = "factor_details"
                details = tuple(
                    self._to_detail(row, stage)
                    for row in self._read_factor_detail_rows(
                        transaction, factor_identities
                    )
                )

                stage = "evaluation_metrics"
                evaluation_metrics = tuple(
                    self._to_evaluation_metric(row, stage)
                    for row in self._read_evaluation_metric_rows(
                        transaction, batch_id
                    )
                )

                stage = "formula_evidence"
                formula_evidence = tuple(
                    self._to_formula_evidence(row, stage)
                    for row in self._read_formula_evidence_rows(
                        transaction,
                        factor_identities,
                        evaluation_metrics,
                    )
                )

                stage = "published_routes"
                routes = tuple(
                    self._to_route(row, stage)
                    for row in transaction.fetch_all(
                        self._routes_query(),
                        (batch_id, batch.publication_uid, batch.publish_version),
                    )
                )
        except CalculationRepositoryError:
            raise
        except Exception as exc:
            raise CalculationRepositoryError(
                stage,
                f"database read failed with {type(exc).__name__}",
            ) from exc

        return CalculationAuditSnapshot(
            captured_at=captured_at,
            batch=batch,
            membership_differences=membership_differences,
            definitions=definitions,
            details=details,
            formula_evidence=formula_evidence,
            evaluation_metrics=evaluation_metrics,
            routes=routes,
        )

    def read_published_route_snapshot(
        self,
        market_scope: str = "all",
        route_profile_key: str = "default",
    ) -> PublishedRouteSnapshot:
        """Read the active publication identity and rank sequence atomically.

        ``market_scope`` and ``route_profile_key`` select one active,
        successful, published batch using bound parameters. The return value
        contains only publication identity and active route ranking fields so
        callers can perform a repeat-read stability check without reloading
        factor definitions, formula evidence, or all evaluation metrics.

        ``ValueError`` is raised for blank selectors. A
        ``CalculationRepositoryError`` is raised for an ambiguous publication,
        invalid row shape, or database failure. No database writes occur.
        """

        scope, profile = self._normalized_selectors(
            market_scope,
            route_profile_key,
        )
        stage = "begin_route_read_only_snapshot"
        try:
            with self._client.transaction() as transaction:
                self._begin_read_only_snapshot(transaction)
                stage = "route_snapshot_identity"
                identity = transaction.fetch_one("SELECT NOW(6) AS captured_at")
                captured_at = self._required_datetime(identity, "captured_at", stage)

                stage = "route_snapshot_publication"
                batch_rows = transaction.fetch_all(
                    self._route_snapshot_batch_query(),
                    (scope, profile),
                )
                if len(batch_rows) != 1:
                    raise CalculationRepositoryError(
                        stage,
                        "expected exactly one active successful published batch "
                        f"but found {len(batch_rows)}",
                    )
                batch_row = batch_rows[0]
                batch_id = self._required_int(batch_row, "id", stage)
                publication_uid = self._required_str(
                    batch_row,
                    "publication_uid",
                    stage,
                )
                publish_version = self._required_str(
                    batch_row,
                    "publish_version",
                    stage,
                )
                snapshot_market_scope = self._required_str(
                    batch_row,
                    "market_scope",
                    stage,
                )
                snapshot_route_profile_key = self._required_str(
                    batch_row,
                    "route_profile_key",
                    stage,
                )

                stage = "route_snapshot_rankings"
                routes = tuple(
                    self._to_route_ranking(row, stage)
                    for row in transaction.fetch_all(
                        self._route_rankings_query(),
                        (batch_id, publication_uid, publish_version),
                    )
                )
        except CalculationRepositoryError:
            raise
        except Exception as exc:
            raise CalculationRepositoryError(
                stage,
                f"database read failed with {type(exc).__name__}",
            ) from exc

        return PublishedRouteSnapshot(
            captured_at=captured_at,
            batch_id=batch_id,
            publication_uid=publication_uid,
            publish_version=publish_version,
            market_scope=snapshot_market_scope,
            route_profile_key=snapshot_route_profile_key,
            routes=routes,
        )

    @staticmethod
    def _normalized_selectors(
        market_scope: str,
        route_profile_key: str,
    ) -> tuple[str, str]:
        scope = market_scope.strip()
        profile = route_profile_key.strip()
        if not scope or not profile:
            raise ValueError("market_scope and route_profile_key must be non-empty")
        return scope, profile

    @staticmethod
    def _begin_read_only_snapshot(transaction: DatabaseTransaction) -> None:
        transaction.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        transaction.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")

    @staticmethod
    def _placeholders(values: Sequence[Any]) -> str:
        if not values:
            raise ValueError("at least one bound value is required")
        return ", ".join("%s" for _ in values)

    @classmethod
    def _factor_identity_map(
        cls,
        rows: Sequence[Mapping[str, Any]],
        stage: str,
        *,
        allow_empty: bool = False,
    ) -> dict[tuple[bool, int], dict[str, Any]]:
        identities: dict[tuple[bool, int], dict[str, Any]] = {}
        for row in rows:
            factor_type = cls._required_str(row, "factor_type", stage)
            if factor_type not in {"sub_factor", "factor"}:
                raise CalculationRepositoryError(
                    stage, f"unsupported factor_type {factor_type!r}"
                )
            factor_id = cls._required_int(row, "factor_id", stage)
            key = (factor_type == "sub_factor", factor_id)
            identity = {
                "factor_ref": cls._required_str(row, "factor_ref", stage),
                "factor_type": factor_type,
                "factor_id": factor_id,
                "batch_factor_version": cls._required_str(
                    row,
                    "factor_version",
                    stage,
                ),
            }
            if key in identities and identities[key] != identity:
                raise CalculationRepositoryError(
                    stage,
                    "one catalog factor maps to multiple frozen batch identities",
                )
            identities[key] = identity
        if not identities and not allow_empty:
            raise CalculationRepositoryError(stage, "published batch contains no factors")
        return identities

    @classmethod
    def _membership_differences(
        cls,
        frozen: Mapping[tuple[bool, int], Mapping[str, Any]],
        metrics: Mapping[tuple[bool, int], Mapping[str, Any]],
        *,
        missing_definition_versions: Sequence[FactorMembershipVersionIssue] = (),
    ) -> FactorMembershipDifferences:
        """Compare frozen and metric members using canonical definition versions.

        ``frozen`` and ``metrics`` are expected to already carry the same
        semantic version in ``batch_factor_version``.  Metric rows whose
        definition version is unavailable are excluded from the bidirectional
        set difference: reporting them as both missing and unexpected would
        turn an unprovable identity into a false drift.  They are returned in
        ``missing_definition_versions`` so the Service can report an explicit
        ``BLOCKED_DATA_PRECONDITION`` finding.
        """

        frozen_by_member = {
            cls._membership_key(identity): identity for identity in frozen.values()
        }
        metrics_by_member = {
            cls._membership_key(identity): identity for identity in metrics.values()
        }
        unresolved_keys = {
            (
                issue.factor_type == "sub_factor",
                issue.factor_id,
            )
            for issue in missing_definition_versions
        }
        if unresolved_keys:
            frozen_by_member = {
                key: identity
                for key, identity in frozen_by_member.items()
                if (
                    str(identity["factor_type"]) == "sub_factor",
                    int(identity["factor_id"]),
                )
                not in unresolved_keys
            }
            metrics_by_member = {
                key: identity
                for key, identity in metrics_by_member.items()
                if (
                    str(identity["factor_type"]) == "sub_factor",
                    int(identity["factor_id"]),
                )
                not in unresolved_keys
            }
        missing_keys = sorted(frozen_by_member.keys() - metrics_by_member.keys())
        unexpected_keys = sorted(metrics_by_member.keys() - frozen_by_member.keys())
        return FactorMembershipDifferences(
            missing_from_metrics=tuple(
                cls._to_factor_identity(frozen_by_member[key])
                for key in missing_keys
            ),
            unexpected_in_metrics=tuple(
                cls._to_factor_identity(metrics_by_member[key])
                for key in unexpected_keys
            ),
            missing_definition_versions=tuple(missing_definition_versions),
        )

    @classmethod
    def _metric_membership_identity_map(
        cls,
        rows: Sequence[Mapping[str, Any]],
        stage: str,
    ) -> tuple[
        dict[tuple[bool, int], dict[str, Any]],
        tuple[FactorMembershipVersionIssue, ...],
    ]:
        """Build metric membership identities from definition versions.

        The metric table's top-level ``factor_version`` is the executable
        formula version and must not be compared with
        ``factor_set_snapshot.members[].factor_version``.  The latter is a
        frozen catalog/definition version and is exposed by the metric
        payload as ``metric_identity.definition_factor_version``.  Missing,
        blank, or conflicting definition versions are recorded as unresolved
        data preconditions and are not converted into membership drift.

        ``CalculationRepositoryError`` is raised for malformed structural
        identity fields (factor type/id/ref), matching the existing strict
        identity-map behavior.
        """

        # Group by the catalog identity first because each factor normally has
        # one TS and one CS metric row.  A single missing or conflicting
        # definition version makes the whole factor identity unprovable.
        grouped: dict[tuple[bool, int], list[Mapping[str, Any]]] = {}
        parsed: dict[tuple[bool, int], tuple[str, str, int]] = {}
        for row in rows:
            factor_type = cls._required_str(row, "factor_type", stage)
            if factor_type not in {"sub_factor", "factor"}:
                raise CalculationRepositoryError(
                    stage, f"unsupported factor_type {factor_type!r}"
                )
            factor_id = cls._required_int(row, "factor_id", stage)
            factor_ref = cls._required_str(row, "factor_ref", stage)
            key = (factor_type == "sub_factor", factor_id)
            identity_shape = (factor_ref, factor_type, factor_id)
            previous_shape = parsed.get(key)
            if previous_shape is not None and previous_shape != identity_shape:
                raise CalculationRepositoryError(
                    stage,
                    "one catalog factor maps to multiple metric identities",
                )
            parsed[key] = identity_shape
            grouped.setdefault(key, []).append(row)

        identities: dict[tuple[bool, int], dict[str, Any]] = {}
        unresolved: list[FactorMembershipVersionIssue] = []
        for key, factor_rows in grouped.items():
            factor_ref, factor_type, factor_id = parsed[key]
            raw_versions = [row.get("definition_factor_version") for row in factor_rows]
            valid_versions = [
                value.strip()
                for value in raw_versions
                if isinstance(value, str) and value.strip()
            ]
            invalid_count = len(raw_versions) - len(valid_versions)
            distinct_versions = set(valid_versions)
            if invalid_count or len(distinct_versions) != 1:
                reason = (
                    "definition_factor_version_missing_or_invalid"
                    if invalid_count
                    else "definition_factor_version_conflict"
                )
                executable_versions = {
                    value.strip()
                    for value in (row.get("factor_version") for row in factor_rows)
                    if isinstance(value, str) and value.strip()
                }
                unresolved.append(
                    FactorMembershipVersionIssue(
                        factor_ref=factor_ref,
                        factor_type=factor_type,
                        factor_id=factor_id,
                        reason=reason,
                        executable_factor_version=(
                            next(iter(executable_versions))
                            if len(executable_versions) == 1
                            else None
                        ),
                    )
                )
                continue

            canonical_version = next(iter(distinct_versions))
            # Keep the canonical field name expected by the existing identity
            # map and downstream membership comparison.  The original row is
            # never mutated, so top-level executable versions remain intact in
            # EvaluationMetric objects loaded later.
            representative = dict(factor_rows[0])
            representative["batch_factor_version"] = canonical_version
            identities[key] = representative

        return identities, tuple(
            sorted(unresolved, key=lambda issue: (issue.factor_type, issue.factor_id))
        )

    @staticmethod
    def _membership_key(identity: Mapping[str, Any]) -> tuple[str, str, int, str]:
        """Build the immutable identity used for batch membership reconciliation.

        A catalog factor can be reused in multiple published batches with a
        different definition/version.  The canonical version is the frozen
        catalog definition version (for metric rows this is copied from
        ``metric_identity.definition_factor_version``), not the executable
        formula hash in the metric table's top-level ``factor_version``.
        Comparing only ``factor_id`` would silently treat an old definition as
        a member of the new batch.
        """

        return (
            str(identity["factor_type"]),
            str(identity["factor_ref"]),
            int(identity["factor_id"]),
            str(identity["batch_factor_version"]),
        )

    @staticmethod
    def _to_factor_identity(identity: Mapping[str, Any]) -> FactorIdentity:
        return FactorIdentity(
            factor_ref=str(identity["factor_ref"]),
            factor_type=str(identity["factor_type"]),
            factor_id=int(identity["factor_id"]),
            factor_version=str(identity["batch_factor_version"]),
        )

    @classmethod
    def _snapshot_factor_identity_map(
        cls,
        snapshot: Mapping[str, Any],
        stage: str,
    ) -> dict[tuple[bool, int], dict[str, Any]]:
        raw_members = cls._value(snapshot, "members", stage)
        if not isinstance(raw_members, list) or not all(
            isinstance(member, Mapping) for member in raw_members
        ):
            raise CalculationRepositoryError(
                stage,
                "factor_set_snapshot.members must be a JSON array of objects",
            )
        factor_count = cls._required_int(snapshot, "factor_count", stage)
        if factor_count != len(raw_members):
            raise CalculationRepositoryError(
                stage,
                "factor_set_snapshot.factor_count does not match members",
            )
        return cls._factor_identity_map(raw_members, stage)

    @staticmethod
    def _ids_for_type(
        identities: Mapping[tuple[bool, int], Mapping[str, Any]],
        is_sub_factor: bool,
    ) -> tuple[int, ...]:
        return tuple(
            factor_id
            for (is_sub, factor_id) in identities
            if is_sub is is_sub_factor
        )

    @classmethod
    def _read_definition_rows(
        cls,
        transaction: DatabaseTransaction,
        identities: Mapping[tuple[bool, int], Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        catalog: dict[tuple[bool, int], dict[str, Any]] = {}
        sub_ids = cls._ids_for_type(identities, True)
        if sub_ids:
            query = f"""
                SELECT id AS factor_id, serial_number,
                       sub_factor_name AS name, `window`,
                       factor_bar_interval, formula_summary,
                       updated_at AS definition_updated_at
                FROM sub_factors
                WHERE id IN ({cls._placeholders(sub_ids)})
                ORDER BY id
            """
            for row in transaction.fetch_all(query, sub_ids):
                catalog[(True, int(row["factor_id"]))] = row
        factor_ids = cls._ids_for_type(identities, False)
        if factor_ids:
            query = f"""
                SELECT id AS factor_id, serial_number, factor_name AS name,
                       NULL AS `window`, NULL AS factor_bar_interval,
                       NULL AS formula_summary,
                       updated_at AS definition_updated_at
                FROM factors
                WHERE id IN ({cls._placeholders(factor_ids)})
                ORDER BY id
            """
            for row in transaction.fetch_all(query, factor_ids):
                catalog[(False, int(row["factor_id"]))] = row
        results: list[dict[str, Any]] = []
        for key, identity in identities.items():
            current = catalog.get(key) or {
                "serial_number": None,
                "name": None,
                "window": None,
                "factor_bar_interval": None,
                "formula_summary": None,
                "definition_updated_at": None,
            }
            results.append({**current, **identity})
        return results

    @classmethod
    def _factor_filter(
        cls,
        identities: Mapping[tuple[bool, int], Mapping[str, Any]],
        alias: str,
    ) -> tuple[str, tuple[Any, ...]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for is_sub_factor in (True, False):
            factor_ids = cls._ids_for_type(identities, is_sub_factor)
            if not factor_ids:
                continue
            clauses.append(
                f"({alias}.is_sub_factor_id = %s AND "
                f"{alias}.factor_id IN ({cls._placeholders(factor_ids)}))"
            )
            parameters.append(int(is_sub_factor))
            parameters.extend(factor_ids)
        return " OR ".join(clauses), tuple(parameters)

    @classmethod
    def _enrich_factor_rows(
        cls,
        rows: Sequence[Mapping[str, Any]],
        identities: Mapping[tuple[bool, int], Mapping[str, Any]],
        stage: str,
    ) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for row in rows:
            is_sub_factor = cls._required_bool(row, "is_sub_factor_id", stage)
            factor_id = cls._required_int(row, "factor_id", stage)
            identity = identities.get((is_sub_factor, factor_id))
            if identity is None:
                raise CalculationRepositoryError(
                    stage, "query returned a factor outside the frozen batch"
                )
            enriched.append({**row, **identity})
        return enriched

    @classmethod
    def _read_factor_detail_rows(
        cls,
        transaction: DatabaseTransaction,
        identities: Mapping[tuple[bool, int], Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        predicate, parameters = cls._factor_filter(identities, "d")
        rows = transaction.fetch_all(
            f"""
            SELECT id, factor_id, is_sub_factor_id, serial_number, name,
                   status, calc_logic, params,
                   data_source_metadata, updated_at
            FROM factors_details AS d
            WHERE {predicate}
            ORDER BY d.is_sub_factor_id DESC, d.factor_id,
                     d.updated_at DESC, d.id DESC
            """,
            parameters,
        )
        return cls._enrich_factor_rows(rows, identities, "factor_details")

    @classmethod
    def _read_formula_evidence_rows(
        cls,
        transaction: DatabaseTransaction,
        identities: Mapping[tuple[bool, int], Mapping[str, Any]],
        metrics: Sequence[EvaluationMetric] = (),
    ) -> list[dict[str, Any]]:
        predicate, parameters = cls._factor_filter(identities, "e")
        # Keep the inexpensive latest-per-context projection for catalog
        # inspection.  A published metric can, however, point at an older
        # completed run, so fetch every row matching an explicit immutable
        # metric link as a second, narrowly bounded query.
        latest_rows = transaction.fetch_all(
            f"""
            SELECT *
            FROM (
                SELECT
                    e.id, e.run_id, e.factor_id, e.is_sub_factor_id,
                    NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                        e.evidence_json,
                        '$.factor_version'
                    )), 'null') AS formula_factor_version,
                    e.calculation_mode, e.factor_bar_interval,
                    e.factor_window_bars, e.return_bar_interval,
                    e.forward_return_bars, e.formula_version,
                    e.formula_hash, e.hash_algorithm,
                    e.normalization_version, e.expression,
                    e.required_fields, e.lookback_json, e.lag_json,
                    e.missing_policy, e.output_unit,
                    e.metadata_complete, e.metadata_warnings,
                    e.source_detail_id, e.recorded_at,
                    r.status AS run_status,
                    r.completed_at AS run_completed_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY e.factor_id, e.is_sub_factor_id,
                            e.calculation_mode, e.factor_bar_interval,
                            e.factor_window_bars, e.return_bar_interval,
                            e.forward_return_bars
                        ORDER BY r.completed_at DESC,
                                 e.recorded_at DESC, e.id DESC
                    ) AS evidence_recency
                FROM factor_ic_run_formula_evidence AS e
                INNER JOIN factor_ic_runs AS r ON r.run_id = e.run_id
                WHERE ({predicate})
                  AND r.status = 'completed'
                  AND r.completed_at IS NOT NULL
            ) AS ranked
            WHERE ranked.evidence_recency = 1
            ORDER BY ranked.factor_id, ranked.is_sub_factor_id,
                     ranked.calculation_mode, ranked.factor_bar_interval,
                     ranked.factor_window_bars, ranked.return_bar_interval,
                     ranked.forward_return_bars, ranked.run_completed_at DESC,
                     ranked.recorded_at DESC, ranked.id DESC
            """,
            parameters,
        )
        rows_by_id: dict[int, dict[str, Any]] = {}
        for row in latest_rows:
            row_id = cls._required_int(row, "id", "formula_evidence")
            rows_by_id[row_id] = row

        link_specs = cls._metric_formula_link_specs(metrics)
        if link_specs:
            link_clauses: list[str] = []
            link_parameters: list[Any] = list(parameters)
            for factor_id, is_sub_factor, links in link_specs:
                terms = [
                    "e.factor_id = %s",
                    "e.is_sub_factor_id = %s",
                ]
                link_parameters.extend((factor_id, int(is_sub_factor)))
                for column_name, value in links:
                    terms.append(f"e.{column_name} = %s")
                    link_parameters.append(value)
                link_clauses.append("(" + " AND ".join(terms) + ")")
            linked_rows = transaction.fetch_all(
                f"""
                SELECT
                    e.id, e.run_id, e.factor_id, e.is_sub_factor_id,
                    NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                        e.evidence_json,
                        '$.factor_version'
                    )), 'null') AS formula_factor_version,
                    e.calculation_mode, e.factor_bar_interval,
                    e.factor_window_bars, e.return_bar_interval,
                    e.forward_return_bars, e.formula_version,
                    e.formula_hash, e.hash_algorithm,
                    e.normalization_version, e.expression,
                    e.required_fields, e.lookback_json, e.lag_json,
                    e.missing_policy, e.output_unit,
                    e.metadata_complete, e.metadata_warnings,
                    e.source_detail_id, e.recorded_at,
                    r.status AS run_status,
                    r.completed_at AS run_completed_at
                FROM factor_ic_run_formula_evidence AS e
                INNER JOIN factor_ic_runs AS r ON r.run_id = e.run_id
                WHERE ({predicate})
                  AND r.status = 'completed'
                  AND r.completed_at IS NOT NULL
                  AND ({" OR ".join(link_clauses)})
                ORDER BY e.id
                """,
                tuple(link_parameters),
            )
            for row in linked_rows:
                row_id = cls._required_int(row, "id", "formula_evidence")
                rows_by_id[row_id] = row

        return cls._enrich_factor_rows(
            tuple(rows_by_id.values()),
            identities,
            "formula_evidence",
        )

    @staticmethod
    def _metric_formula_link_specs(
        metrics: Sequence[EvaluationMetric],
    ) -> tuple[tuple[int, bool, tuple[tuple[str, Any], ...]], ...]:
        """Extract deduplicated immutable-link predicates from batch metrics."""

        specs: set[tuple[int, bool, tuple[tuple[str, Any], ...]]] = set()
        for metric in metrics:
            identity = metric.metric_identity
            if not isinstance(identity, Mapping):
                continue
            flattened = dict(identity)
            for container_name in ("formula", "formula_identity", "formula_evidence"):
                nested = identity.get(container_name)
                if not isinstance(nested, Mapping):
                    continue
                for name in (
                    "formula_hash",
                    "formula_version",
                    "run_id",
                    "formula_evidence_id",
                ):
                    if name not in flattened and name in nested:
                        flattened[name] = nested[name]
            if "formula_evidence_id" not in flattened and "evidence_id" in flattened:
                flattened["formula_evidence_id"] = flattened["evidence_id"]
            links: list[tuple[str, Any]] = []
            for identity_name, column_name in (
                ("formula_evidence_id", "id"),
                ("run_id", "run_id"),
                ("formula_hash", "formula_hash"),
                ("formula_version", "formula_version"),
            ):
                value = flattened.get(identity_name)
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
                    # Structured values cannot be bound to an identity column;
                    # leave them for the Service to classify as an invalid
                    # link instead of crashing the read transaction.
                    continue
                links.append((column_name, value))
            if links:
                specs.add(
                    (
                        metric.factor_id,
                        metric.factor_type == "sub_factor",
                        tuple(links),
                    )
                )
        return tuple(
            sorted(
                specs,
                key=lambda item: (item[1], item[0], str(item[2])),
            )
        )

    @staticmethod
    def _evaluation_metrics_query() -> str:
        return """
            SELECT
                id, eval_batch_id, factor_ref, factor_type, factor_id,
                factor_version, market_scope, label_kind, label_code,
                evaluation_type, `interval`, return_bar_interval,
                forward_return_bars, window_scope, sample_start_date,
                sample_end_date, mean_ic, mean_rank_ic, icir, rank_icir,
                time_series_score, cross_sectional_score,
                routing_score, confidence,
                metric_status, is_valid, scoring_version,
                JSON_EXTRACT(metric_payload, '$.metric_identity') AS metric_identity,
                SHA2(
                    NULLIF(JSON_UNQUOTE(JSON_REMOVE(
                        JSON_EXTRACT(metric_payload, '$.metric_identity'),
                        '$.evaluation_type',
                        '$.ic_scope'
                    )), 'null'),
                    256
                ) AS metric_pair_identity_hash,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.route_eligibility.admission_mode'
                )), 'null') AS route_admission_mode,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.route_eligibility.valid_scopes'
                )), 'null') AS route_valid_scopes,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.route_eligibility.invalid_scopes'
                )), 'null') AS route_invalid_scopes,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.route_eligibility.is_eligible'
                )), 'null') AS route_is_eligible,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.route_eligibility.routing_score'
                )), 'null') AS route_eligibility_routing_score,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.route_eligibility.minimum_route_score'
                )), 'null') AS route_minimum_route_score,
                error_code, error_message
            FROM market_environment_factor_metric
            WHERE eval_batch_id = %s
            ORDER BY label_code, factor_ref, evaluation_type, id
        """

    @staticmethod
    def _routing_metric_evidence_query() -> str:
        return """
            SELECT
                id,
                JSON_EXTRACT(
                    metric_payload,
                    '$.metric_identity'
                ) AS metric_identity,
                JSON_EXTRACT(
                    metric_payload,
                    '$.score_components'
                ) AS score_components,
                JSON_EXTRACT(metric_payload, '$.direction') AS direction,
                JSON_REMOVE(
                    JSON_EXTRACT(metric_payload, '$.oos'),
                    '$.folds'
                ) AS oos,
                JSON_EXTRACT(metric_payload, '$.aggregation') AS aggregation,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.directed_mean_ic'
                )), 'null') AS directed_mean_ic,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.directed_mean_rank_ic'
                )), 'null') AS directed_mean_rank_ic,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.directed_icir'
                )), 'null') AS directed_icir,
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
                    metric_payload,
                    '$.directed_rank_icir'
                )), 'null') AS directed_rank_icir
            FROM market_environment_factor_metric
            WHERE eval_batch_id = %s
              AND routing_score IS NOT NULL
            ORDER BY id
        """

    @classmethod
    def _read_evaluation_metric_rows(
        cls,
        transaction: DatabaseTransaction,
        batch_id: int,
    ) -> list[dict[str, Any]]:
        rows = transaction.fetch_all(cls._evaluation_metrics_query(), (batch_id,))
        evidence_rows = transaction.fetch_all(
            cls._routing_metric_evidence_query(),
            (batch_id,),
        )
        evidence_by_metric_id: dict[int, dict[str, Any | None]] = {}
        evidence_object_names = (
            "metric_identity",
            "score_components",
            "direction",
            "oos",
            "aggregation",
        )
        directed_value_names = (
            "directed_mean_ic",
            "directed_mean_rank_ic",
            "directed_icir",
            "directed_rank_icir",
        )
        for evidence_row in evidence_rows:
            metric_id = cls._required_int(
                evidence_row,
                "id",
                "evaluation_metrics",
            )
            if metric_id in evidence_by_metric_id:
                raise CalculationRepositoryError(
                    "evaluation_metrics",
                    "routing evidence contains a duplicate metric id",
                )
            evidence = {
                name: cls._optional_json_object(
                    evidence_row,
                    name,
                    "evaluation_metrics",
                )
                for name in evidence_object_names
            }
            evidence.update(
                {
                    name: cls._optional_decimal(
                        evidence_row,
                        name,
                        "evaluation_metrics",
                    )
                    for name in directed_value_names
                }
            )
            evidence_by_metric_id[metric_id] = evidence

        enriched: list[dict[str, Any]] = []
        for row in rows:
            metric_id = cls._required_int(row, "id", "evaluation_metrics")
            compact_identity = (
                cls._optional_json_object(row, "metric_identity", "evaluation_metrics")
                if "metric_identity" in row
                else None
            )
            route_eligibility = cls._route_eligibility_from_row(
                row,
                "evaluation_metrics",
            )
            routing_evidence = evidence_by_metric_id.pop(metric_id, None)
            if row.get("routing_score") is not None and routing_evidence is None:
                raise CalculationRepositoryError(
                    "evaluation_metrics",
                    "a scored metric is missing routing evidence",
                )

            evidence_payload = routing_evidence or {}
            routing_identity = evidence_payload.get("metric_identity")
            if compact_identity is not None and routing_identity is not None:
                if compact_identity != routing_identity:
                    raise CalculationRepositoryError(
                        "evaluation_metrics",
                        "compact metric identity differs from routing evidence identity",
                    )
            metric_identity = routing_identity or compact_identity
            payload: JsonObject = {}
            if metric_identity is not None:
                payload["metric_identity"] = metric_identity
            if route_eligibility is not None:
                payload["route_eligibility"] = route_eligibility
            if routing_evidence is not None:
                payload.update(
                    {
                        name: value
                        for name, value in routing_evidence.items()
                        if value is not None
                    }
                )
            enriched.append(
                {
                    **row,
                    "metric_payload": payload or None,
                    "metric_identity": metric_identity,
                    "score_components": evidence_payload.get("score_components"),
                    "route_eligibility": route_eligibility,
                    "direction": evidence_payload.get("direction"),
                    "aggregation": evidence_payload.get("aggregation"),
                }
            )

        if evidence_by_metric_id:
            raise CalculationRepositoryError(
                "evaluation_metrics",
                "routing evidence references a metric outside the batch result",
            )
        return enriched

    @classmethod
    def _route_eligibility_from_row(
        cls,
        row: Mapping[str, Any],
        stage: str,
    ) -> JsonObject | None:
        admission_mode = cls._optional_str(row, "route_admission_mode", stage)
        valid_scopes = cls._optional_json_array(row, "route_valid_scopes", stage)
        invalid_scopes = cls._optional_json_array(
            row,
            "route_invalid_scopes",
            stage,
        )
        is_eligible = cls._optional_json_value(
            row,
            "route_is_eligible",
            stage,
        )
        if is_eligible is not None and not isinstance(is_eligible, bool):
            cls._invalid_value(stage, "route_is_eligible", "a JSON boolean or null")
        routing_score = cls._optional_decimal(
            row,
            "route_eligibility_routing_score",
            stage,
        )
        minimum_route_score = cls._optional_decimal(
            row,
            "route_minimum_route_score",
            stage,
        )
        values: tuple[tuple[str, Any | None], ...] = (
            ("admission_mode", admission_mode),
            ("valid_scopes", valid_scopes),
            ("invalid_scopes", invalid_scopes),
            ("is_eligible", is_eligible),
            ("routing_score", routing_score),
            ("minimum_route_score", minimum_route_score),
        )
        result = {name: value for name, value in values if value is not None}
        return result or None

    @staticmethod
    def _routes_query() -> str:
        return """
            SELECT
                route.id, route.publication_uid, route.eval_batch_id,
                route.metric_id, route.market_scope,
                batch.route_profile_key, route.environment_date,
                route.label_kind, route.label_code, route.as_of_time,
                route.factor_ref, route.factor_type, route.factor_id,
                route.factor_version, route.rank_no,
                route.routing_score, route.confidence,
                route.time_series_score, route.cross_sectional_score,
                route.is_eligible, route.reject_reason_code,
                JSON_REMOVE(
                    route.evidence,
                    '$.time_series.data_diagnostics.artifact_fingerprints',
                    '$.cross_sectional.data_diagnostics.artifact_fingerprints'
                ) AS evidence,
                route.score_rule_version,
                route.publish_version, route.is_active
            FROM market_environment_factor_route AS route
            INNER JOIN market_environment_eval_batch AS batch
                ON batch.id = route.eval_batch_id
            WHERE route.eval_batch_id = %s
              AND route.publication_uid = %s
              AND route.publish_version = %s
              AND route.is_active = 1
            ORDER BY route.label_code, route.rank_no,
                     route.factor_ref, route.id
        """

    @staticmethod
    def _route_snapshot_batch_query() -> str:
        return """
            SELECT
                id, publication_uid, publish_version,
                market_scope, route_profile_key
            FROM market_environment_eval_batch
            WHERE market_scope = %s
              AND route_profile_key = %s
              AND status = 'success'
              AND publish_status = 'published'
              AND is_active = 1
            ORDER BY published_at DESC, id DESC
            LIMIT 2
        """

    @staticmethod
    def _route_rankings_query() -> str:
        return """
            SELECT
                id, metric_id, environment_date, label_kind, label_code,
                as_of_time, factor_ref, factor_type, factor_id,
                factor_version, rank_no, routing_score, confidence,
                time_series_score, cross_sectional_score, score_rule_version
            FROM market_environment_factor_route
            WHERE eval_batch_id = %s
              AND publication_uid = %s
              AND publish_version = %s
              AND is_active = 1
              AND is_eligible = 1
            ORDER BY label_code, rank_no, factor_ref, id
        """

    @classmethod
    def _to_batch(cls, row: Mapping[str, Any], stage: str) -> PublishedEvaluationBatch:
        return PublishedEvaluationBatch(
            id=cls._required_int(row, "id", stage),
            batch_uid=cls._required_str(row, "batch_uid", stage),
            market_scope=cls._required_str(row, "market_scope", stage),
            label_kind=cls._required_str(row, "label_kind", stage),
            route_profile_key=cls._required_str(row, "route_profile_key", stage),
            start_date=cls._required_date(row, "start_date", stage),
            end_date=cls._required_date(row, "end_date", stage),
            as_of_time=cls._required_datetime(row, "as_of_time", stage),
            published_at=cls._required_datetime(row, "published_at", stage),
            publication_uid=cls._required_str(row, "publication_uid", stage),
            publish_version=cls._required_str(row, "publish_version", stage),
            evaluation_config_version=cls._required_str(row, "evaluation_config_version", stage),
            score_rule_version=cls._required_str(row, "score_rule_version", stage),
            code_version=cls._required_str(row, "code_version", stage),
            status=cls._required_str(row, "status", stage),
            publish_status=cls._required_str(row, "publish_status", stage),
            is_active=cls._required_bool(row, "is_active", stage),
            expected_metric_count=cls._required_int(row, "expected_metric_count", stage),
            completed_metric_count=cls._required_int(row, "completed_metric_count", stage),
            insufficient_metric_count=cls._required_int(row, "insufficient_metric_count", stage),
            failed_metric_count=cls._required_int(row, "failed_metric_count", stage),
            factor_set_snapshot_hash=cls._required_str(row, "factor_set_snapshot_hash", stage),
            environment_snapshot_hash=cls._required_str(row, "environment_snapshot_hash", stage),
            release_manifest_hash=cls._optional_str(row, "release_manifest_hash", stage),
            factor_set_snapshot=cls._required_json_object(row, "factor_set_snapshot", stage),
            evaluation_config=cls._required_json_object(row, "evaluation_config", stage),
            environment_status=cls._required_json_object(row, "environment_status", stage),
        )

    @classmethod
    def _to_definition(cls, row: Mapping[str, Any], stage: str) -> FactorDefinition:
        return FactorDefinition(
            factor_ref=cls._required_str(row, "factor_ref", stage),
            factor_type=cls._required_str(row, "factor_type", stage),
            factor_id=cls._required_int(row, "factor_id", stage),
            batch_factor_version=cls._required_str(
                row,
                "batch_factor_version",
                stage,
            ),
            serial_number=cls._optional_str(row, "serial_number", stage),
            name=cls._optional_str(row, "name", stage),
            window=cls._optional_str(row, "window", stage),
            factor_bar_interval=cls._optional_str(row, "factor_bar_interval", stage),
            formula_summary=cls._optional_str(row, "formula_summary", stage),
            definition_updated_at=cls._optional_datetime(row, "definition_updated_at", stage),
        )

    @classmethod
    def _to_detail(cls, row: Mapping[str, Any], stage: str) -> FactorDetail:
        return FactorDetail(
            id=cls._required_int(row, "id", stage),
            factor_ref=cls._required_str(row, "factor_ref", stage),
            factor_type=cls._required_str(row, "factor_type", stage),
            factor_id=cls._required_int(row, "factor_id", stage),
            batch_factor_version=cls._required_str(
                row,
                "batch_factor_version",
                stage,
            ),
            is_sub_factor_id=cls._required_bool(row, "is_sub_factor_id", stage),
            serial_number=cls._required_str(row, "serial_number", stage),
            name=cls._required_str(row, "name", stage),
            status=cls._required_int(row, "status", stage),
            calc_logic=cls._optional_str(row, "calc_logic", stage),
            params=cls._optional_json_object(row, "params", stage),
            data_source_metadata=cls._optional_json_object(row, "data_source_metadata", stage),
            updated_at=cls._optional_datetime(row, "updated_at", stage),
        )

    @classmethod
    def _to_formula_evidence(cls, row: Mapping[str, Any], stage: str) -> FormulaEvidence:
        return FormulaEvidence(
            id=cls._required_int(row, "id", stage),
            run_id=cls._required_str(row, "run_id", stage),
            factor_ref=cls._required_str(row, "factor_ref", stage),
            factor_type=cls._required_str(row, "factor_type", stage),
            factor_id=cls._required_int(row, "factor_id", stage),
            batch_factor_version=cls._required_str(
                row,
                "batch_factor_version",
                stage,
            ),
            is_sub_factor_id=cls._required_bool(row, "is_sub_factor_id", stage),
            calculation_mode=cls._required_str(row, "calculation_mode", stage),
            factor_bar_interval=cls._required_str(row, "factor_bar_interval", stage),
            factor_window_bars=cls._required_str(row, "factor_window_bars", stage),
            return_bar_interval=cls._required_str(row, "return_bar_interval", stage),
            forward_return_bars=cls._required_int(row, "forward_return_bars", stage),
            formula_version=cls._required_str(row, "formula_version", stage),
            formula_hash=cls._required_str(row, "formula_hash", stage),
            hash_algorithm=cls._required_str(row, "hash_algorithm", stage),
            normalization_version=cls._required_str(row, "normalization_version", stage),
            expression=cls._required_str(row, "expression", stage),
            required_fields=cls._required_json_sequence(row, "required_fields", stage),
            lookback=cls._optional_json_value(row, "lookback_json", stage),
            lag=cls._optional_json_value(row, "lag_json", stage),
            missing_policy=cls._optional_str(row, "missing_policy", stage),
            output_unit=cls._optional_str(row, "output_unit", stage),
            metadata_complete=cls._required_bool(row, "metadata_complete", stage),
            metadata_warnings=cls._required_json_sequence(row, "metadata_warnings", stage),
            source_detail_id=cls._optional_int(row, "source_detail_id", stage),
            recorded_at=cls._required_datetime(row, "recorded_at", stage),
            run_status=cls._required_str(row, "run_status", stage),
            run_completed_at=cls._required_datetime(row, "run_completed_at", stage),
            formula_factor_version=(
                cls._optional_str(row, "formula_factor_version", stage)
                if "formula_factor_version" in row
                else None
            ),
        )

    @classmethod
    def _to_evaluation_metric(cls, row: Mapping[str, Any], stage: str) -> EvaluationMetric:
        decimal_names = (
            "mean_ic",
            "mean_rank_ic",
            "icir",
            "rank_icir",
            "time_series_score",
            "cross_sectional_score",
            "routing_score",
            "confidence",
        )
        decimals = {name: cls._optional_decimal(row, name, stage) for name in decimal_names}
        return EvaluationMetric(
            id=cls._required_int(row, "id", stage),
            eval_batch_id=cls._required_int(row, "eval_batch_id", stage),
            factor_ref=cls._required_str(row, "factor_ref", stage),
            factor_type=cls._required_str(row, "factor_type", stage),
            factor_id=cls._required_int(row, "factor_id", stage),
            factor_version=cls._required_str(row, "factor_version", stage),
            market_scope=cls._required_str(row, "market_scope", stage),
            label_kind=cls._required_str(row, "label_kind", stage),
            label_code=cls._required_str(row, "label_code", stage),
            evaluation_type=cls._required_str(row, "evaluation_type", stage),
            interval=cls._required_str(row, "interval", stage),
            return_bar_interval=cls._required_str(row, "return_bar_interval", stage),
            forward_return_bars=cls._required_int(row, "forward_return_bars", stage),
            window_scope=cls._required_str(row, "window_scope", stage),
            sample_start_date=cls._required_date(row, "sample_start_date", stage),
            sample_end_date=cls._required_date(row, "sample_end_date", stage),
            metric_status=cls._required_str(row, "metric_status", stage),
            is_valid=cls._optional_bool(row, "is_valid", stage),
            scoring_version=cls._required_str(row, "scoring_version", stage),
            metric_payload=cls._optional_json_object(row, "metric_payload", stage),
            metric_identity=cls._optional_json_object(row, "metric_identity", stage),
            metric_pair_identity_hash=cls._optional_str(
                row,
                "metric_pair_identity_hash",
                stage,
            ),
            score_components=cls._optional_json_object(row, "score_components", stage),
            route_eligibility=cls._optional_json_object(row, "route_eligibility", stage),
            direction=cls._optional_json_object(row, "direction", stage),
            aggregation=cls._optional_json_object(row, "aggregation", stage),
            error_code=cls._optional_str(row, "error_code", stage),
            error_message=cls._optional_str(row, "error_message", stage),
            **decimals,
        )

    @classmethod
    def _to_route(cls, row: Mapping[str, Any], stage: str) -> PublishedRoute:
        return PublishedRoute(
            id=cls._required_int(row, "id", stage),
            publication_uid=cls._required_str(row, "publication_uid", stage),
            eval_batch_id=cls._required_int(row, "eval_batch_id", stage),
            metric_id=cls._required_int(row, "metric_id", stage),
            market_scope=cls._required_str(row, "market_scope", stage),
            route_profile_key=cls._required_str(row, "route_profile_key", stage),
            environment_date=cls._required_date(row, "environment_date", stage),
            label_kind=cls._required_str(row, "label_kind", stage),
            label_code=cls._required_str(row, "label_code", stage),
            as_of_time=cls._required_datetime(row, "as_of_time", stage),
            factor_ref=cls._required_str(row, "factor_ref", stage),
            factor_type=cls._required_str(row, "factor_type", stage),
            factor_id=cls._required_int(row, "factor_id", stage),
            factor_version=cls._required_str(row, "factor_version", stage),
            rank_no=cls._required_int(row, "rank_no", stage),
            routing_score=cls._required_decimal(row, "routing_score", stage),
            confidence=cls._optional_decimal(row, "confidence", stage),
            time_series_score=cls._optional_decimal(row, "time_series_score", stage),
            cross_sectional_score=cls._optional_decimal(row, "cross_sectional_score", stage),
            is_eligible=cls._required_bool(row, "is_eligible", stage),
            reject_reason_code=cls._optional_str(row, "reject_reason_code", stage),
            evidence=cls._required_json_object(row, "evidence", stage),
            score_rule_version=cls._required_str(row, "score_rule_version", stage),
            publish_version=cls._required_str(row, "publish_version", stage),
            is_active=cls._required_bool(row, "is_active", stage),
        )

    @classmethod
    def _to_route_ranking(
        cls,
        row: Mapping[str, Any],
        stage: str,
    ) -> RouteRankingEntry:
        return RouteRankingEntry(
            id=cls._required_int(row, "id", stage),
            metric_id=cls._required_int(row, "metric_id", stage),
            environment_date=cls._required_date(row, "environment_date", stage),
            label_kind=cls._required_str(row, "label_kind", stage),
            label_code=cls._required_str(row, "label_code", stage),
            as_of_time=cls._required_datetime(row, "as_of_time", stage),
            factor_ref=cls._required_str(row, "factor_ref", stage),
            factor_type=cls._required_str(row, "factor_type", stage),
            factor_id=cls._required_int(row, "factor_id", stage),
            factor_version=cls._required_str(row, "factor_version", stage),
            rank_no=cls._required_int(row, "rank_no", stage),
            routing_score=cls._required_decimal(row, "routing_score", stage),
            confidence=cls._optional_decimal(row, "confidence", stage),
            time_series_score=cls._optional_decimal(row, "time_series_score", stage),
            cross_sectional_score=cls._optional_decimal(
                row,
                "cross_sectional_score",
                stage,
            ),
            score_rule_version=cls._required_str(row, "score_rule_version", stage),
        )

    @staticmethod
    def _value(row: Mapping[str, Any] | None, name: str, stage: str) -> Any:
        if row is None or name not in row:
            raise CalculationRepositoryError(stage, f"required column {name!r} is missing")
        return row[name]

    @classmethod
    def _required_str(
        cls,
        row: Mapping[str, Any] | None,
        name: str,
        stage: str,
        *,
        allow_blank: bool = False,
    ) -> str:
        value = cls._value(row, name, stage)
        if not isinstance(value, str) or (not allow_blank and not value.strip()):
            cls._invalid_value(stage, name, "a non-empty string" if not allow_blank else "a string")
        return value

    @classmethod
    def _optional_str(cls, row: Mapping[str, Any], name: str, stage: str) -> str | None:
        value = cls._value(row, name, stage)
        if value is None:
            return None
        if not isinstance(value, str):
            cls._invalid_value(stage, name, "a string or null")
        return value

    @classmethod
    def _required_int(cls, row: Mapping[str, Any], name: str, stage: str) -> int:
        value = cls._value(row, name, stage)
        if isinstance(value, bool):
            cls._invalid_value(stage, name, "an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, Decimal):
            if not value.is_finite() or value != value.to_integral_value():
                cls._invalid_value(stage, name, "an integer")
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                cls._invalid_value(stage, name, "an integer")
        if isinstance(value, float):
            if not math.isfinite(value) or not value.is_integer():
                cls._invalid_value(stage, name, "an integer")
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                cls._invalid_value(stage, name, "an integer")
        if isinstance(value, str):
            normalized = value.strip()
            if not re.fullmatch(r"[+-]?[0-9]+", normalized):
                cls._invalid_value(stage, name, "an integer")
            try:
                return int(normalized)
            except (TypeError, ValueError, OverflowError):
                cls._invalid_value(stage, name, "an integer")
        cls._invalid_value(stage, name, "an integer")

    @classmethod
    def _optional_int(cls, row: Mapping[str, Any], name: str, stage: str) -> int | None:
        value = cls._value(row, name, stage)
        if value is None:
            return None
        return cls._required_int(row, name, stage)

    @classmethod
    def _required_bool(cls, row: Mapping[str, Any], name: str, stage: str) -> bool:
        value = cls._value(row, name, stage)
        if value in (0, 1, False, True):
            return bool(value)
        cls._invalid_value(stage, name, "0 or 1")

    @classmethod
    def _optional_bool(cls, row: Mapping[str, Any], name: str, stage: str) -> bool | None:
        value = cls._value(row, name, stage)
        if value is None:
            return None
        return cls._required_bool(row, name, stage)

    @classmethod
    def _required_decimal(cls, row: Mapping[str, Any], name: str, stage: str) -> Decimal:
        value = cls._value(row, name, stage)
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            cls._invalid_value(stage, name, "a decimal")

    @classmethod
    def _optional_decimal(
        cls,
        row: Mapping[str, Any],
        name: str,
        stage: str,
    ) -> Decimal | None:
        value = cls._value(row, name, stage)
        if value is None:
            return None
        return cls._required_decimal(row, name, stage)

    @classmethod
    def _required_datetime(
        cls,
        row: Mapping[str, Any] | None,
        name: str,
        stage: str,
    ) -> datetime:
        value = cls._value(row, name, stage)
        if not isinstance(value, datetime):
            cls._invalid_value(stage, name, "a datetime")
        return value

    @classmethod
    def _optional_datetime(
        cls,
        row: Mapping[str, Any],
        name: str,
        stage: str,
    ) -> datetime | None:
        value = cls._value(row, name, stage)
        if value is None:
            return None
        if not isinstance(value, datetime):
            cls._invalid_value(stage, name, "a datetime or null")
        return value

    @classmethod
    def _required_date(cls, row: Mapping[str, Any], name: str, stage: str) -> date:
        value = cls._value(row, name, stage)
        if not isinstance(value, date):
            cls._invalid_value(stage, name, "a date")
        return value

    @classmethod
    def _required_json_object(
        cls,
        row: Mapping[str, Any],
        name: str,
        stage: str,
    ) -> JsonObject:
        value = cls._decode_json(cls._value(row, name, stage), name, stage)
        if not isinstance(value, dict):
            cls._invalid_value(stage, name, "a JSON object")
        return value

    @classmethod
    def _optional_json_object(
        cls,
        row: Mapping[str, Any],
        name: str,
        stage: str,
    ) -> JsonObject | None:
        raw = cls._value(row, name, stage)
        if raw is None:
            return None
        value = cls._decode_json(raw, name, stage)
        if not isinstance(value, dict):
            cls._invalid_value(stage, name, "a JSON object or null")
        return value

    @classmethod
    def _required_json_sequence(
        cls,
        row: Mapping[str, Any],
        name: str,
        stage: str,
    ) -> tuple[Any, ...]:
        value = cls._decode_json(cls._value(row, name, stage), name, stage)
        if not isinstance(value, list):
            cls._invalid_value(stage, name, "a JSON array")
        return tuple(value)

    @classmethod
    def _optional_json_array(
        cls,
        row: Mapping[str, Any],
        name: str,
        stage: str,
    ) -> list[Any] | None:
        raw = cls._value(row, name, stage)
        if raw is None:
            return None
        value = cls._decode_json(raw, name, stage)
        if not isinstance(value, list):
            cls._invalid_value(stage, name, "a JSON array or null")
        return value

    @classmethod
    def _optional_json_value(
        cls,
        row: Mapping[str, Any],
        name: str,
        stage: str,
    ) -> Any | None:
        raw = cls._value(row, name, stage)
        if raw is None:
            return None
        return cls._decode_json(raw, name, stage)

    @staticmethod
    def _decode_json(value: Any, name: str, stage: str) -> Any:
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", "strict")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise CalculationRepositoryError(
                    stage, f"column {name!r} contains invalid JSON"
                ) from exc
        return value

    @staticmethod
    def _invalid_value(stage: str, name: str, expected: str) -> NoReturn:
        raise CalculationRepositoryError(stage, f"column {name!r} must be {expected}")
