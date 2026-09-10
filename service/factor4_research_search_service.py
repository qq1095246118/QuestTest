"""Research factor_search filtering and stats, independently checked against DB summaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import combinations
import re
from typing import Any

from api.factor4_read_api import Factor4ReadAPI
from db.factor4_read_repository import Factor4ReadRepository, ResearchCatalogSnapshot, SummarySample, ValiditySample
from service.factor4_read_service import Factor4ReadService, PageTraversal, ReadCheck, ReadContractError, ReadPrecondition, read_tool_page
from service.factor4_summary_service import Factor4SummaryService, _LOCAL, _equal_number, _number, _time, compare_summary, summary_scope

_THRESHOLDS = {"min_icir": "icir", "min_rank_icir": "rank_icir", "min_score": "final_score"}
_FIELDS = ("coverage_mean", "icir", "rank_icir", "oos_icir", "rank_oos_icir", "final_score", "valid_slice_count")


def _matching_rows(rows: tuple[dict[str, Any], ...], filters: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    matched = []
    for row in rows:
        if "query" in filters and not any(filters["query"].casefold() in str(row.get(key) or "").casefold()
                                         for key in ("name", "cn_name")):
            continue
        if "validity" in filters and row.get("validity_status") != filters["validity"]:
            continue
        try:
            if any(_number(row.get(_THRESHOLDS[key])) < Decimal(str(value))
                   for key, value in filters.items() if key in _THRESHOLDS):
                continue
        except (TypeError, ValueError, ArithmeticError):
            continue
        matched.append(row)
    return tuple(matched)


def _combination_plan(snapshot: ResearchCatalogSnapshot, scenario: str) -> dict[str, Any]:
    rows = tuple(row for row in snapshot.rows if row.get("metric_id") is not None)
    queries = sorted({term.casefold() for row in rows for term in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", str(row.get("name") or ""))})
    for query in queries:
        for validity in ("valid", "invalid", "unknown"):
            base = {"query": query, "validity": validity}
            candidates = _matching_rows(rows, base)
            if len(candidates) < 2:
                continue
            for left, right in combinations(_THRESHOLDS, 2):
                values: dict[str, list[float]] = {}
                for key in (left, right):
                    finite: set[float] = set()
                    for row in candidates:
                        try:
                            finite.add(float(_number(row.get(_THRESHOLDS[key]))))
                        except (TypeError, ValueError, ArithmeticError):
                            continue
                    values[key] = sorted(finite)
                for first in values[left]:
                    for second in values[right]:
                        filters = {**base, left: first, right: second}
                        chosen = {row["factor_id"] for row in _matching_rows(rows, filters)}
                        if bool(chosen) != (scenario != "empty"):
                            continue
                        # Both metric gates need independent counterexamples;
                        # query and status retain their existing standalone coverage.
                        alternatives = [{row["factor_id"] for row in _matching_rows(rows, {k: v for k, v in filters.items() if k != removed})}
                                        for removed in (left, right)]
                        if all(chosen < alternative for alternative in alternatives):
                            return filters
    raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no " + scenario
                           + " research intersection with independent two-metric witnesses")


def _compare_research_members(traversal: PageTraversal, expected: tuple[dict[str, Any], ...],
                              kind: str, *, validity: bool) -> ReadCheck:
    issues = list(traversal.issues)
    blocked = list(traversal.blocked)
    by_ref = {f"{kind}:{row['factor_id']}": row for row in expected}
    refs = [row.get("factor_ref") for row in traversal.rows]
    if len(refs) != len(set(refs)):
        issues.append("research:duplicate_factor")
    if set(refs) - set(by_ref) or traversal.termination == "complete" and set(refs) != set(by_ref):
        issues.append("research:filtered_membership")
    for item in traversal.rows:
        row = by_ref.get(item.get("factor_ref"))
        if row is None:
            continue
        if item.get("id") != row["factor_id"] or item.get("kind") != kind:
            issues.append("research:factor_identity")
        if item.get("metric_run_id") != row.get("run_id"):
            issues.append("research:not_latest_metric_run")
        if validity:
            if item.get("validity_status") != row.get("validity_status"):
                issues.append("research_validity:member_status")
            if item.get("validity_run_id") != row.get("validity_run_id"):
                issues.append("research_validity:member_run")
        if row.get("metric_id") is None:
            if any(item.get(field) is not None for field in _FIELDS):
                issues.append("research_validity:unknown_member_has_unbacked_metric")
            continue
        for field in ("factor_bar_interval", "scoring_version"):
            if item.get(field) != row.get(field):
                issues.append(f"research:scope={field}")
        for field in _FIELDS:
            if field not in item:
                issues.append(f"research:missing_{field}")
            elif item[field] is None or row.get(field) is None:
                if item[field] is not row.get(field):
                    issues.append(f"research:value={field}")
            elif not _equal_number(item[field], row[field]):
                issues.append(f"research:value={field}")
    if traversal.termination != "complete":
        blocked.append("research_membership_not_fully_read:" + traversal.termination)
    return ReadCheck(max(1, len(traversal.rows)), tuple(dict.fromkeys(issues)), {
        "blocked": tuple(dict.fromkeys(blocked)), "expected_count": len(expected),
        "actual_count": len(traversal.rows), "traversal": traversal.termination,
    })


class Factor4ResearchSearchService:
    """Search, paginate and reconcile research candidates; never substitute factor_rank."""

    def __init__(self, summaries: Factor4SummaryService) -> None:
        """Keep the initialized summary service; no I/O and no exceptions."""
        self.summaries = summaries

    def _search_pages(self, sample: SummarySample, filters: dict[str, Any], *, maximum_members: int) -> PageTraversal:
        reads = Factor4ReadService(Factor4ReadAPI(self.summaries.api.mcp))
        return reads._traverse(lambda cursor: self.summaries.api.research_search(
            summary_scope(sample.scope), kind=sample.kind, as_of=sample.as_of.isoformat(),
            limit=50, cursor=cursor, **filters), page_size=50,
            max_pages=max(2, maximum_members // 50 + 2), catalog_budget=True)

    def _readback_validity(self, snapshot: ResearchCatalogSnapshot,
                           actual: tuple[dict[str, Any], ...], expected: tuple[dict[str, Any], ...]) -> ReadCheck:
        by_ref = {f"{snapshot.sample.kind}:{row['factor_id']}": row for row in expected}
        selected = next(((item, by_ref[item["factor_ref"]]) for item in actual
                         if item.get("factor_ref") in by_ref
                         and by_ref[item["factor_ref"]].get("validity_id") is not None), None)
        evidence: dict[str, Any] = {"research_validity_readback_count": 0,
                                    "research_validity_readback_status": "not_applicable",
                                    "research_validity_readback_reason": "no_persisted_validity_for_returned_members"}
        if selected is None:
            if any(row.get("validity_id") is not None for row in expected):
                evidence.update(research_validity_readback_status="blocked",
                                research_validity_readback_reason="no_returned_member_with_persisted_validity",
                                blocked=("research_validity_readback:no_returned_member_with_persisted_validity",))
            return ReadCheck(0, evidence=evidence)
        item, row = selected
        evidence["research_validity_readback_factor_ref"] = item["factor_ref"]
        run_id = item.get("validity_run_id")
        if not isinstance(run_id, str) or not run_id:
            evidence.update(research_validity_readback_status="blocked",
                            research_validity_readback_reason="search_did_not_return_validity_run_id",
                            blocked=("research_validity_readback:missing_public_run_selector",))
            return ReadCheck(0, evidence=evidence)
        arguments = asdict(summary_scope(snapshot.sample.scope))
        arguments["validity_scope"] = arguments.pop("ic_scope")
        arguments.update(factor_ref=item["factor_ref"], run_id=run_id, as_of=snapshot.sample.as_of.isoformat())
        evidence.update(research_validity_readback_status="failed",
                        research_validity_readback_reason="read_failed")
        try:
            returned = read_tool_page(self.summaries.api.validity(arguments)).data.get("item")
            if not isinstance(returned, Mapping):
                raise ReadContractError("validity readback requires a data.item object")
        except ReadPrecondition as error:
            evidence.update(research_validity_readback_status="blocked", blocked=(str(error),))
            return ReadCheck(0, evidence=evidence)
        except ReadContractError as error:
            return ReadCheck(0, ("research_validity:readback:" + str(error),), evidence)
        identity = {"id": row["validity_id"], "metric_id": row["metric_id"], "factor_id": row["factor_id"],
                    "run_id": row["validity_run_id"], "validity_status": row["validity_status"]}
        issues = tuple("research_validity:readback:" + field for field, value in identity.items()
                       if field not in returned or returned[field] != value)
        evidence.update(research_validity_readback_count=1,
                        research_validity_readback_status="failed" if issues else "verified",
                        research_validity_readback_reason="identity_or_status_mismatch" if issues else "same_scope_persisted_validity")
        return ReadCheck(1, issues, evidence)

    def check_explicit_validity_members(self, snapshot: ResearchCatalogSnapshot, validity: str) -> ReadCheck:
        """Verify search members, same-scope statistics and one public-selector validity replay.

        Unknown catalog entities without a summary remain members, with no invented
        metric evidence; absent persisted validity makes only the replay not applicable.
        A returned ref/validity Run drives a real readback, independently checked against
        the captured validity/metric IDs, status and factor identity. A declared cursor
        budget retains checked members and marks
        full membership blocked; it is not retried. Missing positive state samples
        are reported after actual failures. Ambiguous DB evidence raises
        ReadPrecondition; invalid status raises ValueError; protocol errors propagate.
        """
        if validity not in {"valid", "invalid", "unknown"}:
            raise ValueError("unsupported explicit validity status")
        if snapshot.ambiguous_count:
            raise ReadPrecondition("BLOCKED_ORACLE_AMBIGUITY: multiple validity rows reference a latest summary")
        expected = tuple(row for row in snapshot.rows if row["validity_status"] == validity)
        counts = {status: sum(row["validity_status"] == status for row in snapshot.rows)
                  for status in ("valid", "invalid", "unknown")}
        traversal = self._search_pages(snapshot.sample, {"validity": validity}, maximum_members=len(expected))
        members = _compare_research_members(traversal, expected, snapshot.sample.kind, validity=True)
        issues, blocked = list(members.issues), list(members.evidence["blocked"])
        try:
            stats = self.check_explicit_validity_stats(snapshot.sample, counts, validity)
            issues.extend(stats.issues)
        except ReadPrecondition as error:
            blocked.append(str(error))
        except ReadContractError as error:
            issues.append("research_validity:statistics:" + str(error))
        readback = self._readback_validity(snapshot, traversal.rows, expected)
        issues.extend(readback.issues)
        blocked.extend(readback.evidence.get("blocked", ()))
        if not expected:
            blocked.append("positive_research_validity_sample_missing:" + validity)
        return ReadCheck(members.checked_count + readback.checked_count, tuple(dict.fromkeys(issues)), {
            **members.evidence, **readback.evidence, "blocked": tuple(dict.fromkeys(blocked)), "validity": validity,
            "statistics_share_exact_scope": True,
        })

    def check_combined_filters(self, snapshot: ResearchCatalogSnapshot, scenario: str) -> ReadCheck:
        """Check a discriminating query/status/two-threshold intersection or its relaxation.

        Hit and empty scenarios require a natural witness for each metric gate;
        relaxed executes the hit request followed by removing each condition alone.
        Exact members are compared with fixed final results, not the API's previous
        response. No numeric thresholds are sent to catalog_stats. Missing witnesses
        raise ReadPrecondition before requests; unsupported scenario raises ValueError;
        protocol errors propagate and prior result mismatches are not hidden by budget.
        """
        if scenario not in {"hit", "empty", "relaxed"}:
            raise ValueError("unsupported research intersection scenario")
        if snapshot.ambiguous_count:
            raise ReadPrecondition("BLOCKED_ORACLE_AMBIGUITY: multiple validity rows reference a latest summary")
        filters = _combination_plan(snapshot, scenario)
        variants = [("combined", filters)]
        if scenario == "relaxed":
            variants.extend(("without_" + key, {k: v for k, v in filters.items() if k != key}) for key in filters)
        issues: list[str] = []
        blocked: list[str] = []
        actual_sets: dict[str, set[str]] = {}
        checked = 0
        for name, arguments in variants:
            expected = _matching_rows(snapshot.rows, arguments)
            try:
                traversal = self._search_pages(snapshot.sample, arguments, maximum_members=len(expected))
            except ReadContractError as error:
                issues.append(name + ":" + str(error))
                continue
            result = _compare_research_members(traversal, expected, snapshot.sample.kind, validity=True)
            checked += result.checked_count
            issues.extend(name + ":" + issue for issue in result.issues)
            blocked.extend(name + ":" + reason for reason in result.evidence["blocked"])
            if traversal.termination == "complete":
                actual_sets[name] = {row.get("factor_ref") for row in traversal.rows}
        for name, members in actual_sets.items():
            if name != "combined" and "combined" in actual_sets:
                strict = name.removeprefix("without_") in _THRESHOLDS
                monotone = actual_sets["combined"] < members if strict else actual_sets["combined"] <= members
                if not monotone:
                    issues.append(name + ":research:relaxation_did_not_expand")
        return ReadCheck(checked, tuple(dict.fromkeys(issues)), {
            "blocked": tuple(dict.fromkeys(blocked)), "scenario": scenario,
            "request_count": len(variants), "independent_filter_witnesses": tuple(key for key in filters if key in _THRESHOLDS),
        })

    def check_parent_research_and_rank(self, sample: SummarySample) -> ReadCheck:
        """Search and rank completed parent child_aggregate results without child substitution.

        Reuses complete candidate and ranking oracles with one raw-signed Top side,
        so no direction or Top/Bottom overlap contract is added. Invalid fixture
        kind/mode raises ValueError; missing parent candidates raise ReadPrecondition;
        protocol failures propagate. Neither computation nor publishing is invoked.
        """
        if sample.kind != "factor" or sample.scope.get("calculation_mode") != "child_aggregate":
            raise ValueError("parent research requires factor child_aggregate scope")
        search = self.check_research_search(sample)
        if search.issues or search.evidence.get("blocked"):
            return search
        rank = self.summaries.check_rank(sample, ranking_mode="raw_signed", top_k=2, bottom_k=0)
        return ReadCheck(search.checked_count + rank.checked_count, (*search.issues, *rank.issues))

    def check_research_search(self, sample: SummarySample, *, threshold: str | None = None,
                               above_maximum: bool = False) -> ReadCheck:
        """Compare filtered search membership and unfiltered catalog_stats with latest DB rows.

        A DB-derived median threshold exercises inclusive filtering; above-maximum
        must produce an empty terminal page. Every returned factor/run and exposed
        metric is reconciled, with bounded cursor traversal. Missing numeric data
        raises ReadPrecondition; malformed responses raise ReadContractError.
        Statistics do not receive numeric thresholds because their schema lacks them.
        """
        if threshold is not None and threshold not in _THRESHOLDS:
            raise ValueError("unsupported research threshold")
        expected = list(sample.rows)
        kwargs: dict[str, Any] = {}
        if threshold is not None:
            field = _THRESHOLDS[threshold]
            finite = []
            for row in expected:
                try:
                    finite.append(_number(row.get(field)))
                except (ValueError, ArithmeticError):
                    continue
            if not finite:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no finite research threshold candidates")
            boundary = max(finite) + Decimal("0.00000001") if above_maximum else sorted(finite)[len(finite) // 2]
            # Match the actual JSON number sent, including float serialization.
            kwargs[threshold] = float(boundary)
            boundary = Decimal(str(kwargs[threshold]))
            expected = [row for row in expected if row.get(field) is not None and _number(row[field]) >= boundary]
        scope = summary_scope(sample.scope)
        cursor = None
        seen_cursors: set[str] = set()
        actual = []
        issues = []
        for _ in range(100):
            page = read_tool_page(self.summaries.api.research_search(scope, kind=sample.kind,
                as_of=sample.as_of.isoformat(), limit=5, cursor=cursor, **kwargs))
            if len(page.items) > 5:
                issues.append("research:page_limit")
            actual.extend(page.items)
            cursor = page.meta.get("next_cursor")
            if not cursor:
                if page.meta.get("truncated") is True:
                    issues.append("research:terminal_truncated")
                break
            if not isinstance(cursor, str) or cursor in seen_cursors or not page.items:
                raise ReadContractError("research cursor did not advance")
            seen_cursors.add(cursor)
        else:
            raise ReadContractError("research search did not terminate within safety bound")
        by_ref = {f"{sample.kind}:{row['factor_id']}": row for row in expected}
        refs = [row.get("factor_ref") for row in actual]
        if len(refs) != len(set(refs)):
            issues.append("research:duplicate_factor")
        if set(refs) != set(by_ref):
            issues.append("research:filtered_membership")
        for item in actual:
            row = by_ref.get(item.get("factor_ref"))
            if row is None:
                continue
            if item.get("id") != row["factor_id"] or item.get("kind") != sample.kind:
                issues.append("research:factor_identity")
            if item.get("metric_run_id") != row["run_id"]:
                issues.append("research:not_latest_metric_run")
            for field in ("factor_bar_interval", "scoring_version"):
                if item.get(field) != row[field]:
                    issues.append(f"research:scope={field}")
            for field in _FIELDS:
                if field not in item:
                    issues.append(f"research:missing_{field}")
                elif item[field] is None or row.get(field) is None:
                    if item[field] is not row.get(field):
                        issues.append(f"research:value={field}")
                elif not _equal_number(item[field], row[field]):
                    issues.append(f"research:value={field}")
        blocked = []
        try:
            stats = read_tool_page(self.summaries.api.research_stats(scope, kind=sample.kind,
                as_of=sample.as_of.isoformat()))
            if stats.data.get("total") != len(sample.rows):
                issues.append("research:stats_total")
        except ReadPrecondition as error:
            blocked.append(str(error))
        except ReadContractError as error:
            issues.append("research:statistics:" + str(error))
        return ReadCheck(max(1, len(sample.rows)), tuple(sorted(set(issues))),
                         {"expected_count": len(expected), "actual_count": len(actual), "blocked": tuple(blocked)})

    def check_research_completion(self, repository: Factor4ReadRepository, sample: SummarySample, offset: int) -> ReadCheck:
        """Rebuild DB candidates around a genuine completion and query search/stats at that time.

        Offset is -1/0/1 microseconds; no natural lifecycle time raises ReadPrecondition.
        DB and protocol errors propagate. Historical latest selection is independent
        of current API IDs and does not use a previously generated report.
        """
        if offset not in {-1, 0, 1}:
            raise ValueError("completion offset must be -1, 0 or 1")
        times = [_time(row["run_completed_at"], _LOCAL) for row in sample.rows if row.get("run_completed_at")]
        if not times:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no research run completion time")
        snapshot = repository.summaries_at(sample, max(times) + timedelta(microseconds=offset))
        return self.check_research_search(snapshot)

    def check_mixed_metric_batch(self, sample: SummarySample, missing_ref: str, *, good_count: int = 2) -> ReadCheck:
        """Two/three existing factors and one absent preserve latest identities in a metric batch.

        Each good item is compared with its own latest same-scope DB summary, while
        absent identity must return item-level FACTOR_NOT_FOUND. Missing natural
        candidates raise ReadPrecondition; malformed protocol responses propagate.
        """
        if good_count not in {2, 3}:
            raise ValueError("mixed metric good_count must be two or three")
        if len(sample.rows) < good_count:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: mixed metric batch lacks distinct candidates")
        chosen = sample.rows[:good_count]
        good_refs = [f"{sample.kind}:{row['factor_id']}" for row in chosen]
        refs = [*good_refs, missing_ref]
        page = read_tool_page(self.summaries.api.metrics_batch(tuple(refs), summary_scope(sample.scope), as_of=sample.as_of.isoformat()))
        by_ref = {item.get("factor_ref"): item for item in page.items}
        issues = []
        if len(page.items) != len(refs) or set(by_ref) != set(refs):
            issues.append("mixed_metrics:membership")
        for ref, row in zip(good_refs, chosen):
            item = by_ref.get(ref, {})
            if item.get("success") is not True or not isinstance(item.get("data"), dict):
                issues.append("mixed_metrics:good_item_failed")
            else:
                issues.extend(compare_summary([item["data"]], [row]).issues)
        absent = by_ref.get(missing_ref, {})
        error = absent.get("error")
        if absent.get("success") is not False or not isinstance(error, dict) or error.get("code") != "FACTOR_NOT_FOUND" or absent.get("data"):
            issues.append("mixed_metrics:absent_error_not_isolated")
        return ReadCheck(len(refs), tuple(issues))

    def check_explicit_validity_stats(self, sample: SummarySample, counts: dict[str, int], validity: str,
                                       *, ambiguous_count: int = 0) -> ReadCheck:
        """Compare explicit valid/invalid/unknown total and grouped count with the DB left-join oracle.

        Ambiguous linked validity records raise an oracle precondition, not a claimed
        API defect. Unsupported status raises ValueError; protocol failures propagate.
        """
        if validity not in {"valid", "invalid", "unknown"}:
            raise ValueError("unsupported explicit validity status")
        if ambiguous_count:
            raise ReadPrecondition("BLOCKED_ORACLE_AMBIGUITY: multiple validity rows reference a latest summary")
        page = read_tool_page(self.summaries.api.research_stats(summary_scope(sample.scope), kind=sample.kind,
            as_of=sample.as_of.isoformat(), validity=validity))
        groups = page.data.get("groups")
        if not isinstance(groups, list) or any(not isinstance(item, dict) for item in groups):
            raise ReadContractError("research validity stats require structured groups")
        grouped = sum(int(row.get("count") or 0) for row in groups if row.get("kind") == sample.kind and row.get("validity") == validity)
        expected = counts[validity]
        issues = []
        if page.data.get("total") != expected:
            issues.append("research_validity:total")
        if grouped != expected:
            issues.append("research_validity:grouped_count")
        return ReadCheck(max(1, sum(counts.values())), tuple(issues), {"expected_count": expected})

    def check_overall_one_dimension(self, sample: ValiditySample, name: str) -> ReadCheck:
        """Overall valid research search must include a latest TS-only or CS-only factor.

        The discovered target is checked across all name-filtered pages for same metric
        and validity Run and all three status projections. No TS/CS intersection rule
        is invented. Invalid fixture flags raise ValueError; protocol failures propagate.
        """
        validity = sample.validity
        if int(bool(validity.get("time_series_is_valid"))) + int(bool(validity.get("cross_sectional_is_valid"))) != 1:
            raise ValueError("overall one-dimension fixture must have exactly one valid dimension")
        scope = summary_scope(sample.summaries["cs"])
        cursor = None
        seen = set()
        targets = []
        as_of = datetime.now(timezone.utc).isoformat()
        for _ in range(100):
            page = read_tool_page(self.summaries.api.research_search(scope, kind="sub_factor", as_of=as_of,
                validity="valid", validity_scope="overall", query=name, limit=5, cursor=cursor))
            targets.extend(item for item in page.items if item.get("id") == validity["factor_id"])
            cursor = page.meta.get("next_cursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen or not page.items:
                raise ReadContractError("overall research cursor did not advance")
            seen.add(cursor)
        else:
            raise ReadContractError("overall research query did not terminate")
        if len(targets) != 1:
            return ReadCheck(1, ("overall_search:one_dimension_target_missing_or_duplicate",))
        item = targets[0]
        issues = []
        for key in ("metric_run_id", "validity_run_id"):
            if item.get(key) != validity["run_id"]:
                issues.append(f"overall_search:{key}")
        for key in ("time_series_status", "cross_sectional_status", "overall_status", "validity_status"):
            if item.get(key) != validity.get("overall_status" if key == "validity_status" else key):
                issues.append(f"overall_search:{key}")
        return ReadCheck(1, tuple(issues))
