"""Owned historical source boundary for interval and derived financial probes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository


class FinancialHistoryFixtureRepository(SourceFixtureRepository):
    """Extend one owned fixture with a real historical source snapshot, never a result."""

    def insert_sources(self, fixture_id: str, account_id: int, snapshot: dict[str, Any],
                       positions: list[dict[str, Any]]) -> None:
        """Insert current source via the shared helper plus one older raw snapshot.

        The baseline contains only account amounts. All rows use the same exact
        fixture prefix, and the historical ID is explicitly bound into the view.
        Errors propagate for the outer fixture's finally cleanup.
        """
        super().insert_sources(fixture_id, account_id, snapshot, positions)
        now = datetime.now()
        observed = now - timedelta(days=31)
        source_time = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        raw = dict(snapshot["Payload"])
        raw.update({"realized_pnl_gross": "500", "commission_expense": "50", "funding_pnl": "-10",
                    "margin_equity": "1000", "unrealized_pnl": "77", "wallet_balance": "923",
                    "snapshot_time": source_time, "performance_synced_until": source_time})
        raw["field_status"] = {key: dict(status, observed_at=source_time)
                               for key, status in raw.get("field_status", {}).items()}
        self.client.execute(
            "INSERT INTO quant_account_snapshots "
            "(id,created_at,updated_at,version,account_id,observed_at,source_key,source_watermark,data_status,payload) "
            "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s)",
            (fixture_id + "_baseline", now, now, account_id, observed,
             fixture_id + "_baseline", fixture_id, "complete", json.dumps(raw)),
        )

    def insert(self, view_id: str, actor_id: int, account_id: int,
               payload: dict[str, Any], context: dict[str, Any]) -> None:
        """Bind the historical raw snapshot by ID; never precompute a result."""
        bound = dict(payload)
        bound["account_snapshot_ids"] = [view_id + "_baseline", *payload["account_snapshot_ids"]]
        super().insert(view_id, actor_id, account_id, bound, context)

    def delete_sources(self, fixture_id: str, account_id: int) -> dict[str, int]:
        """Delete only the fixture's historical source, then shared current sources."""
        self._require_id(fixture_id)
        result = self.client.execute(
            "DELETE FROM quant_account_snapshots WHERE id=%s AND account_id=%s AND source_key=%s",
            (fixture_id + "_baseline", account_id, fixture_id + "_baseline"),
        )
        totals = super().delete_sources(fixture_id, account_id)
        return {**totals, "baseline_snapshots": result.rowcount}

    def assert_clean(self, fixture_id: str, account_id: int) -> None:
        """Verify the additional baseline is gone as well as shared owned rows."""
        super().assert_clean(fixture_id, account_id)
        result = self.client.fetch_one(
            "SELECT COUNT(*) AS n FROM quant_account_snapshots WHERE id=%s AND account_id=%s",
            (fixture_id + "_baseline", account_id),
        )
        if not result or result["n"] != 0:
            raise RuntimeError("Owned historical fixture cleanup verification failed")
