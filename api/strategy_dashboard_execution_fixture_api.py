"""Read execution outputs from an exact owned raw-event view."""

from __future__ import annotations

from typing import Any

from api.client import HTTPClient


class ExecutionFixtureAPI:
    """Query the deployed risk endpoint without injecting execution outputs."""

    def __init__(self, client: HTTPClient) -> None:
        """Retain the authenticated client; construction performs no I/O."""
        self.client = client

    def risk(self, account_id: int, view_id: str, *,
             from_at: str | None = None, to_at: str | None = None) -> dict[str, Any]:
        """Read one account's frozen execution data; propagate transport errors."""
        params = {"subject_type": "account", "subject_id": str(account_id),
                  "snapshot_id": view_id, "scope": "lifetime", "metric_basis": "accrual"}
        if from_at is not None:
            params["from"] = from_at
        if to_at is not None:
            params["to"] = to_at
        response = self.client.request(
            "GET", "/quantitative-trading/analytics/risk", params=params,
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
            retryable=False, allow_redirects=False,
        )
        return {"http_status": response.status_code, "body": response.json()}
