"""Regression coverage for version-aware Factor 4.0 membership checks."""

from __future__ import annotations

from db.factor4_calculation_repository import (
    Factor4CalculationRepository,
    FactorMembershipVersionIssue,
)


def _identity(version: str) -> dict[str, object]:
    return {
        "factor_ref": "sub_factor:10",
        "factor_type": "sub_factor",
        "factor_id": 10,
        "batch_factor_version": version,
    }


def test_membership_reconciliation_does_not_hide_factor_version_drift() -> None:
    """The same catalog ID with a different frozen version is a drift."""

    differences = Factor4CalculationRepository._membership_differences(
        {(True, 10): _identity("sha256:new")},
        {(True, 10): _identity("sha256:old")},
    )

    assert differences.missing_from_metrics[0].factor_version == "sha256:new"
    assert differences.unexpected_in_metrics[0].factor_version == "sha256:old"


def test_metric_membership_uses_definition_version_not_executable_hash() -> None:
    """A formula hash must not create a false membership drift."""

    metrics, unresolved = Factor4CalculationRepository._metric_membership_identity_map(
        [
            {
                "factor_ref": "sub_factor:10",
                "factor_type": "sub_factor",
                "factor_id": 10,
                "factor_version": "sha256:formula-run",
                "definition_factor_version": "updated_at:2026-08-01T00:00:00Z",
            },
            {
                "factor_ref": "sub_factor:10",
                "factor_type": "sub_factor",
                "factor_id": 10,
                "factor_version": "sha256:formula-run",
                "definition_factor_version": "updated_at:2026-08-01T00:00:00Z",
            },
        ],
        "batch_factor_identities",
    )

    differences = Factor4CalculationRepository._membership_differences(
        {(True, 10): _identity("updated_at:2026-08-01T00:00:00Z")},
        metrics,
        missing_definition_versions=unresolved,
    )

    assert not unresolved
    assert not differences.missing_from_metrics
    assert not differences.unexpected_in_metrics
    assert metrics[(True, 10)]["batch_factor_version"] == (
        "updated_at:2026-08-01T00:00:00Z"
    )


def test_missing_definition_version_is_blocked_without_false_drift() -> None:
    """An absent payload definition version is unverifiable, not a version drift."""

    metrics, unresolved = Factor4CalculationRepository._metric_membership_identity_map(
        [
            {
                "factor_ref": "sub_factor:10",
                "factor_type": "sub_factor",
                "factor_id": 10,
                "factor_version": "sha256:formula-run",
                "definition_factor_version": None,
            }
        ],
        "batch_factor_identities",
    )

    differences = Factor4CalculationRepository._membership_differences(
        {(True, 10): _identity("updated_at:2026-08-01T00:00:00Z")},
        metrics,
        missing_definition_versions=unresolved,
    )

    assert not metrics
    assert len(unresolved) == 1
    assert isinstance(unresolved[0], FactorMembershipVersionIssue)
    assert unresolved[0].reason == "definition_factor_version_missing_or_invalid"
    assert not differences.missing_from_metrics
    assert not differences.unexpected_in_metrics
    assert differences.missing_definition_versions == unresolved


def test_conflicting_scope_definition_versions_are_blocked_without_drift() -> None:
    """TS/CS rows with different definition versions cannot be reconciled."""

    metrics, unresolved = Factor4CalculationRepository._metric_membership_identity_map(
        [
            {
                "factor_ref": "sub_factor:10",
                "factor_type": "sub_factor",
                "factor_id": 10,
                "factor_version": "sha256:formula-ts",
                "definition_factor_version": "updated_at:2026-08-01T00:00:00Z",
            },
            {
                "factor_ref": "sub_factor:10",
                "factor_type": "sub_factor",
                "factor_id": 10,
                "factor_version": "sha256:formula-cs",
                "definition_factor_version": "updated_at:2026-08-02T00:00:00Z",
            },
        ],
        "batch_factor_identities",
    )

    differences = Factor4CalculationRepository._membership_differences(
        {(True, 10): _identity("updated_at:2026-08-01T00:00:00Z")},
        metrics,
        missing_definition_versions=unresolved,
    )

    assert not metrics
    assert unresolved[0].reason == "definition_factor_version_conflict"
    assert not differences.missing_from_metrics
    assert not differences.unexpected_in_metrics
