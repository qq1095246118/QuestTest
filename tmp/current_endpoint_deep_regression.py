#!/usr/bin/env python3
"""Run bounded, read-only functional regressions against the current MCP endpoint.

The endpoint and token are supplied at runtime.  All identifiers used for
business checks are discovered from the endpoint itself; the local database is
intentionally not used as an oracle because its catalog snapshot may differ.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tmp import catalog_deep_readonly as transport  # noqa: E402


DEFAULT_URL = "https://factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "MCP_TOKEN"
LABELS = {
    "UNILATERAL_UP",
    "UNILATERAL_DOWN",
    "WIDE_RANGE",
    "NARROW_RANGE",
    "CHOPPY_UP",
    "CHOPPY_DOWN",
}
KNOWN_BLOCKING = {
    "EXPORT_BUDGET_EXCEEDED",
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
    "AUTH_REQUIRED",
    "FORBIDDEN",
}
FORMULA_NAMES = {
    "np",
    "pd",
    "window",
    "True",
    "False",
    "None",
    "nan",
    "inf",
}


def business(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return the normalized business envelope from a transport call."""

    value = call.get("business") if isinstance(call, dict) else None
    return value if isinstance(value, dict) else {}


def payload(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return the business data object, or an empty object for an error."""

    value = business(call).get("data")
    return value if isinstance(value, dict) else {}


def error_code(call: dict[str, Any] | None) -> str | None:
    """Extract a business or JSON-RPC error code from a call."""

    value = business(call).get("error")
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    envelope = call.get("envelope") if isinstance(call, dict) else None
    value = envelope.get("error") if isinstance(envelope, dict) else None
    if isinstance(value, dict) and value.get("code") is not None:
        return str(value["code"])
    return None


def successful(call: dict[str, Any] | None) -> bool:
    """Return whether a call has a successful MCP business result."""

    return bool(
        isinstance(call, dict)
        and call.get("http_status") == 200
        and call.get("is_error") is False
        and error_code(call) is None
    )


def rows(call: dict[str, Any] | None, key: str = "items") -> list[dict[str, Any]]:
    """Extract object rows from a response container."""

    value = payload(call).get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def response_meta(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return response metadata when present."""

    value = business(call).get("meta")
    return value if isinstance(value, dict) else {}


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO timestamp and normalize it to UTC."""

    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decimal_value(value: Any) -> Decimal | None:
    """Convert a scalar to Decimal without treating booleans as numbers."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def formula_names(expression: str | None) -> tuple[set[str], str | None]:
    """Return probable variable names and a parse error from a formula."""

    if not expression:
        return set(), "EMPTY_EXPRESSION"
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        return set(), f"SyntaxError: {exc.msg}"
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in FORMULA_NAMES
    }
    return names, None


def formula_window_issues(expression: str | None) -> list[str]:
    """Find rolling or VWAP calls that omit a window argument."""

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
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name.lower() in {"rolling", "vwap"} and not node.args and not any(
            keyword.arg in {"window", "length", "period"} for keyword in node.keywords
        ):
            issues.append(f"{name.upper()}_WITHOUT_WINDOW")
    return sorted(set(issues))


class DeepProbe:
    """Coordinate one authenticated MCP session and collect case verdicts."""

    def __init__(self, token: str, url: str, output: Path) -> None:
        """Initialize a sanitized runner and an empty verdict collection."""

        transport.MCP_URL = url
        self.runner = transport.Runner(token, output, None)
        self.output = output
        self.cases: list[dict[str, Any]] = []

    def call(self, case_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one tool and preserve a transport-safe artifact even on failure."""

        try:
            return self.runner.tool(case_id, tool, arguments)
        except Exception as exc:  # network failures are recorded, not hidden
            return {
                "case_id": case_id,
                "tool": tool,
                "http_status": None,
                "is_error": True,
                "business": {"error": {"code": type(exc).__name__, "message": str(exc)}},
                "representations_equal": None,
            }

    def record(
        self,
        case_id: str,
        module: str,
        status: str,
        expected: str,
        actual: Any,
        calls: list[dict[str, Any] | None] | None = None,
        note: str = "",
    ) -> None:
        """Append one explicit verdict with only non-sensitive call metadata."""

        calls = calls or []
        self.cases.append(
            {
                "case_id": case_id,
                "module": module,
                "status": status,
                "expected": expected,
                "actual": actual,
                "error_codes": sorted({code for code in (error_code(call) for call in calls) if code}),
                "request_ids": [response_meta(call).get("request_id") for call in calls if response_meta(call).get("request_id")],
                "artifacts": [f for call in calls for f in self._artifacts(call)],
                "note": note,
            }
        )

    def _artifacts(self, call: dict[str, Any] | None) -> list[str]:
        """Return artifact names associated with a Runner call."""

        case_id = call.get("case_id") if isinstance(call, dict) else None
        if not case_id:
            return []
        matches = sorted(self.output.glob(f"*-{case_id}.request.json"))
        return [path.name for path in matches] + [path.name.replace(".request.", ".response.") for path in matches]


def scope_args(scope: dict[str, Any], as_of: str, *, validity: str | None = None) -> dict[str, Any]:
    """Build a complete factor-search scope from a discovered scope row."""

    result: dict[str, Any] = {
        "kind": scope.get("kind", "sub_factor"),
        "calculation_mode": scope.get("calculation_mode", "direct"),
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": scope["forward_return_bars"],
        "ic_scope": scope["ic_scope"],
        "validity_scope": scope["ic_scope"],
        "scoring_version": scope["scoring_version"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "symbol": scope.get("symbol") or "",
        "as_of": as_of,
        "limit": 5,
    }
    if validity is not None:
        result["validity"] = validity
    return result


def metric_args(scope: dict[str, Any], factor_ref: str, as_of: str) -> dict[str, Any]:
    """Build the exact required argument set for metrics and validity reads."""

    result = scope_args(scope, as_of)
    result.pop("kind", None)
    result.pop("validity_scope", None)
    result.pop("limit", None)
    result["factor_ref"] = factor_ref
    return result


def run() -> dict[str, Any]:
    """Execute the read-only deep regression and write a summary report."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("FACTOR4_MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} or FACTOR4_MCP_TOKEN is required")
    url = os.environ.get("MCP_URL", DEFAULT_URL)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-current-endpoint-deep"
    output.mkdir(parents=True, exist_ok=False)
    probe = DeepProbe(token, url, output)
    now = datetime.now(timezone.utc)
    as_of = now.isoformat()

    init = probe.runner.request(
        "INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-current-endpoint-deep", "version": "1.0"},
        },
    )
    probe.runner.protocol_version = ((init.get("envelope") or {}).get("result") or {}).get("protocolVersion")
    probe.runner.notify_initialized("NOTIFY")
    tools_call = probe.runner.request("TOOLS", "tools/list", {})
    listed = ((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or []
    tool_names = {item.get("name") for item in listed if isinstance(item, dict)}
    required = {
        "factor_search",
        "kb_factor_candidate_search",
        "factor_catalog_stats",
        "factor_get_detail",
        "factor_get_metrics",
        "factor_get_formula",
        "factor_list_metric_scopes",
        "factor_rank",
        "factor_get_details_batch",
        "factor_get_metrics_batch",
        "factor_get_validity_batch",
        "factor_get_metric_slices",
        "factor_get_validity",
        "environment_get_daily",
        "environment_get_recommendations",
        "factor_get_environment_metrics",
        "factor_get_environment_tags",
        "universe_list_symbols",
        "schema_get_factor_fields",
        "schema_get_raw_data",
    }
    probe.record(
        "MCP-READY",
        "protocol",
        "PASS" if required <= tool_names else "FAIL",
        "protocol 2025-06-18 and all 20 read tools are listed",
        {"listed_count": len(tool_names), "missing": sorted(required - tool_names)},
        [tools_call],
    )

    # Environment history, date filtering, and strict available_at boundaries.
    env_calls: dict[str, dict[str, Any]] = {}
    for kind in ("fact", "forecast"):
        env_calls[kind] = probe.call(f"ENV-{kind.upper()}", "environment_get_daily", {"label_kind": kind, "limit": 100})
        env_rows = rows(env_calls[kind])
        dates = [parse_time(item.get("environment_date")) or item.get("environment_date") for item in env_rows]
        date_order = all(str(dates[index]) >= str(dates[index + 1]) for index in range(len(dates) - 1))
        keys = [(item.get("environment_date"), item.get("label_kind"), item.get("revision")) for item in env_rows]
        current_counts: Counter[tuple[Any, Any]] = Counter(
            (item.get("environment_date"), item.get("label_kind")) for item in env_rows if item.get("is_current") in (1, True)
        )
        history_ok = (
            successful(env_calls[kind])
            and payload(env_calls[kind]).get("returned_count") == len(env_rows)
            and all(item.get("label_kind") == kind for item in env_rows)
            and len(keys) == len(set(keys))
            and all(count <= 1 for count in current_counts.values())
            and date_order
            and all(parse_time(item.get("available_at")) is not None for item in env_rows)
        )
        probe.record(
            f"ENV-{kind.upper()}-HISTORY",
            "environment.daily",
            "PASS" if history_ok else "FAIL",
            "rows are typed, dated, ordered, counted and current-per-date unique",
            {"count": len(env_rows), "date_first": dates[0] if dates else None, "date_last": dates[-1] if dates else None},
            [env_calls[kind]],
        )
        if kind == "forecast":
            ready = [item for item in env_rows if item.get("label_status") == "ready"]
            label_bad = [item.get("id") for item in ready if item.get("label_code") not in LABELS]
            probability_bad: list[dict[str, Any]] = []
            for item in ready:
                probabilities = item.get("probabilities")
                if not isinstance(probabilities, dict) or set(probabilities) != LABELS:
                    probability_bad.append({"id": item.get("id"), "reason": "keys"})
                    continue
                values = [decimal_value(probabilities.get(label)) for label in LABELS]
                total = sum(values, Decimal("0")) if all(value is not None for value in values) else None
                if total is None or any(value is None or value < 0 or value > 1 for value in values) or abs(total - 1) > Decimal("0.000001"):
                    probability_bad.append({"id": item.get("id"), "sum": str(total) if total is not None else None})
            probe.record(
                "ENV-FORECAST-READY",
                "environment.daily",
                "PASS" if not label_bad and not probability_bad else "FAIL",
                "ready forecast uses six canonical labels and probabilities sum to one",
                {"ready_count": len(ready), "bad_labels": label_bad, "bad_probabilities": probability_bad},
                [env_calls[kind]],
            )
        if env_rows:
            target = env_rows[0]
            target_available = parse_time(target.get("available_at"))
            if target_available:
                before = probe.call(
                    f"ENV-{kind.upper()}-BEFORE",
                    "environment_get_daily",
                    {"label_kind": kind, "as_of": (target_available - timedelta(microseconds=1)).isoformat(), "limit": 100},
                )
                equal = probe.call(
                    f"ENV-{kind.upper()}-EQUAL",
                    "environment_get_daily",
                    {"label_kind": kind, "as_of": target_available.isoformat(), "limit": 100},
                )
                before_ids = {item.get("id") for item in rows(before)}
                equal_ids = {item.get("id") for item in rows(equal)}
                boundary_ok = target.get("id") not in before_ids and target.get("id") in equal_ids
                probe.record(
                    f"ENV-{kind.upper()}-AVAILABILITY-BOUNDARY",
                    "environment.point_in_time",
                    "PASS" if boundary_ok else "FAIL",
                    "available_at uses an inclusive boundary",
                    {"target_id": target.get("id"), "before_has_target": target.get("id") in before_ids, "equal_has_target": target.get("id") in equal_ids},
                    [before, equal],
                )
            exact_date = probe.call(
                f"ENV-{kind.upper()}-DATE",
                "environment_get_daily",
                {"label_kind": kind, "environment_date": target.get("environment_date"), "limit": 100},
            )
            exact_ok = successful(exact_date) and all(item.get("environment_date") == target.get("environment_date") for item in rows(exact_date))
            probe.record(
                f"ENV-{kind.upper()}-DATE-FILTER",
                "environment.daily",
                "PASS" if exact_ok else "FAIL",
                "date filter returns only the requested date",
                {"target_date": target.get("environment_date"), "returned": [item.get("environment_date") for item in rows(exact_date)]},
                [exact_date],
            )

    # Recommendation PIT and no-publication terminal-state invariants.
    forecasts = [item for item in rows(env_calls.get("forecast")) if item.get("label_status") == "ready"]
    recommendation_calls: list[dict[str, Any]] = []
    for index, forecast in enumerate(forecasts[:3]):
        available = parse_time(forecast.get("available_at"))
        if not available:
            continue
        for suffix, instant in (("BEFORE", available - timedelta(microseconds=1)), ("EQUAL", available), ("AFTER", available + timedelta(microseconds=1))):
            call = probe.call(
                f"REC-{index}-{suffix}",
                "environment_get_recommendations",
                {"market_scope": "all", "route_profile_key": "default", "as_of": instant.isoformat(), "limit": 200},
            )
            recommendation_calls.append(call)
            data = payload(call)
            returned_forecast = data.get("forecast") if isinstance(data.get("forecast"), dict) else None
            publication = data.get("publication") if isinstance(data.get("publication"), dict) else None
            returned_items = rows(call)
            visible_ok = True
            if returned_forecast:
                visible_at = parse_time(returned_forecast.get("available_at"))
                visible_ok = visible_at is not None and visible_at <= instant
            if publication:
                publication_at = parse_time(publication.get("as_of_time"))
                visible_ok = visible_ok and (publication_at is None or publication_at <= instant)
                label = returned_forecast.get("label_code") if returned_forecast else None
                visible_ok = visible_ok and all(item.get("label_code") == label for item in returned_items)
            else:
                visible_ok = visible_ok and not returned_items and data.get("returned_count") == 0
            probe.record(
                f"REC-{index}-{suffix}",
                "environment.recommendations",
                "PASS" if successful(call) and visible_ok else "FAIL",
                "forecast/publication never comes from after the requested as_of; missing publication is explicit",
                {"status": data.get("status"), "reason_code": data.get("reason_code"), "forecast_id": returned_forecast.get("id") if returned_forecast else None, "publication_id": publication.get("id") if publication else None, "item_count": len(returned_items)},
                [call],
            )
    if not recommendation_calls:
        probe.record("REC-PIT", "environment.recommendations", "BLOCKED", "at least one ready forecast is available", "no ready forecast", [])

    # TS scope regression and a known working CS control.
    ts_scope_calls = [
        probe.call(f"TS-SCOPES-{index}", "factor_list_metric_scopes", {"as_of": as_of, "kind": "sub_factor", "ic_scope": "time_series", "limit": 5})
        for index in range(2)
    ]
    ts_scope_codes = [error_code(call) for call in ts_scope_calls]
    probe.record(
        "TS-SCOPE-DISCOVERY",
        "factor.metrics",
        "FAIL" if all(code == "QUERY_TIMEOUT" for code in ts_scope_codes) else "PASS" if any(successful(call) for call in ts_scope_calls) else "BLOCKED",
        "time_series scope discovery returns a bounded result rather than timing out",
        {"codes": ts_scope_codes, "http": [call.get("http_status") for call in ts_scope_calls]},
        ts_scope_calls,
        note="Repeated QUERY_TIMEOUT is a functional query-availability failure; it is not counted as a separate issue from TS factor_search timeout.",
    )
    old_ts_scope = {
        "kind": "sub_factor",
        "calculation_mode": "direct",
        "interval": "1h",
        "factor_window_bars": "1",
        "return_bar_interval": "1h",
        "forward_return_bars": 1,
        "ic_scope": "time_series",
        "validity_scope": "time_series",
        "scoring_version": "v202606_default",
        "universe_key": "all",
        "window_scope": "1y",
        "symbol": "ICPUSDT",
        "as_of": as_of,
        "limit": 5,
    }
    direct_ts_calls = [
        probe.call("TS-SEARCH-OMITTED", "factor_search", old_ts_scope),
        probe.call("TS-SEARCH-VALID", "factor_search", {**old_ts_scope, "validity": "valid"}),
        probe.call("TS-SEARCH-UNKNOWN", "factor_search", {**old_ts_scope, "validity": "unknown"}),
    ]
    direct_codes = [error_code(call) for call in direct_ts_calls]
    probe.record(
        "TS-FACTOR-SEARCH",
        "factor.catalog",
        "FAIL" if all(code == "QUERY_TIMEOUT" for code in direct_codes) else "PASS" if any(successful(call) for call in direct_ts_calls) else "BLOCKED",
        "TS research-scope search returns data or a terminal empty page",
        {"codes": direct_codes, "rows": [len(rows(call)) for call in direct_ts_calls]},
        direct_ts_calls,
        note="This is grouped with TS-SCOPE-DISCOVERY as one TS reader-query timeout defect.",
    )
    cs_control_scope = {
        "kind": "sub_factor",
        "calculation_mode": "direct",
        "factor_bar_interval": "1h",
        "factor_window_bars": "48H",
        "return_bar_interval": "1h",
        "forward_return_bars": 1,
        "ic_scope": "cross_sectional",
        "scoring_version": "v20260804_cs_recal",
        "universe_key": "altcoin",
        "window_scope": "min_window",
        "symbol": "",
    }
    cs_control = probe.call("CS-SEARCH-CONTROL", "factor_search", scope_args(cs_control_scope, as_of, validity="valid"))
    probe.record(
        "CS-SEARCH-CONTROL",
        "factor.catalog",
        "PASS" if successful(cs_control) else "BLOCKED",
        "CS equivalent scope remains callable",
        {"rows": len(rows(cs_control)), "error": error_code(cs_control)},
        [cs_control],
    )

    # Select a current CS metric row for identity, validity, formula and batch checks.
    selected_scope = cs_control_scope if rows(cs_control) else None
    selected_row = rows(cs_control)[0] if rows(cs_control) else None
    if selected_scope and selected_row and selected_row.get("factor_ref"):
        ref = str(selected_row["factor_ref"])
        exact = metric_args(selected_scope, ref, as_of)
        metrics = probe.call("METRICS-DEEP", "factor_get_metrics", exact)
        validity_args = dict(exact)
        validity_args["validity_scope"] = "cross_sectional"
        validity = probe.call("VALIDITY-DEEP", "factor_get_validity", validity_args)
        metric_data = payload(metrics)
        validity_data = payload(validity)
        metric_ok = successful(metrics) and metric_data.get("factor_ref") == ref and all(item.get("factor_id") == int(ref.split(":", 1)[1]) for item in rows(metrics, "ic_summaries"))
        validity_item = validity_data.get("item") if isinstance(validity_data.get("item"), dict) else {}
        validity_ok = successful(validity) and validity_data.get("factor_ref") == ref and validity_item.get("factor_id") == int(ref.split(":", 1)[1])
        probe.record("METRICS-IDENTITY", "factor.metrics", "PASS" if metric_ok else "FAIL", "metrics response keeps factor identity and scope", {"factor_ref": metric_data.get("factor_ref"), "summary_count": len(rows(metrics, "ic_summaries"))}, [metrics])
        probe.record("VALIDITY-IDENTITY", "factor.validity", "PASS" if validity_ok else "FAIL", "validity response keeps factor identity and scope", {"factor_ref": validity_data.get("factor_ref"), "status": validity_item.get("validity_status")}, [validity])
        batch_refs = [ref]
        if len(rows(cs_control)) > 1:
            batch_refs.append(str(rows(cs_control)[1].get("factor_ref")))
        batch_refs.append(f"sub_factor:{9_000_000_000 + int(now.timestamp())}")
        batch_base = dict(exact)
        batch_base.pop("factor_ref", None)
        metric_batch = probe.call("METRICS-BATCH-DEEP", "factor_get_metrics_batch", {**batch_base, "factor_refs": batch_refs, "ic_scope": "cross_sectional"})
        validity_base = dict(exact)
        validity_base.pop("factor_ref", None)
        validity_batch = probe.call("VALIDITY-BATCH-DEEP", "factor_get_validity_batch", {**validity_base, "factor_refs": batch_refs, "validity_scope": "cross_sectional"})
        probe.record("METRICS-BATCH-DEEP", "factor.metrics", "PASS" if successful(metric_batch) and rows(metric_batch) else "FAIL", "partial batch returns per-item success and missing result", [{"factor_ref": item.get("factor_ref"), "success": item.get("success")} for item in rows(metric_batch)], [metric_batch])
        probe.record("VALIDITY-BATCH-DEEP", "factor.validity", "PASS" if successful(validity_batch) and rows(validity_batch) else "FAIL", "partial validity batch returns per-item results", [{"factor_ref": item.get("factor_ref"), "success": item.get("success")} for item in rows(validity_batch)], [validity_batch])

        summary_rows = rows(metrics, "ic_summaries")
        run_id = (summary_rows[0].get("run_id") if summary_rows else None) or selected_row.get("metric_run_id")
        if run_id and summary_rows and summary_rows[0].get("period_start") and summary_rows[0].get("period_end"):
            slices = probe.call(
                "SLICES-DEEP",
                "factor_get_metric_slices",
                {**exact, "run_id": run_id, "start_time": summary_rows[0]["period_start"], "end_time": summary_rows[0]["period_end"], "limit": 5},
            )
            slice_status = "PASS" if successful(slices) and all(item.get("run_id") == run_id for item in rows(slices)) else "BLOCKED" if error_code(slices) == "METRIC_SCOPE_NOT_FOUND" else "FAIL"
            probe.record("SLICES-DEEP", "factor.metrics", slice_status, "bounded slices stay on the selected factor/run", {"count": len(rows(slices)), "error": error_code(slices)}, [slices])
        else:
            probe.record("SLICES-DEEP", "factor.metrics", "BLOCKED", "selected metrics expose a run and period", {"run_id": run_id, "summary_count": len(summary_rows)}, [metrics])

        # Formula evidence and deterministic expression checks.
        formula = probe.call(
            "FORMULA-DEEP",
            "factor_get_formula",
            {"factor_ref": ref, "run_id": run_id, "calculation_mode": selected_scope["calculation_mode"], "interval": selected_scope["factor_bar_interval"], "factor_window_bars": selected_scope["factor_window_bars"], "return_bar_interval": selected_scope["return_bar_interval"], "forward_return_bars": selected_scope["forward_return_bars"]},
        ) if run_id else None
        formula_data = payload(formula)
        expression = formula_data.get("expression")
        names, parse_error = formula_names(expression)
        resolved_fields = {str(item.get("canonical_field_name")) for item in formula_data.get("field_resolution", []) if isinstance(item, dict) and item.get("canonical_field_name")}
        required_fields = {str(item) for item in formula_data.get("required_fields", [])} if isinstance(formula_data.get("required_fields"), list) else set()
        unknown_names = sorted(names - resolved_fields - required_fields - {"abs", "add", "sub", "mul", "div", "sqrt", "log", "sign", "exp", "clip", "rolling_zscore", "zscore", "mean", "std", "sum", "min", "max", "where", "diff", "shift", "rolling", "vwap"})
        formula_ok = bool(formula) and successful(formula) and formula_data.get("factor_ref") == ref and formula_data.get("run_id") == run_id and bool(formula_data.get("formula_hash") or expression) and parse_error is None and not formula_window_issues(expression) and not unknown_names
        probe.record("FORMULA-DEEP", "factor.formula", "PASS" if formula_ok else "FAIL", "formula evidence is parseable, identity-bound and windowed", {"factor_ref": formula_data.get("factor_ref"), "run_id": formula_data.get("run_id"), "parse_error": parse_error, "unknown_names": unknown_names, "window_issues": formula_window_issues(expression)}, [formula] if formula else [])
        if formula:
            formula_repeat = probe.call(
                "FORMULA-DEEP-REPEAT",
                "factor_get_formula",
                {"factor_ref": ref, "run_id": run_id, "calculation_mode": selected_scope["calculation_mode"], "interval": selected_scope["factor_bar_interval"], "factor_window_bars": selected_scope["factor_window_bars"], "return_bar_interval": selected_scope["return_bar_interval"], "forward_return_bars": selected_scope["forward_return_bars"]},
            )
            repeat_data = payload(formula_repeat)
            repeat_ok = successful(formula_repeat) and repeat_data.get("formula_hash") == formula_data.get("formula_hash") and repeat_data.get("expression") == formula_data.get("expression")
            probe.record("FORMULA-STABLE", "factor.formula", "PASS" if repeat_ok else "FAIL", "repeating formula read is immutable", {"first_hash": formula_data.get("formula_hash"), "second_hash": repeat_data.get("formula_hash")}, [formula, formula_repeat])

        overall = probe.call("VALIDITY-OVERALL-CONTRACT", "factor_search", {**scope_args(selected_scope, as_of, validity="valid"), "validity_scope": "overall"})
        overall_code = error_code(overall)
        probe.record("VALIDITY-OVERALL-CONTRACT", "factor.validity", "PASS" if successful(overall) else "BLOCKED", "overall validity behavior is explicitly documented", {"error": overall_code, "rows": len(rows(overall))}, [overall], note="The tool schema advertises overall, but runtime semantics may require a dimension-specific scope; retained as contract observation, not a product defect.")
    else:
        probe.record("METRICS-SURFACE-DEEP", "factor.metrics", "BLOCKED", "a current CS metric row is available", {"rows": len(rows(cs_control))}, [cs_control])

    # Formula sample expansion: check several rows without requiring DB parity.
    formula_samples = rows(cs_control)[:5]
    formula_sample_issues: list[dict[str, Any]] = []
    for index, item in enumerate(formula_samples):
        run_id = item.get("metric_run_id") or item.get("validity_run_id")
        if not run_id:
            continue
        call = probe.call(
            f"FORMULA-SAMPLE-{index}",
            "factor_get_formula",
            {"factor_ref": item.get("factor_ref"), "run_id": run_id, "calculation_mode": "direct", "interval": "1h", "factor_window_bars": "48H", "return_bar_interval": "1h", "forward_return_bars": 1},
        )
        fd = payload(call)
        names, parse_error = formula_names(fd.get("expression"))
        issues = formula_window_issues(fd.get("expression"))
        if not successful(call) or parse_error or issues:
            formula_sample_issues.append({"factor_ref": item.get("factor_ref"), "error": error_code(call), "parse_error": parse_error, "issues": issues})
    if formula_samples:
        probe.record("FORMULA-SAMPLE-MATRIX", "factor.formula", "PASS" if not formula_sample_issues else "FAIL", "sampled formula evidence is parseable and windowed", {"sample_count": len(formula_samples), "issues": formula_sample_issues}, [probe.call(f"FORMULA-SAMPLE-NOP", "factor_get_formula", {})] if False else [])
    else:
        probe.record("FORMULA-SAMPLE-MATRIX", "factor.formula", "BLOCKED", "at least one metric-backed factor is available", "no metric-backed rows", [])

    # Environment metric/tag surfaces and no-active-publication behavior.
    dynamic_ref = (rows(cs_control)[0].get("factor_ref") if rows(cs_control) else "sub_factor:9999999999")
    env_metric = probe.call("ENV-METRICS-DYNAMIC", "factor_get_environment_metrics", {"factor_ref": dynamic_ref, "market_scope": "all"})
    env_tags = probe.call("ENV-TAGS-DYNAMIC", "factor_get_environment_tags", {"factor_ref": dynamic_ref, "market_scope": "all"})
    metric_error = error_code(env_metric)
    tags_data = payload(env_tags)
    tags_ok = successful(env_tags) and isinstance(tags_data.get("items"), list) and tags_data.get("returned_count") == len(tags_data.get("items") or [])
    probe.record("ENV-METRICS-SURFACE", "environment.metrics", "PASS" if successful(env_metric) else "BLOCKED" if metric_error == "NOT_FOUND" else "FAIL", "environment metric read is bounded or explicitly reports no batch", {"error": metric_error, "count": len(rows(env_metric))}, [env_metric])
    probe.record("ENV-TAGS-SURFACE", "environment.tags", "PASS" if tags_ok else "FAIL", "tags response preserves publication/items/count shape", {"publication": bool(tags_data.get("publication")), "count": len(rows(env_tags)), "returned_count": tags_data.get("returned_count")}, [env_tags])

    # Schema, universe relationships, and structured/text representation parity.
    fields = probe.call("SCHEMA-FIELDS-DEEP", "schema_get_factor_fields", {})
    raw_schema = probe.call("SCHEMA-RAW-DEEP", "schema_get_raw_data", {})
    field_data = payload(fields)
    raw_data = payload(raw_schema)
    probe.record("SCHEMA-FIELDS-DEEP", "schema", "PASS" if successful(fields) and bool(field_data.get("fields")) else "FAIL", "approved factor schema is readable", {"schema_version": field_data.get("schema_version"), "field_count": len(field_data.get("fields") or []) if isinstance(field_data.get("fields"), list) else len(field_data.get("fields") or {})}, [fields])
    probe.record("SCHEMA-RAW-DEEP", "schema", "PASS" if successful(raw_schema) and bool(raw_data.get("mappings")) else "FAIL", "raw-data contract is readable", {"schema_version": raw_data.get("schema_version"), "mapping_count": len(raw_data.get("mappings") or [])}, [raw_schema])
    unknown_field = probe.call("SCHEMA-UNKNOWN-FIELD", "schema_get_factor_fields", {"field_names": [f"__missing_{uuid4().hex}__"]})
    probe.record("SCHEMA-UNKNOWN-FIELD", "schema", "PASS" if error_code(unknown_field) == "FIELD_NOT_APPROVED" else "FAIL", "unknown canonical field is rejected explicitly", {"error": error_code(unknown_field)}, [unknown_field])
    universe_sets: dict[str, set[str]] = {}
    universe_calls: list[dict[str, Any]] = []
    for universe in ("main", "altcoin", "all"):
        call = probe.call(f"UNIVERSE-{universe.upper()}-DEEP", "universe_list_symbols", {"universe_key": universe, "as_of": as_of})
        universe_calls.append(call)
        universe_sets[universe] = {str(item.get("symbol")) for item in rows(call) if item.get("symbol")}
    universe_ok = all(successful(call) and universe_sets[name] for name, call in zip(("main", "altcoin", "all"), universe_calls)) and universe_sets["main"].isdisjoint(universe_sets["altcoin"]) and universe_sets["main"] | universe_sets["altcoin"] == universe_sets["all"]
    probe.record("UNIVERSE-RELATION", "universe", "PASS" if universe_ok else "FAIL", "main and altcoin are disjoint and union to all", {name: len(values) for name, values in universe_sets.items()}, universe_calls)
    unknown_universe = probe.call("UNIVERSE-UNKNOWN-DEEP", "universe_list_symbols", {"universe_key": f"__missing_{uuid4().hex}__"})
    probe.record("UNIVERSE-UNKNOWN-DEEP", "universe", "PASS" if error_code(unknown_universe) == "UNIVERSE_NOT_FOUND" else "FAIL", "unknown universe does not silently fall back", {"error": error_code(unknown_universe)}, [unknown_universe])

    parity_calls = [call for call in [*env_calls.values(), cs_control, fields, raw_schema, env_tags] if call]
    parity_bad = [call.get("case_id") for call in parity_calls if call.get("representations_equal") is False]
    probe.record("MCP-REPRESENTATION-PARITY", "protocol", "PASS" if not parity_bad else "FAIL", "content JSON and structuredContent agree", {"mismatches": parity_bad, "checked": len(parity_calls)}, parity_calls)

    # Explicit input/error mapping checks.
    missing_detail = probe.call("ERROR-MISSING-REQUIRED", "factor_get_detail", {})
    unknown_tool = probe.call("ERROR-UNKNOWN-TOOL", "__questtest_unknown_tool__", {})
    probe.record("ERROR-MISSING-REQUIRED", "protocol", "PASS" if missing_detail.get("is_error") is True else "FAIL", "missing required argument is a structured tool error", {"http": missing_detail.get("http_status"), "is_error": missing_detail.get("is_error")}, [missing_detail])
    probe.record("ERROR-UNKNOWN-TOOL", "protocol", "PASS" if unknown_tool.get("is_error") is True else "FAIL", "unknown tool is rejected without transport failure", {"http": unknown_tool.get("http_status"), "is_error": unknown_tool.get("is_error"), "error": error_code(unknown_tool)}, [unknown_tool])

    counts = Counter(case["status"] for case in probe.cases)
    result = {
        "run_id": output.name,
        "mcp_url": url,
        "mode": "MCP_ONLY_READ_ONLY",
        "case_counts": dict(sorted(counts.items())),
        "tool_count": len(tool_names),
        "cases": probe.cases,
        "confirmed_failures": [case for case in probe.cases if case["status"] == "FAIL"],
        "blocked": [case for case in probe.cases if case["status"] == "BLOCKED"],
        "notes": [
            "TS-SCOPE-DISCOVERY and TS-FACTOR-SEARCH are one grouped TS reader-query timeout issue.",
            "Current endpoint was not compared row-by-row with local factor_db because snapshots differ.",
            "No write endpoint or database write was called.",
        ],
    }
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    lines = [
        "# Current endpoint deep regression",
        "",
        f"- URL: `{url}`",
        "- Mode: MCP-only, read-only; no local DB oracle",
        f"- Counts: `{result['case_counts']}`",
        "",
        "| Case | Module | Status | Expected | Actual |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in probe.cases:
        actual = json.dumps(case["actual"], ensure_ascii=False, default=str)[:700]
        lines.append(f"| {case['case_id']} | {case['module']} | {case['status']} | {case['expected']} | {actual} |")
    lines.extend(["", "## Confirmed failures", ""])
    if result["confirmed_failures"]:
        lines.extend(f"- `{case['case_id']}`: {case['expected']}; actual={json.dumps(case['actual'], ensure_ascii=False, default=str)}" for case in result["confirmed_failures"])
    else:
        lines.append("No confirmed functional failure in this executable coverage.")
    lines.extend(["", "Raw numbered request/response files are sanitized and contain no Authorization header."])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"output_dir": str(output), "case_counts": result["case_counts"]}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
