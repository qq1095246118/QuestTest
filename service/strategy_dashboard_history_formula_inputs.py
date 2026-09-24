"""Bind fixed financial scenarios to raw historical account snapshots."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any


HISTORY_METRIC_NAMES = {
    "annualized_return": "annualized_return", "annualized_volatility": "volatility",
    "sharpe": "sharpe_ratio", "calmar": "calmar_ratio",
    "drawdown": "current_drawdown,max_drawdown",
    "cumulative_return": "cumulative_return",
}


def history_input_plan(inputs: dict[str, Any]) -> dict[str, Any]:
    """Create explicit dates without changing the scenario's numerical inputs.

Undated paths with a days span are placed in chronological order across that
exact span; daily carry-forward boundaries are then made explicit as fixture
facts. Dated paths preserve original dates and order, including deliberate
out-of-order scenarios. This function never consumes expected outputs.
"""
    anchor = datetime(2026, 9, 1, tzinfo=timezone.utc)
    if "series" in inputs:
        points = [{"at": datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")), "equity": row["equity"]}
                  for row in inputs["series"]]
        start = min(row["at"] for row in points)
        end = max(row["at"] for row in points)
    else:
        path = inputs.get("equity_path")
        days = Decimal(str(inputs.get("days", max(len(path or []) - 1, 1))))
        end = anchor
        start = end - timedelta(seconds=float(days * 86400))
        if path is None:
            principal = None if inputs.get("D", "100") is None else Decimal(str(inputs.get("D", "100")))
            profit = None if inputs.get("G", "0") is None else Decimal(str(inputs.get("G", "0")))
            count = max(1, int(days))
            dates = [start + timedelta(days=i) for i in range(count + 1)]
            dates = [point for point in dates if point <= end]
            if dates[-1] != end:
                dates.append(end)
            # An independent E=100 fixture fact keeps the account observable when
            # G or D is deliberately missing. It never fills the missing G/D.
            points = [{"at": stamp, "equity": "100" if principal is None or profit is None else (
                       str(principal + profit * Decimal(str((stamp - start).total_seconds())) / (days * 86400))
                       if days > 0 else str(principal + profit))} for stamp in dates]
        elif len(path) < 2 or "days" not in inputs:
            points = [{"at": start + timedelta(days=i), "equity": equity} for i, equity in enumerate(path)]
        else:
            duration = (end - start).total_seconds()
            events = [{"at": start + timedelta(seconds=duration * i / (len(path) - 1)), "equity": equity}
                      for i, equity in enumerate(path)]
            dates = sorted({start + timedelta(days=i) for i in range(int(days) + 1)} | {row["at"] for row in events} | {end})
            points = [{"at": stamp, "equity": max((row for row in events if row["at"] <= stamp), key=lambda row: row["at"])["equity"]}
                      for stamp in dates]
    return {"from": start.isoformat(), "to": end.isoformat(), "points": points, "inputs": inputs}


def bind_history_payload(base: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Build only raw E/W/U/G/D/C/F account amounts for each historical boundary."""
    inputs = plan["inputs"]
    account_id = base["accounts"][0]["id"]
    fixture_id = base["profiles"][0]["Name"]
    base["accounts"][0]["created_at"] = plan["from"]
    base["profiles"][0]["CreatedAt"] = plan["from"]
    start = datetime.fromisoformat(plan["from"])
    end = datetime.fromisoformat(plan["to"])
    duration = Decimal(str((end - start).total_seconds()))
    snapshots = []
    for index, point in enumerate(plan["points"]):
        stamp = point["at"].isoformat()
        fraction = Decimal(str((point["at"] - start).total_seconds())) / duration if duration else Decimal(1)
        principal = None if inputs.get("D", "100") is None else Decimal(str(inputs.get("D", "100")))
        profit = None if inputs.get("G", "0") is None else Decimal(str(inputs.get("G", "0"))) * fraction
        wallet = principal + profit if principal is not None and profit is not None else None
        equity = None if point["equity"] is None else Decimal(str(point["equity"]))
        raw = {"currency": "USDT", "snapshot_time": stamp, "margin_equity": None if equity is None else str(equity),
               "wallet_balance": None if wallet is None else str(wallet),
               "unrealized_pnl": None if equity is None or wallet is None else str(equity - wallet),
               "net_deposit": None if principal is None else str(principal),
               "realized_pnl_gross": None if profit is None else str(profit), "commission_expense": "0",
               "funding_pnl": "0", "margin_used": "0", "precision_status": "exact"}
        raw["field_status"] = {key: {"status": "available" if value is not None else "unavailable",
                                     "reason_code": None if value is not None else "SOURCE_FIELD_UNAVAILABLE",
                                     "observed_at": stamp} for key, value in raw.items()}
        snapshots.append({"ID": fixture_id + "_h" + str(index), "AccountID": account_id, "AssignmentID": None,
                          "ObservedAt": stamp, "SourceKey": fixture_id + "_h" + str(index),
                          "SourceWatermark": fixture_id, "DataStatus": "complete" if all(value is not None for value in (equity, principal, profit)) else "partial",
                          "Payload": raw})
    base["snapshots"] = snapshots
    if "Gbase" in inputs:
        _bind_history_cycle(base, plan)
    return base


def _bind_history_cycle(base: dict[str, Any], plan: dict[str, Any]) -> None:
    """Attach an actual raw baseline/policy/cycle without changing the lifetime window.

    Missing Gclear is an explicit first-cycle fixture prerequisite (no historical
    clearances), not evidence of testing a nonzero clearance. Unsupported inputs
    fail before insertion instead of silently dropping their dependencies.
    """
    inputs = plan["inputs"]
    if inputs.get("Gclear", "0") != "0":
        raise ValueError("UNBOUND_CLEARANCE_SOURCE: historical cycle fixture supports first cycle only")
    if inputs.get("Gbase") is None or inputs.get("G") is None or inputs.get("D") is None:
        raise ValueError("UNBOUND_CYCLE_BASELINE: raw Gbase, G and D are required")
    baseline, gross, principal = (Decimal(str(inputs[key])) for key in ("Gbase", "G", "D"))
    ratio = baseline / gross if gross else Decimal(0)
    if not Decimal(0) <= ratio < Decimal(1):
        raise ValueError("UNBOUND_CYCLE_BASELINE: baseline must precede the final gross amount")
    fixture, account = base["profiles"][0]["Name"], base["accounts"][0]["id"]
    start, end = datetime.fromisoformat(plan["from"]), datetime.fromisoformat(plan["to"])
    stamp = (start + timedelta(seconds=float(Decimal(str((end - start).total_seconds())) * ratio))).isoformat()
    snapshot = deepcopy(base["snapshots"][0])
    snapshot.update(ID=fixture + "_cycle_baseline", SourceKey=fixture + "_cycle_baseline", ObservedAt=stamp)
    snapshot["Payload"].update(snapshot_time=stamp, margin_equity=str(principal + baseline),
                               wallet_balance=str(principal + baseline), unrealized_pnl="0", realized_pnl_gross=str(baseline))
    for field in snapshot["Payload"]["field_status"].values():
        field["observed_at"] = stamp
    base["snapshots"].append(snapshot)
    base["snapshots"].sort(key=lambda row: row["ObservedAt"])
    base["profiles"][0].update(CurrentPolicyID=fixture + "_policy", CurrentCycleID=fixture + "_cycle")
    base["policies"] = [{"ID": fixture + "_policy", "Enabled": True, "Version": 1,
        "AccountID": account, "AssignmentID": "", "CreatedBy": base["profiles"][0]["CreatedBy"],
        "ShareRate": "0.2", "TriggerRate": "0.05", "EffectiveFromCycle": 1,
        "CreatedAt": stamp, "UpdatedAt": stamp, "FormulaVersion": "account_realized_gross_settled_capital_v1"}]
    base["cycles"] = [{"ID": fixture + "_cycle", "AccountID": account, "AssignmentID": "",
        "PolicyID": fixture + "_policy", "BaselineSnapshotID": snapshot["ID"], "Status": "accruing",
        "Number": 1, "Version": 1, "StartedAt": stamp, "CreatedAt": stamp, "UpdatedAt": stamp,
        "EndedAt": None, "NextCycleID": None, "BillID": None}]


def bind_realized_events(base: dict[str, Any], inputs: dict[str, Any],
                         from_at: str, to_at: str) -> dict[str, Any]:
    """Bind individual realized fills and explicit completeness, never aggregate outcomes."""
    fixture_id = base["profiles"][0]["Name"]
    account_id = base["accounts"][0]["id"]
    start, end = datetime.fromisoformat(from_at), datetime.fromisoformat(to_at)
    base["accounts"][0]["created_at"] = from_at
    base["profiles"][0]["CreatedAt"] = from_at
    events = []
    rows = [(pnl, start + timedelta(hours=index + 1)) for index, pnl in enumerate(inputs["pnl"])]
    rows += [(pnl, start - timedelta(days=index + 1)) for index, pnl in enumerate(inputs.get("outside_pnl", []))]
    for index, (pnl, timestamp) in enumerate(rows):
        event_id = fixture_id + "_event_" + str(index)
        stamp = timestamp.isoformat()
        events.append({"ID": event_id, "AccountID": account_id, "AssignmentID": None, "DefinitionID": None,
                       "Kind": "fills", "Source": "nt_pg", "SourceKey": event_id, "EventAt": stamp, "SortAt": stamp,
                       "Payload": {"currency": "USDT", "fill_id": event_id, "symbol": "BTCUSDT", "quantity": "1",
                                   "price": "100", "realized_pnl_gross": pnl,
                                   "commission_expense": inputs.get("commission_expense", "0"),
                                   "precision_status": "exact"}})
    base["events"] = events
    base["coverage"] = [{"ID": fixture_id + "_coverage", "AccountID": account_id, "Kind": "fills", "Source": "nt_pg",
                         "Status": "complete", "Retention": "complete", "FromAt": start.isoformat(), "ToAt": end.isoformat(),
                         "ObservedAt": end.isoformat(), "UpdatedAt": end.isoformat()}]
    return base
