"""Offline guards for page-scope requests and exact-generation database reads."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from api.strategy_dashboard_current_overview_api import CurrentOverviewAPI
from db.strategy_dashboard_current_overview_repository import CurrentOverviewRepository


pytestmark = pytest.mark.unit
_AT = "2026-09-24T03:21:00.024939179Z"
_LATER = "2026-09-24T03:22:00.025Z"
_METRICS = {
    "margin_equity", "unrealized_pnl", "realized_pnl_gross", "cumulative_return",
    "annualized_return", "current_drawdown", "commission_expense", "funding_pnl",
}


class _Response:
    """Hold a fixed response without any HTTP transport."""

    status_code = 200

    def json(self) -> dict[str, Any]:
        """Return a detached harmless envelope for the tested API adapter."""
        return {"success": True, "data": {"items": []}, "meta": {"snapshot_id": "page-snapshot"}}


class _HTTP:
    """Capture adapter calls instead of creating a session or opening a socket."""

    def __init__(self) -> None:
        """Create empty request evidence without network access."""
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> _Response:
        """Capture request metadata and return the fixed response."""
        self.calls.append({"method": method, "path": path, **deepcopy(kwargs)})
        return _Response()


class _DB:
    """Fixed read-only database responses that record both read stages."""

    def __init__(self, candidates: list[dict[str, Any]], selected: dict[str, Any] | None,
                 *, read_only: bool = True) -> None:
        """Store detached responses and the client read-only flag without I/O."""
        self._read_only = read_only
        self.candidates, self.selected = deepcopy(candidates), deepcopy(selected)
        self.calls: list[tuple[str, str, Any]] = []

    def fetch_all(self, query: str, parameters: Any) -> list[dict[str, Any]]:
        """Record the lightweight discovery read and return candidate metadata."""
        self.calls.append(("all", query, parameters))
        return deepcopy(self.candidates)

    def fetch_one(self, query: str, parameters: Any) -> dict[str, Any] | None:
        """Record the exact-ID read and return its fixed persisted source row."""
        self.calls.append(("one", query, parameters))
        return deepcopy(self.selected)


def _candidate(identifier: str, fetched_at: str = _AT) -> dict[str, Any]:
    return {"id": identifier, "cutoff": "2026-09-24 11:22:00", "fetched_at": fetched_at}


def _source_row(identifier: str, fetched_at: str = _AT) -> dict[str, Any]:
    return {"id": identifier, "cutoff": "2026-09-24 11:22:00",
            "source": json.dumps({"raw": {"fetched_at": fetched_at, "samples": []}})}


def test_directory_and_summary_keep_distinct_page_scopes_and_only_issue_gets() -> None:
    """Prevent silently changing the page's running-summary scope to all/lifetime."""
    client = _HTTP()
    api = CurrentOverviewAPI(client)
    directory = api.accounts_page()
    api.accounts_page(page=2, snapshot_id="page-snapshot")
    summary = api.summary(from_at="2026-08-26T00:00:00+08:00", to_at=_AT)
    first, second, current = client.calls
    assert first["path"] == "/admin/quant-accounts"
    assert first["params"] | {"metrics": None} == {
        "population": "all", "scope": "lifetime", "metric_basis": "accrual",
        "metrics": None, "pagination": "page", "page": 1, "limit": 100,
    }
    assert second["params"] == first["params"] | {"page": 2, "snapshot_id": "page-snapshot"}
    assert current["path"] == "/quantitative-trading/analytics/summary"
    assert current["params"] | {"metrics": None} == {
        "subject_type": "portfolio", "scope": "cycle", "metric_basis": "display",
        "population": "running", "metric_source": "prometheus",
        "from": "2026-08-26T00:00:00+08:00", "to": _AT, "metrics": None,
    }
    for call in client.calls:
        metrics = call["params"]["metrics"].split(",")
        assert len(metrics) == 8 and set(metrics) == _METRICS
        assert call["method"] == "GET"
        assert call["headers"] == {"X-Quant-Contract": "2", "Cache-Control": "no-cache"}
        assert call["allow_redirects"] is False and call["retryable"] is False
    assert "snapshot_id" not in current["params"]
    for result, request in ((directory, first), (summary, current)):
        assert set(result) == {"http_status", "body", "request_params"}
        assert result["http_status"] == 200
        assert result["body"] == _Response().json()
        assert result["request_params"] == request["params"]


@pytest.mark.parametrize("page", [0, -1, True, 1.5])
def test_invalid_directory_page_fails_before_http(page: Any) -> None:
    """Reject malformed page indexes rather than repeatedly fetching page one."""
    client = _HTTP()
    with pytest.raises(ValueError, match="positive integer"):
        CurrentOverviewAPI(client).accounts_page(page=page)
    assert client.calls == []


@pytest.mark.parametrize("limit", [0, -1, 101, True, 2.5, "2"])
def test_invalid_directory_limit_fails_before_http(limit: Any) -> None:
    """Invalid paging sizes must not silently become a full-directory request."""
    client = _HTTP()
    with pytest.raises(ValueError, match="integer from 1 to 100"):
        CurrentOverviewAPI(client).accounts_page(limit=limit)
    assert client.calls == []


def test_small_directory_pages_preserve_filters_source_ids_and_snapshot() -> None:
    """Exercise exact small-page and NT-only selection parameters without coercion."""
    client = _HTTP()
    api = CurrentOverviewAPI(client)
    account_ids = ["2019", "nt:bn_test_leeman", "2019"]
    result = api.accounts_page(page=2, limit=2, snapshot_id="stable-page",
                               runtime_status="running", account_ids=account_ids,
                               exchange="binance")
    request = client.calls[0]
    assert request["path"] == "/admin/quant-accounts"
    assert request["params"] == {
        "population": "all", "scope": "lifetime", "metric_basis": "accrual",
        "metrics": ",".join([
            "margin_equity", "unrealized_pnl", "realized_pnl_gross", "cumulative_return",
            "annualized_return", "current_drawdown", "commission_expense", "funding_pnl",
        ]),
        "pagination": "page", "page": 2, "limit": 2, "snapshot_id": "stable-page",
        "runtime_status": "running", "account_ids": "2019,nt:bn_test_leeman,2019",
        "exchange": "binance",
    }
    account_ids.append("9999")
    next_result = api.accounts_page(limit=1)
    assert result["request_params"] == request["params"]
    assert "account_ids" not in next_result["request_params"]
    assert "runtime_status" not in next_result["request_params"]
    assert "exchange" not in next_result["request_params"]


def test_portfolio_selection_and_metric_projection_preserve_order_and_duplicates() -> None:
    """The API wrapper must send the caller's selection, not implement aggregation."""
    client = _HTTP()
    api = CurrentOverviewAPI(client)
    account_ids = ["2019", "2011", "2019"]
    metrics = ["funding_pnl", "margin_equity", "funding_pnl"]
    result = api.summary(from_at=_AT, to_at=_LATER, account_ids=account_ids, metrics=metrics)
    params = client.calls[0]["params"]
    assert params == {
        "subject_type": "portfolio", "scope": "cycle", "metric_basis": "display",
        "population": "running", "metric_source": "prometheus", "from": _AT,
        "to": _LATER, "metrics": "funding_pnl,margin_equity,funding_pnl",
        "account_ids": "2019,2011,2019",
    }
    account_ids.clear()
    metrics.reverse()
    api.summary(from_at=_AT, to_at=_LATER)
    assert result["request_params"] == params
    assert "account_ids" not in client.calls[1]["params"]
    assert set(client.calls[1]["params"]["metrics"].split(",")) == _METRICS


def test_explicit_empty_selection_is_not_silently_replaced_by_defaults() -> None:
    """Preserve explicit empty CSV values for server-side contract validation."""
    client = _HTTP()
    api = CurrentOverviewAPI(client)
    directory = api.accounts_page(account_ids=[])
    summary = api.summary(from_at=_AT, to_at=_LATER, account_ids=[], metrics=[])
    assert directory["request_params"]["account_ids"] == ""
    assert summary["request_params"]["account_ids"] == ""
    assert summary["request_params"]["metrics"] == ""


def test_local_account_summary_keeps_numeric_identity_and_current_source_scope() -> None:
    """Account analytics uses a local subject ID rather than portfolio membership."""
    client = _HTTP()
    api = CurrentOverviewAPI(client)
    result = api.account_summary("2011", from_at=_AT, to_at=_LATER,
                                 metrics=["funding_pnl", "unrealized_pnl"])
    default = api.account_summary("2019", from_at=_AT, to_at=_LATER)
    assert result["request_params"] == {
        "subject_type": "account", "subject_id": "2011", "scope": "cycle",
        "metric_basis": "display", "population": "running", "metric_source": "prometheus",
        "from": _AT, "to": _LATER, "metrics": "funding_pnl,unrealized_pnl",
    }
    assert default["request_params"]["subject_id"] == "2019"
    assert set(default["request_params"]["metrics"].split(",")) == _METRICS
    for request in client.calls:
        assert request["method"] == "GET"
        assert request["path"] == "/quantitative-trading/analytics/summary"
        assert request["retryable"] is False and request["allow_redirects"] is False
        assert request["headers"] == {"X-Quant-Contract": "2", "Cache-Control": "no-cache"}
        assert "account_ids" not in request["params"]


def test_recorded_request_parameters_are_detached_from_client_mutations() -> None:
    """Evidence must retain sent parameters even when a client mutates its input."""
    class MutatingHTTP(_HTTP):
        def request(self, method: str, path: str, **kwargs: Any) -> _Response:
            response = super().request(method, path, **kwargs)
            kwargs["params"]["metrics"] = "client-mutated"
            kwargs["params"]["extra"] = "client-added"
            return response

    client = MutatingHTTP()
    api = CurrentOverviewAPI(client)
    responses = [api.accounts_page(), api.summary(from_at=_AT, to_at=_LATER),
                 api.account_summary("2011", from_at=_AT, to_at=_LATER)]
    for result, request in zip(responses, client.calls):
        assert result["request_params"] == request["params"]
        assert "extra" not in result["request_params"]
        assert result["request_params"]["metrics"] != "client-mutated"


def test_writable_database_is_rejected_before_query() -> None:
    """Keep this natural-source audit read-only even with a misconfigured caller."""
    client = _DB([], None, read_only=False)
    with pytest.raises(PermissionError, match="read-only"):
        CurrentOverviewRepository(client)
    assert client.calls == []


def test_generation_lookup_uses_metadata_then_exact_id_and_not_latest_values() -> None:
    """A newer unrelated batch must not replace the response's requested generation."""
    client = _DB([_candidate("latest-unrelated", _LATER), _candidate("matching")], _source_row("matching"))
    result = CurrentOverviewRepository(client, namespace="selected-namespace").source_at(_AT)
    assert result == {
        "batch_id": "matching", "cutoff": "2026-09-24 11:22:00",
        "source": {"raw": {"fetched_at": _AT, "samples": []}}, "matched_batch_ids": ["matching"],
    }
    assert len(client.calls) == 2
    first_kind, metadata_sql, metadata_args = client.calls[0]
    assert first_kind == "all" and metadata_args == ("selected-namespace", "committed")
    assert "LIMIT 30" in metadata_sql
    assert "ORDER BY created_at DESC,id DESC" in metadata_sql
    assert "JSON_UNQUOTE(JSON_EXTRACT(current_data,'$.account_source.raw.fetched_at'))" in metadata_sql
    assert "JSON_EXTRACT(current_data,'$.account_source')" not in metadata_sql
    next_kind, exact_sql, exact_args = client.calls[1]
    assert next_kind == "one" and exact_args == ("matching", "selected-namespace", "committed")
    assert "WHERE id=%s AND namespace=%s AND status=%s" in exact_sql
    assert "JSON_EXTRACT(current_data,'$.account_source')" in exact_sql


def test_duplicate_generation_chooses_newest_match_and_keeps_all_matching_ids() -> None:
    """A repeated source fetch records all matching batches without duplicate reads."""
    client = _DB([_candidate("newest"), _candidate("other", _LATER), _candidate("older")],
                 _source_row("newest"))
    result = CurrentOverviewRepository(client).source_at(_AT)
    assert result["batch_id"] == "newest"
    assert result["matched_batch_ids"] == ["newest", "older"]
    assert len(client.calls) == 2
    assert client.calls[1][2] == ("newest", "quant-minute-test", "committed")


@pytest.mark.parametrize("candidates", [[], [_candidate("newer", _LATER)]])
def test_missing_generation_never_reads_latest_source(candidates: list[dict[str, Any]]) -> None:
    """No matching generation is an explicit gap, not an unrelated-value fallback."""
    client = _DB(candidates, _source_row("newer", _LATER))
    with pytest.raises(ValueError, match="SOURCE_GENERATION_UNAVAILABLE"):
        CurrentOverviewRepository(client).source_at(_AT)
    assert [kind for kind, _, _ in client.calls] == ["all"]


def test_empty_response_generation_fails_before_database_query() -> None:
    """Avoid treating a missing response timestamp as permission to read latest."""
    client = _DB([_candidate("newest")], _source_row("newest"))
    with pytest.raises(ValueError, match="SOURCE_GENERATION_UNAVAILABLE"):
        CurrentOverviewRepository(client).source_at("")
    assert client.calls == []


@pytest.mark.parametrize("row", [None, _source_row("matching", _LATER)])
def test_disappeared_or_changed_selected_batch_is_not_used(row: dict[str, Any] | None) -> None:
    """Check the source still belongs to the requested generation after discovery."""
    client = _DB([_candidate("matching")], row)
    with pytest.raises(ValueError, match="SOURCE_GENERATION_UNAVAILABLE"):
        CurrentOverviewRepository(client).source_at(_AT)
    assert len(client.calls) == 2
