"""Read-only current-page query variations against exact database generations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from service.strategy_dashboard_current_overview_audit import CurrentOverviewAudit, source_expectations


DIRECTORY_SCENARIOS = (
    "small_pages", "running_filter", "stopped_filter", "exchange_filter",
    "nt_only_selection", "local_and_nt_selection",
)
ACCOUNT_SCENARIOS = ("bound_known", "unbound_known", "missing_source")
SUBSET_SCENARIOS = (
    "bound_known", "unbound_known", "two_known", "known_and_missing",
    "only_missing", "stopped_only", "running_and_stopped", "order_invariant",
)
WINDOW_SCENARIOS = ("today", "seven_days", "equivalent_timezone")


class OverviewSampleGap(RuntimeError):
    """A natural data branch is unavailable; not a product failure or pass."""


def _compact_response(response: dict[str, Any]) -> dict[str, Any]:
    body = response.get("body", {})
    return {"http_status": response.get("http_status"), "request_params": response.get("request_params"),
            "meta": body.get("meta", {}), "data": body.get("data", {}),
            "success": body.get("success"), "error": body.get("error")}


def _compact_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: row.get(key) for key in (
        "account_id", "nt_account_id", "local_account_id", "nt_enabled", "actual_status",
        "runtime_status", "lifecycle_status", "exchange", "metrics", "nt_strategy_ids",
    )} | {"has_current_assignment": bool(row.get("current_assignment"))} for row in accounts]


class CurrentOverviewQueries:
    """Collect each query scenario independently; one failed branch cannot hide others."""

    def __init__(self, audit: CurrentOverviewAudit,
                 evidence_sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        """Reuse live adapters and optionally persist compact evidence, without I/O."""
        self.audit, self.evidence_sink = audit, evidence_sink

    def _source(self, generation: str) -> dict[str, Any]:
        stored = self.audit.repository.source_at(generation)
        if stored["source"].get("raw", {}).get("fetched_at") != generation:
            raise ValueError("DATABASE_SOURCE_GENERATION_MISMATCH")
        return stored

    def _save(self, evidence: dict[str, Any]) -> dict[str, Any]:
        if self.evidence_sink:
            self.evidence_sink(evidence)
        return evidence

    @staticmethod
    def _members(accounts: list[dict[str, Any]], expected: dict[str, Any], scenario: str) -> list[dict[str, Any]]:
        running = [row for row in accounts if row["nt_account_id"] in expected["running_account_ids"]]
        known_ids = set(expected["metrics"]["margin_equity"]["known_accounts"])
        local = [row for row in running if str(row.get("account_id", "")).isascii()
                 and str(row.get("account_id", "")).isdigit()]
        known = [row for row in local if row["nt_account_id"] in known_ids]
        missing = [row for row in local if row["nt_account_id"] not in known_ids]
        bound = [row for row in known if row.get("current_assignment")]
        unbound = [row for row in known if not row.get("current_assignment")]
        stopped = [row for row in accounts if row.get("actual_status") == "stopped"
                   and str(row.get("account_id", "")).isascii() and str(row.get("account_id", "")).isdigit()]
        choices = {"bound_known": bound[:1], "unbound_known": unbound[:1], "missing_source": missing[:1],
                   "two_known": known[:2], "order_invariant": known[:2],
                   "known_and_missing": known[:1] + missing[:1], "only_missing": missing[:1],
                   "stopped_only": stopped[:1], "running_and_stopped": known[:1] + stopped[:1]}
        needed = 2 if scenario in {"two_known", "order_invariant", "known_and_missing", "running_and_stopped"} else 1
        selected = choices.get(scenario, [])
        if len(selected) != needed or len({row["account_id"] for row in selected}) != needed:
            raise OverviewSampleGap(f"SAMPLE_GAP: {scenario}; required natural account combination unavailable")
        return selected

    def directory_variant(self, scenario: str) -> dict[str, Any]:
        """Compare real small-page/filter selections with the complete directory.

The comparison only uses a matched source generation. Directory membership is
compared in the case, not silently assumed stable by this collector. Missing
scenario populations raise OverviewSampleGap; errors stay errors instead of
becoming successful empty comparisons.
"""
        if scenario not in DIRECTORY_SCENARIOS:
            raise ValueError("Unknown directory scenario")
        for _ in range(3):
            base, base_pages = self.audit.directory()
            options: dict[str, Any] = {}
            selected = base
            if scenario == "small_pages":
                if len(base) < 3:
                    raise OverviewSampleGap("SAMPLE_GAP: need three accounts for actual pagination")
                options["limit"] = 2
            elif scenario.endswith("_filter") and scenario != "exchange_filter":
                state = scenario.removesuffix("_filter")
                options["runtime_status"] = state
                selected = [row for row in base if row.get("actual_status") == state]
            elif scenario == "exchange_filter":
                exchanges = {str(row.get("exchange", "")).lower() for row in base if row.get("exchange")}
                if len(exchanges) < 2:
                    raise OverviewSampleGap("SAMPLE_GAP: need multiple exchanges to prove isolation")
                exchange = sorted(exchanges)[0]
                options["exchange"] = exchange
                selected = [row for row in base if str(row.get("exchange", "")).lower() == exchange]
            else:
                nt_only = [row for row in base if row.get("actual_status") == "running"
                           and str(row.get("account_id", "")).startswith("nt:")]
                local = [row for row in base if row.get("actual_status") == "running"
                         and str(row.get("account_id", "")).isascii() and str(row.get("account_id", "")).isdigit()]
                if not nt_only or (scenario == "local_and_nt_selection" and not local):
                    raise OverviewSampleGap("SAMPLE_GAP: required NT-only/local running accounts unavailable")
                selected = nt_only[:1] + (local[:1] if scenario == "local_and_nt_selection" else [])
                options["account_ids"] = [str(row["account_id"]) for row in selected]
            if not selected:
                raise OverviewSampleGap(f"SAMPLE_GAP: {scenario} has no natural members")
            actual, pages = self.audit.directory(**options)
            generation = base_pages[0]["body"]["meta"]["fetched_at"]
            if pages[0]["body"]["meta"]["fetched_at"] != generation:
                continue
            source = self._source(generation)
            return self._save({"kind": "directory", "scenario": scenario, "read_only": True,
                "batch_id": source["batch_id"], "fetched_at": generation,
                "expected_accounts": _compact_accounts(selected), "actual_accounts": _compact_accounts(actual),
                "options": options, "page_count": len(pages),
                "pages": [{"request_params": page["request_params"], "meta": page["body"]["meta"]} for page in pages],
                "expected": source_expectations(selected, source["source"]),
                "actual_population_source": source_expectations(actual, source["source"])})
        raise RuntimeError("SOURCE_GENERATION_DRIFT: directory variation crossed three ticks")

    def summary_variant(self, kind: str, scenario: str, *, from_at: str, to_at: str) -> dict[str, Any]:
        """Read account/subset/projection/window variants and same-generation inputs.

Request each scenario separately. HTTP errors are saved as business evidence;
source mismatches alone permit bounded recollection. Projections and date pairs
compare stable-generation results, never force current lifetime values into
interval differences. Unsupported NT-only account-detail IDs are not fabricated.
"""
        for _ in range(3):
            accounts, pages = self.audit.directory()
            generation = pages[0]["body"]["meta"]["fetched_at"]
            source = self._source(generation)
            all_expected = source_expectations(accounts, source["source"])
            selected = accounts
            kwargs: dict[str, Any] = {"from_at": from_at, "to_at": to_at}
            reference = None
            if kind in {"account", "subset"}:
                selected = self._members(accounts, all_expected, scenario)
                if kind == "account":
                    response = self.audit.api.account_summary(str(selected[0]["account_id"]), **kwargs)
                else:
                    kwargs["account_ids"] = [str(row["account_id"]) for row in selected]
                    response = self.audit.api.summary(**kwargs)
                    if scenario == "order_invariant":
                        reference = response
                        kwargs["account_ids"] = list(reversed(kwargs["account_ids"]))
                        response = self.audit.api.summary(**kwargs)
            elif kind == "projection":
                reference = self.audit.api.summary(**kwargs)
                response = self.audit.api.summary(**kwargs, metrics=[scenario])
            elif kind == "window":
                reference = self.audit.api.summary(**kwargs)
                end = datetime.fromisoformat(to_at.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8)))
                if scenario in {"today", "seven_days"}:
                    begin = (end - timedelta(days=0 if scenario == "today" else 6)).replace(
                        hour=0, minute=0, second=0, microsecond=0)
                    kwargs["from_at"] = begin.isoformat()
                elif scenario == "equivalent_timezone":
                    kwargs["from_at"] = datetime.fromisoformat(from_at.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
                    kwargs["to_at"] = end.astimezone(timezone.utc).isoformat()
                else:
                    raise ValueError("Unknown window scenario")
                response = self.audit.api.summary(**kwargs)
            else:
                raise ValueError("Unknown current overview query kind")
            responses = [response] + ([reference] if reference is not None else [])
            successful = [item for item in responses if item["http_status"] == 200 and item.get("body", {}).get("success") is True]
            if any(item["body"].get("meta", {}).get("fetched_at") != generation for item in successful):
                continue
            record = {"kind": kind, "scenario": scenario, "read_only": True,
                      "batch_id": source["batch_id"], "fetched_at": generation,
                      "selected_accounts": _compact_accounts(selected),
                      "expected": source_expectations(selected, source["source"]),
                      "response": _compact_response(response),
                      "reference": _compact_response(reference) if reference is not None else None}
            return self._save(record)
        raise RuntimeError("SOURCE_GENERATION_DRIFT: summary variation crossed three ticks")
