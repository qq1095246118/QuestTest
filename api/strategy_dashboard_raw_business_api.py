"""Query real formula evaluation from an exact owned raw-business read view."""

from __future__ import annotations

from typing import Any

from api.client import HTTPClient


class RawBusinessFixtureAPI:
    """Call deployed summary evaluation; never supply precomputed formula results."""

    def __init__(self, client: HTTPClient) -> None:
        """Keep an authenticated HTTP client without sending a request."""
        self.client = client

    def summary(self, account_id: int, view_id: str, *, metrics: str,
                scope: str = "lifetime", metric_basis: str = "accrual",
                from_at: str | None = None, to_at: str | None = None) -> dict[str, Any]:
        """Read the exact raw fixture view and return HTTP status plus JSON envelope."""
        params = {"subject_type": "account", "subject_id": str(account_id), "snapshot_id": view_id,
                  "scope": scope, "metric_basis": metric_basis, "metrics": metrics}
        if from_at is not None:
            params["from"] = from_at
        if to_at is not None:
            params["to"] = to_at
        response = self.client.request(
            "GET", "/quantitative-trading/analytics/summary", params=params,
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
            retryable=False, allow_redirects=False,
        )
        return {"http_status": response.status_code, "body": response.json()}
