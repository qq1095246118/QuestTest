"""Current overview query dimensions; every parameter is a separate live collection."""

from datetime import datetime
from typing import Any

import pytest

from service.strategy_dashboard_current_overview_audit import AMOUNT_SOURCES, METRIC_SOURCES, finite_decimal
from service.strategy_dashboard_current_overview_queries import (
    ACCOUNT_SCENARIOS, DIRECTORY_SCENARIOS, SUBSET_SCENARIOS, WINDOW_SCENARIOS, OverviewSampleGap,
)
from tests.cases.strategy_dashboard.test_source_current_overview import BUG_TITLE, _close


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.strategy_dashboard]


def _collect(runtime: dict[str, Any], kind: str, scenario: str) -> dict[str, Any]:
    try:
        if kind == "directory":
            return runtime["queries"].directory_variant(scenario)
        return runtime["queries"].summary_variant(kind, scenario, from_at=runtime["from_at"], to_at=runtime["to_at"])
    except OverviewSampleGap as exc:
        pytest.skip(str(exc))


def _success(record: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    assert response["http_status"] == 200 and response.get("success") is True, {
        "scenario": record["scenario"], "http_status": response["http_status"],
        "error": response.get("error"), "evidence": record.get("evidence_report"),
    }
    assert response["meta"].get("mock") is False, "NATURAL_SOURCE_NOT_CONFIRMED"
    assert response["meta"].get("fetched_at") == record["fetched_at"], "SOURCE_GENERATION_MISMATCH"
    data = response["data"]
    assert isinstance(data.get("metrics"), dict) and isinstance(data.get("context"), dict)
    return data


def _context_matches_query(response: dict[str, Any]) -> None:
    context, query = response["data"]["context"], response["request_params"]
    for field in ("subject_type", "population", "metric_source"):
        assert context.get(field) == query[field], (field, context)
    assert context.get("scope") == "source_lifetime", context
    assert context.get("window_applied") is False, context
    for field in ("from", "to"):
        echoed = context.get("requested_" + field)
        assert echoed and datetime.fromisoformat(echoed.replace("Z", "+00:00")) == datetime.fromisoformat(query[field].replace("Z", "+00:00")), (field, context)


def _metric_signature(metric: dict[str, Any]) -> dict[str, Any]:
    # Freshness changes with wall-clock time, even inside one source generation.
    return {key: metric.get(key) for key in (
        "value", "known_value", "status", "reason_code", "observed_at", "source", "unit", "basis", "formula_version",
    )}


def _source_errors(actual: dict[str, Any], source: dict[str, Any] | None) -> list[str]:
    if not isinstance(actual, dict):
        return ["METRIC_OBJECT_MISSING"]
    errors = []
    if source is None:
        if actual.get("value") is not None or actual.get("known_value") is not None:
            errors.append("MISSING_SOURCE_REPLACED_WITH_VALUE")
        if actual.get("status") not in {"partial", "unavailable"} or not actual.get("reason_code"):
            errors.append("MISSING_SOURCE_CLAIMED_COMPLETE")
        return errors
    value = actual.get("value") if actual.get("value") is not None else actual.get("known_value")
    try:
        if not _close(value, source["value"]):
            errors.append("DATABASE_SOURCE_VALUE_MISMATCH")
        if actual.get("known_value") is not None and not _close(actual["known_value"], source["value"]):
            errors.append("KNOWN_VALUE_MISMATCH")
    except ValueError:
        errors.append("KNOWN_SOURCE_HAS_NO_FINITE_VALUE")
    if actual.get("observed_at") != source["observed_at"]:
        errors.append("DATABASE_SOURCE_OBSERVED_AT_MISMATCH")
    if actual.get("status") not in {"complete", "partial"}:
        errors.append("KNOWN_SOURCE_STATUS_INVALID")
    if actual.get("status") == "partial" and not actual.get("reason_code"):
        errors.append("PARTIAL_REASON_MISSING")
    return errors


@pytest.mark.parametrize("scenario", DIRECTORY_SCENARIOS)
def test_current_directory_query_preserves_members_and_source_values(scenario: str, current_overview_runtime: dict[str, Any]) -> None:
    """Small pages, status/exchange filters and NT-only selections cannot lose rows."""
    record = _collect(current_overview_runtime, "directory", scenario)
    expected = {str(row["account_id"]): row for row in record["expected_accounts"]}
    rows = record["actual_accounts"]
    actual = {str(row["account_id"]): row for row in rows}
    assert len(actual) == len(rows) and set(actual) == set(expected), {
        "scenario": scenario, "expected": sorted(expected), "actual": sorted(actual),
        "evidence": record.get("evidence_report"),
    }
    errors = []
    for identity, before in expected.items():
        after = actual[identity]
        for field in ("nt_account_id", "actual_status", "exchange"):
            if after.get(field) != before.get(field):
                errors.append((identity, field, before.get(field), after.get(field)))
        for metric in METRIC_SOURCES:
            if metric not in before.get("metrics", {}) or metric not in after.get("metrics", {}):
                errors.append((identity, metric, "METRIC_MISSING"))
                continue
            if _metric_signature(after["metrics"][metric]) != _metric_signature(before["metrics"][metric]):
                errors.append((identity, metric, "FILTER_CHANGED_SOURCE_METRIC"))
            if before["nt_account_id"] in record["expected"]["running_account_ids"]:
                source = record["expected"]["metrics"][metric]["known_accounts"].get(before["nt_account_id"])
                problems = _source_errors(after["metrics"][metric], source)
                if problems:
                    errors.append((identity, metric, problems))
    if scenario == "small_pages":
        assert record["page_count"] > 1, "SAMPLE_GAP: no actual pagination"
        snapshot = record["pages"][0]["meta"]["snapshot_id"]
        assert snapshot
        for page in record["pages"][1:]:
            assert page["request_params"]["snapshot_id"] == snapshot
    assert not errors, {"errors": errors, "evidence": record.get("evidence_report")}


@pytest.mark.parametrize("scenario", ACCOUNT_SCENARIOS)
def test_current_account_summary_eight_metrics_match_database(scenario: str, current_overview_runtime: dict[str, Any]) -> None:
    """Compare bound/unbound/missing-source local accounts, not only the happy path."""
    record = _collect(current_overview_runtime, "account", scenario)
    data = _success(record, record["response"])
    _context_matches_query(record["response"])
    account = record["selected_accounts"][0]
    assert str(data["context"].get("subject_id")) == str(account["account_id"])
    assert data.get("counts", {}).get("account_count") == 1
    errors = []
    for metric in METRIC_SOURCES:
        source = record["expected"]["metrics"][metric]["known_accounts"].get(account["nt_account_id"])
        actual = data["metrics"].get(metric)
        problems = _source_errors(actual, source)
        if problems:
            errors.append({"metric": metric, "source": source, "actual": actual, "errors": problems})
    assert not errors, {"account": account["account_id"], "errors": errors, "evidence": record.get("evidence_report")}


@pytest.mark.parametrize("scenario", SUBSET_SCENARIOS)
def test_current_selected_portfolio_matches_selected_database_accounts(scenario: str, current_overview_runtime: dict[str, Any]) -> None:
    """Assert selected membership plus all amounts, collecting every discrepancy."""
    record = _collect(current_overview_runtime, "subset", scenario)
    response = record["response"]
    data = _success(record, response)
    _context_matches_query(response)
    selected = {str(row["account_id"]) for row in record["selected_accounts"]}
    echoed = data["context"].get("selection", {}).get("account_ids")
    echoed_ids = echoed.split(",") if isinstance(echoed, str) else echoed
    assert isinstance(echoed_ids, list) and {str(value) for value in echoed_ids} == selected, ("SELECTION_NOT_PRESERVED", echoed, selected)
    expected = record["expected"]
    members = expected["running_account_ids"]
    errors, gaps = [], []
    if data.get("counts", {}).get("account_count") != len(members):
        errors.append({"field": "account_count", "expected": len(members), "actual": data.get("counts"), "members": members})
    for metric in METRIC_SOURCES:
        row = expected["metrics"][metric]
        actual = data["metrics"].get(metric)
        if not isinstance(actual, dict):
            errors.append({"metric": metric, "error": "METRIC_OBJECT_MISSING"})
            continue
        if not members:
            # Empty running selection must not silently fall back to all accounts.
            try:
                assert all(actual.get(key) is None or finite_decimal(actual[key]) == 0 for key in ("value", "known_value"))
            except (AssertionError, ValueError):
                errors.append({"metric": metric, "error": "EMPTY_SELECTION_HAS_NONZERO_VALUE", "actual": actual})
            continue
        if not row["known_accounts"]:
            problems = _source_errors(actual, None)
        elif metric in AMOUNT_SOURCES:
            value = actual.get("value") if actual.get("value") is not None else actual.get("known_value")
            problems = []
            if value is None and row["missing_accounts"] and actual.get("status") in {"partial", "unavailable"}:
                gaps.append(metric)
            else:
                times = {source["observed_at"] for source in row["known_accounts"].values()}
                comparable = {"value": row["known_subtotal"], "observed_at": next(iter(times)) if len(times) == 1 else actual.get("observed_at")}
                problems += _source_errors(actual, comparable)
        elif len(members) == 1:
            problems = _source_errors(actual, row["known_accounts"].get(members[0]))
        else:
            problems = []
            for field in ("value", "known_value"):
                try:
                    if actual.get(field) is not None:
                        finite_decimal(actual[field])
                except ValueError:
                    problems.append("NONFINITE_RATIO")
        if row["missing_accounts"] and (actual.get("status") not in {"partial", "unavailable"} or not actual.get("reason_code")):
            problems.append("MISSING_MEMBERS_CLAIMED_COMPLETE")
        if problems:
            errors.append({"metric": metric, "errors": problems, "expected": row, "actual": actual})
    if scenario == "order_invariant":
        reference = _success(record, record["reference"])
        assert record["reference"]["request_params"]["account_ids"] != response["request_params"]["account_ids"]
        if reference["counts"] != data["counts"] or any(
            _metric_signature(reference["metrics"].get(metric, {})) != _metric_signature(data["metrics"].get(metric, {})) for metric in METRIC_SOURCES
        ):
            errors.append({"error": "MEMBER_ORDER_CHANGED_RESULT"})
    assert not errors, {"bug": BUG_TITLE, "scenario": scenario, "errors": errors, "evidence": record.get("evidence_report")}
    if gaps:
        pytest.skip(f"SOURCE_SUBTOTAL_UNAVAILABLE: {gaps}; membership/coverage checked; evidence={record.get('evidence_report')}")


@pytest.mark.parametrize("metric", list(METRIC_SOURCES))
def test_current_metric_projection_preserves_full_query_value(metric: str, current_overview_runtime: dict[str, Any]) -> None:
    """Each of eight standalone metric requests must preserve full-query identity."""
    record = _collect(current_overview_runtime, "projection", metric)
    data, reference = _success(record, record["response"]), _success(record, record["reference"])
    _context_matches_query(record["response"])
    assert record["response"]["request_params"]["metrics"] == metric
    assert metric in data["metrics"] and metric in reference["metrics"], "REQUESTED_METRIC_MISSING"
    assert data["counts"] == reference["counts"], "PROJECTION_CHANGED_MEMBERSHIP"
    assert _metric_signature(data["metrics"][metric]) == _metric_signature(reference["metrics"][metric]), {
        "metric": metric, "projected": data["metrics"][metric], "full": reference["metrics"][metric],
        "evidence": record.get("evidence_report"),
    }


@pytest.mark.parametrize("scenario", WINDOW_SCENARIOS)
def test_current_source_lifetime_respects_requested_window_semantics(scenario: str, current_overview_runtime: dict[str, Any]) -> None:
    """Today, seven days and timezone-equivalent ranges keep explicit lifetime scope."""
    record = _collect(current_overview_runtime, "window", scenario)
    data, reference = _success(record, record["response"]), _success(record, record["reference"])
    for response in (record["response"], record["reference"]):
        _context_matches_query(response)
    assert data["counts"] == reference["counts"], "DATE_CHANGED_CURRENT_POPULATION"
    errors = []
    for metric in METRIC_SOURCES:
        if metric not in data["metrics"] or metric not in reference["metrics"]:
            errors.append((metric, "METRIC_MISSING"))
        elif _metric_signature(data["metrics"][metric]) != _metric_signature(reference["metrics"][metric]):
            errors.append((metric, "DATE_CHANGED_SAME_GENERATION_LIFETIME_VALUE"))
    assert not errors, {"errors": errors, "evidence": record.get("evidence_report")}
