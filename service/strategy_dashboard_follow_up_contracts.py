"""Result-level contracts for the newly registered strategy-dashboard cases.

These helpers deliberately validate only response facts that are already part of
the dashboard result contract: identity, generation, status, dependency and
idempotency.  They do not calculate product metrics and do not turn a synthetic
fixture into a live business pass.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class ContractCheck:
    """Return a stable result for one response-level contract check."""

    passed: bool
    issues: tuple[str, ...] = ()

    def require(self) -> None:
        """Raise ``AssertionError`` with all issues when the check is invalid."""
        if not self.passed:
            raise AssertionError("; ".join(self.issues))


def check_same_generation(records: Sequence[Mapping[str, Any]]) -> ContractCheck:
    """Ensure complete surfaces share one source identity and window.

    A non-complete surface is allowed only when it carries an explicit reason;
    this prevents a missing generation from being treated as a successful empty
    result.
    """
    issues: list[str] = []
    complete: list[Mapping[str, Any]] = []
    for record in records:
        status = record.get("status", "complete")
        if status in {"partial", "refreshing", "unavailable"}:
            if not record.get("reason_code"):
                issues.append("NON_COMPLETE_REASON_MISSING")
            continue
        complete.append(record)
        if any(not record.get(field) for field in ("snapshot_id", "generation", "as_of")):
            issues.append("COMPLETE_GENERATION_IDENTITY_MISSING")
    identities = {
        tuple(record.get(field) for field in ("snapshot_id", "generation", "as_of", "scope_id", "window"))
        for record in complete
    }
    if len(identities) > 1:
        issues.append("MIXED_SOURCE_GENERATION")
    if not complete and not issues:
        issues.append("NO_COMPLETE_SURFACE")
    return ContractCheck(not issues, tuple(issues))


def check_atomic_batch(
    expected_ids: Sequence[str],
    first_attempt: Sequence[Mapping[str, Any]],
    final_attempt: Sequence[Mapping[str, Any]],
) -> ContractCheck:
    """Check partial ingestion visibility and idempotent retry completion."""
    issues: list[str] = []
    expected = set(expected_ids)
    first_by_id = {str(row.get("entity_id")): row for row in first_attempt}
    final_ids = [str(row.get("entity_id")) for row in final_attempt]
    if len(first_by_id) != len(first_attempt):
        issues.append("FIRST_ATTEMPT_DUPLICATE_ENTITY")
    if len(set(final_ids)) != len(final_ids):
        issues.append("RETRY_DUPLICATE_ENTITY")
    if set(first_by_id) - expected or set(final_ids) != expected:
        issues.append("BATCH_ENTITY_SET_MISMATCH")
    if any(row.get("status") == "complete" for row in first_attempt if row.get("entity_id") == "B"):
        issues.append("FAILED_ENTITY_MARKED_COMPLETE")
    if sum(row.get("entity_id") == "A" for row in final_attempt) != 1:
        issues.append("SUCCESSFUL_ENTITY_REPLAYED")
    return ContractCheck(not issues, tuple(issues))


def check_filter_contract(params: Mapping[str, Any], response: Mapping[str, Any]) -> ContractCheck:
    """Ensure invalid selection/window input cannot silently fall back to all data."""
    issues: list[str] = []
    start, end = params.get("from"), params.get("to")
    if start is not None and end is not None and start >= end:
        status = response.get("http_status")
        body = response.get("body") if isinstance(response.get("body"), Mapping) else {}
        data = body.get("data") if isinstance(body.get("data"), Mapping) else {}
        if status not in {400, 422} and data.get("status") not in {"empty", "unavailable"}:
            issues.append("INVALID_WINDOW_FALLBACK")
        if data.get("status") == "complete" or data.get("count") == "all":
            issues.append("INVALID_WINDOW_COMPLETE")
    if "account_ids" in params and not params["account_ids"]:
        body = response.get("body") if isinstance(response.get("body"), Mapping) else {}
        data = body.get("data") if isinstance(body.get("data"), Mapping) else {}
        if data.get("count") not in {0, None} or data.get("status") == "complete":
            issues.append("EMPTY_SELECTION_FALLBACK")
    return ContractCheck(not issues, tuple(issues))


def check_latest_request_wins(responses: Sequence[Mapping[str, Any]]) -> ContractCheck:
    """Ensure a late response from an older query cannot become the displayed result."""
    issues: list[str] = []
    if not responses:
        return ContractCheck(False, ("NO_RESPONSES",))
    latest = max(responses, key=lambda row: int(row.get("request_seq", -1)))
    displayed = responses[-1]
    if displayed.get("request_seq") != latest.get("request_seq"):
        issues.append("STALE_RESPONSE_DISPLAYED")
    if displayed.get("query_key") != latest.get("query_key"):
        issues.append("STALE_QUERY_KEY_DISPLAYED")
    if displayed.get("generation") != latest.get("generation"):
        issues.append("STALE_GENERATION_DISPLAYED")
    return ContractCheck(not issues, tuple(issues))


def check_dependency_projection(
    source: Mapping[str, Any],
    derived: Mapping[str, Mapping[str, Any]],
    dependencies: Mapping[str, Sequence[str]],
) -> ContractCheck:
    """Ensure only metrics depending on a missing source are degraded."""
    issues: list[str] = []
    for metric, required in dependencies.items():
        result = derived.get(metric, {})
        missing = any(source.get(field) is None for field in required)
        if missing and (result.get("value") is not None or result.get("status") == "complete"):
            issues.append(f"{metric}:MISSING_DEPENDENCY_ACCEPTED")
        if not missing and result.get("status") not in {"complete", "ok"}:
            issues.append(f"{metric}:KNOWN_DEPENDENCY_DEGRADED")
    return ContractCheck(not issues, tuple(issues))


def check_hierarchy_conservation(
    account_totals: Mapping[str, Any],
    strategy_contributions: Sequence[Mapping[str, Any]],
    portfolio: Mapping[str, Any],
) -> ContractCheck:
    """Check physical-account uniqueness and explicit unallocated contribution."""
    issues: list[str] = []
    account_ids = account_totals.get("account_ids", [])
    if len(set(account_ids)) != len(account_ids):
        issues.append("ACCOUNT_COUNTED_TWICE")
    try:
        contributed = sum((Decimal(str(row["value"])) for row in strategy_contributions), Decimal(0))
        unallocated = Decimal(str(portfolio.get("unallocated", "0")))
        total = Decimal(str(portfolio["value"]))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return ContractCheck(False, ("HIERARCHY_NUMERIC_FIELD_MISSING",))
    if contributed + unallocated != total:
        issues.append("CONTRIBUTION_NOT_CONSERVED")
    if portfolio.get("unallocated") is None:
        issues.append("UNALLOCATED_NOT_EXPLICIT")
    return ContractCheck(not issues, tuple(issues))


def check_action_readthrough(
    action_result: Mapping[str, Any],
    read_results: Sequence[Mapping[str, Any]],
) -> ContractCheck:
    """Check that post-action reads expose one new generation and frozen states."""
    issues: list[str] = []
    generation = action_result.get("generation")
    if not generation:
        issues.append("ACTION_GENERATION_MISSING")
    for result in read_results:
        if result.get("generation") != generation:
            issues.append("POST_ACTION_STALE_READ")
    if action_result.get("locked_bill_status") != "locked":
        issues.append("LOCKED_BILL_NOT_FROZEN")
    if action_result.get("current_cycle_status") not in {"accruing", "active"}:
        issues.append("SUCCESSOR_CYCLE_NOT_ACTIVE")
    return ContractCheck(not issues, tuple(issues))


def check_concurrent_idempotency(results: Sequence[Mapping[str, Any]]) -> ContractCheck:
    """Allow one mutation winner and require all replays to reuse its result."""
    issues: list[str] = []
    winners = [row for row in results if row.get("status") == "success"]
    if len(winners) != 1:
        issues.append("MULTIPLE_MUTATION_WINNERS")
    if winners:
        winner = winners[0]
        for row in results:
            if row.get("idempotency_key") == winner.get("idempotency_key") and row.get("result_id") != winner.get("result_id"):
                issues.append("IDEMPOTENCY_RESULT_CHANGED")
    if any(row.get("created_cycle_count", 0) > 1 for row in results):
        issues.append("DUPLICATE_SUCCESSOR_CYCLE")
    return ContractCheck(not issues, tuple(issues))

