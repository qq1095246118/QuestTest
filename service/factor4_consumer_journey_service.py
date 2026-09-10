"""Public scope discovery and selected-result reads, with independent DB oracles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import timedelta, timezone
from typing import Any, Literal

from api.factor4_formula_api import Factor4FormulaAPI
from db.factor4_consumer_repository import Factor4ConsumerRepository
from db.factor4_read_repository import SUMMARY_KEYS, SliceSample, SummarySample
from service.factor4_formula_service import compare_exact_formula_output
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_page
from service.factor4_summary_service import (
    Factor4SummaryService, _equal_number, _time, compare_rank, compare_summary,
    compare_validity, summary_scope,
)

Entry = Literal["search", "rank"]
_SEARCH_METRICS = ("coverage_mean", "icir", "rank_icir", "oos_icir", "rank_oos_icir", "final_score", "valid_slice_count")


class Factor4ConsumerJourneyService:
    """Carry public selectors through the user's read workflow without DB backfill."""

    def __init__(self, summaries: Factor4SummaryService, repository: Factor4ConsumerRepository) -> None:
        """Retain the gated API and read-only repository; no I/O or exceptions."""
        self.summaries = summaries
        self.repository = repository
        self.formulas = Factor4FormulaAPI(summaries.api.mcp)

    def check_discovered_selection(self, seed: SummarySample, entry: Entry) -> ReadCheck:
        """Discover a public scope, select a real result and read its final evidence.

        The seed supplies only initial kind, interval, universe, TS/CS, shape and
        fixed as_of. Every later request uses public response fields. DB reads are
        independent expectations, never replacement request parameters. Returns
        checked stages and explicit missing evidence; business issues take priority.
        Invalid entry raises ValueError; malformed responses and DB errors propagate.
        """
        if entry not in {"search", "rank"}:
            raise ValueError("consumer entry must be search or rank")
        as_of = seed.as_of.isoformat()
        page = read_tool_page(self.summaries.api.list_scopes(
            kind=seed.kind, ic_scope=seed.scope["ic_scope"],
            interval=seed.scope["factor_bar_interval"], universe_key=seed.scope["universe_key"],
            as_of=as_of, limit=100))
        issues: list[str] = []
        blocked: list[str] = []
        stages: list[str] = ["scope_discovery"]
        for row in page.items:
            if any(key not in row for key in SUMMARY_KEYS):
                issues.append("consumer:scope_missing_public_selector")
            if row.get("kind") != seed.kind or any(row.get(key) != seed.scope[key] for key in
                    ("ic_scope", "factor_bar_interval", "universe_key")):
                issues.append("consumer:scope_initial_filter_mismatch")
        if issues:
            return ReadCheck(1, tuple(dict.fromkeys(issues)), {"stages": tuple(stages)})
        eligible = [row for row in page.items if all(key in row for key in SUMMARY_KEYS)
                    and bool(row["symbol"]) == bool(seed.scope["symbol"])]
        if not eligible:
            if page.meta.get("truncated"):
                blocked.append("BLOCKED_DATA_PRECONDITION: requested shape outside capped public scope page")
            else:
                issues.append("consumer:known_available_shape_missing_from_scopes")
            return ReadCheck(1, tuple(issues), {"blocked": tuple(blocked), "stages": tuple(stages)})
        # Choose solely from the public response; prefer a bounded candidate set.
        selected_scope = min(eligible, key=lambda row: (
            int(row.get("available_factor_count") or 0), str(tuple(row[key] for key in SUMMARY_KEYS))))
        public_scope = {key: selected_scope[key] for key in SUMMARY_KEYS}
        scope = summary_scope(public_scope)
        oracle = self.repository.summaries_at(SummarySample(seed.kind, seed.as_of, public_scope, (), ()), seed.as_of)
        if not oracle.rows:
            return ReadCheck(1, (*issues, "consumer:public_scope_has_no_visible_db_results"), {"stages": tuple(stages)})
        if entry == "rank":
            selection = read_tool_page(self.summaries.api.rank(scope, kind=seed.kind, as_of=as_of,
                ranking_mode="raw_signed", metric="mean_ic", top_k=2, bottom_k=0))
            ranked = compare_rank(selection, oracle, ranking_mode="raw_signed", metric="mean_ic", top_k=2, bottom_k=0)
            issues.extend(ranked.issues)
            items = selection.data["top_items"]
        else:
            selection = read_tool_page(self.summaries.api.research_search(scope, kind=seed.kind, as_of=as_of, limit=2))
            items = list(selection.items)
            if not 0 < len(items) <= 2:
                issues.append("consumer:search_selection_cardinality")
            by_ref = {f"{seed.kind}:{row['factor_id']}": row for row in oracle.rows}
            if len({row.get("factor_ref") for row in items}) != len(items):
                issues.append("consumer:search_duplicate_factor")
            for item in items:
                row = by_ref.get(item.get("factor_ref"))
                if row is None or item.get("kind") != seed.kind or item.get("id") != row["factor_id"]:
                    issues.append("consumer:search_factor_identity")
                    continue
                if item.get("metric_run_id") != row["run_id"]:
                    issues.append("consumer:search_run_identity")
                for field in ("factor_bar_interval", "scoring_version"):
                    if item.get(field) != row[field]:
                        issues.append("consumer:search_scope=" + field)
                if item.get("metric_period_end") is not None:
                    if row.get("period_end") is None or _time(item["metric_period_end"]) != _time(row["period_end"], timezone.utc):
                        issues.append("consumer:search_metric_period")
                for field in _SEARCH_METRICS:
                    if field not in item or (item[field] is None or row.get(field) is None) and item[field] is not row.get(field):
                        issues.append("consumer:search_metric=" + field)
                    elif item[field] is not None and not _equal_number(item[field], row[field]):
                        issues.append("consumer:search_metric=" + field)
        stages.append(entry)
        if issues or not items:
            return ReadCheck(len(stages), tuple(dict.fromkeys(issues)), {"stages": tuple(stages)})
        selected = items[0]
        ref = selected["factor_ref"]
        run = selected["run_id" if entry == "rank" else "metric_run_id"]
        expected = next(row for row in oracle.rows if f"{seed.kind}:{row['factor_id']}" == ref)
        evidence = {"entry": entry, "factor_ref": ref, "run_id": run, "scope": public_scope}
        try:
            expected_periods = self.repository.selected_run_summaries(expected, seed.as_of)
            if not expected_periods:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: selected Run results changed during journey")
            metrics = read_tool_page(self.summaries.api.metrics(ref, scope, as_of=as_of, run_id=run))
            returned = metrics.data.get("ic_summaries")
            if not isinstance(returned, list) or any(not isinstance(row, dict) for row in returned):
                raise ReadContractError("consumer metrics requires ic_summaries object array")
            issues.extend(compare_summary(returned, expected_periods).issues)
            if metrics.data.get("factor_ref") != ref:
                issues.append("consumer:metrics_factor_identity")
            resolved = metrics.data.get("resolved_scope")
            for key, value in asdict(scope).items():
                if key == "interval":
                    continue
                if not isinstance(resolved, Mapping) or key not in resolved or resolved[key] != value:
                    issues.append("consumer:resolved_scope=" + key)
            if entry == "rank":
                matching = [row for row in returned if row.get("id") == selected["metric_id"]]
                if len(matching) != 1 or not _equal_number(matching[0].get("mean_ic"), selected["raw_metric_value"]):
                    issues.append("consumer:rank_to_metrics_binding")
            else:
                matching = [row for row in returned if row.get("run_id") == run and all(
                    row.get(field) is None and selected.get(field) is None
                    or _equal_number(row.get(field), selected.get(field)) for field in _SEARCH_METRICS)]
                if selected.get("metric_period_end") is not None:
                    matching = [row for row in matching if row.get("period_end") is not None
                                and _time(row["period_end"]) == _time(selected["metric_period_end"])]
                if not matching:
                    issues.append("consumer:search_to_metrics_binding")
                elif len(matching) > 1:
                    blocked.append("BLOCKED_DOC: public search summary does not uniquely identify a metric period")
            stages.append("metrics")
            if not issues and len(matching) == 1:
                public = matching[0]
                period = next(row for row in expected_periods if row["id"] == public["id"])
                tail = self._read_evidence(public, period, ref, run, public_scope, seed)
                issues.extend(tail.issues)
                blocked.extend(tail.evidence.get("blocked", ()))
                stages.extend(tail.evidence.get("stages", ()))
        except ReadPrecondition as error:
            blocked.append(str(error))
        except ReadContractError as error:
            issues.append(str(error))
        return ReadCheck(len(stages), tuple(dict.fromkeys(issues)), {
            **evidence, "blocked": tuple(blocked), "stages": tuple(stages)})

    def _read_evidence(self, public: dict[str, Any], expected: dict[str, Any], ref: str, run: str,
                       scope: dict[str, Any], seed: SummarySample) -> ReadCheck:
        persisted = self.repository.selected_result_evidence(expected, seed.as_of)
        issues: list[str] = []
        blocked: list[str] = []
        stages: list[str] = []
        args = {**asdict(summary_scope(scope)), "factor_ref": ref, "run_id": run,
                "as_of": seed.as_of.isoformat()}
        validity_args = {key: value for key, value in args.items() if key != "ic_scope"}
        validity_args["validity_scope"] = scope["ic_scope"]
        side = "ts" if scope["ic_scope"] == "time_series" else "cs"
        if len(persisted.validities) != 1:
            blocked.append("BLOCKED_DATA_PRECONDITION: selected result lacks unique visible validity evidence")
        else:
            try:
                data = read_tool_page(self.summaries.api.validity(validity_args)).data.get("item")
                if not isinstance(data, Mapping):
                    raise ReadContractError("consumer validity requires an item object")
                issues.extend(compare_validity(data, persisted.validities[0], side).issues)
                stages.append("validity")
            except ReadPrecondition as error:
                blocked.append(str(error))
            except ReadContractError as error:
                issues.append(str(error))
        if len(persisted.formulas) != 1:
            blocked.append("BLOCKED_DATA_PRECONDITION: selected result lacks unique visible formula evidence")
        else:
            try:
                # The formula request uses the public summary, not persisted evidence.
                data = read_tool_page(self.formulas.formula(public, as_of=seed.as_of.isoformat())).data
                issues.extend(compare_exact_formula_output(persisted.formulas[0], data).issues)
                stages.append("formula")
            except ReadPrecondition as error:
                blocked.append(str(error))
            except ReadContractError as error:
                issues.append(str(error))
        try:
            if public.get("period_start") is None or public.get("period_end") is None:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: selected result has no public slice period")
            start = _time(public["period_start"])
            end = min(_time(public["period_end"]) + timedelta(days=1), seed.as_of)
            if end <= _time(public["period_end"]):
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: selected result period has not fully elapsed")
            sample = self.repository.slices_for_summary(expected, discover_symbol=False)
            rows = tuple(row for row in sample.rows if _time(row["slice_start"], timezone.utc) >= start
                         and _time(row["slice_end"], timezone.utc) < end
                         and _time(row["as_of_time"], timezone.utc) <= seed.as_of) if sample is not None else ()
            if not rows:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: selected result lacks slices in the public scope and period")
            check = self.summaries.check_metric_slices(SliceSample(public, rows), limit=7,
                as_of=seed.as_of.isoformat(), time_range=(start.isoformat(), end.isoformat()))
            issues.extend(check.issues)
            stages.append("slices")
        except ReadPrecondition as error:
            blocked.append(str(error))
        except ReadContractError as error:
            issues.append(str(error))
        return ReadCheck(len(stages), tuple(issues), {"blocked": tuple(blocked), "stages": tuple(stages)})
