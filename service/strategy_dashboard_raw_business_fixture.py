"""Evaluate raw business facts through the deployed non-minute formula engine."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator
from uuid import uuid4

from api.strategy_dashboard_raw_business_api import RawBusinessFixtureAPI
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository
from service.strategy_dashboard_source_fixture_flow import position_source_payload


class RawBusinessFixtureFlow:
    """Own a temporary identity and raw-input view, evaluate, then verify cleanup."""

    def __init__(self, repository: SourceFixtureRepository, api: RawBusinessFixtureAPI, actor_id: int,
                 evidence_sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        """Receive test-only source repository and live API; construction has no effects."""
        self.repository, self.api, self.actor_id = repository, api, actor_id
        self.evidence_sink = evidence_sink

    @contextmanager
    def execute(self, builder: Callable[[dict[str, Any]], dict[str, Any]], *, metrics: str,
                context: dict[str, Any] | None = None, scope: str = "lifetime",
                metric_basis: str = "accrual", from_at: str | None = None,
                to_at: str | None = None) -> Iterator[dict[str, Any]]:
        """Inject raw facts, call actual engine and clean exact resources even on failure.

The builder receives a fresh isolated account/profile and can add source
snapshots, events, cycles or policies. It must not include formula output fields.
This boundary is after source collection; it does not verify external ingestion.
"""
        fixture_id = "qfi_" + uuid4().hex
        account_id = None
        evidence: dict[str, Any] = {"fixture_id": fixture_id, "injection_boundary": "raw_business_read_view"}
        try:
            account_id = self.repository.create_account(fixture_id, self.actor_id)
            evidence["account_id"] = account_id
            base = position_source_payload(account_id, self.actor_id, fixture_id)
            base.update({"assignments": [], "definitions": [], "snapshots": [],
                         "positions": [], "position_batches": [], "strategy_samples": [],
                         "clearances": [], "account_performance": [], "order_history": []})
            business = builder(base)
            if business["accounts"][0]["id"] != account_id or business["profiles"][0]["AccountID"] != account_id:
                raise ValueError("Raw business fixture identity was changed")
            bound_context = {**(context or {}), "minute_mode": False}
            evidence["raw_business"] = business
            evidence["context"] = bound_context
            self.repository.insert(fixture_id, self.actor_id, account_id, business, bound_context)
            evidence["summary_response"] = self.api.summary(account_id, fixture_id, metrics=metrics,
                                                            scope=scope, metric_basis=metric_basis,
                                                            from_at=from_at, to_at=to_at)
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
