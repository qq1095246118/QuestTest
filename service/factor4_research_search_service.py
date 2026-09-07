"""Research factor_search filtering and stats, independently checked against DB summaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from db.factor4_read_repository import Factor4ReadRepository, SummarySample, ValiditySample
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_page
from service.factor4_summary_service import Factor4SummaryService, _LOCAL, _equal_number, _number, _time, compare_summary, summary_scope

_THRESHOLDS = {"min_icir": "icir", "min_rank_icir": "rank_icir", "min_score": "final_score"}
_FIELDS = ("coverage_mean", "icir", "rank_icir", "oos_icir", "rank_oos_icir", "final_score", "valid_slice_count")


class Factor4ResearchSearchService:
    """Search, paginate and reconcile research candidates; never substitute factor_rank."""

    def __init__(self, summaries: Factor4SummaryService) -> None:
        """Keep the initialized summary service; no I/O and no exceptions."""
        self.summaries = summaries

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
        stats = read_tool_page(self.summaries.api.research_stats(scope, kind=sample.kind,
            as_of=sample.as_of.isoformat()))
        if stats.data.get("total") != len(sample.rows):
            issues.append("research:stats_total")
        return ReadCheck(max(1, len(sample.rows)), tuple(sorted(set(issues))),
                         {"expected_count": len(expected), "actual_count": len(actual)})

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
