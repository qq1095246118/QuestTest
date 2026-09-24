"""Bind raw account amounts and independently identified cycle/clearance facts."""

from __future__ import annotations

from typing import Any, Mapping


def bind_cycle_source(base: dict[str, Any], amounts: Mapping[str, Any]) -> dict[str, Any]:
    """Add latest/baseline snapshots and frozen-policy facts, without computed ratios.

    Facts exist only in the isolated read view. No policy, cycle, clearance or
    bill is published to a business table, and this does not perform settlement.
    Input keys are G, Gbase, D and an explicitly initialized Gclear=0.
    Nonzero/missing clearance requires another verified source binding and raises
    before a view is inserted; it is never silently ignored.
    """
    if amounts.get("Gclear") != "0":
        raise ValueError("UNBOUND_CLEARANCE_SOURCE: only explicit first-cycle Gclear=0 is supported")
    account = base["accounts"][0]["id"]
    fixture = base["profiles"][0]["Name"]
    actor = base["profiles"][0]["CreatedBy"]
    start, end = "2026-09-01T00:00:00Z", "2026-09-08T00:00:00Z"
    base["accounts"][0]["created_at"] = start
    base["profiles"][0].update(CurrentPolicyID=fixture + "_policy", CurrentCycleID=fixture + "_cycle", CreatedAt=start)
    base["snapshots"] = []

    def snapshot(suffix: str, stamp: str, gross: Any) -> dict[str, Any]:
        fields = {"margin_equity": "10000", "wallet_balance": "10000", "unrealized_pnl": "0",
                  "net_deposit": amounts.get("D"), "realized_pnl_gross": gross,
                  "commission_expense": amounts.get("C", "0"), "funding_pnl": amounts.get("F", "0")}
        raw = {**fields, "currency": "USDT", "snapshot_time": stamp, "precision_status": "exact",
               "performance_source": "exchange_account_performance_summaries", "performance_synced_until": stamp,
               "field_status": {key: {"status": "available" if value is not None else "unavailable",
                                       "observed_at": stamp,
                                       "reason_code": None if value is not None else "SOURCE_FIELD_UNAVAILABLE"}
                                for key, value in fields.items()}}
        return {"ID": fixture + suffix, "AccountID": account, "AssignmentID": None,
                "ObservedAt": stamp, "CreatedAt": stamp, "UpdatedAt": stamp, "SourceKey": fixture + suffix,
                "SourceWatermark": fixture, "DataStatus": "complete", "Version": 1, "Payload": raw}

    base["snapshots"] = [snapshot("_baseline", start, amounts.get("Gbase")), snapshot("_latest", end, amounts.get("G"))]
    base["policies"] = [{"ID": fixture + "_policy", "Enabled": amounts.get("enabled", True), "Version": 1,
        "AccountID": account, "AssignmentID": "", "CreatedBy": actor,
        "ShareRate": amounts.get("share_rate", "0.2"), "TriggerRate": amounts.get("threshold", "0.05"),
        "EffectiveFromCycle": 1, "CreatedAt": start, "UpdatedAt": start,
        "FormulaVersion": "account_realized_gross_settled_capital_v1"}]
    base["cycles"] = [{"ID": fixture + "_cycle", "AccountID": account, "AssignmentID": "", "PolicyID": fixture + "_policy",
        "BaselineSnapshotID": fixture + "_baseline", "Status": "accruing", "Number": 1, "Version": 1,
        "StartedAt": start, "CreatedAt": start, "UpdatedAt": start, "EndedAt": None, "NextCycleID": None, "BillID": None}]
    return base
