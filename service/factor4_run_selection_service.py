"""Same-partition historical run selection; no temporary script/report dependencies."""

from __future__ import annotations

from datetime import datetime, timezone

from db.factor4_read_repository import SUMMARY_KEYS, Factor4ReadRepository, SummarySample, ValiditySample
from service.factor4_read_service import ReadCheck, ReadPrecondition
from service.factor4_recommendation_service import lifecycle_time
from service.factor4_summary_service import Factor4SummaryService
from service.factor4_validity_service import Endpoint, Factor4ValidityService
from service.factor4_slice_service import Factor4SliceService


class Factor4RunSelectionService:
    """Compose summary and validity reads against independently discovered DB revisions."""

    def __init__(self, summaries: Factor4SummaryService) -> None:
        """Retain the initialized summary service; performs no I/O and raises no errors."""
        self.summaries = summaries

    @staticmethod
    def latest_validity(
        candidates: tuple[ValiditySample, ...], sample: ValiditySample, scope: str, as_of: datetime,
    ) -> ValiditySample:
        """Resolve an unambiguous visible default validity without inventing Run precedence.

        Candidates must come from the independently read full exact partition.
        Compare completion recency and validity-publication recency separately;
        differing winners or missing scope/time evidence raise ReadPrecondition,
        because the retained tool contract does not define that disagreement.
        This method performs no I/O and never filters out invalid validity flags.
        """
        if scope not in {"ts", "cs"} or as_of.tzinfo is None:
            raise ValueError("latest validity requires ts/cs scope and aware as_of")
        expected = sample.summaries[scope]
        visible: list[tuple[ValiditySample, datetime, datetime]] = []
        for candidate in candidates:
            row = candidate.validity
            try:
                completed = lifecycle_time(row.get("run_completed_at"), database=True)
                published = lifecycle_time(row.get("updated_at"), database=True)
            except (TypeError, ValueError):
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: validity candidate availability missing") from None
            if max(completed, published) > as_of:
                continue
            summary = candidate.summaries.get(scope)
            if not summary:
                raise ReadPrecondition("BLOCKED_DOC: latest validity candidate scope cannot be resolved without its summary")
            if any(summary.get(key) != expected.get(key) for key in ("factor_id", "is_sub_factor_id", *SUMMARY_KEYS)):
                continue
            visible.append((candidate, completed, published))
        if not visible:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no visible exact-scope validity candidate")
        completion = max(item[1] for item in visible)
        publication = max(item[2] for item in visible)
        by_completion = {item[0].validity.get("id") for item in visible if item[1] == completion}
        by_publication = {item[0].validity.get("id") for item in visible if item[2] == publication}
        if len(by_completion) != 1 or by_completion != by_publication:
            raise ReadPrecondition("BLOCKED_DOC: default validity Run precedence differs between completion and validity publication")
        identifier = next(iter(by_completion))
        return next(item[0] for item in visible if item[0].validity.get("id") == identifier)

    def check_run_selection(
        self, history: tuple[ValiditySample, ...], scope: str, *, older: bool,
        repository: Factor4ReadRepository,
    ) -> ReadCheck:
        """Compare endpoint-specific default selection or one explicitly requested older Run.

        Requires two distinct runs in one exact partition. Missing natural data raises
        ReadPrecondition, invalid scope raises ValueError, protocol errors propagate.
        Default metrics use the established summary selector independently of validity
        update order; default validity requires an unambiguous candidate. Contradictions
        are returned before any document precondition can hide a metrics failure.
        """
        if scope not in {"ts", "cs"}:
            raise ValueError("scope must be ts or cs")
        if len(history) < 2:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no two completed runs in one exact partition")
        try:
            ordered = sorted(history, key=lambda item: lifecycle_time(item.validity.get("run_completed_at"), database=True), reverse=True)
        except (TypeError, ValueError):
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: completed Run ordering evidence missing") from None
        latest, previous = ordered[:2]
        if latest.validity.get("run_id") == previous.validity.get("run_id"):
            raise ValueError("run selection requires two distinct runs")
        left, right = latest.summaries[scope], previous.summaries[scope]
        for key in ("factor_id", "is_sub_factor_id", *SUMMARY_KEYS):
            if left.get(key) != right.get(key):
                raise ValueError("run selection histories are not the same exact partition")
        chosen = previous if older else latest
        row = chosen.summaries[scope]
        as_of = datetime.now(timezone.utc)
        kind = "sub_factor" if row.get("is_sub_factor_id") else "factor"
        metric = SummarySample(kind, as_of, {key: row[key] for key in SUMMARY_KEYS}, (row,), ())
        if not older:
            independently_selected = repository.summaries_at(metric, as_of)
            rows = tuple(item for item in independently_selected.rows if item.get("factor_id") == row.get("factor_id"))
            if not rows:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no latest completed summary for requested factor")
            metric = SummarySample(kind, as_of, dict(metric.scope), rows, ())
        metric_check = self.summaries.check_metrics(metric, explicit_run=older)
        if not older:
            try:
                chosen = self.latest_validity(repository.validity_candidates(chosen, scope, as_of), chosen, scope, as_of)
            except ReadPrecondition as error:
                if metric_check.issues:
                    return ReadCheck(metric_check.checked_count, metric_check.issues, {"blocked": str(error)})
                raise
        checks = (metric_check, self.summaries.check_validity(chosen, scope, as_of=as_of.isoformat(), explicit_run=older))
        return ReadCheck(sum(check.checked_count for check in checks),
                         tuple(issue for check in checks for issue in check.issues))

    def check_default_argument_variant(
        self, repository: Factor4ReadRepository, sample: ValiditySample, endpoint: Endpoint, variant: str,
    ) -> ReadCheck:
        """Resolve the existing default/null Run matrix independently for each endpoint.

        Only run_omitted/run_null are accepted (ValueError otherwise). Metrics read
        completed summaries independently; validity compares all visible candidates
        and blocks unknown ordering. Return original variant findings at one fixed
        as_of. Repository, MCP and documented precondition exceptions propagate.
        """
        if variant not in {"run_omitted", "run_null"} or endpoint not in {"metrics", "validity"}:
            raise ValueError("default argument selection requires metrics/validity and omitted/null Run")
        as_of = datetime.now(timezone.utc)
        service = Factor4ValidityService(self.summaries)
        if endpoint == "validity":
            selected = self.latest_validity(repository.validity_candidates(sample, "ts", as_of), sample, "ts", as_of)
            return service.check_argument_variant(selected, endpoint, variant, as_of=as_of.isoformat())
        row = sample.summaries["ts"]
        kind = "sub_factor" if row.get("is_sub_factor_id") else "factor"
        scope = SummarySample(kind, as_of, {key: row[key] for key in SUMMARY_KEYS}, (row,), ())
        selected = repository.summaries_at(scope, as_of)
        rows = tuple(item for item in selected.rows if item.get("factor_id") == row.get("factor_id"))
        if not rows:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no latest completed summary for default Run matrix")
        expected = SummarySample(kind, as_of, dict(scope.scope), rows, ())
        return service.check_argument_variant(sample, endpoint, variant, as_of=as_of.isoformat(), metric_expected=expected)

    def check_default_slice_run(self, repository: Factor4ReadRepository) -> ReadCheck:
        """Check omitted-Run TS slices using their actual symbol and latest summary.

        Discover a real slice scope independently of validity, then resolve the
        default completed summary before fetching its slices without changing the
        symbol. Missing current slices block instead of falling back to older Runs.
        Returns existing slice-page assertions at the same fixed as_of; repository
        and protocol exceptions propagate, and no data is synthesized or written.
        """
        seed = repository.slice_sample("time_series", symbol_mode="symbol")
        if seed is None:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no actual TS-symbol slice scope")
        row, as_of = seed.summary, datetime.now(timezone.utc)
        kind = "sub_factor" if row.get("is_sub_factor_id") else "factor"
        scope = SummarySample(kind, as_of, {key: row[key] for key in SUMMARY_KEYS}, (row,), ())
        selected = repository.summaries_at(scope, as_of)
        rows = [item for item in selected.rows if item.get("factor_id") == row.get("factor_id")]
        if len(rows) != 1:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: latest exact-symbol summary is missing or ambiguous")
        sample = repository.slices_for_summary(rows[0], discover_symbol=False)
        if sample is None:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: latest completed Run has no slices in the exact symbol scope")
        return Factor4SliceService(self.summaries).check_snapshot_pages(
            repository, sample, explicit_run=False, as_of=as_of.isoformat())
