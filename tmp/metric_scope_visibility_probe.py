#!/usr/bin/env python3
"""Probe point-in-time visibility and pagination of metric scopes.

The probe is read-only.  It discovers scope identities and completed runs from
the test database, then compares every bounded API page with the database
snapshot visible at the requested ``as_of`` time.  The endpoint has no cursor
for this resource, so a scope absent from a 100-row page is treated as
unobserved rather than as a negative result.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402
from tmp.catalog_deep_readonly import (  # noqa: E402
    MCP_URL,
    Runner,
    _data,
    _error_code,
    _items,
    _meta,
    _success,
    _write_json,
)


TOKEN_ENV = "SCOPE_MCP_TOKEN"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
UTC_TZ = timezone.utc
BLOCKING_CODES = {
    "AUTH_REQUIRED",
    "FORBIDDEN",
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE",
}

IDENTITY_KEYS = (
    "kind",
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


def parse_time(value: Any, naive_timezone: timezone | ZoneInfo = LOCAL_TZ) -> datetime | None:
    """Normalize a timestamp to an aware local datetime.

    ``completed_at`` is a MySQL local ``DATETIME`` in this environment, while
    metric period fields are stored as UTC wall-clock values.  Callers pass the
    appropriate interpretation for naive values explicitly.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_timezone)
    return parsed.astimezone(LOCAL_TZ)


def parse_period(value: Any) -> datetime | None:
    """Normalize a metric period timestamp whose naive DB value is UTC."""

    return parse_time(value, UTC_TZ)


def normalized(value: Any) -> Any:
    """Normalize scope scalar values for identity comparison."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def identity(row: dict[str, Any]) -> tuple[Any, ...]:
    """Build the scope identity excluding run-dependent fields."""

    return tuple(normalized(row.get(key)) for key in IDENTITY_KEYS)


def db_scope_groups(db: DatabaseClient) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    """Load completed run groups for sub-factor scopes covered by the API filter.

    Each group retains the distinct factor IDs present in that run.  The MCP
    ``available_factor_count`` is an across-run count, so the IDs are unioned
    after applying the point-in-time cutoff.
    """

    rows = db.fetch_all(
        """
        SELECT
            s.ic_scope,
            s.calculation_mode,
            s.factor_bar_interval,
            s.factor_window_bars,
            s.return_bar_interval,
            s.forward_return_bars,
            s.universe_key,
            COALESCE(s.symbol, '') AS symbol,
            s.window_scope,
            s.scoring_version,
            r.run_id,
            r.completed_at,
            COUNT(DISTINCT s.factor_id) AS available_factor_count,
            MAX(s.period_end) AS metric_period_end,
            GROUP_CONCAT(DISTINCT s.factor_id) AS factor_ids
        FROM factor_ic_summary_metrics AS s
        JOIN factor_ic_runs AS r ON r.run_id = s.run_id
        WHERE r.status = 'completed'
          AND s.is_sub_factor_id = 1
          AND s.ic_scope = 'time_series'
          AND s.factor_bar_interval = '1h'
          AND s.universe_key = 'all'
        GROUP BY
            s.ic_scope,
            s.calculation_mode,
            s.factor_bar_interval,
            s.factor_window_bars,
            s.return_bar_interval,
            s.forward_return_bars,
            s.universe_key,
            COALESCE(s.symbol, ''),
            s.window_scope,
            s.scoring_version,
            r.run_id,
            r.completed_at
        ORDER BY r.completed_at, r.run_id
        """
    )
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        api_shape = {**row, "kind": "sub_factor"}
        grouped[identity(api_shape)].append(dict(row))
    for values in grouped.values():
        values.sort(key=lambda row: (parse_time(row.get("completed_at")) or datetime.min.replace(tzinfo=LOCAL_TZ), str(row.get("run_id"))))
    return dict(grouped)


def expected_scope(groups: list[dict[str, Any]], as_of: datetime) -> dict[str, Any] | None:
    """Return the aggregate scope row visible at ``as_of``.

    The endpoint exposes the latest completed run timestamp, the union count of
    distinct factor IDs across all visible completed runs, and the latest
    metric period.  This is intentionally different from the per-run count
    retained by :func:`db_scope_groups`.
    """

    eligible = [row for row in groups if (parse_time(row.get("completed_at")) or datetime.max.replace(tzinfo=LOCAL_TZ)) <= as_of]
    if not eligible:
        return None
    latest = max(
        eligible,
        key=lambda row: (
            parse_time(row.get("completed_at")) or datetime.min.replace(tzinfo=LOCAL_TZ),
            str(row.get("run_id")),
        ),
    )
    factor_ids: set[str] = set()
    for row in eligible:
        raw_ids = row.get("factor_ids")
        if raw_ids is not None:
            factor_ids.update(item for item in str(raw_ids).split(",") if item)
    # ``GROUP_CONCAT`` is available on the real MySQL test DB.  Keep a
    # defensive fallback for an offline substitute that only exposes counts.
    if not factor_ids:
        factor_count = sum(int(row.get("available_factor_count") or 0) for row in eligible)
    else:
        factor_count = len(factor_ids)
    periods = [parse_period(row.get("metric_period_end")) for row in eligible]
    latest_period = max((period for period in periods if period is not None), default=None)
    result = dict(latest)
    result["kind"] = "sub_factor"
    result["available_factor_count"] = factor_count
    result["metric_period_end"] = latest_period
    return result


def row_matches_db(api_row: dict[str, Any], db_row: dict[str, Any]) -> list[str]:
    """Return field-level mismatches between one API scope and DB evidence."""

    mismatches: list[str] = []
    for key in IDENTITY_KEYS:
        if normalized(api_row.get(key)) != normalized(db_row.get(key)):
            mismatches.append(key)
    expected_completed = parse_time(db_row.get("completed_at"))
    actual_completed = parse_time(api_row.get("run_completed_at"))
    if expected_completed != actual_completed:
        mismatches.append("run_completed_at")
    try:
        if int(api_row.get("available_factor_count")) != int(db_row.get("available_factor_count")):
            mismatches.append("available_factor_count")
    except (TypeError, ValueError):
        mismatches.append("available_factor_count")
    expected_period = parse_period(db_row.get("metric_period_end"))
    actual_period = parse_period(api_row.get("metric_period_end"))
    if expected_period != actual_period:
        mismatches.append("metric_period_end")
    return mismatches


def page_mismatches(
    items: list[dict[str, Any]],
    groups: dict[tuple[Any, ...], list[dict[str, Any]]],
    as_of: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare all rows in one bounded API page with the DB snapshot.

    Returns ``(mismatches, future_rows)``.  A missing identity is a mismatch;
    an identity that exists in the DB but is not on this page is deliberately
    not considered a failure because the endpoint advertises truncation.
    """

    mismatches: list[dict[str, Any]] = []
    future_rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in items:
        item_identity = identity(item)
        if item_identity in seen:
            mismatches.append({"identity": item_identity, "fields": ["duplicate_identity"]})
        seen.add(item_identity)
        expected = expected_scope(groups.get(item_identity, []), as_of)
        if expected is None:
            mismatches.append({"identity": item_identity, "fields": ["missing_db_scope"]})
        else:
            fields = row_matches_db(item, expected)
            if fields:
                mismatches.append({"identity": item_identity, "fields": fields})
        completed = parse_time(item.get("run_completed_at"))
        if completed is not None and completed > as_of:
            future_rows.append(item)
    return mismatches, future_rows


def main() -> None:
    """Execute bounded metric-scope visibility checks and write evidence."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("FACTOR4_MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} or FACTOR4_MCP_TOKEN is required")
    settings = SettingsLoader.load("test", PROJECT_ROOT)
    if settings.environment != "test" or not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("test environment gate failed")

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = PROJECT_ROOT / "reports" / "factor4-deep" / f"{run_stamp}-metric-scope-visibility"
    db = DatabaseClient.from_settings(settings.database)
    groups = db_scope_groups(db)
    runner = Runner(token, output, db)
    cases: list[dict[str, Any]] = []

    init = runner.request(
        "MCP-INIT",
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest-scope-visibility", "version": "1.0"}},
    )
    runner.protocol_version = (((init.get("envelope") or {}).get("result") or {}).get("protocolVersion"))
    init_result = (init.get("envelope") or {}).get("result") or {}
    if not (init.get("http_status") == 200 and isinstance(init_result, dict) and runner.protocol_version == "2025-06-18"):
        cases.append({"case_id": "MCP-INIT", "status": "BLOCKED", "reason": "valid token did not negotiate the supported protocol", "error_code": _error_code(init)})
        _write_json(output / "summary.json", {"environment": "test", "cases": cases})
        print(json.dumps({"output_dir": str(output), "counts": dict(Counter(x["status"] for x in cases))}))
        return
    runner.notify_initialized("MCP-NOTIFY")

    base_args = {"kind": "sub_factor", "ic_scope": "time_series", "interval": "1h", "universe_key": "all", "limit": 100}
    now = datetime.now(LOCAL_TZ)
    current = runner.tool("SCOPE-CURRENT", "factor_list_metric_scopes", {**base_args, "as_of": now.isoformat()})
    current_items = _items(current)
    current_mismatches, current_future = page_mismatches(current_items, groups, now)
    current_ok = (
        _success(current)
        and len(current_items) <= 100
        and not current_mismatches
        and not current_future
    )
    cases.append({
        "case_id": "SCOPE-CURRENT-PAGE",
        "status": "PASS" if current_ok else ("BLOCKED" if _error_code(current) in BLOCKING_CODES else "FAIL"),
        "reason": "current scope page is bounded and every returned row matches the DB aggregate" if current_ok else "current scope page failed, exceeded limit, exposed a future run, or disagreed with DB",
        "returned_count": len(current_items),
        "limit": 100,
        "error_code": _error_code(current),
        "db_mismatch_count": len(current_mismatches),
        "future_row_count": len(current_future),
        "db_mismatches": current_mismatches[:10],
    })

    # Select an identity visible now that has at least one prior completed run.
    selected: tuple[Any, ...] | None = None
    latest: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None
    for item in current_items:
        candidate_groups = groups.get(identity(item), [])
        if len(candidate_groups) >= 2:
            selected = identity(item)
            latest = expected_scope(candidate_groups, now)
            eligible = [
                row
                for row in candidate_groups
                if (parse_time(row.get("completed_at")) or datetime.max.replace(tzinfo=LOCAL_TZ)) <= now
            ]
            previous = eligible[-2] if len(eligible) >= 2 else None
            if latest is not None and previous is not None and parse_time(latest.get("completed_at")) != parse_time(previous.get("completed_at")):
                break

    if selected is None or latest is None or previous is None:
        cases.append({"case_id": "SCOPE-PIT-BOUNDARIES", "status": "BLOCKED", "reason": "no returned identity has two completed DB runs", "db_identity_count": len(groups), "returned_count": len(current_items)})
    else:
        latest_completed = parse_time(latest.get("completed_at"))
        if latest_completed is None:
            cases.append({"case_id": "SCOPE-PIT-BOUNDARIES", "status": "BLOCKED", "reason": "selected DB run has no completed_at"})
        else:
            boundaries = (
                ("BEFORE", latest_completed - timedelta(microseconds=1)),
                ("AT", latest_completed),
                ("AFTER", latest_completed + timedelta(microseconds=1)),
            )
            boundary_evidence: dict[str, Any] = {
                "identity": dict(zip(IDENTITY_KEYS, selected)),
                "latest_run": {"run_id": latest.get("run_id"), "completed_at": latest.get("completed_at")},
                "previous_run": {"run_id": previous.get("run_id"), "completed_at": previous.get("completed_at")},
                "checks": {},
            }
            all_ok = True
            blocked = False
            for label, boundary in boundaries:
                call = runner.tool(f"SCOPE-PIT-{label}", "factor_list_metric_scopes", {**base_args, "as_of": boundary.isoformat()})
                items = _items(call)
                db_mismatches, future_rows = page_mismatches(items, groups, boundary)
                target_rows = [item for item in items if identity(item) == selected]
                db_expected = expected_scope(groups[selected], boundary)
                target_observed = bool(target_rows)
                target_ok = True
                target_mismatches: list[str] = []
                # The resource is capped at 100 and has no cursor.  A target
                # identity may therefore be absent solely because it falls
                # outside this bounded page; validate it only when observed.
                if target_rows and len(target_rows) != 1:
                    target_ok = False
                    target_mismatches.append("target_row_count")
                elif target_rows and db_expected is None:
                    target_ok = False
                    target_mismatches.append("missing_db_scope")
                elif target_rows:
                    target_mismatches = row_matches_db(target_rows[0], db_expected)
                    target_ok = not target_mismatches
                page_ok = _success(call) and len(items) <= 100 and not future_rows and not db_mismatches and target_ok
                if _error_code(call) in BLOCKING_CODES:
                    blocked = True
                all_ok = all_ok and page_ok
                boundary_evidence["checks"][label] = {
                    "as_of": boundary.isoformat(),
                    "returned_count": len(items),
                    "target_row_count": len(target_rows),
                    "target_observed": target_observed,
                    "expected_run_id": db_expected.get("run_id") if db_expected else None,
                    "returned_run_completed_at": target_rows[0].get("run_completed_at") if target_rows else None,
                    "target_mismatches": target_mismatches,
                    "page_db_mismatch_count": len(db_mismatches),
                    "page_db_mismatches": db_mismatches[:10],
                    "future_row_count": len(future_rows),
                    "http_status": call.get("http_status"),
                    "error_code": _error_code(call),
                }
            cases.append({
                "case_id": "SCOPE-PIT-BOUNDARIES",
                "status": "PASS" if all_ok else ("BLOCKED" if blocked else "FAIL"),
                "reason": "before/at/after bounded pages contain only DB-visible rows and expose no future run" if all_ok else "scope page visibility or run completion boundary differs from DB",
                "evidence": boundary_evidence,
            })

    # A smaller limit verifies the declared page bound independently of the
    # historical identity checks.  The endpoint has no cursor argument in its
    # schema, so pagination here means bounded page cardinality and metadata.
    small = runner.tool("SCOPE-LIMIT-ONE", "factor_list_metric_scopes", {**base_args, "limit": 1, "as_of": now.isoformat()})
    small_items = _items(small)
    small_mismatches, small_future = page_mismatches(small_items, groups, now)
    small_ok = (
        _success(small)
        and len(small_items) <= 1
        and (_meta(small).get("next_cursor") in (None, ""))
        and not small_mismatches
        and not small_future
    )
    cases.append({
        "case_id": "SCOPE-LIMIT-ONE",
        "status": "PASS" if small_ok else ("BLOCKED" if _error_code(small) in BLOCKING_CODES else "FAIL"),
        "reason": "limit=1 is honored and the returned row matches DB" if small_ok else "scope page cardinality, metadata, or DB identity violates the declared contract",
        "returned_count": len(small_items),
        "meta_keys": sorted(_meta(small)),
        "error_code": _error_code(small),
        "db_mismatch_count": len(small_mismatches),
        "future_row_count": len(small_future),
        "db_mismatches": small_mismatches[:10],
    })

    counts = Counter(case["status"] for case in cases)
    result = {
        "run_id": run_stamp,
        "environment": "test",
        "mcp_url": MCP_URL,
        "read_only": True,
        "db_scope_identity_count": len(groups),
        "cases": cases,
        "case_counts": dict(counts),
    }
    _write_json(output / "summary.json", result)
    lines = [
        "# Metric scope visibility probe",
        "",
        f"- Environment: `test`; read-only: `true`",
        f"- Counts: `{dict(counts)}`",
        "",
        "| Case | Status | Result |",
        "|---|---|---|",
    ]
    for case in cases:
        lines.append(f"| {case['case_id']} | {case['status']} | {case['reason']} |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "counts": dict(counts), "returned_current": len(current_items)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
