"""Resolve a current-overview response to its bounded, persisted source generation."""

from __future__ import annotations

import json
from typing import Any

from db.client import DatabaseClient


class CurrentOverviewRepository:
    """Read one exact source batch without scanning or exporting full batch JSON."""

    def __init__(self, client: DatabaseClient, namespace: str = "quant-minute-test") -> None:
        """Receive a read-only client and batch namespace; perform no database I/O.

        Raise PermissionError for writable clients and ValueError for an empty
        namespace. Neither connection settings nor credentials are returned.
        """
        if not getattr(client, "_read_only", False):
            raise PermissionError("Current overview reconciliation requires a read-only database client")
        if not namespace.strip():
            raise ValueError("Source batch namespace must not be empty")
        self.client = client
        self.namespace = namespace

    def source_at(self, fetched_at: str) -> dict[str, Any]:
        """Return the newest committed batch matching the exact response timestamp.

        Inspect only the latest 30 batches' generation metadata, then read only
        the selected account_source. Return batch_id, cutoff, source and all
        matched_batch_ids in newest-first order. A missing generation raises
        ValueError with SOURCE_GENERATION_UNAVAILABLE; it never falls back to
        an unrelated latest batch. Query/JSON errors propagate unchanged.
        """
        if not fetched_at:
            raise ValueError("SOURCE_GENERATION_UNAVAILABLE: response has no fetched_at")
        candidates = self.client.fetch_all(
            "SELECT id,cutoff,"
            "JSON_UNQUOTE(JSON_EXTRACT(current_data,'$.account_source.raw.fetched_at')) AS fetched_at "
            "FROM quant_collection_batches WHERE namespace=%s AND status=%s "
            "ORDER BY created_at DESC,id DESC LIMIT 30",
            (self.namespace, "committed"),
        )
        matched = [row for row in candidates if row.get("fetched_at") == fetched_at]
        if not matched:
            raise ValueError(
                "SOURCE_GENERATION_UNAVAILABLE: response generation is not among the latest 30 committed batches"
            )
        selected = matched[0]
        row = self.client.fetch_one(
            "SELECT id,cutoff,JSON_EXTRACT(current_data,'$.account_source') AS source "
            "FROM quant_collection_batches WHERE id=%s AND namespace=%s AND status=%s",
            (selected["id"], self.namespace, "committed"),
        )
        if row is None:
            raise ValueError("SOURCE_GENERATION_UNAVAILABLE: selected committed batch no longer exists")
        source = json.loads(row["source"]) if isinstance(row["source"], (str, bytes, bytearray)) else row["source"]
        if not isinstance(source, dict) or not isinstance(source.get("raw"), dict):
            raise ValueError("SOURCE_GENERATION_INVALID: selected batch has no account source object")
        if source["raw"].get("fetched_at") != fetched_at:
            raise ValueError("SOURCE_GENERATION_UNAVAILABLE: selected source changed during the read")
        return {
            "batch_id": row["id"],
            "cutoff": str(row["cutoff"]),
            "source": source,
            "matched_batch_ids": [item["id"] for item in matched],
        }
