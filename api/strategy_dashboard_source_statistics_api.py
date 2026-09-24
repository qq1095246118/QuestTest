"""Read deployed statistics endpoints against the actual monitoring source."""

from __future__ import annotations

from typing import Any

from api.client import HTTPClient


class SourceStatisticsAPI:
    """Read natural account samples; this API does not support injected snapshots."""

    def __init__(self, client: HTTPClient) -> None:
        """Keep an authenticated client without performing any network call."""
        self.client = client

    def summary(self, account_id: int) -> dict[str, Any]:
        """Return the fresh source summary envelope; transport errors propagate."""
        response = self.client.request(
            "GET", "/quantitative-trading/analytics/summary",
            params={"subject_type": "account", "subject_id": str(account_id),
                    "metric_source": "prometheus", "scope": "lifetime", "metric_basis": "accrual",
                    "metrics": "sortino_ratio,volatility,win_rate,profit_loss_ratio,calmar_ratio"},
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
            retryable=False, allow_redirects=False,
        )
        return {"http_status": response.status_code, "body": response.json()}
