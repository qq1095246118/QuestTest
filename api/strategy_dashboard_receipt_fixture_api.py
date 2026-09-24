"""Read owned bill-detail receipt aggregates without financial mutations."""

from __future__ import annotations

from typing import Any

from api.client import HTTPClient


class ReceiptFixtureAPI:
    """Query the deployed detail renderer for an owned raw receipt source view."""

    def __init__(self, client: HTTPClient) -> None:
        """Keep the authenticated client; construction sends no requests."""
        self.client = client

    def detail(self, bill_id: str, view_id: str, *, receipt_limit: int = 1,
               cursor: str | None = None) -> dict[str, Any]:
        """Read one frozen detail page; propagate HTTP transport exceptions."""
        params: dict[str, Any] = {"snapshot_id": view_id, "receipt_limit": receipt_limit, "notice_limit": 1}
        if cursor is not None:
            params["receipt_cursor"] = cursor
        response = self.client.request(
            "GET", "/quantitative-trading/profit-sharing/bills/" + bill_id,
            params=params, headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
            retryable=False, allow_redirects=False,
        )
        return {"http_status": response.status_code, "body": response.json()}
