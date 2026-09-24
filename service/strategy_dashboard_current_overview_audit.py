"""Reconcile the current page's complete account directory with persisted inputs.

The directory defines the authorized running population independently of the
summary. Persisted raw account samples, never summary outputs, define amounts.
This does not reconstruct upstream exchange events or invent portfolio ratios.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from api.strategy_dashboard_current_overview_api import CurrentOverviewAPI
from db.strategy_dashboard_current_overview_repository import CurrentOverviewRepository


AMOUNT_SOURCES = {
    "margin_equity": "account_equity_usdt",
    "unrealized_pnl": "account_unrealized_pnl_usdt",
    "realized_pnl_gross": "account_realized_pnl_usdt",
    "commission_expense": "account_total_commission_usdt",
    "funding_pnl": "account_total_funding_fee_usdt",
}
RATIO_SOURCES = {
    "cumulative_return": "account_net_value",
    "annualized_return": "account_annualized_return",
    "current_drawdown": "account_current_drawdown",
}
METRIC_SOURCES = AMOUNT_SOURCES | RATIO_SOURCES
METRIC_TITLES = {
    "margin_equity": "保证金", "unrealized_pnl": "未实现盈亏",
    "realized_pnl_gross": "已实现盈亏", "commission_expense": "总手续费",
    "funding_pnl": "累计资费", "cumulative_return": "总收益率",
    "annualized_return": "年化收益率", "current_drawdown": "实时回撤",
}


def finite_decimal(value: Any) -> Decimal:
    """Parse a nonempty finite source/result number; reject booleans and NaN/inf."""
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise ValueError("MISSING_OR_INVALID_NUMBER")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("INVALID_NUMBER") from exc
    if not number.is_finite():
        raise ValueError("NONFINITE_NUMBER")
    return number


def source_expectations(accounts: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any]:
    """Compute eight per-account values and five signed Decimal sums.

Input accounts must be the fully paginated authorized directory, not the
summary's selected members. Zero is a real value; absent/null samples stay
missing. Repeated identical account-level samples are counted once, conflicting
samples fail. Return member lists, known/missing lists and raw value evidence.
"""
    ids: set[str] = set()
    running: list[dict[str, Any]] = []
    for account in accounts:
        status = account.get("actual_status", account.get("runtime_status"))
        identity = account.get("nt_account_id")
        if not isinstance(identity, str) or not identity:
            if status == "running":
                raise ValueError("DIRECTORY_SOURCE_ID_MISSING")
            continue
        if identity in ids:
            raise ValueError(f"DIRECTORY_DUPLICATE_SOURCE_ACCOUNT: {identity}")
        ids.add(identity)
        if status == "running":
            if account.get("nt_enabled") is False or account.get("lifecycle_status") == "archived":
                raise ValueError(f"DIRECTORY_CONFLICTING_RUNNING_STATE: {identity}")
            running.append(account)
    raw = source.get("raw", {})
    samples = raw.get("samples")
    if not isinstance(samples, list):
        raise ValueError("SOURCE_SAMPLES_MISSING")
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sample in samples:
        indexed.setdefault((sample.get("account_id"), sample.get("metric")), []).append(sample)
    metrics: dict[str, Any] = {}
    with localcontext() as context:
        context.prec = 50
        for metric, source_metric in METRIC_SOURCES.items():
            known: dict[str, Any] = {}
            missing: list[str] = []
            for account in running:
                identity = account["nt_account_id"]
                rows = indexed.get((identity, source_metric), [])
                if not rows or all(row.get("value") is None for row in rows):
                    missing.append(identity)
                    continue
                signatures = {(finite_decimal(row.get("value")), row.get("observed_at")) for row in rows}
                if len(signatures) != 1:
                    raise ValueError(f"AMBIGUOUS_ACCOUNT_SOURCE: {identity}/{source_metric}")
                value, observed_at = next(iter(signatures))
                if not isinstance(observed_at, str) or not observed_at:
                    raise ValueError(f"SOURCE_OBSERVED_AT_MISSING: {identity}/{source_metric}")
                expected = value - 1 if metric == "cumulative_return" else value
                known[identity] = {"value": str(expected), "source_value": str(value),
                                   "source_metric": source_metric, "observed_at": observed_at,
                                   "duplicate_sample_count": len(rows)}
            subtotal = (str(sum((Decimal(row["value"]) for row in known.values()), Decimal(0)))
                        if known and metric in AMOUNT_SOURCES else None)
            metrics[metric] = {"known_accounts": known, "missing_accounts": sorted(missing),
                               "known_subtotal": subtotal,
                               "is_complete": bool(running) and not missing}
    return {"running_account_ids": sorted(account["nt_account_id"] for account in running),
            "excluded_account_ids": sorted(ids - {account["nt_account_id"] for account in running}),
            "metrics": metrics}


def _body(response: dict[str, Any], action: str) -> dict[str, Any]:
    body = response.get("body")
    if response.get("http_status") != 200 or not isinstance(body, dict) or body.get("success") is not True:
        raise RuntimeError(f"CURRENT_OVERVIEW_HTTP_FAILURE: {action}; HTTP {response.get('http_status')}")
    if body.get("meta", {}).get("mock") is not False:
        raise ValueError(f"NATURAL_SOURCE_NOT_CONFIRMED: {action}")
    if not isinstance(body.get("data"), dict) or not body.get("meta", {}).get("fetched_at"):
        raise ValueError(f"SOURCE_GENERATION_MISSING: {action}")
    return body


class CurrentOverviewAudit:
    """Collect fresh page requests and exact-generation DB inputs without writes."""

    def __init__(self, repository: CurrentOverviewRepository, api: CurrentOverviewAPI) -> None:
        """Keep independent source reader and API; no I/O until collect is called."""
        self.repository, self.api = repository, api

    def directory(self, *, limit: int = 100, runtime_status: str | None = None,
                  account_ids: list[str] | None = None,
                  exchange: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read every page of one directory selection, rejecting incomplete snapshots.

Optional selection parameters are passed unchanged to each page. Return rows
and response evidence; transport, pagination and generation errors propagate.
"""
        options = {"limit": limit, "runtime_status": runtime_status,
                   "account_ids": account_ids, "exchange": exchange}
        # Preserve the original no-argument adapter contract for default reads.
        options = {key: value for key, value in options.items()
                   if value is not None and not (key == "limit" and value == 100)}
        first = self.api.accounts_page(**options)
        body = _body(first, "accounts page 1")
        data, meta = body["data"], body["meta"]
        total, pages = data.get("total"), data.get("total_pages")
        if (data.get("total_status") != "exact" or type(total) is not int or total < 0
                or type(pages) is not int or not 0 <= pages <= 100):
            raise ValueError("DIRECTORY_PAGINATION_NOT_EXACT")
        if pages != (total + limit - 1) // limit:
            raise ValueError("DIRECTORY_PAGE_COUNT_MISMATCH")
        rows = list(data.get("items", []))
        responses = [first]
        for page in range(2, pages + 1):
            snapshot = meta.get("snapshot_id")
            if not snapshot:
                raise ValueError("DIRECTORY_SNAPSHOT_MISSING")
            response = self.api.accounts_page(page=page, snapshot_id=snapshot, **options)
            other = _body(response, f"accounts page {page}")
            if (other["meta"].get("snapshot_id") != snapshot
                    or other["meta"]["fetched_at"] != meta["fetched_at"]
                    or other["data"].get("total") != total):
                raise ValueError("DIRECTORY_SNAPSHOT_CHANGED")
            rows.extend(other["data"].get("items", []))
            responses.append(response)
        if len(rows) != total or responses[-1]["body"]["data"].get("has_more") is not False:
            raise ValueError("DIRECTORY_INCOMPLETE")
        return rows, responses

    def collect(self, *, from_at: str, to_at: str) -> dict[str, Any]:
        """Return new API responses, matched source generation and DB expectations.

Retry collection at most three times only if requests cross a collection tick;
never retry business discrepancies to obtain green results. Missing generations,
HTTP failures and ambiguous input fail explicitly, never reuse a cached report.
"""
        attempts = []
        for _ in range(3):
            accounts, directory_responses = self.directory()
            summary = self.api.summary(from_at=from_at, to_at=to_at)
            body = _body(summary, "summary")
            directory_generation = directory_responses[0]["body"]["meta"]["fetched_at"]
            generation = body["meta"]["fetched_at"]
            attempts.append({"directory_fetched_at": directory_generation, "summary_fetched_at": generation})
            if directory_generation != generation:
                continue
            stored = self.repository.source_at(generation)
            if stored["source"].get("raw", {}).get("fetched_at") != generation:
                raise ValueError("DATABASE_SOURCE_GENERATION_MISMATCH")
            expected = source_expectations(accounts, stored["source"])
            # Keep only audit-relevant fields, excluding directory personal data.
            compact_accounts = [{key: row.get(key) for key in (
                "account_id", "local_account_id", "nt_account_id", "nt_enabled", "actual_status",
                "runtime_status", "lifecycle_status", "metrics",
                "exchange", "nt_strategy_ids",
            )} | {"has_current_assignment": bool(row.get("current_assignment"))} for row in accounts]
            compact_pages = [{"http_status": response["http_status"],
                              "request_params": response["request_params"],
                              "meta": response["body"]["meta"],
                              "total": response["body"]["data"]["total"]}
                             for response in directory_responses]
            return {"scope": "current_running_portfolio_natural_source", "read_only": True,
                    "membership_basis": "complete_authorized_account_directory_not_summary_counts",
                    "amount_basis": "same_generation_persisted_raw_samples",
                    "from": from_at, "to": to_at, "fetched_at": generation,
                    "batch_id": stored["batch_id"], "cutoff": stored["cutoff"],
                    "matched_batch_ids": stored.get("matched_batch_ids", [stored["batch_id"]]),
                    "collection_attempts": attempts, "directory_pages": compact_pages,
                    "accounts": compact_accounts,
                    "summary": {"http_status": summary["http_status"], "request_params": summary["request_params"],
                                "meta": body["meta"], "context": body["data"].get("context"),
                                "counts": body["data"].get("counts"), "metrics": body["data"].get("metrics")},
                    "expected": expected}
        raise RuntimeError("SOURCE_GENERATION_DRIFT: directory and summary crossed three collection ticks")
