"""Independent adversarial fixtures for source aggregation; no product-pass claims."""

from typing import Any

import pytest

from service.strategy_dashboard_current_overview_audit import (
    AMOUNT_SOURCES, METRIC_SOURCES, RATIO_SOURCES, CurrentOverviewAudit, source_expectations,
)
from tests.cases.strategy_dashboard import test_source_current_overview as acceptance


pytestmark = pytest.mark.unit
STAMP = "2026-09-24T03:20:00Z"
OBSERVED = "2026-09-24T03:19:51Z"
VALUES = {
    "alpha": ["100", "5", "-40", "-4", "3", "0.8", "-0.5", "0.2"],
    "nt_only": ["200", "-10", "30", "-2", "-9", "1.1", "0.1", "0.05"],
    "stopped": ["10000", "10000", "10000", "10000", "10000", "2", "2", "0"],
}


def _inputs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accounts = [{"account_id": "nt:" + name, "local_account_id": None, "nt_account_id": name,
                 "actual_status": "stopped" if name == "stopped" else "running",
                 "nt_enabled": True, "lifecycle_status": "active"}
                for name in ["alpha", "nt_only", "missing", "stopped"]]
    samples = [{"account_id": name, "metric": metric, "value": value, "observed_at": OBSERVED}
               for name, values in VALUES.items() for metric, value in zip(METRIC_SOURCES.values(), values)]
    return accounts, {"raw": {"fetched_at": STAMP, "samples": samples}}


def _evidence() -> dict[str, Any]:
    accounts, source = _inputs()
    expected = source_expectations(accounts, source)
    for account in accounts:
        account["metrics"] = {}
        for metric, data in expected["metrics"].items():
            known = data["known_accounts"].get(account["nt_account_id"])
            account["metrics"][metric] = ({"value": known["value"], "status": "complete", "observed_at": OBSERVED}
                                          if known else {"value": None, "status": "unavailable", "reason_code": "MISSING"})
    metrics = {metric: {"value": row["known_subtotal"], "status": "partial", "reason_code": "MISSING", "observed_at": OBSERVED}
               for metric, row in expected["metrics"].items()}
    return {"expected": expected, "accounts": accounts, "evidence_report": "offline-only",
            "batch_id": "source-a", "summary": {"counts": {"account_count": 3},
                "context": {"subject_type": "portfolio", "population": "running"}, "metrics": metrics}}


def test_independent_signed_totals_include_nt_only_and_exclude_stopped() -> None:
    accounts, source = _inputs()
    result = source_expectations(accounts, source)
    assert result["running_account_ids"] == ["alpha", "missing", "nt_only"]
    assert result["excluded_account_ids"] == ["stopped"]
    totals = {key: row["known_subtotal"] for key, row in result["metrics"].items() if key in AMOUNT_SOURCES}
    assert totals == {"margin_equity": "300", "unrealized_pnl": "-5", "realized_pnl_gross": "-10",
                      "commission_expense": "-6", "funding_pnl": "-6"}
    assert result["metrics"]["cumulative_return"]["known_accounts"]["alpha"]["value"] == "-0.2"
    assert result["metrics"]["cumulative_return"]["known_subtotal"] is None


def test_zero_negative_and_missing_are_not_confused() -> None:
    accounts, source = _inputs()
    source["raw"]["samples"][0]["value"] = "0"
    result = source_expectations(accounts, source)["metrics"]["margin_equity"]
    assert set(result["known_accounts"]) == {"alpha", "nt_only"}
    assert result["known_subtotal"] == "200"
    assert result["missing_accounts"] == ["missing"]
    assert result["is_complete"] is False


def test_duplicate_account_level_samples_from_strategy_labels_are_counted_once() -> None:
    accounts, source = _inputs()
    duplicate = dict(source["raw"]["samples"][0], strategy_id="second-binding")
    source["raw"]["samples"].append(duplicate)
    result = source_expectations(accounts, source)["metrics"]["margin_equity"]
    assert result["known_subtotal"] == "300"
    assert result["known_accounts"]["alpha"]["duplicate_sample_count"] == 2


@pytest.mark.parametrize("change", [{"value": "101"}, {"observed_at": "2026-09-24T03:18:51Z"}])
def test_ambiguous_source_cannot_silently_pick_one_sample(change: dict[str, str]) -> None:
    accounts, source = _inputs()
    source["raw"]["samples"].append(dict(source["raw"]["samples"][0], **change))
    with pytest.raises(ValueError, match="AMBIGUOUS_ACCOUNT_SOURCE"):
        source_expectations(accounts, source)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "", True])
def test_invalid_amount_never_becomes_a_zero_or_known_subtotal(value: Any) -> None:
    accounts, source = _inputs()
    source["raw"]["samples"][0]["value"] = value
    with pytest.raises(ValueError):
        source_expectations(accounts, source)


def test_duplicate_directory_membership_is_detected_before_summing() -> None:
    accounts, source = _inputs()
    with pytest.raises(ValueError, match="DIRECTORY_DUPLICATE_SOURCE_ACCOUNT"):
        source_expectations(accounts + [accounts[0]], source)


def test_nonparticipating_unmapped_account_does_not_block_running_sources() -> None:
    accounts, source = _inputs()
    accounts.append({"account_id": "local-only", "actual_status": "stopped", "nt_account_id": None})
    assert source_expectations(accounts, source)["metrics"]["margin_equity"]["known_subtotal"] == "300"
    accounts[-1]["actual_status"] = "running"
    with pytest.raises(ValueError, match="DIRECTORY_SOURCE_ID_MISSING"):
        source_expectations(accounts, source)


@pytest.mark.parametrize("metric", list(AMOUNT_SOURCES))
def test_live_assertion_rejects_single_account_summary_even_when_partial(metric: str) -> None:
    evidence = _evidence()
    evidence["summary"]["metrics"][metric]["value"] = evidence["expected"]["metrics"][metric]["known_accounts"]["alpha"]["value"]
    with pytest.raises(AssertionError, match=acceptance.BUG_TITLE):
        acceptance.test_current_portfolio_amount_equals_database_running_account_sum(metric, evidence)


def test_live_assertions_accept_true_known_subtotal_and_complete_directory_scope() -> None:
    evidence = _evidence()
    acceptance.test_current_overview_population_includes_all_directory_running_accounts(evidence)
    for metric in AMOUNT_SOURCES:
        acceptance.test_current_portfolio_amount_equals_database_running_account_sum(metric, evidence)
    for metric in METRIC_SOURCES:
        acceptance.test_running_account_metric_matches_exact_database_source(metric, evidence)
    acceptance.test_current_amount_completeness_preserves_missing_source_accounts(evidence)
    for metric in RATIO_SOURCES:
        acceptance.test_current_portfolio_ratio_does_not_claim_complete_with_missing_accounts(metric, evidence)


def test_single_account_count_cannot_hide_omitted_nt_only_and_missing_accounts() -> None:
    evidence = _evidence()
    evidence["summary"]["counts"]["account_count"] = 1
    with pytest.raises(AssertionError, match=acceptance.BUG_TITLE):
        acceptance.test_current_overview_population_includes_all_directory_running_accounts(evidence)


def test_missing_account_cannot_be_returned_as_complete_zero() -> None:
    evidence = _evidence()
    evidence["accounts"][2]["metrics"]["margin_equity"] = {"value": "0", "status": "complete"}
    with pytest.raises(AssertionError, match="missing source"):
        acceptance.test_running_account_metric_matches_exact_database_source("margin_equity", evidence)


def test_mislabeled_complete_ratio_cannot_hide_missing_accounts() -> None:
    evidence = _evidence()
    evidence["summary"]["metrics"]["cumulative_return"] = {"value": "-0.2", "status": "complete"}
    with pytest.raises(AssertionError, match=acceptance.BUG_TITLE):
        acceptance.test_current_portfolio_ratio_does_not_claim_complete_with_missing_accounts("cumulative_return", evidence)


def test_unchanged_amount_from_old_timestamp_is_not_a_current_source_pass() -> None:
    evidence = _evidence()
    evidence["summary"]["metrics"]["commission_expense"]["observed_at"] = "2026-08-01T00:00:00Z"
    with pytest.raises(AssertionError, match="SUMMARY_SOURCE_OBSERVED_AT_MISMATCH"):
        acceptance.test_current_portfolio_amount_equals_database_running_account_sum("commission_expense", evidence)


@pytest.mark.parametrize("field", ["value", "known_value"])
def test_partial_status_does_not_make_nonfinite_ratio_acceptable(field: str) -> None:
    evidence = _evidence()
    evidence["summary"]["metrics"]["cumulative_return"][field] = "NaN"
    with pytest.raises(ValueError, match="NONFINITE_NUMBER"):
        acceptance.test_current_portfolio_ratio_does_not_claim_complete_with_missing_accounts("cumulative_return", evidence)


class _API:
    def __init__(self, *, rollover: bool = False, broken_snapshot: bool = False) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.round = 0
        self.rollover = rollover
        self.broken_snapshot = broken_snapshot

    def accounts_page(self, *, page: int = 1, snapshot_id: str | None = None) -> dict[str, Any]:
        self.calls.append(("directory", page, snapshot_id))
        if page == 1:
            self.round += 1
        count = 100 if page == 1 else 1
        rows = [{"nt_account_id": str((page - 1) * 100 + index), "actual_status": "running", "metrics": {}}
                for index in range(count)]
        return {"http_status": 200, "request_params": {"page": page}, "body": {"success": True,
            "meta": {"mock": False, "fetched_at": STAMP, "snapshot_id": "changed" if page == 2 and self.broken_snapshot else "directory-snapshot"},
            "data": {"items": rows, "total_status": "exact", "total": 101, "total_pages": 2, "has_more": page == 1}}}

    def summary(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("summary", self.round))
        return {"http_status": 200, "request_params": kwargs, "body": {"success": True,
            "meta": {"mock": False, "fetched_at": "later" if self.rollover and self.round == 1 else STAMP},
            "data": {"metrics": {}, "counts": {"account_count": 1}, "context": {}}}}


class _Repository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def source_at(self, stamp: str) -> dict[str, Any]:
        self.calls.append(stamp)
        return {"batch_id": "b", "cutoff": "t", "source": {"raw": {"fetched_at": stamp, "samples": []}}}


def test_full_pagination_snapshot_and_generation_rollover_do_not_use_summary_count() -> None:
    api, repository = _API(rollover=True), _Repository()
    evidence = CurrentOverviewAudit(repository, api).collect(from_at="start", to_at="end")
    assert len(evidence["expected"]["running_account_ids"]) == 101
    assert len(evidence["collection_attempts"]) == 2
    assert repository.calls == [STAMP]
    assert api.calls.count(("directory", 2, "directory-snapshot")) == 2


def test_changed_pagination_snapshot_is_not_a_partial_success() -> None:
    repository = _Repository()
    with pytest.raises(ValueError, match="DIRECTORY_SNAPSHOT_CHANGED"):
        CurrentOverviewAudit(repository, _API(broken_snapshot=True)).collect(from_at="start", to_at="end")
    assert not repository.calls
