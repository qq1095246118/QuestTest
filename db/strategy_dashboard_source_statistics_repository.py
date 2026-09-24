"""Read source snapshots and local identities without modifying platform data."""

from __future__ import annotations

import json
from typing import Any

from db.client import DatabaseClient


class SourceStatisticsRepository:
    """Resolve the exact persisted monitoring generation used by a response."""

    def __init__(self, client: DatabaseClient) -> None:
        """Receive a read-only database client; connection errors propagate on use."""
        self.client = client

    def mapped_accounts(self) -> list[dict[str, Any]]:
        """Return active local accounts with a nonempty NT source mapping."""
        return self.client.fetch_all(
            "SELECT account_id,active_nt_account_id FROM quant_account_profiles "
            "WHERE status=%s AND active_nt_account_id IS NOT NULL AND active_nt_account_id<>%s "
            "ORDER BY account_id", ("active", ""),
        )

    def recent_sources(self) -> list[dict[str, Any]]:
        """Read at most ten recent source batches, excluding synthetic fixtures."""
        rows = self.client.fetch_all(
            "SELECT id,cutoff,JSON_EXTRACT(current_data,'$.account_source') AS source "
            "FROM quant_collection_batches WHERE namespace=%s AND status=%s "
            "ORDER BY created_at DESC LIMIT 10", ("quant-minute-test", "committed"),
        )
        output = []
        for row in rows:
            source = json.loads(row["source"]) if isinstance(row["source"], str) else row["source"]
            if source and source.get("raw", {}).get("fetched_at"):
                output.append({"batch_id": row["id"], "cutoff": str(row["cutoff"]), "source": source})
        return output

    def source_at(self, fetched_at: str) -> dict[str, Any]:
        """Find the response's exact monitoring timestamp or reject mixed generations."""
        for row in self.recent_sources():
            if row["source"]["raw"]["fetched_at"] == fetched_at:
                return row
        raise ValueError("SOURCE_GENERATION_UNAVAILABLE: response generation is not among recent batches")
