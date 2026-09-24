"""Map raw execution events to the live model without calculating latest times."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping

from api.strategy_dashboard_execution_fixture_api import ExecutionFixtureAPI
from service.strategy_dashboard_raw_business_fixture import RawBusinessFixtureFlow


def execution_source_payload(base: dict[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Build raw trigger/completion rows; reject unsupported events and result fields.

    Timestamp selection, maxima, chain association and time-window filtering are
    left to the deployed service. Duplicate facts and input order are preserved.
    This starts after external event collection and does not place trading orders.
    """
    rows = inputs.get("events")
    if not isinstance(rows, list):
        raise ValueError("Raw execution event list is required")
    payload = deepcopy(base)
    account_id = payload["accounts"][0]["id"]
    fixture_id = payload["profiles"][0]["Name"]
    times = [datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) for row in rows]
    started = (min(times) if times else datetime.now(timezone.utc)) - timedelta(days=1)
    payload["accounts"][0]["created_at"] = started.isoformat()
    payload["profiles"][0]["CreatedAt"] = started.isoformat()
    events = []
    for index, row in enumerate(rows):
        kind = row.get("kind")
        if kind not in {"trigger", "completed"}:
            raise ValueError("Only observed trigger/completion source kinds are mapped")
        at = row["timestamp"]
        chain_id = row.get("chain_id", "fixture-chain")
        source = {"signal_id": chain_id}
        if kind == "trigger":
            source["received_at"] = at
        else:
            source.update(batch_id=chain_id, status="succeeded", finished_at=at)
        events.append({"ID": f"{fixture_id}_event_{index}", "AccountID": account_id,
                       "AssignmentID": None, "DefinitionID": None,
                       "Kind": "signals" if kind == "trigger" else "signal-batches",
                       "EventAt": at, "SortAt": at, "CreatedAt": at, "UpdatedAt": at,
                       "Version": 1, "Source": "questtest_fixture",
                       "SourceKey": f"{fixture_id}_source_{index}", "Payload": source})
    payload["events"] = events
    return payload


class ExecutionSourceFlow:
    """Compare two live output surfaces using one owned raw execution dataset."""

    def __init__(self, raw_flow: RawBusinessFixtureFlow, api: ExecutionFixtureAPI) -> None:
        """Receive existing source lifecycle and HTTP adapter; no I/O occurs here."""
        self.raw_flow, self.api = raw_flow, api

    @contextmanager
    def execute(self, inputs: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        """Submit raw events and query summary/risk before exact cleanup.

        Neither latest timestamps nor latency values are stored in the input.
        Transport or fixture failures propagate after cleanup is attempted.
        """
        window = inputs.get("window", {})
        with self.raw_flow.execute(
            lambda base: execution_source_payload(base, inputs), metrics="margin_equity",
            from_at=window.get("from"), to_at=window.get("to"),
        ) as evidence:
            evidence["risk_response"] = self.api.risk(
                evidence["account_id"], evidence["fixture_id"],
                from_at=window.get("from"), to_at=window.get("to"),
            )
            yield evidence
