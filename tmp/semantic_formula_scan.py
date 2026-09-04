#!/usr/bin/env python3
"""Read-only semantic scan of Factor 4 formula definitions and evidence.

This utility is intentionally diagnostic.  It does not call MCP or mutate the
database; it records candidates that need a product contract before being
classified as defects.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql

ROOT = Path(__file__).resolve().parents[1]

_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_HORIZON_RE = re.compile(r"(?:^|[_-])(?P<n>\d+(?:\.\d+)?)(?P<u>h|hr|hours?|d|days?|m|min|mins|s|sec|secs)?(?:$|[_-])", re.I)
_NEGATIVE_TEMPORAL_RE = re.compile(
    r"\.(?:shift|diff|pct_change)\s*\(\s*(?:periods\s*=\s*)?-\s*\d", re.I
)

_FUNCTIONS = {
    "abs", "all", "any", "clip", "count", "correlation", "cov", "diff",
    "exp", "float", "kurtosis", "log", "max", "mean", "median", "min",
    "nan", "pct_change", "percentile_rank", "rank", "rolling_vwap", "round",
    "rsi", "sign", "skewness", "std", "sum", "truerange", "vwap",
    "where", "zeros", "sqrt", "corr", "var", "ewm", "shift", "len",
}
_KNOWN_DERIVED = {
    "returns", "rolling_vwap", "truerange", "rsi", "percentile_rank",
    "skewness", "kurtosis", "downside_skewness", "correlation",
}
_NON_FIELDS = {"window", "min_periods", "True", "False", "None", "nan", "inf", "np", "pd"}


def _decode(value: Any) -> Any:
    """Decode JSON columns while preserving scalar values."""
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


def _json_safe(value: Any) -> Any:
    """Convert database values to JSON-safe scalars."""
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def _names(expression: str | None) -> tuple[set[str], str | None]:
    """Return variable names used as data inputs, excluding call names."""
    if not expression:
        return set(), None
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        return set(), exc.msg
    names: set[str] = set()
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            bound.update(arg.arg for arg in node.args.args)
        elif isinstance(node, ast.comprehension):
            if isinstance(node.target, ast.Name):
                bound.add(node.target.id)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                bound.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                # The root object is still visited separately as a Name.
                pass
        elif isinstance(node, ast.Name):
            names.add(node.id)
    names -= bound
    names -= _FUNCTIONS
    names -= _NON_FIELDS
    return {name for name in names if _FIELD_RE.fullmatch(name)}, None


def _call_name(node: ast.Call) -> str:
    """Return a normalized method/function name for an AST call."""
    if isinstance(node.func, ast.Name):
        return node.func.id.lower()
    if isinstance(node.func, ast.Attribute):
        return node.func.attr.lower()
    return ""


def _constant_int(node: ast.AST | None) -> int | float | None:
    """Read a numeric AST constant, including unary signs."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float)):
        if isinstance(node.op, ast.USub):
            return -node.operand.value
        if isinstance(node.op, ast.UAdd):
            return node.operand.value
    return None


def _temporal_calls(expression: str | None) -> list[dict[str, Any]]:
    """Extract temporal calls and their constant offsets/windows."""
    if not expression:
        return []
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return []
    out: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in {"shift", "diff", "pct_change", "rolling", "ewm", "expanding"}:
            continue
        values: list[Any] = []
        for arg in node.args:
            values.append(_constant_int(arg))
        for kw in node.keywords:
            if kw.arg in {"periods", "window", "span", "halflife", "min_periods", "center"}:
                values.append({kw.arg: _constant_int(kw.value) if kw.arg != "center" else getattr(kw.value, "value", None)})
        out.append({"name": name, "args": values, "line": getattr(node, "lineno", None)})
    return out


def _calc_function_contract(calc_function: str | None) -> dict[str, Any]:
    """Extract generated wrapper required/derived fields when present."""
    if not calc_function:
        return {"required_fields": [], "derived_fields": [], "factor_bar_interval": None, "temporal_unit": None}
    result: dict[str, Any] = {"required_fields": [], "derived_fields": [], "factor_bar_interval": None, "temporal_unit": None}
    for key in ("required_fields", "derived_fields"):
        match = re.search(rf"\b{key}\s*=\s*(\[[^\n]*\])", calc_function)
        if match:
            try:
                result[key] = ast.literal_eval(match.group(1))
            except (SyntaxError, ValueError):
                pass
    for key in ("factor_bar_interval", "temporal_unit"):
        match = re.search(rf"\b{key}\s*=\s*([\"'][^\"']+[\"'])", calc_function)
        if match:
            result[key] = match.group(1)[1:-1]
    return result


def _horizon_from_name(name: str | None) -> tuple[float, str] | None:
    """Extract the last explicit horizon token from a factor name."""
    if not name:
        return None
    matches = list(_HORIZON_RE.finditer(name))
    if not matches:
        return None
    match = matches[-1]
    unit = (match.group("u") or "").lower()
    return float(match.group("n")), unit


def _source_fields(metadata: Any) -> tuple[set[str], set[str], list[dict[str, Any]]]:
    """Collect raw/resolved/derived fields and field-resolution records."""
    if not isinstance(metadata, dict):
        return set(), set(), []
    raw: set[str] = set()
    derived: set[str] = set(str(x) for x in metadata.get("derived_fields", []) if x)
    for key in ("required_fields", "resolved_raw_fields"):
        value = metadata.get(key)
        if isinstance(value, list):
            raw.update(str(x) for x in value if x)
    resolutions = metadata.get("field_resolution")
    if not isinstance(resolutions, list):
        resolutions = []
    for row in resolutions:
        if not isinstance(row, dict):
            continue
        field = row.get("canonical_field_name") or row.get("field_name")
        if field:
            if str(row.get("field_class", "")).lower() == "derived" or str(row.get("resolution_status", "")).lower() == "derived":
                derived.add(str(field))
            else:
                raw.add(str(field))
    return raw, derived, [x for x in resolutions if isinstance(x, dict)]


def _active_route_factors(cursor: Any) -> set[int]:
    """Return active eligible factor IDs from the publication route table."""
    cursor.execute("SELECT DISTINCT factor_id FROM market_environment_factor_route WHERE is_active=1 AND is_eligible=1")
    return {int(row["factor_id"]) for row in cursor.fetchall() if row.get("factor_id") is not None}


def _run() -> dict[str, Any]:
    """Run the read-only scan and return a compact report."""
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
            snapshot = cursor.fetchone()
            cursor.execute(
                """SELECT d.id,d.factor_id,d.is_sub_factor_id,d.name,d.description,d.calc_function,
                          d.calc_logic,d.params,d.data_source_metadata,d.data_source,d.status,d.updated_at,
                          COUNT(e.id) AS evidence_count,MIN(e.id) AS first_evidence_id,MAX(e.id) AS last_evidence_id,
                          COUNT(DISTINCT e.expression) AS evidence_expression_count,
                          COUNT(DISTINCT e.factor_bar_interval) AS evidence_interval_count,
                          GROUP_CONCAT(DISTINCT e.factor_bar_interval ORDER BY e.factor_bar_interval SEPARATOR ',') AS evidence_intervals,
                          GROUP_CONCAT(DISTINCT e.factor_window_bars ORDER BY e.factor_window_bars SEPARATOR ',') AS evidence_windows
                   FROM factors_details d
                   LEFT JOIN factor_ic_run_formula_evidence e ON e.source_detail_id=d.id
                   GROUP BY d.id
                   ORDER BY d.id"""
            )
            details = [dict(row) for row in cursor.fetchall()]
            active_factors = _active_route_factors(cursor)
            for row in details:
                row["params"] = _decode(row.get("params"))
                row["data_source_metadata"] = _decode(row.get("data_source_metadata"))
                row["calc_contract"] = _calc_function_contract(row.get("calc_function"))

            # Per-detail semantic classifications.
            negative_temporal: list[dict[str, Any]] = []
            future_target_use: list[dict[str, Any]] = []
            center_or_backfill: list[dict[str, Any]] = []
            unresolved_inputs: list[dict[str, Any]] = []
            declared_only_misses: list[dict[str, Any]] = []
            interval_mismatches: list[dict[str, Any]] = []
            frequency_observations: list[dict[str, Any]] = []
            annualization_candidates: list[dict[str, Any]] = []
            formula_empty: list[dict[str, Any]] = []
            temporal_constants: list[dict[str, Any]] = []

            for row in details:
                expression = str(row.get("calc_logic") or "")
                params = row.get("params") if isinstance(row.get("params"), dict) else {}
                metadata = row.get("data_source_metadata") if isinstance(row.get("data_source_metadata"), dict) else {}
                contract = row.get("calc_contract") or {}
                names, parse_error = _names(expression)
                calls = _temporal_calls(expression)
                if not expression:
                    formula_empty.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "evidence_count": row["evidence_count"]})
                neg_calls = [call for call in calls if any(isinstance(v, (int, float)) and v < 0 for v in call["args"]) ]
                if neg_calls or _NEGATIVE_TEMPORAL_RE.search(expression):
                    negative_temporal.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "expression": expression, "calls": neg_calls, "evidence_count": row["evidence_count"], "active_route": int(row["factor_id"]) in active_factors})
                target_names = sorted(name for name in names if name in {"returns", "forward_return", "forward_returns", "future_return", "target", "label", "y"} or "return" in name and ("forward" in name or "future" in name))
                if target_names or re.search(r"\b(?:lead|future|forward|anticipat)\w*", str(row.get("name") or ""), re.I):
                    future_target_use.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "target_names": target_names, "expression": expression, "description": str(row.get("description") or "")[:500], "evidence_count": row["evidence_count"], "active_route": int(row["factor_id"]) in active_factors})
                if re.search(r"center\s*=\s*True|\b(?:bfill|backfill)\s*\(|\b(?:lead|future)\s*\(", expression, re.I):
                    center_or_backfill.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "expression": expression, "evidence_count": row["evidence_count"]})

                declared = set(str(x) for x in (params.get("declared_fields") or params.get("fields") or []) if x)
                contract_required = set(str(x) for x in (contract.get("required_fields") or []) if x)
                metadata_raw, metadata_derived, resolutions = _source_fields(metadata)
                # The generated wrapper is the strongest available statement of runtime inputs.
                approved = declared | contract_required | metadata_raw | metadata_derived | _KNOWN_DERIVED
                missing = sorted(names - approved)
                if missing:
                    item = {"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "missing": missing, "declared": sorted(declared), "wrapper_required": sorted(contract_required), "metadata_raw": sorted(metadata_raw), "metadata_derived": sorted(metadata_derived), "expression": expression, "evidence_count": row["evidence_count"], "active_route": int(row["factor_id"]) in active_factors}
                    unresolved_inputs.append(item)
                elif sorted(names - declared):
                    declared_only_misses.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "not_in_params": sorted(names - declared), "resolved_by_wrapper_or_metadata": sorted(names & approved), "evidence_count": row["evidence_count"]})

                detail_interval = params.get("factor_bar_interval") or contract.get("factor_bar_interval")
                metadata_interval = metadata.get("factor_interval") or metadata.get("factor_bar_interval")
                dataset_intervals = sorted({str(ds.get("source_interval") or ds.get("target_interval")) for ds in (metadata.get("datasets") or []) if isinstance(ds, dict) and (ds.get("source_interval") or ds.get("target_interval"))})
                if detail_interval and metadata_interval and str(detail_interval).lower() != str(metadata_interval).lower():
                    interval_mismatches.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "kind": "params_vs_metadata", "detail_interval": detail_interval, "metadata_interval": metadata_interval, "dataset_intervals": dataset_intervals})
                if detail_interval and dataset_intervals and any(str(x).lower() != str(detail_interval).lower() for x in dataset_intervals):
                    interval_mismatches.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "kind": "params_vs_dataset", "detail_interval": detail_interval, "metadata_interval": metadata_interval, "dataset_intervals": dataset_intervals})
                frequencies = sorted({str(x.get("frequency")) for x in resolutions if x.get("frequency")})
                if frequencies and detail_interval and any(freq.lower() not in {str(detail_interval).lower(), "factor_bar_interval"} for freq in frequencies):
                    frequency_observations.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "factor_interval": detail_interval, "field_frequencies": frequencies, "fields": sorted(names), "evidence_count": row["evidence_count"]})
                if re.search(r"\b(?:8760|365\s*\*\s*24|24\s*\*\s*365|252|365)\b", expression):
                    annualization_candidates.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "factor_interval": detail_interval, "expression": expression, "evidence_count": row["evidence_count"]})
                if calls:
                    temporal_constants.append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": row["name"], "factor_interval": detail_interval, "window": params.get("window", params.get("factor_window_bars")), "calls": calls, "evidence_count": row["evidence_count"]})

            # Evidence-level exact mismatches against linked immutable details.
            cursor.execute(
                """SELECT e.id,e.factor_id,e.is_sub_factor_id,e.factor_bar_interval,e.factor_window_bars,
                          e.return_bar_interval,e.forward_return_bars,e.expression,e.required_fields,
                          e.lookback_json,e.lag_json,e.source_detail_id,e.metadata_complete,e.metadata_warnings,
                          d.name,d.calc_logic,d.params,d.data_source_metadata
                   FROM factor_ic_run_formula_evidence e
                   LEFT JOIN factors_details d ON d.id=e.source_detail_id
                   ORDER BY e.id"""
            )
            evidence = [dict(row) for row in cursor.fetchall()]
            evidence_missing: list[dict[str, Any]] = []
            evidence_interval: list[dict[str, Any]] = []
            evidence_future: list[dict[str, Any]] = []
            warning_counts: Counter[str] = Counter()
            by_factor: defaultdict[int, dict[str, Any]] = defaultdict(lambda: {"rows": 0, "future": 0, "missing": 0, "interval": 0, "metadata_incomplete": 0})
            details_by_id = {int(row["id"]): row for row in details}
            for row in evidence:
                factor_id = int(row["factor_id"])
                stats = by_factor[factor_id]
                stats["rows"] += 1
                warnings = _decode(row.get("metadata_warnings"))
                if isinstance(warnings, list):
                    warning_counts.update(str(x) for x in warnings)
                if not row.get("metadata_complete"):
                    stats["metadata_incomplete"] += 1
                expression = str(row.get("expression") or "")
                if _NEGATIVE_TEMPORAL_RE.search(expression):
                    stats["future"] += 1
                    evidence_future.append({"evidence_id": row["id"], "factor_id": factor_id, "source_detail_id": row.get("source_detail_id"), "name": row.get("name"), "factor_window_bars": row.get("factor_window_bars"), "factor_bar_interval": row.get("factor_bar_interval"), "expression": expression})
                source = details_by_id.get(int(row["source_detail_id"])) if row.get("source_detail_id") is not None else None
                if source:
                    params = source.get("params") if isinstance(source.get("params"), dict) else {}
                    expected_interval = params.get("factor_bar_interval") or (source.get("calc_contract") or {}).get("factor_bar_interval")
                    if expected_interval and str(expected_interval).lower() != str(row.get("factor_bar_interval") or "").lower():
                        stats["interval"] += 1
                        evidence_interval.append({"evidence_id": row["id"], "factor_id": factor_id, "source_detail_id": row.get("source_detail_id"), "name": source.get("name"), "evidence_interval": row.get("factor_bar_interval"), "detail_interval": expected_interval, "evidence_window": row.get("factor_window_bars"), "expression": expression})
                    names, _ = _names(expression)
                    required = set(str(x) for x in (_decode(row.get("required_fields")) or []) if x)
                    metadata = source.get("data_source_metadata") if isinstance(source.get("data_source_metadata"), dict) else {}
                    raw, derived, _ = _source_fields(metadata)
                    approved = required | raw | derived | _KNOWN_DERIVED | set(str(x) for x in ((source.get("calc_contract") or {}).get("required_fields") or []))
                    miss = sorted(names - approved)
                    if miss:
                        stats["missing"] += 1
                        evidence_missing.append({"evidence_id": row["id"], "factor_id": factor_id, "source_detail_id": row.get("source_detail_id"), "name": source.get("name"), "missing": miss, "required_fields": sorted(required), "expression": expression})

            # Formula family comparison: same stem, different declared windows.
            families: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in details:
                params = row.get("params") if isinstance(row.get("params"), dict) else {}
                window = params.get("window", params.get("factor_window_bars"))
                horizon = _horizon_from_name(str(row.get("name") or ""))
                if window is None or horizon is None:
                    continue
                name = str(row.get("name") or "")
                # Remove the final horizon token and common interval suffix; retain semantic stem.
                stem = re.sub(r"(?:[_-]\d+(?:\.\d+)?(?:h|hr|hours?|d|days?|m|min|mins|s|sec|secs)?)+$", "", name, flags=re.I)
                stem = re.sub(r"_copy_.*$", "", stem, flags=re.I)
                families[stem.lower()].append({"detail_id": row["id"], "factor_id": row["factor_id"], "name": name, "window": window, "horizon": horizon, "expression": row.get("calc_logic"), "evidence_count": row["evidence_count"]})
            fixed_across_horizon: list[dict[str, Any]] = []
            for stem, members in families.items():
                by_expr: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
                for member in members:
                    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "#", str(member.get("expression") or ""))
                    by_expr[normalized].append(member)
                windows = {str(member["window"]) for member in members}
                if len(windows) < 2:
                    continue
                # Report families where formulas are byte-identical despite different declared windows,
                # excluding formulas that intentionally use only the dynamic `window` placeholder.
                for normalized, same in by_expr.items():
                    expressions = {str(member.get("expression") or "") for member in same}
                    if len(same) >= 2 and len(expressions) == 1 and "window" not in normalized:
                        fixed_across_horizon.append({"stem": stem, "members": same[:30], "member_count": len(same)})

            report = {
                "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-semantic-formula-scan"),
                "environment": "test",
                "mode": "READ_ONLY",
                "snapshot": {key: _json_safe(value) for key, value in snapshot.items()},
                "counts": {"details": len(details), "details_with_evidence": sum(int(row["evidence_count"] or 0) > 0 for row in details), "evidence": len(evidence), "active_eligible_factor_ids": len(active_factors)},
                "candidate_counts": {
                    "negative_temporal_detail": len(negative_temporal),
                    "future_target_detail": len(future_target_use),
                    "center_or_backfill_detail": len(center_or_backfill),
                    "unresolved_input_detail": len(unresolved_inputs),
                    "params_only_input_miss_detail": len(declared_only_misses),
                    "detail_interval_mismatch": len(interval_mismatches),
                    "field_frequency_observation": len(frequency_observations),
                    "annualization_candidate": len(annualization_candidates),
                    "empty_formula_detail": len(formula_empty),
                    "evidence_negative_temporal": len(evidence_future),
                    "evidence_unresolved_input": len(evidence_missing),
                    "evidence_interval_mismatch": len(evidence_interval),
                    "fixed_formula_family": len(fixed_across_horizon),
                },
                "warning_counts": dict(warning_counts),
                "active_route_semantic_candidates": {
                    "negative_temporal": [row for row in negative_temporal if row["active_route"]],
                    "unresolved_inputs": [row for row in unresolved_inputs if row["active_route"]],
                    "future_target": [row for row in future_target_use if row["active_route"]],
                },
                "samples": {
                    "negative_temporal_detail": negative_temporal[:100],
                    "future_target_detail": future_target_use[:100],
                    "center_or_backfill_detail": center_or_backfill[:100],
                    "unresolved_input_detail": unresolved_inputs[:200],
                    "params_only_input_miss_detail": declared_only_misses[:200],
                    "detail_interval_mismatch": interval_mismatches[:100],
                    "field_frequency_observation": frequency_observations[:200],
                    "annualization_candidate": annualization_candidates[:100],
                    "empty_formula_detail": formula_empty[:100],
                    "evidence_negative_temporal": evidence_future[:100],
                    "evidence_unresolved_input": evidence_missing[:200],
                    "evidence_interval_mismatch": evidence_interval[:100],
                    "fixed_formula_family": fixed_across_horizon[:100],
                },
                "by_factor": {str(key): value for key, value in sorted(by_factor.items()) if value["future"] or value["missing"] or value["interval"]},
            }
            connection.rollback()
            return report
    finally:
        connection.close()


def main() -> None:
    """Run the scan and write a redacted JSON report plus concise summary."""
    report = _run()
    output = ROOT / "reports" / "factor4-deep" / report["run_id"]
    output.mkdir(parents=True, exist_ok=False)
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_json_safe) + "\n", encoding="utf-8")
    counts = report["candidate_counts"]
    lines = [
        "# Semantic formula scan",
        "",
        f"- Environment: test; mode: read-only; snapshot: `{report['snapshot'].get('snapshot_at')}`",
        f"- Details: {report['counts']['details']}; evidence: {report['counts']['evidence']}; active eligible factors: {report['counts']['active_eligible_factor_ids']}",
        "",
        "## Candidate counts",
        "",
    ]
    for key, value in counts.items():
        lines.append(f"- `{key}`: {value}")
    lines.extend([
        "",
        "Candidates are not defects until the formula/data contract confirms intended semantics.",
        "See `results.json` for samples and active-route intersections.",
    ])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "candidate_counts": counts, "active_route_semantic_candidates": report["active_route_semantic_candidates"]}, ensure_ascii=False, default=_json_safe))


if __name__ == "__main__":
    main()
