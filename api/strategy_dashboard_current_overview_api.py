"""Read the current-overview directory and summary using the deployed page scope."""

from __future__ import annotations

from typing import Any

from api.client import HTTPClient


CURRENT_OVERVIEW_METRICS = (
    "margin_equity,unrealized_pnl,realized_pnl_gross,cumulative_return,"
    "annualized_return,current_drawdown,commission_expense,funding_pnl"
)


class CurrentOverviewAPI:
    """Expose only GET requests for current directory and analytics sources."""

    def __init__(self, client: HTTPClient) -> None:
        """Receive an authenticated client; perform no I/O and return no value."""
        self.client = client

    def accounts_page(self, *, page: int = 1, snapshot_id: str | None = None,
                      limit: int = 100, runtime_status: str | None = None,
                      account_ids: list[str] | None = None,
                      exchange: str | None = None) -> dict[str, Any]:
        """Read one directory page, optionally pinned to its returned snapshot.

        Return the status, parsed response and non-sensitive request parameters.
        Page and limit must be integers; invalid bounds raise ValueError before
        HTTP. Optional status, exchange and IDs are forwarded without business
        filtering. IDs may be local decimal strings or directory ``nt:`` source
        identities; supplied order and duplicates are preserved. The default is
        still the full directory used by the frontend. Transport/JSON errors
        propagate unchanged.
        """
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("Directory page must be a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Directory limit must be an integer from 1 to 100")
        params: dict[str, Any] = {
            "population": "all",
            "scope": "lifetime",
            "metric_basis": "accrual",
            "metrics": CURRENT_OVERVIEW_METRICS,
            "pagination": "page",
            "page": page,
            "limit": limit,
        }
        if snapshot_id is not None:
            params["snapshot_id"] = snapshot_id
        if runtime_status is not None:
            params["runtime_status"] = runtime_status
        if account_ids is not None:
            params["account_ids"] = ",".join(account_ids)
        if exchange is not None:
            params["exchange"] = exchange
        return self._get("/admin/quant-accounts", params)

    def summary(self, *, from_at: str, to_at: str,
                account_ids: list[str] | None = None,
                metrics: list[str] | None = None) -> dict[str, Any]:
        """Read a running portfolio using ISO boundaries and optional selections.

        Omitted metrics select all eight current cards. Explicit metric/member
        lists preserve their order and duplicates as CSV rather than silently
        filtering or deduplicating the request. Return status, parsed body and
        detached request parameters without credentials. Transport/JSON errors
        propagate; values and window semantics are validated by the caller.
        """
        params = self._summary_params(from_at=from_at, to_at=to_at, metrics=metrics)
        params["subject_type"] = "portfolio"
        if account_ids is not None:
            params["account_ids"] = ",".join(account_ids)
        return self._get("/quantitative-trading/analytics/summary", params)

    def account_summary(self, account_id: str, *, from_at: str, to_at: str,
                        metrics: list[str] | None = None) -> dict[str, Any]:
        """Read current analytics for a local numeric-string account identity.

        The caller supplies the local account ID; directory-only ``nt:`` IDs
        are not mapped to or claimed to support this endpoint. Optional metrics
        preserve supplied order. Return HTTP status, parsed JSON and detached
        parameters; transport/JSON errors propagate without retry or fallback.
        """
        params = self._summary_params(from_at=from_at, to_at=to_at, metrics=metrics)
        params.update({"subject_type": "account", "subject_id": account_id})
        return self._get("/quantitative-trading/analytics/summary", params)

    @staticmethod
    def _summary_params(*, from_at: str, to_at: str,
                        metrics: list[str] | None) -> dict[str, Any]:
        """Return a new current-source parameter dictionary for one request."""
        return {
            "scope": "cycle",
            "metric_basis": "display",
            "population": "running",
            "metric_source": "prometheus",
            "from": from_at,
            "to": to_at,
            "metrics": CURRENT_OVERVIEW_METRICS if metrics is None else ",".join(metrics),
        }

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Issue one uncached GET without retries, redirects or logged headers."""
        request_params = dict(params)
        response = self.client.request(
            "GET", path, params=dict(request_params),
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
            retryable=False, allow_redirects=False,
        )
        return {"http_status": response.status_code, "body": response.json(), "request_params": request_params}
