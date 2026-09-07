"""Validity evidence visibility, exact requests, batch isolation and PIT boundaries."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from api.factor_data_mcp_api import MCPResponse
from db.factor4_read_repository import SUMMARY_KEYS, Factor4ReadRepository, SummarySample, ValiditySample
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_body, read_tool_page
from service.factor4_summary_service import Factor4SummaryService, compare_summary, compare_validity, summary_scope, validity_arguments
from service.factor4_recommendation_service import lifecycle_time

Endpoint = Literal["metrics", "validity"]
_ABSENT_CODES = frozenset({"METRIC_SCOPE_NOT_FOUND", "METRICS_NOT_FOUND", "VALIDITY_SCOPE_NOT_FOUND",
                           "METRIC_NOT_FOUND", "RUN_NOT_FOUND", "FACTOR_NOT_FOUND", "INVALID_ARGUMENT"})


class Factor4ValidityService:
    """Read only persisted final evidence and compare each returned business identity."""

    def __init__(self, summaries: Factor4SummaryService) -> None:
        """Keep the initialized summary service; no I/O or exceptions."""
        self.summaries = summaries

    def _arguments(self, sample: ValiditySample, scope: str, endpoint: Endpoint, *, as_of: str | None = None) -> dict[str, Any]:
        args = validity_arguments(sample, scope, as_of=as_of, explicit_run=True)
        if endpoint == "metrics":
            args["ic_scope"] = args.pop("validity_scope")
            args["scoring_version"] = sample.summaries.get(scope, {}).get("scoring_version") or args["scoring_version"]
        return args

    def _call(self, endpoint: Endpoint, args: dict[str, Any]) -> MCPResponse:
        if endpoint == "metrics":
            return self.summaries.api.metrics_query(args)
        if endpoint == "validity":
            return self.summaries.api.validity(args)
        raise ValueError("unsupported evidence endpoint")

    def _compare(self, response: MCPResponse, sample: ValiditySample, scope: str, endpoint: Endpoint) -> ReadCheck:
        page = read_tool_page(response)
        if endpoint == "validity":
            item = page.data.get("item")
            if not isinstance(item, dict):
                raise ReadContractError("validity success did not return an item")
            return compare_validity(item, sample, scope)
        rows = page.data.get("ic_summaries")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ReadContractError("metric success requires an ic_summaries array")
        return compare_summary(rows, [sample.summaries[scope]])

    def _absent(self, response: MCPResponse, *, codes: frozenset[str] = _ABSENT_CODES) -> ReadCheck:
        body = read_tool_body(response)
        error, data = body.get("error"), body.get("data")
        if response.is_tool_error:
            if not isinstance(error, dict) or error.get("code") not in codes:
                return ReadCheck(1, ("evidence:unexpected_rejection_code",))
        elif error is not None:
            return ReadCheck(1, ("evidence:error_flag_mismatch",))
        elif not isinstance(data, dict):
            raise ReadContractError("evidence response has neither data nor explicit error")
        if isinstance(data, dict) and any(data.get(key) for key in ("items", "item", "ic_summaries")):
            return ReadCheck(1, ("evidence:unexpected_visible_data",))
        return ReadCheck(1)

    def check_exact_evidence(self, sample: ValiditySample, scope: str, endpoint: Endpoint) -> ReadCheck:
        """Read explicit TS/CS evidence and check DB fields; protocol errors propagate."""
        return self._compare(self._call(endpoint, self._arguments(sample, scope, endpoint)), sample, scope, endpoint)

    def check_argument_variant(
        self, sample: ValiditySample, endpoint: Endpoint, variant: str, *,
        as_of: str | None = None, metric_expected: SummarySample | None = None,
    ) -> ReadCheck:
        """Verify one aggregate symbol/run/as-of boundary without accepting arbitrary errors.

        Canonical omitted/null fields must preserve the selected row. Invalid scopes
        may return a contracted absence or an empty success. Explicit null symbol on
        validity additionally accepts INVALID_ARGUMENT, retaining the historical
        compatibility exclusion. Bad as-of must be explicitly rejected. No I/O other
        than the requested MCP read; missing data/protocol errors propagate.
        A supplied as_of fixes the request to the discovery instant. metric_expected
        supplies independently selected default-Run summaries only for metrics
        run_omitted/run_null; other combinations raise ValueError before any request.
        """
        if metric_expected is not None and (endpoint != "metrics" or variant not in {"run_omitted", "run_null"}):
            raise ValueError("independent metric expectation requires an omitted/null metrics Run")
        args = self._arguments(sample, "ts", endpoint, as_of=as_of)
        if args.get("symbol"):
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: argument matrix requires aggregate TS evidence")
        specs = {"symbol_omitted": ("symbol", "omit"), "symbol_null": ("symbol", None),
                 "symbol_wrong": ("symbol", "QUESTTEST_NO_SUCH_SYMBOL"), "run_omitted": ("run_id", "omit"),
                 "run_null": ("run_id", None), "run_wrong": ("run_id", "questtest-absent-run"),
                 "as_of_omitted": ("as_of", "omit"), "as_of_null": ("as_of", None),
                 "version_wrong": ("scoring_version", "questtest_absent_scoring_version"),
                 "factor_ref_omitted": ("factor_ref", "omit"),
                 "scope_bad": ("ic_scope" if endpoint == "metrics" else "validity_scope", "questtest_bad_scope"),
                 "unexpected_argument": ("questtest_unexpected_argument", True)}
        if variant not in specs:
            raise ValueError("unsupported argument variant")
        field, value = specs[variant]
        if value == "omit":
            args.pop(field)
        else:
            args[field] = value
        response = self._call(endpoint, args)
        if variant.startswith("as_of_") or variant in {"factor_ref_omitted", "scope_bad", "unexpected_argument"}:
            check = self._absent(response, codes=frozenset({"INVALID_ARGUMENT"}))
            return ReadCheck(1, (*check.issues, *(() if response.is_tool_error else ("evidence:missing_as_of_not_rejected",))))
        if variant in {"symbol_wrong", "run_wrong", "version_wrong"}:
            return self._absent(response)
        if endpoint == "validity" and variant == "symbol_null" and response.is_tool_error:
            check = self._absent(response, codes=frozenset({"INVALID_ARGUMENT"}))
            return ReadCheck(1, check.issues, {"null_symbol_compatibility_exclusion": True})
        if metric_expected is not None:
            page = read_tool_page(response)
            rows = page.data.get("ic_summaries")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ReadContractError("metric success requires an ic_summaries array")
            return compare_summary(rows, list(metric_expected.rows))
        return self._compare(response, sample, "ts", endpoint)

    def check_completion_boundary(
        self, sample: ValiditySample, endpoint: Endpoint, offset: int, *,
        repository: Factor4ReadRepository | None = None,
    ) -> ReadCheck:
        """Verify explicit-run invisibility before and exact visibility at/after completion.

        Offset is -1/0/1 microseconds. Missing natural timestamp raises ReadPrecondition,
        invalid offset raises ValueError, protocol failures propagate as failures.
        Validity additionally requires repository history: before a later update,
        an already-visible same-Run revision remains a valid response. Missing
        revision history blocks; it must never be inferred to mean empty output.
        """
        if offset not in {-1, 0, 1}:
            raise ValueError("completion boundary offset must be -1, 0 or 1")
        completed = sample.validity.get("run_completed_at")
        if not isinstance(completed, datetime):
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: validity run completion timestamp is absent")
        completed = completed.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if completed.tzinfo is None else completed
        run_completed = completed
        if endpoint == "validity":
            # A validity record is written after run completion; it cannot be visible
            # at an earlier instant than its own persisted availability timestamp.
            recorded = sample.validity.get("updated_at") or sample.validity.get("created_at")
            if not isinstance(recorded, datetime):
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: validity publication timestamp is absent")
            recorded = recorded.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if recorded.tzinfo is None else recorded
            completed = max(completed, recorded)
        as_of = completed + timedelta(microseconds=offset)
        expected = sample
        if endpoint == "validity" and as_of >= run_completed:
            if repository is None:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: exact-Run validity revision history is required")
            expected = self._visible_explicit_validity(
                repository.validity_candidates(sample, "ts", as_of, include_unavailable=True), sample, as_of)
        args = self._arguments(sample, "ts", endpoint, as_of=as_of.isoformat())
        response = self._call(endpoint, args)
        absent = as_of < run_completed if endpoint == "metrics" else as_of < run_completed or expected is None
        return self._absent(response) if absent else self._compare(response, expected, "ts", endpoint)

    @staticmethod
    def _visible_explicit_validity(
        candidates: tuple[ValiditySample, ...], sample: ValiditySample, as_of: datetime,
    ) -> ValiditySample | None:
        """Resolve persisted same-Run publication history or block unrecoverable old state."""
        matching: list[tuple[ValiditySample, datetime]] = []
        inspected = 0
        for candidate in candidates:
            if candidate.validity.get("run_id") != sample.validity.get("run_id"):
                continue
            summary = candidate.summaries.get("ts")
            if not summary:
                raise ReadPrecondition("BLOCKED_DOC: validity revision scope evidence is unavailable")
            if any(summary.get(key) != sample.summaries["ts"].get(key)
                   for key in ("factor_id", "is_sub_factor_id", *SUMMARY_KEYS)):
                continue
            inspected += 1
            try:
                published = lifecycle_time(candidate.validity.get("updated_at"), database=True)
                completed = lifecycle_time(candidate.validity.get("run_completed_at"), database=True)
            except (TypeError, ValueError):
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: validity revision availability missing") from None
            if max(published, completed) <= as_of:
                matching.append((candidate, published))
            elif completed <= as_of:
                try:
                    created = lifecycle_time(candidate.validity.get("created_at"), database=True)
                except (TypeError, ValueError):
                    raise ReadPrecondition("BLOCKED_DOC: validity first publication cannot be proved without creation/history evidence") from None
                if created <= as_of < published:
                    raise ReadPrecondition("BLOCKED_DOC: validity row existed before its current update but historical state is unavailable")
        if not inspected:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: exact-Run validity history is empty")
        if not matching:
            return None
        newest = max(published for _, published in matching)
        selected = [candidate for candidate, published in matching if published == newest]
        if len(selected) != 1:
            raise ReadPrecondition("BLOCKED_DOC: tied same-Run validity revisions have no unique publication order")
        return selected[0]

    def check_batch_error_isolation(self, sample: ValiditySample, endpoint: Endpoint, missing_ref: str) -> ReadCheck:
        """A present and DB-verified absent factor preserve isolated item results.

        The good item is fully compared with DB; the absent item must have the stable
        FACTOR_NOT_FOUND code and no data. No failed batch/top-level error counts as a
        pass. Malformed/transport responses raise ReadContractError or their original error.
        """
        args = self._arguments(sample, "ts", endpoint)
        good_ref = args.pop("factor_ref")
        refs = [good_ref, missing_ref]
        args["factor_refs"] = refs
        if endpoint == "metrics":
            response = self.summaries.api.metrics_batch(tuple(refs), summary_scope(sample.summaries["ts"]),
                as_of=args["as_of"], run_id=sample.validity["run_id"])
        else:
            response = self.summaries.api.validity_batch(args)
        page = read_tool_page(response)
        items = page.items
        issues = []
        if len(items) != 2 or [item.get("factor_ref") for item in items] != refs:
            issues.append("batch:membership_or_order")
        by_ref = {item.get("factor_ref"): item for item in items}
        good, absent = by_ref.get(good_ref, {}), by_ref.get(missing_ref, {})
        if good.get("success") is not True or not isinstance(good.get("data"), dict):
            issues.append("batch:good_item_failed")
        elif endpoint == "metrics":
            issues.extend(compare_summary([good["data"]], [sample.summaries["ts"]]).issues)
        else:
            issues.extend(compare_validity(good["data"], sample, "ts").issues)
        error = absent.get("error")
        if absent.get("success") is not False or not isinstance(error, dict) or error.get("code") != "FACTOR_NOT_FOUND" or absent.get("data"):
            issues.append("batch:absent_item_not_isolated")
        return ReadCheck(2, tuple(issues))

    def check_incomplete_visibility(self, repository: Factor4ReadRepository, sample: ValiditySample,
                                    scope: str, *, explicit_run: bool = True) -> ReadCheck:
        """Incomplete historical rows may be hidden, but cannot supply unsupported valid evidence.

        Any exposed item is re-read by primary key and checked against its real same-run
        summary FK. Omitted run may select a newer complete record, not an unverified
        fallback. DB/protocol errors propagate; historical incompleteness alone is not a bug.
        """
        args = validity_arguments(sample, scope, explicit_run=explicit_run)
        response = self.summaries.api.validity(args)
        if response.is_tool_error:
            return self._absent(response, codes=frozenset({"VALIDITY_SCOPE_NOT_FOUND"}))
        page = read_tool_page(response)
        item = page.data.get("item")
        return self._compare_exposed_validity(repository, sample, scope, item, explicit_run=explicit_run)

    def _compare_exposed_validity(self, repository: Factor4ReadRepository, sample: ValiditySample,
                                 scope: str, item: object, *, explicit_run: bool) -> ReadCheck:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            raise ReadContractError("exposed validity must have a persisted integer identity")
        persisted = repository.validity_by_id(item["id"])
        if persisted is None:
            return ReadCheck(1, ("validity:exposed_missing_db_row",))
        summary, row = persisted.summaries[scope], persisted.validity
        issues = []
        for key in ("factor_id", "is_sub_factor_id", "universe_key", "window_scope", "factor_bar_interval",
                    "factor_window_bars", "return_bar_interval", "forward_return_bars"):
            if row.get(key) != sample.validity.get(key):
                issues.append(f"validity:exposed_wrong_partition={key}")
        if explicit_run and row.get("run_id") != sample.validity.get("run_id"):
            issues.append("validity:exposed_wrong_run")
        dimension = "time_series" if scope == "ts" else "cross_sectional"
        if not summary and not row.get(f"{dimension}_is_valid"):
            issues.extend(compare_validity(item, persisted, scope).issues)
        elif not summary or summary.get("run_id") != row.get("run_id") or summary.get("factor_id") != row.get("factor_id"):
            issues.append("validity:unsupported_summary_evidence")
        else:
            issues.extend(compare_validity(item, persisted, scope).issues)
        return ReadCheck(1, tuple(issues))

    def check_incomplete_batch(self, repository: Factor4ReadRepository, sample: ValiditySample,
                               missing_ref: str) -> ReadCheck:
        """Keep incomplete historical evidence and a missing factor isolated in a real batch.

        A suppressed incomplete row requires VALIDITY_SCOPE_NOT_FOUND; an exposed
        row is independently re-read and checked against its actual DB summary FK.
        The absent factor requires FACTOR_NOT_FOUND, and protocol/DB errors propagate.
        """
        args = validity_arguments(sample, "ts", explicit_run=True)
        present_ref = args.pop("factor_ref")
        args["factor_refs"] = [present_ref, missing_ref]
        page = read_tool_page(self.summaries.api.validity_batch(args))
        by_ref = {item.get("factor_ref"): item for item in page.items}
        issues = []
        if len(page.items) != 2 or set(by_ref) != {present_ref, missing_ref}:
            issues.append("incomplete_batch:membership")
        present = by_ref.get(present_ref, {})
        if present.get("success") is True:
            issues.extend(self._compare_exposed_validity(repository, sample, "ts", present.get("data"),
                                                         explicit_run=True).issues)
        elif present.get("success") is not False or not isinstance(present.get("error"), dict) or present["error"].get("code") != "VALIDITY_SCOPE_NOT_FOUND" or present.get("data"):
            issues.append("incomplete_batch:unexpected_suppression")
        absent = by_ref.get(missing_ref, {})
        if absent.get("success") is not False or not isinstance(absent.get("error"), dict) or absent["error"].get("code") != "FACTOR_NOT_FOUND" or absent.get("data"):
            issues.append("incomplete_batch:absent_error_not_isolated")
        return ReadCheck(2, tuple(issues))

    def check_incomplete_metrics(self, repository: Factor4ReadRepository, sample: ValiditySample, scope: str) -> ReadCheck:
        """Verify metric visibility independently of a missing validity FK, against the same-run DB row.

        A missing FK does not imply metrics are absent. The real summary table is
        queried independently, and only actual missing metric evidence may be hidden.
        DB, transport and contract errors propagate; no synthetic metrics are supplied.
        """
        expected = repository.summaries_for_validity_scope(sample, scope)
        response = self._call("metrics", self._arguments(sample, scope, "metrics"))
        if not expected:
            return self._absent(response)
        rows = read_tool_page(response).data.get("ic_summaries")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ReadContractError("independent metric response must contain an ic_summaries array")
        return compare_summary(rows, list(expected))

    def check_metric_batch_input(self, sample: ValiditySample, *, duplicate: bool) -> ReadCheck:
        """Exercise deferred empty and duplicate metric-batch inputs with explicit assertions.

        Empty requires INVALID_ARGUMENT. Duplicate refs may be rejected or deduplicated
        to one correct successful item, as the original accepted contract states.
        No unrelated error can count as a pass; protocol/transport failures propagate.
        """
        ref = f"sub_factor:{sample.validity['factor_id']}"
        response = self.summaries.api.metrics_batch((ref, ref) if duplicate else (),
            summary_scope(sample.summaries["ts"]), as_of=datetime.now(timezone.utc).isoformat(),
            run_id=sample.validity["run_id"])
        if response.is_tool_error:
            return self._absent(response, codes=frozenset({"INVALID_ARGUMENT"}))
        page = read_tool_page(response)
        if not duplicate:
            return ReadCheck(1, ("metric_batch:empty_input_not_rejected",))
        if len(page.items) != 1 or page.items[0].get("factor_ref") != ref or page.items[0].get("success") is not True:
            return ReadCheck(1, ("metric_batch:duplicate_not_deduplicated",))
        data = page.items[0].get("data")
        if not isinstance(data, dict):
            raise ReadContractError("deduplicated metric batch did not expose metric data")
        return compare_summary([data], [sample.summaries["ts"]])

    def check_validity_batch_peers(self, peers: tuple[ValiditySample, ...], missing_ref: str) -> ReadCheck:
        """Compare two same-scope validity singles and their mixed batch with an absent ref.

        Every successful batch result matches its own DB row and explicit single.
        Missing natural peers raise ReadPrecondition; malformed responses propagate.
        """
        if len(peers) < 2 or peers[0].validity["factor_id"] == peers[1].validity["factor_id"]:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: validity batch needs two distinct same-scope factors")
        peers = peers[:2]
        issues = []
        for sample in peers:
            issues.extend(self.check_exact_evidence(sample, "cs", "validity").issues)
        args = validity_arguments(peers[0], "cs")
        args.pop("factor_ref")
        refs = [f"sub_factor:{sample.validity['factor_id']}" for sample in peers] + [missing_ref]
        args["factor_refs"] = refs
        page = read_tool_page(self.summaries.api.validity_batch(args))
        by_ref = {item.get("factor_ref"): item for item in page.items}
        if len(page.items) != 3 or set(by_ref) != set(refs):
            issues.append("validity_batch:peer_membership")
        for ref, sample in zip(refs, peers):
            item = by_ref.get(ref, {})
            if item.get("success") is not True or not isinstance(item.get("data"), dict):
                issues.append("validity_batch:peer_failed")
            else:
                issues.extend(compare_validity(item["data"], sample, "cs").issues)
        absent = by_ref.get(missing_ref, {})
        error = absent.get("error")
        if absent.get("success") is not False or not isinstance(error, dict) or error.get("code") != "FACTOR_NOT_FOUND" or absent.get("data"):
            issues.append("validity_batch:peer_absent_not_isolated")
        return ReadCheck(3, tuple(issues))
