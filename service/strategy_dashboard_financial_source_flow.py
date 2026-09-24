"""Owned source snapshots feeding the deployed cumulative-return calculation."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from service.strategy_dashboard_source_fixture_flow import SourceFixtureFlow


class FinancialSourceFlow:
    """Pass cumulative source amounts to the real service, never the expected ratio."""

    def __init__(self, source_flow: SourceFixtureFlow) -> None:
        """Use an existing environment-validated, owned-fixture flow."""
        self.source_flow = source_flow

    @contextmanager
    def cumulative_return(self, inputs: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        """Create G/D source fields, query actual G/D, and clean all owned rows.

        This begins at the account-performance source snapshot, not exchange fills.
        Missing inputs remain missing. HTTP and cleanup errors propagate; the real
        response is returned without substituting reference-calculator output.
        """
        def prepare(payload: dict[str, Any]) -> dict[str, Any]:
            raw = payload["snapshots"][0]["Payload"]
            for short, key in (("G", "realized_pnl_gross"), ("D", "net_deposit"),
                               ("C", "commission_expense"), ("F", "funding_pnl")):
                raw[key] = inputs.get(short)
                raw["field_status"][key] = {
                    "status": "available" if raw[key] is not None else "unavailable",
                    "reason_code": None if raw[key] is not None else "SOURCE_FIELD_UNAVAILABLE",
                    "observed_at": raw["snapshot_time"],
                }
            raw["performance_source"] = "exchange_account_performance_summaries"
            raw["performance_synced_until"] = raw["snapshot_time"]
            return payload

        with self.source_flow.execute(
            payload_builder=prepare,
            metrics="realized_pnl_gross,net_deposit,cumulative_return,margin_equity,unrealized_pnl",
        ) as evidence:
            evidence["calculation_boundary"] = "account performance G/D source fields to deployed cumulative-return result"
            yield evidence
