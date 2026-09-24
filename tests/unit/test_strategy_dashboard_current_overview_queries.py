"""Adversarial guards for live query assertions; never count as product passes."""

from copy import deepcopy
from typing import Any

import pytest

from service.strategy_dashboard_current_overview_audit import (
    METRIC_SOURCES, CurrentOverviewAudit, source_expectations,
)
from service.strategy_dashboard_current_overview_queries import CurrentOverviewQueries, OverviewSampleGap
from tests.cases.strategy_dashboard import test_source_current_overview_queries as acceptance


pytestmark = pytest.mark.unit
STAMP = "2026-09-24T03:21:00Z"
OBSERVED = "2026-09-24T03:20:51Z"
FROM = "2026-08-26T00:00:00+08:00"
TO = "2026-09-24T11:22:00+08:00"
ALPHA = dict(zip(METRIC_SOURCES, ["100", "5", "-40", "-4", "3", "-0.2", "-0.5", "0.2"]))
BETA = dict(zip(METRIC_SOURCES, ["200", "-10", "30", "-2", "-9", "0.1", "0.1", "0.05"]))
TOTAL = dict(zip(METRIC_SOURCES, ["300", "-5", "-10", "-6", "-6", "0.01", "0.02", "0.03"]))


def _account(identifier: str, name: str, *, stopped: bool = False, bound: bool = False) -> dict[str, Any]:
    return {"account_id": identifier, "nt_account_id": name, "nt_enabled": not stopped,
            "actual_status": "stopped" if stopped else "running", "lifecycle_status": "active",
            "exchange": "binance", "current_assignment": {"id": 1} if bound else None}


ACCOUNTS = [_account("1", "alpha", bound=True), _account("2", "beta"),
            _account("3", "missing"), _account("4", "stopped", stopped=True)]
SOURCE = {"raw": {"fetched_at": STAMP, "samples": [
    {"account_id": name, "metric": source_metric,
     "value": ("0.8" if name == "alpha" else "1.1") if metric == "cumulative_return" else values[metric],
     "observed_at": OBSERVED}
    for name, values in (("alpha", ALPHA), ("beta", BETA))
    for metric, source_metric in METRIC_SOURCES.items()
]}}


def _metrics(values: dict[str, str] | None, *, partial: bool = False) -> dict[str, Any]:
    return {metric: {"value": values[metric] if values is not None else None,
                     "status": "partial" if partial else "complete" if values is not None else "unavailable",
                     "reason_code": "SOURCE_MISSING" if partial or values is None else None,
                     "observed_at": OBSERVED if values is not None else None}
            for metric in METRIC_SOURCES}


def _response(values: dict[str, str] | None, members: list[str], *, account: bool = False) -> dict[str, Any]:
    params = {"subject_type": "account" if account else "portfolio", "population": "running",
              "metric_source": "prometheus", "from": FROM, "to": TO,
              "metrics": ",".join(METRIC_SOURCES)}
    context = {"subject_type": params["subject_type"], "population": "running", "metric_source": "prometheus",
               "scope": "source_lifetime", "window_applied": False,
               "requested_from": FROM, "requested_to": TO, "selection": {"account_ids": members}}
    if account:
        params["subject_id"] = context["subject_id"] = members[0]
    else:
        params["account_ids"] = ",".join(members)
    return {"http_status": 200, "success": True, "request_params": params,
            "meta": {"mock": False, "fetched_at": STAMP},
            "data": {"context": context, "counts": {"account_count": len(members)}, "metrics": _metrics(values)}}


def _record(scenario: str = "two_known", *, account: bool = False) -> dict[str, Any]:
    selections = {"two_known": [0, 1], "order_invariant": [0, 1], "bound_known": [0],
                  "only_missing": [2], "stopped_only": [3]}
    selected = [deepcopy(ACCOUNTS[index]) for index in selections[scenario]]
    values = TOTAL if len(selected) == 2 else ALPHA if scenario == "bound_known" else None
    response = _response(values, [row["account_id"] for row in selected], account=account)
    if scenario == "stopped_only":
        response["data"]["counts"]["account_count"] = 0
    record = {"scenario": scenario, "fetched_at": STAMP, "evidence_report": "offline-only",
              "selected_accounts": selected, "expected": source_expectations(selected, SOURCE), "response": response}
    if scenario == "order_invariant":
        record["reference"] = deepcopy(response)
        response["request_params"]["account_ids"] = "2,1"
    return record


class _StaticQueries:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    def summary_variant(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.record

    def directory_variant(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.record


def _runtime(record: dict[str, Any]) -> dict[str, Any]:
    return {"queries": _StaticQueries(record), "from_at": FROM, "to_at": TO}


@pytest.mark.parametrize("change", ["selection", "global_count", "omitted_amount"])
def test_subset_assertion_detects_wrong_members_or_single_account_fallback(change: str) -> None:
    record = _record()
    acceptance.test_current_selected_portfolio_matches_selected_database_accounts("two_known", _runtime(record))
    data = record["response"]["data"]
    if change == "selection":
        data["context"]["selection"]["account_ids"] = ["1", "3"]
    elif change == "global_count":
        data["counts"]["account_count"] = 4
    else:
        data["metrics"]["margin_equity"]["value"] = "100"
    with pytest.raises(AssertionError):
        acceptance.test_current_selected_portfolio_matches_selected_database_accounts("two_known", _runtime(record))


@pytest.mark.parametrize("scenario", ["bound_known", "two_known"])
def test_deployed_csv_selection_echo_is_accepted_but_wrong_ids_are_rejected(scenario: str) -> None:
    record = _record(scenario)
    selection = record["response"]["data"]["context"]["selection"]
    selection["account_ids"] = record["response"]["request_params"]["account_ids"]
    acceptance.test_current_selected_portfolio_matches_selected_database_accounts(scenario, _runtime(record))
    selection["account_ids"] = "3,4"
    with pytest.raises(AssertionError, match="SELECTION_NOT_PRESERVED"):
        acceptance.test_current_selected_portfolio_matches_selected_database_accounts(scenario, _runtime(record))


def test_partial_account_value_is_compared_with_db_not_excused_by_partial_status() -> None:
    record = _record("bound_known", account=True)
    record["response"]["data"]["metrics"] = _metrics(ALPHA, partial=True)
    acceptance.test_current_account_summary_eight_metrics_match_database("bound_known", _runtime(record))
    record["response"]["data"]["metrics"]["funding_pnl"]["value"] = "-9"
    with pytest.raises(AssertionError):
        acceptance.test_current_account_summary_eight_metrics_match_database("bound_known", _runtime(record))


def test_missing_source_is_not_replaced_by_another_accounts_value_or_zero() -> None:
    record = _record("only_missing")
    acceptance.test_current_selected_portfolio_matches_selected_database_accounts("only_missing", _runtime(record))
    record["response"]["data"]["metrics"]["margin_equity"]["value"] = "0"
    with pytest.raises(AssertionError):
        acceptance.test_current_selected_portfolio_matches_selected_database_accounts("only_missing", _runtime(record))


def test_stopped_only_selection_accepts_empty_but_rejects_global_amount_fallback() -> None:
    record = _record("stopped_only")
    acceptance.test_current_selected_portfolio_matches_selected_database_accounts("stopped_only", _runtime(record))
    record["response"]["data"]["metrics"]["margin_equity"]["value"] = "300"
    with pytest.raises(AssertionError, match="EMPTY_SELECTION_HAS_NONZERO_VALUE"):
        acceptance.test_current_selected_portfolio_matches_selected_database_accounts("stopped_only", _runtime(record))


def test_identically_wrong_ordered_responses_cannot_pass_without_independent_db_total() -> None:
    record = _record("order_invariant")
    acceptance.test_current_selected_portfolio_matches_selected_database_accounts("order_invariant", _runtime(record))
    for response in (record["response"], record["reference"]):
        response["data"]["metrics"]["margin_equity"]["value"] = "100"
    with pytest.raises(AssertionError):
        acceptance.test_current_selected_portfolio_matches_selected_database_accounts("order_invariant", _runtime(record))


@pytest.mark.parametrize("change", ["value", "generation", "missing_metric"])
def test_projection_assertion_detects_value_generation_or_requested_metric_changes(change: str) -> None:
    record = _record()
    record["scenario"] = "margin_equity"
    record["reference"] = deepcopy(record["response"])
    record["response"]["request_params"]["metrics"] = "margin_equity"
    acceptance.test_current_metric_projection_preserves_full_query_value("margin_equity", _runtime(record))
    if change == "value":
        record["response"]["data"]["metrics"]["margin_equity"]["value"] = "100"
    elif change == "generation":
        record["response"]["meta"]["fetched_at"] = "2026-09-24T03:22:00Z"
    else:
        record["response"]["data"]["metrics"].pop("margin_equity")
    with pytest.raises(AssertionError):
        acceptance.test_current_metric_projection_preserves_full_query_value("margin_equity", _runtime(record))


def test_window_assertion_checks_request_echo_and_same_generation_values() -> None:
    record = _record()
    record["scenario"] = "today"
    record["reference"] = deepcopy(record["response"])
    record["response"]["request_params"]["from"] = "2026-09-24T00:00:00+08:00"
    with pytest.raises(AssertionError):
        acceptance.test_current_source_lifetime_respects_requested_window_semantics("today", _runtime(record))
    record["response"]["data"]["context"]["requested_from"] = "2026-09-23T16:00:00Z"
    acceptance.test_current_source_lifetime_respects_requested_window_semantics("today", _runtime(record))
    record["response"]["data"]["metrics"]["funding_pnl"]["value"] = "3"
    with pytest.raises(AssertionError, match="DATE_CHANGED_SAME_GENERATION_LIFETIME_VALUE"):
        acceptance.test_current_source_lifetime_respects_requested_window_semantics("today", _runtime(record))


class _Repository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def source_at(self, generation: str) -> dict[str, Any]:
        self.calls.append(generation)
        return {"batch_id": "fixed-source", "source": deepcopy(SOURCE)}


class _API:
    """Only transport/page fixtures; no portfolio formula implementation."""

    def __init__(self, *, rollover: str = "none", failure: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.summary_calls = 0
        self.rollover, self.failure = rollover, failure

    def accounts_page(self, *, page: int = 1, snapshot_id: str | None = None,
                      limit: int = 100, **filters: Any) -> dict[str, Any]:
        params = {"page": page, "limit": limit, **filters}
        if snapshot_id is not None:
            params["snapshot_id"] = snapshot_id
        self.calls.append(("directory", deepcopy(params)))
        rows = deepcopy(ACCOUNTS[:3])
        return {"http_status": 200, "request_params": params,
                "body": {"success": True, "meta": {"mock": False, "fetched_at": STAMP, "snapshot_id": "frozen"},
                         "data": {"items": rows[(page - 1) * limit:page * limit], "total": 3,
                                  "total_status": "exact", "total_pages": (3 + limit - 1) // limit,
                                  "has_more": page * limit < 3}}}

    def summary(self, **kwargs: Any) -> dict[str, Any]:
        self.summary_calls += 1
        self.calls.append(("summary", deepcopy(kwargs)))
        if self.failure:
            return {"http_status": 500, "request_params": kwargs,
                    "body": {"success": False, "error": {"code": "SOURCE_UNAVAILABLE"}}}
        result = _response(ALPHA, ["1"])
        if self.rollover == "always" or (self.rollover == "once" and self.summary_calls == 1):
            result["meta"]["fetched_at"] = "2026-09-24T03:22:00Z"
        return {"http_status": 200, "request_params": kwargs,
                "body": {key: result[key] for key in ("success", "meta", "data")}}


def test_directory_small_pages_keep_filters_and_snapshot_on_every_page() -> None:
    api, repository = _API(), _Repository()
    filters = {"limit": 2, "runtime_status": "running", "account_ids": ["1", "nt:external"], "exchange": "binance"}
    rows, pages = CurrentOverviewAudit(repository, api).directory(**filters)
    assert [row["account_id"] for row in rows] == ["1", "2", "3"]
    assert len(pages) == 2
    assert api.calls == [("directory", {"page": 1, **filters}),
                         ("directory", {"page": 2, "snapshot_id": "frozen", **filters})]
    assert repository.calls == []


def test_directory_http_failure_is_not_a_successful_empty_population() -> None:
    class FailedDirectoryAPI(_API):
        def accounts_page(self, **kwargs: Any) -> dict[str, Any]:
            return {"http_status": 500, "body": {"success": False}, "request_params": kwargs}

    repository = _Repository()
    with pytest.raises(RuntimeError, match="CURRENT_OVERVIEW_HTTP_FAILURE"):
        CurrentOverviewAudit(repository, FailedDirectoryAPI()).directory()
    assert repository.calls == []


def test_source_generation_rollover_recollects_once_and_permanent_drift_is_bounded() -> None:
    api, repository = _API(rollover="once"), _Repository()
    record = CurrentOverviewQueries(CurrentOverviewAudit(repository, api)).summary_variant(
        "subset", "bound_known", from_at=FROM, to_at=TO)
    assert record["fetched_at"] == STAMP and api.summary_calls == 2
    always = _API(rollover="always")
    with pytest.raises(RuntimeError, match="crossed three ticks"):
        CurrentOverviewQueries(CurrentOverviewAudit(repository, always)).summary_variant(
            "subset", "bound_known", from_at=FROM, to_at=TO)
    assert always.summary_calls == 3


def test_http_failure_keeps_response_evidence_without_retrying_business_failures() -> None:
    api, saved = _API(failure=True), []
    queries = CurrentOverviewQueries(CurrentOverviewAudit(_Repository(), api), evidence_sink=saved.append)
    record = queries.summary_variant("subset", "bound_known", from_at=FROM, to_at=TO)
    assert saved == [record]
    assert record["response"]["http_status"] == 500
    assert record["response"]["error"] == {"code": "SOURCE_UNAVAILABLE"}
    assert api.summary_calls == 1
    with pytest.raises(AssertionError):
        acceptance.test_current_selected_portfolio_matches_selected_database_accounts("bound_known", _runtime(record))


def test_absent_natural_members_are_explicit_gap_not_substituted_happy_path() -> None:
    accounts = [ACCOUNTS[0]]
    expected = source_expectations(accounts, SOURCE)
    with pytest.raises(OverviewSampleGap, match="unbound_known"):
        CurrentOverviewQueries._members(accounts, expected, "unbound_known")


def test_directory_assertion_catches_omitted_members_and_equal_but_wrong_source_values() -> None:
    account = deepcopy(ACCOUNTS[0])
    account["metrics"] = _metrics(ALPHA)
    record = {"scenario": "running_filter", "expected_accounts": [account],
              "actual_accounts": [deepcopy(account)], "expected": source_expectations([account], SOURCE)}
    acceptance.test_current_directory_query_preserves_members_and_source_values("running_filter", _runtime(record))
    record["actual_accounts"] = []
    with pytest.raises(AssertionError):
        acceptance.test_current_directory_query_preserves_members_and_source_values("running_filter", _runtime(record))
    record["actual_accounts"] = [deepcopy(account)]
    for row in (record["expected_accounts"][0], record["actual_accounts"][0]):
        row["metrics"]["margin_equity"]["value"] = "200"
    with pytest.raises(AssertionError, match="DATABASE_SOURCE_VALUE_MISMATCH"):
        acceptance.test_current_directory_query_preserves_members_and_source_values("running_filter", _runtime(record))
