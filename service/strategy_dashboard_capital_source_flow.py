"""Bind raw cumulative account endpoints to deployed interval calculations."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, localcontext
from typing import Any, Iterator, Mapping

from service.strategy_dashboard_raw_business_fixture import RawBusinessFixtureFlow


def net_deposit_change(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Independently subtract cumulative deposit endpoints; never aggregate events.

    A missing/nonfinite endpoint remains unavailable. This current query profile
    verifies the interval difference and does not prove upstream deposit-event
    classification, transfer deduplication, or currency conversion.
    """
    try:
        with localcontext() as context:
            context.prec = 50
            start, end = (Decimal(str(inputs[side]["D"])) for side in ("start", "end"))
            if not start.is_finite() or not end.is_finite():
                raise ValueError("Nonfinite deposit endpoint")
            return {"status": "ok", "net_deposit_change": str(end - start)}
    except (KeyError, ValueError, ArithmeticError):
        return {"status": "unavailable", "net_deposit_change": None}


def capital_source_payload(base: dict[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Copy two cumulative raw snapshots, including missing values and currency.

    No differences, sums, ratios, or expected outputs are computed here. An
    explicit reset needs a separately verified source model and is rejected.
    """
    if inputs.get("counter_reset") is True or "events" in inputs:
        raise ValueError("This adapter only supports comparable cumulative endpoints")
    payload = deepcopy(base)
    account_id = payload["accounts"][0]["id"]
    fixture_id = payload["profiles"][0]["Name"]
    window = inputs.get("window", {"from": "2026-09-01T00:00:00Z", "to": "2026-09-08T00:00:00Z"})
    payload["accounts"][0]["created_at"] = window["from"]
    payload["profiles"][0]["CreatedAt"] = window["from"]
    snapshots = []
    for side, stamp in (("start", window["from"]), ("end", window["to"])):
        row = inputs[side]
        fields = {"margin_equity": row.get("E", "10000"), "wallet_balance": row.get("W", "10000"),
                  "unrealized_pnl": row.get("U", "0"), "net_deposit": row.get("D", "10000"),
                  "realized_pnl_gross": row.get("G"), "commission_expense": row.get("C"),
                  "funding_pnl": row.get("F")}
        # A D-specific family explicitly represents a missing endpoint with None.
        raw = {**fields, "currency": row.get("currency", "USDT"), "snapshot_time": stamp,
               "precision_status": "exact", "field_status": {
                   key: {"status": "available" if value is not None else "unavailable", "observed_at": stamp,
                         "reason_code": None if value is not None else "SOURCE_FIELD_UNAVAILABLE"}
                   for key, value in fields.items()}}
        snapshots.append({"ID": fixture_id + "_" + side, "AccountID": account_id,
                          "AssignmentID": None, "ObservedAt": stamp, "CreatedAt": stamp, "UpdatedAt": stamp,
                          "SourceKey": fixture_id + "_" + side, "SourceWatermark": fixture_id,
                          "DataStatus": "complete", "Version": 1, "Payload": raw})
    payload["snapshots"] = snapshots
    return payload


class CapitalSourceFlow:
    """Prepare raw endpoints and query only the corresponding deployed amount."""

    def __init__(self, raw_flow: RawBusinessFixtureFlow) -> None:
        """Keep the owned source lifecycle; construction does not access data."""
        self.raw_flow = raw_flow

    @contextmanager
    def execute(self, inputs: Mapping[str, Any], *, metric: str) -> Iterator[dict[str, Any]]:
        """Read the actual selected interval amount and always clean the fixture."""
        if metric not in {"net_trading_pnl", "net_deposit"}:
            raise ValueError("Unverified capital source metric")
        window = inputs.get("window", {"from": "2026-09-01T00:00:00Z", "to": "2026-09-08T00:00:00Z"})
        with self.raw_flow.execute(lambda base: capital_source_payload(base, inputs), metrics=metric,
                                   from_at=window["from"], to_at=window["to"]) as evidence:
            evidence["input_boundary"] = "raw cumulative endpoints, not deposit or trading-event aggregation"
            evidence["request"] = {
                "method": "GET", "path": "/quantitative-trading/analytics/summary",
                "headers": {"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
                "params": {"subject_type": "account", "subject_id": str(evidence["account_id"]),
                           "snapshot_id": evidence["fixture_id"], "scope": "lifetime", "metric_basis": "accrual",
                           "metrics": metric, "from": window["from"], "to": window["to"]},
            }
            yield evidence
