"""Current overview: fresh API vs persisted per-account source, without injection."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from service.strategy_dashboard_current_overview_audit import (
    AMOUNT_SOURCES, METRIC_SOURCES, METRIC_TITLES, RATIO_SOURCES,
    finite_decimal,
)


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.strategy_dashboard]
BUG_TITLE = "当前数据卡片参与汇总的账户范围不一致"


def _close(actual: Any, expected: Any) -> bool:
    left, right = finite_decimal(actual), finite_decimal(expected)
    return abs(left - right) <= max(Decimal("1e-8"), abs(right) * Decimal("1e-12"))


@pytest.fixture(scope="module")
def current_overview_evidence(current_overview_runtime: dict[str, Any]) -> dict[str, Any]:
    """Collect one fresh read-only generation per run and persist auditable inputs."""
    runtime = current_overview_runtime
    evidence = runtime["audit"].collect(from_at=runtime["from_at"], to_at=runtime["to_at"])
    runtime["save"](evidence)
    return evidence


def test_current_overview_population_includes_all_directory_running_accounts(current_overview_evidence: dict[str, Any]) -> None:
    """BV-101: summary cannot shrink the directory population to local bindings."""
    evidence = current_overview_evidence
    ids = evidence["expected"]["running_account_ids"]
    if len(ids) < 2:
        pytest.skip("SAMPLE_GAP: need multiple running accounts to exercise aggregation")
    summary = evidence["summary"]
    assert summary["context"]["subject_type"] == "portfolio"
    assert summary["context"]["population"] == "running"
    assert summary["counts"]["account_count"] == len(ids), {
        "bug": BUG_TITLE, "expected_running_accounts": ids, "actual_counts": summary["counts"],
        "batch_id": evidence["batch_id"], "evidence": evidence["evidence_report"],
    }


@pytest.mark.parametrize("metric", list(METRIC_SOURCES))
def test_running_account_metric_matches_exact_database_source(metric: str, current_overview_evidence: dict[str, Any]) -> None:
    """Verify all eight directory metrics account by account, including missing data."""
    evidence = current_overview_evidence
    expected = evidence["expected"]["metrics"][metric]
    if not expected["known_accounts"]:
        pytest.skip(f"SAMPLE_GAP: no known source for {metric}")
    accounts = {row["nt_account_id"]: row for row in evidence["accounts"]}
    errors = []
    for identity in evidence["expected"]["running_account_ids"]:
        actual = accounts[identity]["metrics"].get(metric)
        source = expected["known_accounts"].get(identity)
        try:
            assert isinstance(actual, dict), "missing metric object"
            if source is None:
                assert actual.get("value") is None, "missing source must not be zero"
                assert actual.get("status") in {"unavailable", "partial"}
                assert actual.get("reason_code")
            else:
                assert _close(actual.get("value"), source["value"]), "source value mismatch"
                assert actual.get("status") == "complete"
                assert actual.get("observed_at") == source["observed_at"], "mixed observed_at"
        except (AssertionError, ValueError) as exc:
            errors.append({"account": identity, "metric": metric, "source": source, "actual": actual, "error": str(exc)})
    assert not errors, errors


@pytest.mark.parametrize("metric", list(AMOUNT_SOURCES))
def test_current_portfolio_amount_equals_database_running_account_sum(metric: str, current_overview_evidence: dict[str, Any]) -> None:
    """Independently sum signed account inputs; partial does not excuse omitting known accounts."""
    evidence = current_overview_evidence
    expected = evidence["expected"]["metrics"][metric]
    if len(expected["known_accounts"]) < 2:
        pytest.skip(f"SAMPLE_GAP: need two known accounts for {metric}")
    actual = evidence["summary"]["metrics"][metric]
    value = actual.get("value")
    if value is None:
        value = actual.get("known_value")
    if value is None and expected["missing_accounts"] and actual.get("status") in {"partial", "unavailable"}:
        pytest.skip(f"SOURCE_SUBTOTAL_UNAVAILABLE: {metric}; no numeric subtotal to compare")
    assert value is not None, {"metric": metric, "expected": expected["known_subtotal"], "actual": actual}
    assert _close(value, expected["known_subtotal"]), {
        "bug": BUG_TITLE, "metric": METRIC_TITLES[metric], "actual": value,
        "database_subtotal": expected["known_subtotal"], "known_accounts": expected["known_accounts"],
        "missing_accounts": expected["missing_accounts"], "batch_id": evidence["batch_id"],
        "evidence": evidence["evidence_report"],
    }
    if actual.get("known_value") is not None:
        assert _close(actual["known_value"], expected["known_subtotal"])
    source_times = {row["observed_at"] for row in expected["known_accounts"].values()}
    if len(source_times) == 1:
        assert actual.get("observed_at") == next(iter(source_times)), {
            "metric": metric, "reason": "SUMMARY_SOURCE_OBSERVED_AT_MISMATCH",
            "expected": sorted(source_times), "actual": actual.get("observed_at"),
        }


def test_current_amount_completeness_preserves_missing_source_accounts(current_overview_evidence: dict[str, Any]) -> None:
    """Known subtotal is not a complete total when any running account lacks input."""
    evidence = current_overview_evidence
    errors = []
    for metric in AMOUNT_SOURCES:
        expected = evidence["expected"]["metrics"][metric]
        actual = evidence["summary"]["metrics"].get(metric, {})
        if expected["missing_accounts"]:
            valid = actual.get("status") in {"partial", "unavailable"} and bool(actual.get("reason_code"))
        else:
            valid = actual.get("status") == "complete"
        if not valid:
            errors.append({"metric": metric, "actual": actual, "missing_accounts": expected["missing_accounts"]})
    assert not errors, errors


@pytest.mark.parametrize("metric", list(RATIO_SOURCES))
def test_current_portfolio_ratio_does_not_claim_complete_with_missing_accounts(metric: str, current_overview_evidence: dict[str, Any]) -> None:
    """Check coverage only; do not sum rates or approve an undefined portfolio formula."""
    evidence = current_overview_evidence
    expected = evidence["expected"]["metrics"][metric]
    actual = evidence["summary"]["metrics"].get(metric, {})
    for field in ("value", "known_value"):
        if actual.get(field) is not None:
            finite_decimal(actual[field])
    if not expected["missing_accounts"]:
        pytest.skip(f"SAMPLE_GAP: {metric} has no missing account input; composite formula not covered here")
    assert actual.get("status") in {"partial", "unavailable"} and actual.get("reason_code"), {
        "bug": BUG_TITLE, "metric": METRIC_TITLES[metric], "actual": actual,
        "missing_accounts": expected["missing_accounts"], "evidence": evidence["evidence_report"],
        "scope": "population coverage only; no composite ratio formula asserted",
    }
