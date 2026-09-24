"""Inject only raw receipt facts and independent bill-ledger inputs into an owned view."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Any, Iterator, Mapping

from api.strategy_dashboard_receipt_fixture_api import ReceiptFixtureAPI
from service.strategy_dashboard_raw_business_fixture import RawBusinessFixtureFlow


def receipt_registration_statistics(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Independently recompute the observed detail receipt-registration profile.

    Registered amount includes confirmed and reported facts; pending includes
    reported facts. Time assertions apply only to unambiguous ordered examples.
    A contract-pending time field is omitted rather than guessed. This profile
    does not calculate confirmed ledger amounts. Invalid facts raise ValueError.
    """
    rows = [row for row in inputs["receipts"] if row.get("bill_id", "selected") == "selected"]
    seen = set()
    registered, pending = Decimal(0), Decimal(0)
    times = []
    with localcontext() as context:
        context.prec = 50
        for row in rows:
            if row["id"] in seen:
                raise ValueError("DUPLICATE_RECEIPT_ID")
            seen.add(row["id"])
            if row.get("currency", "USDT") != inputs["bill"].get("currency", "USDT"):
                raise ValueError("UNCONVERTED_RECEIPT_CURRENCY")
            status = row["status"]
            if status not in {"confirmed", "reported", "rejected"}:
                raise ValueError("UNMAPPED_RECEIPT_STATUS")
            amount = Decimal(row["amount"])
            if not amount.is_finite() or amount < 0:
                raise ValueError("INVALID_RECEIPT_AMOUNT")
            if status == "rejected":
                continue
            registered += amount
            if status == "reported":
                pending += amount
            stamp = datetime.fromisoformat(row["received_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("RECEIPT_TIMEZONE_REQUIRED")
            times.append(stamp.astimezone(timezone.utc))
    result = {"status": "ok", "registered_received_amount": str(registered),
              "pending_received_amount": str(pending), "receipt_count": len(rows)}
    if inputs.get("time_assertion") != "contract_pending":
        result["last_registered_at"] = max(times).isoformat().replace("+00:00", "Z") if times else None
    return result


def receipt_source_payload(base: dict[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Copy events with deterministic fixture identity; never aggregate amounts or times.

    The bill's independent received ledger amount and last received time are
    intentionally separate from receipts. Tests must establish which output is
    calculated from events and which field merely reads that ledger.
    """
    payload = deepcopy(base)
    account_id = payload["accounts"][0]["id"]
    fixture_id = payload["profiles"][0]["Name"]
    actor_id = payload["profiles"][0]["CreatedBy"]
    dates = [datetime.fromisoformat(row["received_at"].replace("Z", "+00:00")) for row in inputs["receipts"]]
    created_at = ((min(dates) if dates else datetime.now(timezone.utc)) - timedelta(days=2)).isoformat()
    bill = inputs["bill"]
    payload["bills"] = [{"ID": fixture_id + "_bill", "BillNo": fixture_id + "_bill",
        "AccountID": account_id, "BusinessOwnerID": actor_id, "Version": 1,
        "CreatedAt": created_at, "UpdatedAt": created_at, "TriggeredAt": created_at,
        "Currency": bill.get("currency", "USDT"), "Receivable": bill["receivable"],
        "Received": bill["ledger_received"], "PaymentStatus": "unpaid", "SettlementStatus": "pending",
        "BillingScope": "account", "PolicyID": fixture_id + "_policy", "CycleID": fixture_id + "_cycle",
        "SnapshotID": fixture_id, "ShareRate": "0.2", "TriggerRate": "0.05", "EligibleProfit": "",
        "Calculation": {"test_mode": True, "amount_source": "receipt_read_view_fixture"},
        "LastReceivedAt": bill.get("ledger_last_received_at"), "DueAt": None}]
    if any(row.get("bill_id", "selected") != "selected" for row in inputs["receipts"]):
        other = inputs["other_bill"]
        other_bill = deepcopy(payload["bills"][0])
        other_bill.update(ID=fixture_id + "_other_bill", BillNo=fixture_id + "_other_bill",
                          Receivable=other["receivable"], Received=other["ledger_received"],
                          LastReceivedAt=None)
        payload["bills"].append(other_bill)
    receipts = []
    for row in inputs["receipts"]:
        receipts.append({"ID": fixture_id + "_" + row["id"],
            "BillID": fixture_id + ("_bill" if row.get("bill_id", "selected") == "selected" else "_other_bill"),
            "AccountID": account_id, "Version": 1, "Amount": row["amount"],
            "Currency": row.get("currency", "USDT"), "Status": row["status"],
            "ReceivedAt": row["received_at"], "CreatedAt": row.get("reported_at", row["received_at"]),
            "UpdatedAt": row.get("reported_at", row["received_at"]),
            "ReviewedAt": row.get("reviewed_at"), "ReportedBy": actor_id,
            "ReviewedBy": actor_id if row.get("reviewed_at") is not None else None,
            "Evidence": [], "SourceType": "bank_transfer", "FundingSource": "external"})
    payload["receipts"] = receipts
    return payload


class ReceiptSourceFlow:
    """Use one raw source view to observe calculation separately from ledger reads."""

    def __init__(self, raw_flow: RawBusinessFixtureFlow, api: ReceiptFixtureAPI) -> None:
        """Retain source lifecycle/API adapters without performing I/O."""
        self.raw_flow, self.api = raw_flow, api

    @contextmanager
    def execute(self, inputs: Mapping[str, Any], *, receipt_limit: int = 1) -> Iterator[dict[str, Any]]:
        """Insert owned receipt facts, fetch all pages, then verify precise cleanup.

        No bill, receipt, review, notice or clearance operation is called. All
        facts reside only in this query fixture; pagination is bounded to input
        row count plus one so a repeated-cursor bug cannot cause an endless run.
        """
        with self.raw_flow.execute(lambda base: receipt_source_payload(base, inputs),
                                   metrics="margin_equity") as evidence:
            pages = []
            cursor = None
            seen = set()
            for _ in range(len(inputs["receipts"]) + 1):
                response = self.api.detail(evidence["fixture_id"] + "_bill", evidence["fixture_id"],
                                           receipt_limit=receipt_limit, cursor=cursor)
                pages.append(response)
                if response["http_status"] != 200 or response["body"].get("success") is not True:
                    break
                page = response["body"]["data"]["receipts"]
                if not page["has_more"]:
                    break
                cursor = page["next_cursor"]
                if not cursor or cursor in seen:
                    raise RuntimeError("Receipt pagination repeated or omitted cursor")
                seen.add(cursor)
            else:
                raise RuntimeError("Receipt pagination exceeded source-row bound")
            evidence["detail_pages"] = pages
            yield evidence
