#!/usr/bin/env python3
"""Resume the read-only Factor 4.0 metric-slice scope checks.

The probe reuses a previously confirmed rolling fixture, verifies that it is
still present in the test database, and reconciles TS-symbol, TS-aggregate,
and CS-aggregate MCP pages against the exact database rows.  The known slice
``end_time`` equality behavior is deliberately excluded: every control range
ends one day after the final persisted slice.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tmp.critical_readonly_gap_probe import (  # noqa: E402
    BLOCKING_CODES,
    MCPClient,
    MCP_URL,
    data,
    error_code,
    meta,
    rows,
    successful,
    write_json,
)
from tmp.slice_reconcile_probe import (  # noqa: E402
    TEST_HOST_PREFIX,
    db_connection,
    db_snapshot_rows,
    discover_scope,
    equal,
    identity_mismatches,
    period_iso,
    table_watermark,
)


TOKEN = os.environ.get("MCP_TOKEN") or os.environ.get("FACTOR4_MCP_TOKEN")
SCOPE_ORDER = ("ts_symbol", "ts_aggregate", "cs_aggregate")
SCOPE_LABELS = {
    "ts_symbol": "TS-SYMBOL",
    "ts_aggregate": "TS-AGGREGATE",
    "cs_aggregate": "CS-AGGREGATE",
}
RESOLVED_SCOPE_FIELDS = (
    "ic_scope",
    "calculation_mode",
    "interval",
    "factor_window_bars",
    "return_bar_interval",
    "forward_return_bars",
    "universe_key",
    "window_scope",
    "symbol",
    "scoring_version",
)


def scope_kind(ic_scope: str, symbol: str) -> str | None:
    """Map one persisted IC/symbol pair to a supported test scope kind."""

    if ic_scope == "time_series" and symbol:
        return "ts_symbol"
    if ic_scope == "time_series" and not symbol:
        return "ts_aggregate"
    if ic_scope == "cross_sectional" and not symbol:
        return "cs_aggregate"
    return None


def discover_related_scopes() -> dict[str, dict[str, Any]]:
    """Verify and expand the known TS-symbol fixture into three exact scopes.

    Returns:
        A mapping keyed by ``ts_symbol``, ``ts_aggregate``, and
        ``cs_aggregate`` when corresponding slice and summary rows exist.

    Raises:
        RuntimeError: If the database query cannot be executed.
    """

    base = discover_scope()
    if not base:
        return {}
    connection = db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                """
                SELECT s.ic_scope, COALESCE(s.symbol,'') AS symbol,
                       COUNT(*) AS row_count, MIN(s.slice_start) AS min_start,
                       MAX(s.slice_end) AS max_end
                FROM factor_ic_slice_metrics s
                JOIN factor_ic_runs r ON r.run_id=s.run_id AND r.status='completed'
                WHERE s.run_id=%s AND s.factor_id=%s AND s.is_sub_factor_id=1
                  AND s.calculation_mode=%s
                  AND s.factor_bar_interval=%s AND s.factor_window_bars=%s
                  AND s.return_bar_interval=%s AND s.forward_return_bars=%s
                  AND s.universe_key=%s AND s.window_scope=%s
                  AND (
                    (s.ic_scope='time_series' AND COALESCE(s.symbol,'') IN ('', %s))
                    OR (s.ic_scope='cross_sectional' AND COALESCE(s.symbol,'')='')
                  )
                GROUP BY s.ic_scope, COALESCE(s.symbol,'')
                """,
                (
                    base["run_id"],
                    base["factor_id"],
                    base["calculation_mode"],
                    base["factor_bar_interval"],
                    base["factor_window_bars"],
                    base["return_bar_interval"],
                    base["forward_return_bars"],
                    base["universe_key"],
                    base["window_scope"],
                    base["symbol"],
                ),
            )
            groups = [dict(row) for row in cursor.fetchall()]
            discovered: dict[str, dict[str, Any]] = {}
            for group in groups:
                kind = scope_kind(str(group["ic_scope"]), str(group.get("symbol") or ""))
                if not kind:
                    continue
                scope = {
                    **base,
                    "ic_scope": group["ic_scope"],
                    "symbol": group.get("symbol") or "",
                    "row_count": int(group["row_count"]),
                    "min_start": group["min_start"],
                    "max_end": group["max_end"],
                }
                cursor.execute(
                    """
                    SELECT scoring_version
                    FROM factor_ic_summary_metrics
                    WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=1
                      AND ic_scope=%s AND calculation_mode=%s
                      AND factor_bar_interval=%s AND factor_window_bars=%s
                      AND return_bar_interval=%s AND forward_return_bars=%s
                      AND universe_key=%s AND COALESCE(symbol,'')=%s
                      AND window_scope=%s
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    (
                        scope["run_id"],
                        scope["factor_id"],
                        scope["ic_scope"],
                        scope["calculation_mode"],
                        scope["factor_bar_interval"],
                        scope["factor_window_bars"],
                        scope["return_bar_interval"],
                        scope["forward_return_bars"],
                        scope["universe_key"],
                        scope["symbol"],
                        scope["window_scope"],
                    ),
                )
                summary = cursor.fetchone()
                if summary and summary.get("scoring_version"):
                    scope["scoring_version"] = summary["scoring_version"]
                    discovered[kind] = scope
            return discovered
    finally:
        connection.rollback()
        connection.close()


def request_args(
    scope: dict[str, Any],
    db_rows: list[dict[str, Any]],
    *,
    limit: int,
) -> dict[str, Any]:
    """Build one exact slice request with the excluded end boundary avoided."""

    return {
        "factor_ref": f"sub_factor:{scope['factor_id']}",
        "ic_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": str(scope["factor_window_bars"]),
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scoring_version": scope["scoring_version"],
        "run_id": scope["run_id"],
        "symbol": scope["symbol"],
        "start_time": period_iso(db_rows[0]["slice_start"]),
        "end_time": period_iso(db_rows[-1]["slice_end"] + timedelta(days=1)),
        "limit": limit,
    }


def expected_resolved_scope(scope: dict[str, Any]) -> dict[str, Any]:
    """Return the expected MCP resolved-scope representation."""

    return {
        "ic_scope": scope["ic_scope"],
        "calculation_mode": scope["calculation_mode"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": str(scope["factor_window_bars"]),
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "symbol": scope["symbol"],
        "scoring_version": scope["scoring_version"],
    }


def compact_call(call: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize one MCP call without persisting a large response twice."""

    return {
        "http_status": call.get("http_status") if call else None,
        "success": successful(call),
        "error_code": error_code(call),
        "item_count": len(rows(call)),
        "item_ids": [item.get("id") for item in rows(call)],
        "next_cursor": bool(meta(call).get("next_cursor")),
    }


def run_scope(
    client: MCPClient,
    kind: str,
    scope: dict[str, Any],
    db_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    """Walk and reconcile all pages for one exact slice scope."""

    label = SCOPE_LABELS[kind]
    args = request_args(scope, db_rows, limit=7)
    calls: list[dict[str, Any]] = []
    pages: list[list[dict[str, Any]]] = []
    cursors_seen: set[str] = set()
    first_cursor: str | None = None
    current_cursor: str | None = None
    terminal = False
    loop_error: str | None = None
    for page_number in range(1, 21):
        call_args = dict(args)
        if current_cursor:
            call_args["cursor"] = current_cursor
        call = client.tool(
            f"SLICE-{label}-PAGE-{page_number}",
            "factor_get_metric_slices",
            call_args,
        )
        calls.append(call)
        page_rows = rows(call)
        pages.append(page_rows)
        if not successful(call):
            loop_error = error_code(call) or call.get("parse_error") or "MCP_CALL_FAILED"
            break
        next_cursor = meta(call).get("next_cursor")
        if page_number == 1 and next_cursor:
            first_cursor = str(next_cursor)
        if next_cursor and str(next_cursor) in cursors_seen:
            loop_error = "CURSOR_REPEATED"
            break
        if next_cursor:
            cursors_seen.add(str(next_cursor))
            current_cursor = str(next_cursor)
            continue
        terminal = True
        break

    returned = [item for page in pages for item in page]
    db_by_id = {str(item["id"]): item for item in db_rows}
    field_mismatches: list[dict[str, Any]] = []
    for item in returned:
        db_row = db_by_id.get(str(item.get("id")))
        mismatches = ["missing_db_row"] if not db_row else identity_mismatches(item, db_row)
        if mismatches:
            field_mismatches.append({"id": item.get("id"), "fields": mismatches})
    api_scope = data(calls[0]).get("resolved_scope") if calls else {}
    api_scope = api_scope if isinstance(api_scope, dict) else {}
    expected_scope = expected_resolved_scope(scope)
    scope_mismatches = [
        field
        for field in RESOLVED_SCOPE_FIELDS
        if not equal(api_scope.get(field), expected_scope[field])
    ]
    returned_ids = [item.get("id") for item in returned]
    expected_ids = [item["id"] for item in db_rows]
    monotonic = all(
        (str(left.get("as_of_time")), int(left.get("id")))
        <= (str(right.get("as_of_time")), int(right.get("id")))
        for left, right in zip(returned, returned[1:])
    )
    evidence = {
        "scope_kind": kind,
        "scope": {
            key: scope.get(key)
            for key in (
                "run_id",
                "factor_id",
                "ic_scope",
                "calculation_mode",
                "factor_bar_interval",
                "factor_window_bars",
                "return_bar_interval",
                "forward_return_bars",
                "universe_key",
                "symbol",
                "window_scope",
                "scoring_version",
            )
        },
        "request_range": {
            "start_time": args["start_time"],
            "end_time": args["end_time"],
            "end_boundary_avoided": True,
        },
        "page_counts": [len(page) for page in pages],
        "page_limits_respected": all(len(page) <= int(args["limit"]) for page in pages),
        "terminal_page_reached": terminal,
        "cursor_count": len(cursors_seen),
        "loop_error": loop_error,
        "returned_count": len(returned),
        "db_count": len(db_rows),
        "returned_ids": returned_ids,
        "expected_ids": expected_ids,
        "no_duplicates": len(returned_ids) == len(set(returned_ids)),
        "monotonic": monotonic,
        "field_mismatches": field_mismatches,
        "resolved_scope_mismatches": scope_mismatches,
        "first_call": compact_call(calls[0] if calls else None),
    }
    return calls, first_cursor, {"args": args, "evidence": evidence}


def main() -> None:
    """Execute the resumed slice checks and write sanitized evidence."""

    if not TOKEN:
        raise SystemExit("MCP_TOKEN or FACTOR4_MCP_TOKEN is required")
    if not MCP_URL.startswith(TEST_HOST_PREFIX):
        raise SystemExit("test MCP host gate failed")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-resume" / f"{stamp}-slice-scopes"
    output.mkdir(parents=True, exist_ok=False)
    cases: list[dict[str, Any]] = []

    def record(
        case_id: str,
        status: str,
        expected: str,
        actual: Any,
        *,
        call: dict[str, Any] | None = None,
        note: str = "",
    ) -> None:
        """Append one compact case verdict."""

        cases.append(
            {
                "case_id": case_id,
                "module": "factor.metrics.slices",
                "status": status,
                "expected": expected,
                "actual": actual,
                "call": compact_call(call) if call else None,
                "note": note,
            }
        )

    scopes = discover_related_scopes()
    fixture_rows = {kind: db_snapshot_rows(scope) for kind, scope in scopes.items()}
    before = {kind: table_watermark(scope) for kind, scope in scopes.items()}
    write_json(
        output / "fixture.json",
        {
            "scopes": scopes,
            "row_counts": {kind: len(value) for kind, value in fixture_rows.items()},
            "row_ids": {kind: [row["id"] for row in value] for kind, value in fixture_rows.items()},
        },
    )

    client = MCPClient(TOKEN, output)
    init = client.request(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-resume-slice-scopes", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    client.protocol_version = str(init_result.get("protocolVersion") or "") or None
    init_ok = successful(init) and client.protocol_version is not None
    record(
        "MCP-INIT",
        "PASS" if init_ok else "BLOCKED",
        "test MCP initializes with protocol 2025-06-18",
        {"protocol_version": client.protocol_version, "server": init_result.get("serverInfo")},
        call=init,
    )
    if init_ok:
        notify = client.request("MCP-NOTIFY", "notifications/initialized", {})
        record(
            "MCP-NOTIFY",
            "PASS" if notify.get("http_status") in {200, 202, 204} else "FAIL",
            "initialized notification is accepted",
            {"http_status": notify.get("http_status")},
            call=notify,
        )

    cursor_by_scope: dict[str, str] = {}
    args_by_scope: dict[str, dict[str, Any]] = {}
    if init_ok:
        for kind in SCOPE_ORDER:
            scope = scopes.get(kind)
            db_rows = fixture_rows.get(kind) or []
            label = SCOPE_LABELS[kind]
            if not scope or len(db_rows) < 8:
                record(
                    f"SLICE-{label}-FIXTURE",
                    "BLOCKED",
                    "completed exact scope has at least eight persisted slice rows",
                    {"scope_found": bool(scope), "row_count": len(db_rows)},
                )
                continue
            calls, first_cursor, result = run_scope(client, kind, scope, db_rows)
            evidence = result["evidence"]
            args_by_scope[kind] = result["args"]
            if first_cursor:
                cursor_by_scope[kind] = first_cursor
            call_blocked = next(
                (call for call in calls if error_code(call) in BLOCKING_CODES),
                None,
            )
            if call_blocked:
                status = "BLOCKED"
            else:
                passed = (
                    all(successful(call) for call in calls)
                    and evidence["page_limits_respected"]
                    and evidence["terminal_page_reached"]
                    and evidence["returned_ids"] == evidence["expected_ids"]
                    and evidence["no_duplicates"]
                    and evidence["monotonic"]
                    and not evidence["field_mismatches"]
                    and not evidence["resolved_scope_mismatches"]
                )
                status = "PASS" if passed else "FAIL"
            record(
                f"SLICE-{label}-RECONCILE",
                status,
                "every row is returned once, ordered, paginated, exact-scope, and field-identical to DB",
                evidence,
                call=calls[0] if calls else None,
                note="The request end_time is after the last slice; the excluded equality boundary cannot affect this verdict.",
            )

        binding_pairs = (
            ("ts_symbol", "ts_aggregate"),
            ("ts_aggregate", "cs_aggregate"),
            ("cs_aggregate", "ts_symbol"),
        )
        for source, target in binding_pairs:
            source_cursor = cursor_by_scope.get(source)
            target_args = args_by_scope.get(target)
            case_id = f"SLICE-CURSOR-BIND-{SCOPE_LABELS[source]}-TO-{SCOPE_LABELS[target]}"
            if not source_cursor or not target_args:
                record(
                    case_id,
                    "BLOCKED",
                    "a signed cursor cannot cross from the source scope to the target scope",
                    {"source_cursor_present": bool(source_cursor), "target_scope_present": bool(target_args)},
                )
                continue
            changed_args = {**target_args, "cursor": source_cursor}
            changed_call = client.tool(case_id, "factor_get_metric_slices", changed_args)
            rejected = (
                not successful(changed_call)
                and error_code(changed_call) == "INVALID_ARGUMENT"
                and not rows(changed_call)
            )
            record(
                case_id,
                "PASS" if rejected else "FAIL",
                "a signed cursor cannot cross from the source scope to the target scope",
                {
                    "source_scope": source,
                    "target_scope": target,
                    "error_code": error_code(changed_call),
                    "returned_ids": [item.get("id") for item in rows(changed_call)],
                },
                call=changed_call,
            )

    after = {kind: table_watermark(scope) for kind, scope in scopes.items()}
    unchanged = before == after
    record(
        "SLICE-DB-READ-ONLY",
        "PASS" if unchanged else "FAIL",
        "the targeted slice scope counts and creation watermarks remain unchanged",
        {"before": before, "after": after, "unchanged": unchanged},
    )
    counts = Counter(case["status"] for case in cases)
    report = {
        "run_id": stamp,
        "environment": "test",
        "mcp_url": MCP_URL,
        "mode": "READ_ONLY",
        "status_counts": dict(sorted(counts.items())),
        "excluded": {
            "slice_end_time_equality_boundary": (
                "Excluded by current test agreement; requests use an end_time after the final slice."
            )
        },
        "cases": cases,
        "new_failures": [case for case in cases if case["status"] == "FAIL"],
        "blocked_cases": [case for case in cases if case["status"] == "BLOCKED"],
        "sensitive_values_written": False,
    }
    write_json(output / "results.json", report)
    write_json(output / "call-ledger.json", client.calls)
    lines = [
        "# Resumed metric-slice scope checks",
        "",
        "- Environment: `test`; database mode: `READ_ONLY` with rollback",
        f"- Counts: `{dict(sorted(counts.items()))}`",
        "- Excluded: exact `end_time == slice_end` boundary; all reconciliation controls end after the final slice.",
        "",
        "| Case | Status | Expected |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| {case['case_id']} | {case['status']} | {case['expected']} |" for case in cases
    )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output_dir": str(output),
                "status_counts": dict(sorted(counts.items())),
                "scope_rows": {kind: len(value) for kind, value in fixture_rows.items()},
                "db_unchanged": unchanged,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
