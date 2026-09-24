"""Owned financial source views for query-time calculations, never real billing actions."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from api.strategy_dashboard_source_fixture_api import SourceFixtureAPI
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository
from service.strategy_dashboard_source_fixture_flow import position_source_payload


class LedgerSourceFlow:
    """Supply immutable billed amounts to the deployed summary read model.

    Receivable/received are inputs to outstanding/progress aggregation. This flow
    does not claim to validate profit thresholds, bill generation or confirmation.
    Nothing is inserted into real bill/receipt tables or sent to payment systems.
    """

    def __init__(self, repository: SourceFixtureRepository, api: SourceFixtureAPI,
                 actor_id: int, evidence_sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        """Use a test-only repository and authenticated deployed read API."""
        self.repository, self.api, self.actor_id = repository, api, actor_id
        self.evidence_sink = evidence_sink

    @contextmanager
    def execute(self, inputs: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        """Create one owned identity/view, query actual output, and always clean up.

        The supplied source amounts are copied, never calculated from the expected
        results. Partial failures still attempt view and identity cleanup.
        """
        fixture_id = "qfi_" + uuid4().hex
        account_id = None
        evidence: dict[str, Any] = {"fixture_id": fixture_id,
            "calculation_boundary": "bill ledger amounts to query-time aggregate, no billing/receipt action"}
        try:
            account_id = self.repository.create_account(fixture_id, self.actor_id)
            evidence["account_id"] = account_id
            payload = position_source_payload(account_id, self.actor_id, fixture_id)
            payload.update(assignments=[], definitions=[], snapshots=[], positions=[], position_batches=[])
            timestamp = datetime.now(timezone.utc).isoformat()
            bills = []
            for row in inputs["bills"]:
                bills.append({"ID": fixture_id + "_" + row["id"], "BillNo": fixture_id + "_" + row["id"],
                    "AccountID": account_id, "BusinessOwnerID": self.actor_id, "Version": 1,
                    "CreatedAt": timestamp, "UpdatedAt": timestamp, "TriggeredAt": timestamp,
                    "Currency": row.get("currency", "USDT"), "Receivable": row.get("receivable"),
                    "Received": row.get("received"), "PaymentStatus": row.get("payment_status", "unpaid"),
                    "SettlementStatus": "pending", "BillingScope": "account",
                    "PolicyID": fixture_id + "_policy", "CycleID": fixture_id + "_cycle",
                    "SnapshotID": fixture_id, "ShareRate": "0.2", "TriggerRate": "0.05",
                    "EligibleProfit": "", "Calculation": {"test_mode": True, "amount_source": "ledger_fixture"},
                    "LastReceivedAt": row.get("last_received_at"), "DueAt": row.get("due_at")})
            payload["bills"] = bills
            evidence["source_bills"] = bills
            self.repository.insert(fixture_id, self.actor_id, account_id, payload, {"purpose": "analytics"})
            evidence["summary_response"] = self.api.summary(account_id, fixture_id)
            yield evidence
        finally:
            try:
                evidence["cleanup"] = self.repository.delete(fixture_id, self.actor_id)
            finally:
                try:
                    if account_id is not None:
                        evidence["identity_cleanup"] = self.repository.delete_account(fixture_id, self.actor_id, account_id)
                        self.repository.assert_clean(fixture_id, account_id)
                        evidence["cleanup_verified_zero"] = True
                finally:
                    if self.evidence_sink:
                        self.evidence_sink(evidence)
