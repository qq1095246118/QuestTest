"""Rank admission filters use current latest DB rows, never historical aggregate maxima."""

from dataclasses import replace
from decimal import Decimal

from db.factor4_read_repository import SummarySample
from service.factor4_read_service import ReadCheck, ReadPrecondition, read_tool_body, read_tool_page
from service.factor4_summary_service import Factor4SummaryService, _number, compare_rank, summary_scope


class Factor4RankFilterService:
    """Apply independently computed final-metric filters to actual factor_rank responses."""

    def __init__(self, summaries: Factor4SummaryService) -> None:
        """Retain the initialized summary service; no I/O or exceptions."""
        self.summaries = summaries

    def check_filters(self, sample: SummarySample, variant: str, *,
                       memberships: dict[str, frozenset[int]] | None = None) -> ReadCheck:
        """Check inclusive slices/coverage, OOS and theme filters with exact DB candidates.

        The maximum slice count comes only from current latest rows. Missing natural
        numeric/theme evidence raises ReadPrecondition, invalid variants raise
        ValueError, and transport/contract errors propagate. Empty eligible sets are
        successful only if the actual endpoint returns a legal empty rank response.
        """
        if not sample.rows:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no rank filter candidates")
        kwargs = {}
        pool = list(sample.rows)
        metric = "final_score" if variant == "final_score_empty" else "mean_ic"
        if variant == "baseline":
            pass
        elif variant in {"slices_equal", "slices_above", "final_score_empty"}:
            maximum = max(int(row.get("valid_slice_count") or 0) for row in pool)
            kwargs["min_valid_slice_count"] = maximum + int(variant != "slices_equal")
        elif variant in {"coverage_median", "coverage_one"}:
            values = sorted(_number(row["coverage_mean"]) for row in pool if row.get("coverage_mean") is not None)
            if not values:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no rank coverage values")
            kwargs["min_coverage_mean"] = 1.0 if variant == "coverage_one" else float(values[len(values) // 2])
        elif variant == "require_oos":
            kwargs["require_oos"] = True
        elif variant in {"theme_hit", "theme_miss"}:
            memberships = memberships or {}
            nonempty = [key for key, ids in memberships.items() if ids]
            if variant == "theme_hit":
                if not nonempty:
                    raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: selected rank factors have no inherited theme")
                key = sorted(nonempty, key=lambda key: (-len(memberships[key]), key))[0]
                pool = [row for row in pool if row["factor_id"] in memberships[key]]
            else:
                key = "questtest_no_such_theme"
                while key in memberships:
                    key += "_absent"
                pool = []
            kwargs["theme"] = key
        else:
            raise ValueError("unsupported rank filter variant")
        evaluated_count = len(pool)
        eligible = []
        for row in pool:
            try:
                _number(row.get(metric))
            except (ValueError, ArithmeticError):
                continue
            if int(row.get("valid_slice_count") or 0) < kwargs.get("min_valid_slice_count", 0):
                continue
            if _number(row.get("coverage_mean") or 0) < Decimal(str(kwargs.get("min_coverage_mean", 0))):
                continue
            if kwargs.get("require_oos") and not (row.get("oos_period_start") and row.get("oos_period_end")
                    and (row.get("oos_icir") is not None or row.get("rank_oos_icir") is not None)):
                continue
            eligible.append(row)
        page = read_tool_page(self.summaries.api.rank(summary_scope(sample.scope), kind=sample.kind,
            as_of=sample.as_of.isoformat(), metric=metric, ranking_mode="raw_signed", top_k=2, bottom_k=1, **kwargs))
        issues = []
        if eligible:
            issues.extend(compare_rank(page, replace(sample, rows=tuple(eligible)), ranking_mode="raw_signed",
                metric=metric, top_k=2, bottom_k=1).issues)
        elif page.data.get("top_items") != [] or page.data.get("bottom_items") != [] or page.data.get("returned_count") != 0:
            issues.append("rank_filter:expected_empty")
        if page.data.get("candidate_count") != len(eligible):
            issues.append("rank_filter:candidate_count")
        if page.data.get("evaluated_count") != evaluated_count:
            issues.append("rank_filter:evaluated_count")
        return ReadCheck(len(sample.rows), tuple(issues), {"expected_candidates": len(eligible)})

    def check_no_requested_side_rejected(self, sample: SummarySample) -> ReadCheck:
        """Both zero-sized sides must return INVALID_ARGUMENT, as prior adjudication established.

        Sends the actual request, requires no rank items in its rejection, and returns
        issues on unexpected business behavior. Protocol/transport errors propagate.
        """
        response = self.summaries.api.rank(summary_scope(sample.scope), kind=sample.kind,
            as_of=sample.as_of.isoformat(), top_k=0, bottom_k=0, ranking_mode="raw_signed")
        body = read_tool_body(response)
        error, data = body.get("error"), body.get("data")
        rejected = response.is_tool_error and isinstance(error, dict) and error.get("code") == "INVALID_ARGUMENT"
        no_rows = not isinstance(data, dict) or not (data.get("top_items") or data.get("bottom_items"))
        return ReadCheck(1, () if rejected and no_rows else ("rank_filter:zero_sides_not_rejected",))
