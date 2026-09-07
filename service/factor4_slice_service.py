"""Persisted slice continuation, query binding and read-snapshot consistency."""

from __future__ import annotations

from datetime import datetime, timezone

from db.factor4_read_repository import Factor4ReadRepository, SliceSample
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_body, read_tool_page
from service.factor4_summary_service import Factor4SummaryService, metric_slice_arguments
from service.factor4_summary_service import _time


class Factor4SliceService:
    """Compose bounded MCP slice reads; never treat rejected reads as successful data."""

    def __init__(self, summaries: Factor4SummaryService) -> None:
        """Keep the initialized summary service; no I/O and no exceptions."""
        self.summaries = summaries

    def check_snapshot_pages(self, repository: Factor4ReadRepository, sample: SliceSample,
                             *, repeat: bool = False, explicit_run: bool = True,
                             as_of: str | None = None) -> ReadCheck:
        """Reconcile all exact-scope pages, optionally twice, between scope watermarks.

        Shared DB changes raise SNAPSHOT_DRIFT (an inconclusive run), never a product
        mutation claim. Missing data and protocol errors propagate; mismatches return
        issues. Completed reads do not prove the absence of all external DB writes.
        A supplied aware as_of pins requests to independent default-Run discovery;
        an invalid/naive instant raises ValueError before reading a watermark.
        """
        if as_of is not None and datetime.fromisoformat(as_of.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("slice snapshot as_of must be timezone-aware")
        before = repository.slice_watermark(sample)
        checks = []
        as_of = as_of or datetime.now(timezone.utc).isoformat()
        failure: Exception | None = None
        try:
            for _ in range(2 if repeat else 1):
                checks.append(self.summaries.check_metric_slices(sample, limit=7,
                    explicit_run=explicit_run, as_of=as_of, resolved_scope=True))
        except (ReadPrecondition, ReadContractError) as exc:
            failure = exc
        finally:
            after = repository.slice_watermark(sample)
        if before != after:
            raise ReadPrecondition("SNAPSHOT_DRIFT: persisted slice scope changed during read; result is inconclusive")
        if failure is not None:
            raise failure
        return ReadCheck(sum(item.checked_count for item in checks),
                         tuple(issue for item in checks for issue in item.issues),
                         {"watermark_stable": True, "read_count": len(checks)})

    def check_cursor_binding(self, source: SliceSample, *, target: SliceSample | None = None,
                              mutation: str = "scope") -> ReadCheck:
        """Obtain a genuine cursor then verify scope/limit/symbol/signature binding.

        Needs >7 persisted source rows. No natural rows raises ReadPrecondition; a
        missing server continuation is a contract failure, not skipped. Transport or
        malformed payload errors propagate. Rejection requires INVALID_ARGUMENT and
        no business rows; unexpected server errors cannot pass this test.
        """
        if mutation not in {"scope", "limit", "symbol", "tamper"}:
            raise ValueError("unsupported cursor mutation")
        if len(source.rows) < 8:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: cursor source needs eight persisted rows")
        as_of = datetime.now(timezone.utc).isoformat()
        args = metric_slice_arguments(source, as_of=as_of)
        page = read_tool_page(self.summaries.api.metric_slices(dict(args)))
        cursor = page.meta.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or len(page.items) != 7:
            raise ReadContractError("slice cursor control did not return seven rows and a continuation")
        if mutation == "scope":
            if target is None:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: cursor target scope is absent")
            changed = metric_slice_arguments(target, as_of=as_of)
            if changed == args:
                raise ValueError("cursor binding requires a different target scope")
        else:
            changed = dict(args)
        changed["cursor"] = cursor
        if mutation == "limit":
            changed["limit"] = 6
        elif mutation == "symbol":
            changed["symbol"] = "" if args.get("symbol") else "QUESTTEST_NO_SUCH_SYMBOL"
        elif mutation == "tamper":
            # Change an interior byte, not base64 padding bits that can decode identically.
            middle = len(cursor) // 2
            changed["cursor"] = cursor[:middle] + ("A" if cursor[middle] != "A" else "B") + cursor[middle + 1:]
        response = self.summaries.api.metric_slices(changed)
        body = read_tool_body(response)
        error = body.get("error")
        data = body.get("data")
        issues = []
        if not response.is_tool_error or not isinstance(error, dict) or error.get("code") != "INVALID_ARGUMENT":
            issues.append("slices:cursor_binding_not_rejected")
        if isinstance(data, dict) and (data.get("items") or data.get("item") or data.get("ic_summaries")):
            issues.append("slices:rejected_cursor_exposed_data")
        return ReadCheck(1, tuple(issues))

    def check_exact_slice_end_boundary(self, sample: SliceSample) -> ReadCheck:
        """Execute the historically deferred exact slice_end equality contract.

        Uses the earliest persisted slice's range and compares the entire expected
        contained-row ID set, rather than assuming only one record shares that range.
        Missing data raises ReadPrecondition; request/protocol failures propagate.
        """
        if not sample.rows:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no exact slice-end fixture")
        first = sample.rows[0]
        start, end = (_time(first[key], timezone.utc) for key in ("slice_start", "slice_end"))
        expected = [row["id"] for row in sample.rows
                    if _time(row["slice_start"], timezone.utc) >= start and _time(row["slice_end"], timezone.utc) <= end]
        args = metric_slice_arguments(sample, limit=100)
        args.update(start_time=start.isoformat(), end_time=end.isoformat())
        page = read_tool_page(self.summaries.api.metric_slices(args))
        actual = [row.get("id") for row in page.items]
        return ReadCheck(len(expected), () if actual == expected and not page.meta.get("next_cursor")
                         else ("slices:exact_end_boundary_membership",))

    def check_run_completion(self, sample: SliceSample, completed: datetime, offset: int) -> ReadCheck:
        """Verify an explicit slice run at -1/0/+1 microsecond around run completion.

        Requires an aware completion time; invalid offsets/times raise ValueError.
        Before completion only a known missing-scope error or successful empty data
        is acceptable. At/after completion all same-run rows are DB-reconciled.
        Protocol errors propagate; missing natural slices raise ReadPrecondition.
        """
        from datetime import timedelta
        if offset not in {-1, 0, 1} or completed.tzinfo is None:
            raise ValueError("completion must be aware and offset must be -1, 0 or 1")
        instant = (completed + timedelta(microseconds=offset)).isoformat()
        if offset >= 0:
            return self.summaries.check_metric_slices(sample, limit=7, as_of=instant, resolved_scope=True)
        response = self.summaries.api.metric_slices(metric_slice_arguments(sample, as_of=instant))
        body = read_tool_body(response)
        error, data = body.get("error"), body.get("data")
        issues = []
        if response.is_tool_error:
            if not isinstance(error, dict) or error.get("code") not in {"METRIC_SCOPE_NOT_FOUND", "METRIC_SLICES_NOT_FOUND", "RUN_NOT_FOUND"}:
                issues.append("slices:unexpected_before_completion_error")
        elif not isinstance(data, dict):
            raise ReadContractError("pre-completion slices have neither explicit error nor data")
        if isinstance(data, dict) and data.get("items"):
            issues.append("slices:run_visible_before_completion")
        return ReadCheck(len(sample.rows), tuple(issues))
