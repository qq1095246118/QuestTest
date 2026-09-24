"""Read existing financial result endpoints for one owned source fixture."""

from __future__ import annotations

from typing import Any

from api.client import HTTPClient


class FinancialFixtureAPI:
    """Read formula results without inventing ingestion or recalculation endpoints."""

    def __init__(self, client: HTTPClient) -> None:
        """Store an authenticated test client; construction makes no request."""
        self.client = client

    def summary(self, account_id: int, view_id: str, *, scope: str = "lifetime",
                from_at: str | None = None, to_at: str | None = None) -> dict[str, Any]:
        """Return HTTP status and raw summary JSON for this exact source view.

        Transport failures propagate. The caller must verify the echoed snapshot
        identity before using any metric as evidence.
        """
        params: dict[str, Any] = {
            "subject_type": "account", "subject_id": str(account_id), "snapshot_id": view_id,
            "scope": scope, "metric_basis": "accrual",
            "metrics": "margin_equity,wallet_balance,unrealized_pnl,realized_pnl_gross,net_deposit,"
                       "commission_expense,funding_pnl,cumulative_return,annualized_return,"
                       "current_drawdown,max_drawdown,sharpe_ratio,calmar_ratio,position_ratio",
        }
        if from_at is not None:
            params["from"] = from_at
        if to_at is not None:
            params["to"] = to_at
        return self._read("summary", params)

    def risk(self, account_id: int, view_id: str) -> dict[str, Any]:
        """Return raw risk components calculated from this exact input view."""
        return self._read("risk", {"subject_type": "account", "subject_id": str(account_id),
                                  "snapshot_id": view_id, "scope": "lifetime", "metric_basis": "accrual"})

    def _read(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.client.request(
            "GET", "/quantitative-trading/analytics/" + endpoint, params=params,
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
            retryable=False, allow_redirects=False,
        )
        return {"http_status": response.status_code, "body": response.json()}
