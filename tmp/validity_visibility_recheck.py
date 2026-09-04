#!/usr/bin/env python3
"""Reconcile incomplete validity rows with the Factor Data MCP read path.

The check is deliberately read-only.  It discovers active route factors from the
test database, finds their newest validity rows, and compares the MCP response
for those exact scopes with the underlying summary rows.  Incomplete historical
rows are reported as a data-quality observation unless the MCP exposes them as
valid evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymysql

from config.settings import SettingsLoader


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
OUTPUT_ROOT = ROOT / "reports" / "factor4-deep"
TOKEN_ENV = "VALIDITY_MCP_TOKEN"
LATEST_TOKEN_ENV = "LATEST_MCP_TOKEN"
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)


def json_default(value: Any) -> str:
    """Serialize database-native values for evidence JSON."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def redact(value: Any) -> Any:
    """Remove credential-like fields from a nested evidence object."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one UTF-8, credential-free JSON artifact."""

    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )


def parse_body(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse an MCP JSON response or one SSE data event."""

    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" not in content_type.lower():
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    events: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        data = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            value = json.loads("\n".join(data))
            if isinstance(value, dict):
                events.append(value)
    if len(events) != 1:
        raise ValueError(f"expected one MCP data event, got {len(events)}")
    return events[0]


class McpClient:
    """Minimal MCP HTTP client with sanitized request/response capture."""

    def __init__(self, token: str, output_dir: Path, label: str) -> None:
        """Initialize a client for one bearer token and evidence directory."""

        self.token = token
        self.output_dir = output_dir
        self.label = label
        self.sequence = 0
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _headers(self) -> dict[str, str]:
        """Build protocol headers without persisting credentials."""

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "QuestTest-validity-visibility/1.0",
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        return headers

    def request(
        self,
        case_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        notification: bool = False,
    ) -> dict[str, Any]:
        """Send one MCP request and return transport plus parsed business data."""

        self.sequence += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = f"{case_id}-{uuid4()}"
        if params is not None:
            payload["params"] = params
        request = urllib.request.Request(
            MCP_URL,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            headers=self._headers(),
            method="POST",
        )
        started = time.monotonic()
        response_headers: dict[str, str] = {}
        raw = b""
        status = 0
        transport_error: str | None = None
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
                status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            transport_error = f"{type(exc).__name__}: {exc}"
        elapsed = round(time.monotonic() - started, 3)
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        parse_error: str | None = None
        envelope: dict[str, Any] | None = None
        if raw:
            try:
                envelope = parse_body(raw, response_headers.get("content-type", ""))
            except Exception as exc:  # retain transport evidence on malformed data
                parse_error = f"{type(exc).__name__}: {exc}"
        result = envelope.get("result") if isinstance(envelope, dict) else None
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        text_business: dict[str, Any] | None = None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text_value = content[0].get("text")
            if isinstance(text_value, str):
                try:
                    parsed = json.loads(text_value)
                    text_business = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    text_business = None
        business = structured if isinstance(structured, dict) else text_business
        call = {
            "case_id": case_id,
            "client": self.label,
            "method": method,
            "http_status": status,
            "elapsed_seconds": elapsed,
            "content_type": response_headers.get("content-type"),
            "parse_error": parse_error,
            "transport_error": transport_error,
            "envelope": envelope,
            "business": business if isinstance(business, dict) else {},
            "representations_equal": (
                structured == text_business
                if isinstance(structured, dict) and isinstance(text_business, dict)
                else None
            ),
        }
        stem = f"{self.sequence:03d}-{self.label}-{case_id}"
        write_json(self.output_dir / f"{stem}.request.json", payload)
        if envelope is not None:
            write_json(self.output_dir / f"{stem}.response.json", envelope)
        else:
            (self.output_dir / f"{stem}.response.txt").write_text(
                raw.decode("utf-8", "replace") if raw else (transport_error or ""),
                encoding="utf-8",
            )
        return call

    def initialize(self) -> dict[str, Any]:
        """Negotiate the MCP protocol and send the initialized notification."""

        call = self.request(
            "MCP-INIT",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "QuestTest-validity-visibility", "version": "1.0"},
            },
        )
        result = (call.get("envelope") or {}).get("result") or {}
        if isinstance(result, dict):
            version = result.get("protocolVersion")
            self.protocol_version = str(version) if version else None
        self.request("MCP-NOTIFY", "notifications/initialized", {}, notification=True)
        return call

    def tool(self, case_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a named MCP tool."""

        return self.request(
            case_id,
            "tools/call",
            {"name": name, "arguments": arguments},
        )


def business(call: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized structured business envelope."""

    value = call.get("business")
    return value if isinstance(value, dict) else {}


def error_code(call: dict[str, Any]) -> str | None:
    """Extract a business or JSON-RPC error code."""

    envelope = call.get("envelope") or {}
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        code = envelope["error"].get("code")
        return str(code) if code is not None else None
    value = business(call).get("error")
    if isinstance(value, dict):
        for key in ("code", "error_code", "type"):
            if value.get(key) is not None:
                return str(value[key])
    return None


def is_success(call: dict[str, Any]) -> bool:
    """Return whether an MCP tool returned a successful business envelope."""

    envelope = call.get("envelope")
    return bool(
        call.get("http_status") == 200
        and call.get("parse_error") is None
        and isinstance(envelope, dict)
        and isinstance(envelope.get("result"), dict)
        and envelope["result"].get("isError") is not True
        and isinstance(business(call), dict)
        and "error" not in business(call)
    )


def data(call: dict[str, Any]) -> dict[str, Any]:
    """Return the business data object, or an empty object."""

    value = business(call).get("data")
    return value if isinstance(value, dict) else {}


def db_read_snapshot() -> dict[str, Any]:
    """Discover active routes and validity/summary evidence in one read-only snapshot."""

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
        read_timeout=120,
        write_timeout=30,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                """
                SELECT r.id AS route_id, r.factor_id, r.factor_ref, r.factor_type,
                       r.metric_id, r.label_code, r.rank_no, r.market_scope,
                       r.label_kind, r.as_of_time, r.factor_version AS route_factor_version,
                       r.score_rule_version AS route_score_rule_version, r.evidence,
                       m.evaluation_type, m.interval, m.return_bar_interval,
                       m.forward_return_bars, m.window_scope, m.scoring_version,
                       m.metric_status, m.is_valid AS metric_is_valid,
                       m.factor_version AS metric_factor_version
                FROM market_environment_factor_route AS r
                JOIN market_environment_factor_metric AS m ON m.id = r.metric_id
                WHERE r.is_active = 1
                ORDER BY r.eval_batch_id DESC, r.label_code, r.rank_no
                """
            )
            routes = [dict(row) for row in cursor.fetchall()]
            factor_ids = sorted({int(row["factor_id"]) for row in routes if row["factor_type"] == "sub_factor"})
            if not factor_ids:
                raise RuntimeError("No active sub-factor routes found")
            placeholders = ",".join(["%s"] * len(factor_ids))
            cursor.execute(
                f"""
                SELECT id, run_id, factor_id, is_sub_factor_id, serial_number,
                       universe_key, factor_bar_interval, factor_window_bars,
                       return_bar_interval, forward_return_bars, window_scope,
                       period_start, period_end, time_series_summary_id,
                       cross_sectional_summary_id, time_series_scoring_version,
                       time_series_score, time_series_status, time_series_is_valid,
                       cross_sectional_scoring_version, cross_sectional_score,
                       cross_sectional_status, cross_sectional_is_valid, overall_score,
                       overall_status, overall_is_valid, validity_threshold,
                       created_at, updated_at
                FROM factor_validity_status
                WHERE is_sub_factor_id = 1 AND factor_id IN ({placeholders})
                ORDER BY factor_id, updated_at DESC, id DESC
                """,
                factor_ids,
            )
            validity_rows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                f"""
                SELECT id, run_id, factor_id, is_sub_factor_id, ic_scope,
                       calculation_mode, factor_bar_interval, factor_window_bars,
                       return_bar_interval, forward_return_bars, universe_key, symbol,
                       window_scope, scoring_version, period_start, period_end,
                       valid_slice_count, coverage_mean, mean_ic, mean_rank_ic, icir,
                       rank_icir, final_score, created_at, updated_at
                FROM factor_ic_summary_metrics
                WHERE is_sub_factor_id = 1 AND factor_id IN ({placeholders})
                """,
                factor_ids,
            )
            summaries = [dict(row) for row in cursor.fetchall()]
            run_ids = sorted({str(row["run_id"]) for row in validity_rows})
            run_placeholders = ",".join(["%s"] * len(run_ids))
            cursor.execute(
                f"""
                SELECT run_id, status, created_at, completed_at
                FROM factor_ic_runs
                WHERE run_id IN ({run_placeholders})
                """,
                run_ids,
            )
            runs = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(time_series_is_valid = 1) AS ts_valid,
                       SUM(cross_sectional_is_valid = 1) AS cs_valid,
                       SUM(overall_is_valid = 1) AS overall_valid,
                       SUM(time_series_is_valid = 1 AND time_series_summary_id IS NULL) AS ts_valid_null_fk,
                       SUM(time_series_is_valid = 1 AND time_series_summary_id IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM factor_ic_summary_metrics s
                                           WHERE s.id = factor_validity_status.time_series_summary_id)) AS ts_valid_dangling_fk,
                       SUM(cross_sectional_is_valid = 1 AND cross_sectional_summary_id IS NULL) AS cs_valid_null_fk,
                       SUM(cross_sectional_is_valid = 1 AND cross_sectional_summary_id IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM factor_ic_summary_metrics s
                                           WHERE s.id = factor_validity_status.cross_sectional_summary_id)) AS cs_valid_dangling_fk
                FROM factor_validity_status
                """
            )
            global_counts = dict(cursor.fetchone())
            connection.rollback()
    finally:
        connection.close()

    route_by_factor: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for route in routes:
        if route["factor_type"] != "sub_factor":
            continue
        evidence = route.get("evidence")
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except json.JSONDecodeError:
                evidence = {}
        route["evidence"] = evidence if isinstance(evidence, dict) else {}
        route_by_factor[int(route["factor_id"])].append(route)
    validity_by_factor: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in validity_rows:
        validity_by_factor[int(row["factor_id"])].append(row)
    latest_by_factor = {
        factor_id: rows[0] for factor_id, rows in validity_by_factor.items() if rows
    }
    summary_by_run_scope: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in summaries:
        summary_by_run_scope[(int(row["factor_id"]), str(row["run_id"]))].append(row)
    run_by_id = {str(row["run_id"]): row for row in runs}
    candidates: list[dict[str, Any]] = []
    for factor_id, validity in latest_by_factor.items():
        ts_valid = validity.get("time_series_status") == "valid" and validity.get("time_series_is_valid") == 1
        cs_valid = validity.get("cross_sectional_status") == "valid" and validity.get("cross_sectional_is_valid") == 1
        missing_fk = validity.get("time_series_summary_id") is None or validity.get("cross_sectional_summary_id") is None
        if not (missing_fk and (ts_valid or cs_valid)):
            continue
        route = route_by_factor.get(factor_id, [None])[0]
        candidates.append(
            {
                "factor_ref": f"sub_factor:{factor_id}",
                "factor_id": factor_id,
                "validity": validity,
                "route": route,
                "same_run_summaries": summary_by_run_scope.get((factor_id, str(validity["run_id"])), []),
                "run": run_by_id.get(str(validity["run_id"])),
            }
        )
    candidates.sort(key=lambda item: (str(item["validity"].get("updated_at")), item["factor_id"]), reverse=True)

    complete_controls = [
        row
        for row in validity_rows
        if row.get("time_series_summary_id") is not None
        and row.get("cross_sectional_summary_id") is not None
        and row.get("time_series_status") in {"valid", "invalid"}
    ]
    control = complete_controls[0] if complete_controls else None
    control_summaries = (
        summary_by_run_scope.get((int(control["factor_id"]), str(control["run_id"])), [])
        if control
        else []
    )
    return {
        "environment": "test",
        "database": settings.name,
        "read_only": True,
        "route_count": len(routes),
        "active_sub_factor_count": len(route_by_factor),
        "global_validity_counts": global_counts,
        "routes": routes,
        "validity_rows_for_active_route_factors": validity_rows,
        "summaries_for_active_route_factors": summaries,
        "runs_for_active_route_validity": runs,
        "candidates": candidates,
        "complete_control": {"validity": control, "summaries": control_summaries} if control else None,
    }


def scope_args(row: dict[str, Any], validity_scope: str, *, run_id: str | None = None) -> dict[str, Any]:
    """Build exact MCP validity/metric scope arguments from one DB row."""

    version_key = "time_series_scoring_version" if validity_scope == "time_series" else "cross_sectional_scoring_version"
    args: dict[str, Any] = {
        "factor_ref": f"sub_factor:{row['factor_id']}",
        "calculation_mode": "direct",
        "universe_key": row["universe_key"],
        "window_scope": row["window_scope"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scoring_version": row[version_key],
        "symbol": "",
    }
    if run_id is not None:
        args["run_id"] = run_id
    return args


def compact_call(call: dict[str, Any]) -> dict[str, Any]:
    """Reduce an MCP call to report-safe verdict fields."""

    value = data(call)
    item = value.get("item")
    items = value.get("items")
    compact: dict[str, Any] = {
        "http_status": call.get("http_status"),
        "elapsed_seconds": call.get("elapsed_seconds"),
        "is_error": ((call.get("envelope") or {}).get("result") or {}).get("isError")
        if isinstance(call.get("envelope"), dict)
        else None,
        "error_code": error_code(call),
        "success": is_success(call),
        "representations_equal": call.get("representations_equal"),
    }
    if isinstance(item, dict):
        compact["item"] = {
            key: item.get(key)
            for key in (
                "id",
                "run_id",
                "factor_id",
                "factor_window_bars",
                "window_scope",
                "universe_key",
                "time_series_summary_id",
                "cross_sectional_summary_id",
                "time_series_status",
                "time_series_is_valid",
                "cross_sectional_status",
                "cross_sectional_is_valid",
                "overall_status",
                "overall_is_valid",
            )
        }
    if isinstance(items, list):
        compact["items"] = [
            {
                "factor_ref": item.get("factor_ref"),
                "success": item.get("success"),
                "id": (item.get("data") or {}).get("id") if isinstance(item.get("data"), dict) else None,
                "error_code": ((item.get("error") or {}).get("code")) if isinstance(item.get("error"), dict) else None,
            }
            for item in items
            if isinstance(item, dict)
        ]
    return compact


def main() -> None:
    """Run the dynamic read-only validity visibility comparison."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = OUTPUT_ROOT / f"{stamp}-validity-visibility"
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = db_read_snapshot()
    write_json(output_dir / "db-snapshot.json", snapshot)

    calls: dict[str, dict[str, Any]] = {}
    latest_token = os.environ.get(LATEST_TOKEN_ENV)
    if latest_token:
        latest_client = McpClient(latest_token, output_dir, "latest-token")
        calls["latest_token_initialize"] = latest_client.initialize()

    client = McpClient(token, output_dir, "usable-token")
    calls["initialize"] = client.initialize()
    candidates = snapshot["candidates"]
    for candidate in candidates:
        row = candidate["validity"]
        run_id = str(row["run_id"])
        for scope in ("time_series", "cross_sectional"):
            args = scope_args(row, scope, run_id=run_id)
            calls[f"validity_{candidate['factor_id']}_{scope}"] = client.tool(
                f"VALIDITY-{candidate['factor_id']}-{scope}",
                "factor_get_validity",
                {**args, "validity_scope": scope},
            )
            calls[f"metrics_{candidate['factor_id']}_{scope}"] = client.tool(
                f"METRICS-{candidate['factor_id']}-{scope}",
                "factor_get_metrics",
                {**args, "ic_scope": scope},
            )

    if candidates:
        first = candidates[0]["validity"]
        no_run_args = scope_args(first, "time_series")
        calls["validity_first_without_run_id"] = client.tool(
            "VALIDITY-FIRST-NO-RUN",
            "factor_get_validity",
            {**no_run_args, "validity_scope": "time_series"},
        )
        missing_ref = f"sub_factor:{9_000_000_000 + int(datetime.now().timestamp())}"
        batch_args = scope_args(first, "time_series", run_id=str(first["run_id"]))
        batch_args.pop("factor_ref", None)
        batch_args["factor_refs"] = [f"sub_factor:{first['factor_id']}", missing_ref]
        batch_args["validity_scope"] = "time_series"
        calls["validity_batch_incomplete_and_missing"] = client.tool(
            "VALIDITY-BATCH-INCOMPLETE-MISSING",
            "factor_get_validity_batch",
            batch_args,
        )

    control = snapshot.get("complete_control")
    if isinstance(control, dict) and isinstance(control.get("validity"), dict):
        row = control["validity"]
        for scope in ("time_series", "cross_sectional"):
            args = scope_args(row, scope, run_id=str(row["run_id"]))
            calls[f"control_validity_{row['factor_id']}_{scope}"] = client.tool(
                f"CONTROL-VALIDITY-{row['factor_id']}-{scope}",
                "factor_get_validity",
                {**args, "validity_scope": scope},
            )

    summary: dict[str, Any] = {
        "environment": "test",
        "read_only": True,
        "mcp_url": MCP_URL,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(candidates),
        "candidate_factor_refs": [item["factor_ref"] for item in candidates],
        "calls": {key: compact_call(call) for key, call in calls.items()},
        "database_global_counts": snapshot["global_validity_counts"],
        "observations": [],
    }
    latest_probe = summary["calls"].get("latest_token_initialize")
    if latest_probe:
        summary["observations"].append(
            {
                "code": "LATEST_TOKEN_AUTH_PROBE",
                "detail": "The supplied latest token was probed separately; its transport/business status is recorded without the token.",
                "http_status": latest_probe.get("http_status"),
                "error_code": latest_probe.get("error_code"),
            }
        )
    candidate_validity_codes = [
        summary["calls"].get(f"validity_{item['factor_id']}_time_series", {}).get("error_code")
        for item in candidates
    ]
    candidate_metric_success = [
        summary["calls"].get(f"metrics_{item['factor_id']}_time_series", {}).get("success")
        for item in candidates
    ]
    if candidates and all(code == "VALIDITY_SCOPE_NOT_FOUND" for code in candidate_validity_codes):
        summary["observations"].append(
            {
                "code": "INCOMPLETE_VALIDITY_SUPPRESSED",
                "detail": "MCP did not expose database validity rows whose valid flag lacks summary evidence.",
                "candidate_validity_error_codes": candidate_validity_codes,
                "candidate_metric_reads_successful": candidate_metric_success,
                "classification": "database_history_observation_not_mcp_defect",
            }
        )
    control_calls = [value for key, value in summary["calls"].items() if key.startswith("control_validity_")]
    if control_calls and all(value.get("success") for value in control_calls):
        summary["observations"].append(
            {
                "code": "COMPLETE_VALIDITY_CONTROL_VISIBLE",
                "detail": "A validity row with both summary foreign keys populated was returned by MCP for both scopes.",
                "classification": "mcp_validity_path_operational",
            }
        )
    write_json(output_dir / "results.json", summary)
    lines = [
        "# Validity visibility recheck",
        "",
        f"- Environment: `test`; mode: `read-only`",
        f"- Active routes: `{snapshot['route_count']}`; active sub-factors: `{snapshot['active_sub_factor_count']}`",
        f"- Latest active-route validity candidates: `{len(candidates)}`",
        f"- Candidate refs: `{', '.join(item['factor_ref'] for item in candidates) or 'none'}`",
        "",
        "## Classification",
        "",
        "- Rows with `is_valid=1` and missing summary evidence are a database history observation.",
        "- MCP is considered affected only if it returns those incomplete rows as valid evidence, or hides complete rows.",
        "- See `results.json` for per-call status and `db-snapshot.json` for the consistent read snapshot.",
    ]
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "candidate_count": len(candidates), "observations": summary["observations"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
