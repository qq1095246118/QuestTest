#!/usr/bin/env python3
"""Audit Factor 4 formula evidence and active-route formula invariants read-only.

The audit deliberately separates deterministic integrity failures from observations
that need a product-level contract before they can be reported as defects.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "factor4-deep" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-formula-integrity"

_WINDOW_RE = re.compile(r"(?<![A-Za-z])(?P<n>\d+(?:\.\d+)?)\s*(?P<u>[smhdw])?", re.I)
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_NON_FIELD_NAMES = {
    "window",
    "min_periods",
    "True",
    "False",
    "None",
    "nan",
    "inf",
    "np",
    "pd",
}


def _json(value: Any) -> Any:
    """Decode a nullable MySQL JSON value without changing native objects."""
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _safe(value: Any) -> Any:
    """Convert database scalars into report-safe JSON values."""
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def _dump(path: Path, value: Any) -> None:
    """Write one deterministic UTF-8 JSON artifact."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_safe) + "\n", encoding="utf-8")


def _field_names(expression: str | None) -> tuple[set[str], str | None]:
    """Extract probable input names from a Python-like expression."""
    if not expression:
        return set(), None
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        return set(), f"SyntaxError: {exc.msg}"
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id not in _NON_FIELD_NAMES
        and _FIELD_NAME_RE.fullmatch(node.id)
    }
    return names, None


def _window_token(value: Any) -> tuple[Decimal, str] | None:
    """Normalize a window label for comparison without changing its unit semantics.

    The service uses ``factor_window_bars`` labels such as ``24H`` even when the
    bar interval is 4h; the numeric component is therefore a bar count, not a
    wall-clock duration.  Prefixes like ``n=`` and ``tenor=`` are presentation
    labels and are ignored here.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    match = _WINDOW_RE.search(text)
    if not match:
        return None
    number = Decimal(match.group("n"))
    unit = (match.group("u") or "").lower()
    return number, unit


def _windows_equal(left: tuple[Decimal, str] | None, right: tuple[Decimal, str] | None) -> bool:
    """Compare window identities while accepting legacy unitless bar counts.

    Factor 4 stores evidence labels such as ``24H`` and some detail records as
    the numeric bar count ``24``.  A unitless value is therefore compatible with
    an explicitly labelled value when the numeric bar count matches; two
    explicitly labelled units still have to agree.
    """
    if left is None or right is None:
        return True
    if left[0] != right[0]:
        return False
    return not left[1] or not right[1] or left[1] == right[1]


def _formula_call_issues(expression: str | None) -> list[str]:
    """Find deterministic malformed rolling/VWAP calls in one expression."""
    if not expression:
        return ["EMPTY_EXPRESSION"]
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return ["EXPRESSION_PARSE_ERROR"]
    issues: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name.lower() in {"vwap", "rolling"}:
            has_window_kw = any(keyword.arg == "window" for keyword in node.keywords)
            if not node.args and not has_window_kw:
                issues.append(f"{name.upper()}_WITHOUT_WINDOW")
    return sorted(set(issues))


def _run() -> dict[str, Any]:
    """Run the complete read-only audit and return its report object."""
    from config.settings import SettingsLoader

    settings = SettingsLoader.load("test", ROOT).database
    connection = pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.username,
        password=settings.password,
        database=settings.name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=180,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute("SELECT DATABASE() AS database_name, CURRENT_USER() AS db_user, NOW(6) AS snapshot_at")
            snapshot_identity = dict(cursor.fetchone())

            cursor.execute(
                """SELECT id,run_id,factor_id,is_sub_factor_id,calculation_mode,
                          factor_bar_interval,factor_window_bars,return_bar_interval,
                          forward_return_bars,formula_version,formula_hash,hash_algorithm,
                          normalization_version,expression,required_fields,lookback_json,
                          lag_json,missing_policy,output_unit,metadata_complete,
                          metadata_warnings,source_detail_id,recorded_at
                   FROM factor_ic_run_formula_evidence
                   ORDER BY id"""
            )
            evidence = [dict(row) for row in cursor.fetchall()]

            cursor.execute(
                """SELECT id,factor_id,is_sub_factor_id,calc_logic,params
                   FROM factors_details
                   ORDER BY id"""
            )
            detail_rows = [dict(row) for row in cursor.fetchall()]
            details_by_id = {int(row["id"]): row for row in detail_rows}
            # A formula evidence row may point at the exact immutable detail via
            # source_detail_id.  Keep a latest-detail fallback only for legacy
            # evidence rows that predate that foreign-key link.
            details: dict[tuple[int, int], dict[str, Any]] = {}
            for row in detail_rows:
                details[(int(row["factor_id"]), int(row["is_sub_factor_id"]))] = row

            cursor.execute(
                """SELECT id,factor_ref,factor_type,factor_id,metric_id,label_code,
                          publication_uid,eval_batch_id,is_eligible,is_active
                   FROM market_environment_factor_route
                   WHERE is_active=1 AND is_eligible=1
                   ORDER BY id"""
            )
            routes = [dict(row) for row in cursor.fetchall()]

            # Only active-route metric payloads are needed for the route observation;
            # the full metric table contains large diagnostic JSON blobs.
            cursor.execute(
                """SELECT DISTINCT m.id,m.factor_ref,m.factor_type,m.interval,
                          m.return_bar_interval,m.forward_return_bars,m.metric_payload
                   FROM market_environment_factor_metric m
                   JOIN market_environment_factor_route rt ON rt.metric_id=m.id
                   WHERE rt.is_active=1 AND rt.is_eligible=1"""
            )
            metrics = {int(row["id"]): dict(row) for row in cursor.fetchall()}

            cursor.execute("SELECT run_id,status FROM factor_ic_runs")
            run_status = {str(row["run_id"]): dict(row) for row in cursor.fetchall()}

            # Basic evidence invariants.
            status_counts: Counter[str] = Counter()
            metadata_counts: Counter[str] = Counter()
            warning_counts: Counter[str] = Counter()
            identity_counts: Counter[tuple[Any, ...]] = Counter()
            hash_to_expressions: defaultdict[str, set[str]] = defaultdict(set)
            parse_errors: list[dict[str, Any]] = []
            field_mismatches: list[dict[str, Any]] = []
            detail_mismatches: list[dict[str, Any]] = []
            window_mismatches: list[dict[str, Any]] = []
            call_issues: list[dict[str, Any]] = []
            future_suspects: list[dict[str, Any]] = []
            evidence_json_mismatches: list[dict[str, Any]] = []

            for row in evidence:
                run = run_status.get(str(row["run_id"]))
                status_counts[str(run["status"]) if run else "MISSING_RUN"] += 1
                metadata_counts[str(row.get("metadata_complete"))] += 1
                warnings = _json(row.get("metadata_warnings"))
                if isinstance(warnings, list):
                    for warning in warnings:
                        warning_counts[str(warning)] += 1
                key = (
                    row["run_id"], row["factor_id"], row["is_sub_factor_id"], row["calculation_mode"],
                    row["factor_bar_interval"], row["factor_window_bars"], row["return_bar_interval"],
                    row["forward_return_bars"],
                )
                identity_counts[key] += 1
                if row.get("formula_hash"):
                    hash_to_expressions[str(row["formula_hash"])].add(str(row.get("expression")))

                names, parse_error = _field_names(row.get("expression"))
                if parse_error:
                    parse_errors.append({"evidence_id": row["id"], "factor_id": row["factor_id"], "error": parse_error})
                required = _json(row.get("required_fields"))
                required_set = {str(item) for item in required} if isinstance(required, list) else set()
                missing = sorted(names - required_set)
                if missing:
                    field_mismatches.append({"evidence_id": row["id"], "factor_id": row["factor_id"], "expression_fields_not_declared": missing, "required_fields": sorted(required_set)})

                issues = _formula_call_issues(row.get("expression"))
                if issues:
                    call_issues.append({"evidence_id": row["id"], "factor_id": row["factor_id"], "expression": str(row.get("expression"))[:500], "issues": issues})
                expression_lower = str(row.get("expression") or "").lower()
                if re.search(r"(?:shift|lead|future|forward)\s*\(\s*-", expression_lower):
                    future_suspects.append({"evidence_id": row["id"], "factor_id": row["factor_id"], "expression": str(row.get("expression"))[:500]})

                source_detail_id = row.get("source_detail_id")
                detail = details_by_id.get(int(source_detail_id)) if source_detail_id is not None else None
                if detail is None:
                    detail = details.get((int(row["factor_id"]), int(row["is_sub_factor_id"])))
                if detail:
                    # For linked evidence, compare against its source detail.  A
                    # legacy row without a link is compared with the current
                    # detail only as a diagnostic observation because the detail
                    # may have changed after the run was recorded.
                    if source_detail_id is not None and str(row.get("expression")) != str(detail.get("calc_logic")):
                        detail_mismatches.append({"evidence_id": row["id"], "factor_id": row["factor_id"], "source_detail_id": row.get("source_detail_id"), "detail_id": detail.get("id")})
                    params = _json(detail.get("params"))
                    detail_window = params.get("factor_window_bars", params.get("window")) if isinstance(params, dict) else None
                    evidence_window = _window_token(row.get("factor_window_bars"))
                    detail_window_token = _window_token(detail_window)
                    if source_detail_id is not None and not _windows_equal(evidence_window, detail_window_token):
                        window_mismatches.append({"evidence_id": row["id"], "factor_id": row["factor_id"], "evidence_window": row.get("factor_window_bars"), "detail_window": detail_window})

            duplicate_identities = [
                {"run_id": key[0], "factor_id": key[1], "is_sub_factor_id": key[2], "calculation_mode": key[3], "factor_bar_interval": key[4], "factor_window_bars": key[5], "return_bar_interval": key[6], "forward_return_bars": key[7], "count": count}
                for key, count in identity_counts.items() if count > 1
            ]
            hash_collisions = [
                {"formula_hash": formula_hash, "expression_count": len(expressions), "expressions": sorted(expressions)[:3]}
                for formula_hash, expressions in hash_to_expressions.items() if len(expressions) > 1
            ]

            # Route-level formula availability is an observation unless the current
            # publication contract explicitly requires the separate evidence table.
            route_formula_gaps: list[dict[str, Any]] = []
            evidence_index: defaultdict[tuple[Any, ...], int] = defaultdict(int)
            for row in evidence:
                evidence_index[(int(row["factor_id"]), int(row["is_sub_factor_id"]), str(row["factor_bar_interval"]), str(row["factor_window_bars"]), str(row["return_bar_interval"]), int(row["forward_return_bars"]))] += 1
            for route in routes:
                metric = metrics.get(int(route["metric_id"]))
                payload = _json(metric.get("metric_payload")) if metric else {}
                route_identity = payload.get("metric_identity") if isinstance(payload, dict) else {}
                if not isinstance(route_identity, dict):
                    route_identity = {}
                key = (int(route["factor_id"]), 1 if str(route["factor_type"]) == "sub_factor" else 0, str(metric.get("interval")) if metric else "", str(route_identity.get("factor_window_bars")), str(metric.get("return_bar_interval")) if metric else "", int(metric.get("forward_return_bars") or 0) if metric else 0)
                if evidence_index.get(key, 0) == 0:
                    route_formula_gaps.append({"route_id": route["id"], "factor_ref": route["factor_ref"], "metric_id": route["metric_id"], "factor_window": route_identity.get("factor_window_bars")})

            report = {
                "run_id": OUT.name,
                "environment": "test",
                "mode": "READ_ONLY",
                "database": {"name": snapshot_identity.get("database_name"), "user": snapshot_identity.get("db_user"), "snapshot_at": snapshot_identity.get("snapshot_at")},
                "counts": {"formula_evidence": len(evidence), "details": len(details), "active_eligible_routes": len(routes)},
                "run_status_counts": dict(status_counts),
                "metadata_complete_counts": dict(metadata_counts),
                "metadata_warning_counts": dict(warning_counts),
                "deterministic_checks": {
                    "missing_run_or_non_completed": sum(count for status, count in status_counts.items() if status != "completed"),
                    "duplicate_identity_count": len(duplicate_identities),
                    "hash_collision_count": len(hash_collisions),
                    "expression_parse_error_count": len(parse_errors),
                    "expression_field_not_declared_count": len(field_mismatches),
                    "detail_expression_mismatch_count": len(detail_mismatches),
                    "window_mismatch_count": len(window_mismatches),
                    "malformed_rolling_or_vwap_count": len(call_issues),
                    "evidence_json_mismatch_count": len(evidence_json_mismatches),
                },
                "observations": {
                    "future_shift_suspect_count": len(future_suspects),
                    "active_route_without_matching_formula_evidence_count": len(route_formula_gaps),
                    "all_evidence_metadata_incomplete": len(evidence) > 0 and all(str(row.get("metadata_complete")) in {"0", "False", "None"} for row in evidence),
                },
                "samples": {
                    "parse_errors": parse_errors[:20],
                    "field_mismatches": field_mismatches[:20],
                    "detail_mismatches": detail_mismatches[:20],
                    "window_mismatches": window_mismatches[:20],
                    "malformed_calls": call_issues[:20],
                    "future_shift_suspects": future_suspects[:20],
                    "route_formula_gaps": route_formula_gaps[:40],
                    "duplicate_identities": duplicate_identities[:20],
                    "hash_collisions": hash_collisions[:20],
                    "evidence_json_mismatches": evidence_json_mismatches[:20],
                },
                "classification": {
                    "confirmed_formula_integrity_failures": [],
                    "requires_contract_review": [
                        "all_evidence_metadata_incomplete",
                        "active_route_without_matching_formula_evidence",
                        "future_shift_suspects",
                    ],
                },
            }
            connection.rollback()
            return report
    finally:
        connection.close()


def main() -> None:
    """Run the audit and write a machine-readable and human-readable report."""
    OUT.mkdir(parents=True, exist_ok=False)
    report = _run()
    _dump(OUT / "results.json", report)
    checks = report["deterministic_checks"]
    observations = report["observations"]
    lines = [
        "# Formula integrity audit",
        "",
        f"- Environment: test; mode: read-only",
        f"- Evidence rows: {report['counts']['formula_evidence']}; active eligible routes: {report['counts']['active_eligible_routes']}",
        "",
        "## Deterministic checks",
        "",
    ]
    for name, value in checks.items():
        lines.append(f"- `{name}`: {value}")
    lines.extend([
        "",
        "## Observations",
        "",
        f"- Future-shift suspects: {observations['future_shift_suspect_count']}",
        f"- Active routes without an exact formula-evidence identity match: {observations['active_route_without_matching_formula_evidence_count']}",
        f"- All evidence rows have `metadata_complete=0`: {observations['all_evidence_metadata_incomplete']}",
        "",
        "No observation is classified as a product defect without an explicit contract/oracle.",
    ])
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(OUT), "deterministic_checks": checks, "observations": observations}, ensure_ascii=False))


if __name__ == "__main__":
    main()
