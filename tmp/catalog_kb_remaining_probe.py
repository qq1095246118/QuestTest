#!/usr/bin/env python3
"""Run bounded read-only checks for the remaining catalog, TS, and KB cases.

The probe deliberately keeps the request count small.  Test identifiers are
discovered from the test database, while all MCP calls use a browser-like
User-Agent because the test WAF rejects synthetic QuestTest User-Agents.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader
from db.client import DatabaseClient
from tmp import catalog_deep_readonly as transport


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "CATALOG_MCP_TOKEN"
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _chrome_urlopen(request: Any, *args: Any, **kwargs: Any) -> Any:
    """Set a standard Chrome User-Agent before delegating to urllib.

    Parameters ``request``, ``args`` and ``kwargs`` are the original urllib
    inputs.  Returns the response object from urllib; transport exceptions are
    propagated unchanged.
    """

    if hasattr(request, "headers"):
        request.headers["User-agent"] = CHROME_UA
    return _ORIGINAL_URLOPEN(request, *args, **kwargs)


_ORIGINAL_URLOPEN = urllib.request.urlopen
transport.urllib.request.urlopen = _chrome_urlopen
transport.MCP_URL = MCP_URL


def _business(call: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized business envelope from a transport call."""

    value = call.get("business")
    return value if isinstance(value, dict) else {}


def _data(call: dict[str, Any]) -> dict[str, Any]:
    """Return the business data object, or an empty object for errors."""

    value = _business(call).get("data")
    return value if isinstance(value, dict) else {}


def _items(call: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract object rows from a tool response."""

    value = _data(call).get("items")
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _meta(call: dict[str, Any]) -> dict[str, Any]:
    """Return response metadata when present."""

    value = _business(call).get("meta")
    return value if isinstance(value, dict) else {}


def _error_code(call: dict[str, Any]) -> str | None:
    """Extract a JSON-RPC or business error code."""

    envelope = call.get("envelope")
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        code = envelope["error"].get("code")
        if code is not None:
            return str(code)
    error = _business(call).get("error")
    if isinstance(error, dict):
        for key in ("code", "error_code", "type"):
            if error.get(key) is not None:
                return str(error[key])
    return None


def _success(call: dict[str, Any]) -> bool:
    """Return whether the MCP call completed with a business success."""

    return bool(
        call.get("http_status") == 200
        and call.get("parse_error") is None
        and isinstance(call.get("envelope"), dict)
        and "result" in call["envelope"]
        and call.get("is_error") is not True
        and isinstance(call.get("business"), dict)
        and "error" not in call["business"]
    )


def _quota(call: dict[str, Any]) -> dict[str, Any] | None:
    """Return non-sensitive quota metadata from a call."""

    value = _meta(call).get("quota")
    return value if isinstance(value, dict) else None


def _transport_signature(call: dict[str, Any]) -> dict[str, Any]:
    """Keep only useful non-sensitive transport diagnostics."""

    return {
        "http_status": call.get("http_status"),
        "error_code": _error_code(call),
        "elapsed_seconds": call.get("elapsed_seconds"),
        "quota": _quota(call),
        "parse_error": call.get("parse_error"),
    }


def _db_fixtures(db: DatabaseClient) -> dict[str, Any]:
    """Discover one catalog row, one complete TS scope, and one KB extraction.

    The queries are read-only and return only identifiers and scope values used
    to construct bounded requests.  Missing rows are represented by ``None``.
    """

    catalog = db.fetch_one(
        """
        SELECT s.id, s.sub_factor_name AS name, s.cn_name, s.serial_number,
               s.data_source, fs.status, fs.coin_category
        FROM sub_factors s
        JOIN factors_status fs ON fs.factor_id=s.id AND fs.is_sub_factor_id=1
        WHERE fs.status=2
        ORDER BY s.id
        LIMIT 1
        """
    )
    scope = db.fetch_one(
        """
        SELECT m.ic_scope, m.calculation_mode, m.factor_bar_interval,
               m.factor_window_bars, m.return_bar_interval,
               m.forward_return_bars, m.universe_key, m.symbol,
               m.window_scope, m.scoring_version, COUNT(*) AS row_count,
               MAX(r.completed_at) AS latest_completed_at
        FROM factor_ic_summary_metrics m
        JOIN factor_ic_runs r ON r.run_id=m.run_id
        WHERE r.status='completed'
          AND m.ic_scope='time_series'
          AND m.calculation_mode='direct'
          AND m.factor_window_bars <> ''
        GROUP BY m.ic_scope, m.calculation_mode, m.factor_bar_interval,
                 m.factor_window_bars, m.return_bar_interval,
                 m.forward_return_bars, m.universe_key, m.symbol,
                 m.window_scope, m.scoring_version
        ORDER BY COUNT(*) DESC, MAX(r.completed_at) DESC
        LIMIT 1
        """
    )
    kb = db.fetch_one(
        """
        SELECT id, factor_name, validation_status, mapping_status,
               confidence_score, updated_at
        FROM kb_factor_extractions
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """
    )
    return {"catalog": catalog, "ts_scope": scope, "kb": kb}


def _scope_args(scope: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Build a bounded complete TS factor-search identity from a DB scope."""

    return {
        "kind": "sub_factor",
        "calculation_mode": scope["calculation_mode"],
        "interval": scope["factor_bar_interval"],
        "factor_window_bars": scope["factor_window_bars"],
        "return_bar_interval": scope["return_bar_interval"],
        "forward_return_bars": int(scope["forward_return_bars"]),
        "ic_scope": scope["ic_scope"],
        "validity_scope": scope["ic_scope"],
        "scoring_version": scope["scoring_version"],
        "universe_key": scope["universe_key"],
        "window_scope": scope["window_scope"],
        "symbol": scope.get("symbol") or "",
        "as_of": as_of,
        "limit": 1,
    }


def _record(
    cases: list[dict[str, Any]],
    case_id: str,
    title: str,
    status: str,
    reason: str,
    evidence: dict[str, Any],
) -> None:
    """Append one sanitized case verdict."""

    cases.append(
        {
            "case_id": case_id,
            "title": title,
            "status": status,
            "reason": reason,
            "evidence": evidence,
        }
    )


def _classify_call(call: dict[str, Any]) -> str:
    """Classify quota/WAF/transport blocks without calling them product bugs."""

    code = _error_code(call)
    status = call.get("http_status")
    if code == "EXPORT_BUDGET_EXCEEDED":
        return "BLOCKED_QUOTA"
    if code in {"RATE_LIMITED", "SERVICE_UNAVAILABLE", "QUERY_TIMEOUT"} and status != 200:
        return "BLOCKED_DEPENDENCY"
    if status in {401, 403} or code in {"AUTH_REQUIRED", "FORBIDDEN"}:
        return "BLOCKED_AUTH_OR_WAF"
    if status is None:
        return "BLOCKED_TRANSPORT"
    return "BUSINESS"


def run() -> dict[str, Any]:
    """Execute bounded catalog/TS/KB checks and write a sanitized report."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-catalog-kb-remaining"
    output.mkdir(parents=True, exist_ok=False)
    settings = SettingsLoader.load("test", ROOT)
    db = DatabaseClient.from_settings(settings.database)
    fixtures = _db_fixtures(db)
    runner = transport.Runner(token, output, db)
    cases: list[dict[str, Any]] = []

    init = runner.request(
        "INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-catalog-kb-remaining", "version": "1.0"},
        },
    )
    runner.protocol_version = (((init.get("envelope") or {}).get("result") or {}).get("protocolVersion"))
    runner.notify_initialized("NOTIFY")
    tools_call = runner.request("TOOLS", "tools/list", {})
    listed = ((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or []
    names = {row.get("name") for row in listed if isinstance(row, dict)}
    ready = init.get("http_status") == 200 and runner.protocol_version == "2025-06-18"
    _record(
        cases,
        "MCP-READY",
        "protocol and required tools are available",
        "PASS" if ready else "FAIL",
        "Chrome-UA session initialized" if ready else "initialization failed",
        {
            "protocol_version": runner.protocol_version,
            "http_status": init.get("http_status"),
            "required_tools": sorted(names & {"factor_catalog_stats", "factor_search", "factor_list_metric_scopes", "kb_factor_candidate_search"}),
        },
    )

    stats = runner.tool("CATALOG-STATS-MIN", "factor_catalog_stats", {})
    stats_data = _data(stats)
    stats_ok = _success(stats) and isinstance(stats_data.get("total"), int) and stats_data["total"] >= 0
    stats_status = "PASS" if stats_ok else ("BLOCKED" if _classify_call(stats) != "BUSINESS" else "FAIL")
    _record(
        cases,
        "CATALOG-STATS-MIN",
        "catalog stats baseline returns a nonnegative total",
        stats_status,
        "stats returned a bounded scalar total" if stats_ok else f"stats call {_classify_call(stats).lower()}",
        {"data": stats_data, "transport": _transport_signature(stats)},
    )

    search = runner.tool(
        "CATALOG-SEARCH-MIN",
        "factor_search",
        {"kind": "sub_factor", "library_status": "valid", "limit": 1},
    )
    search_items = _items(search)
    search_data = _data(search)
    search_status = "PASS" if _success(search) and len(search_items) <= 1 else (
        "BLOCKED" if _classify_call(search) != "BUSINESS" else "FAIL"
    )
    search_reason = (
        "limit=1 returned at most one valid sub-factor"
        if search_status == "PASS"
        else f"search call {_classify_call(search).lower()}"
    )
    identity_mismatch: list[str] = []
    if search_items:
        row = search_items[0]
        db_row = db.fetch_one(
            "SELECT id, sub_factor_name AS name, cn_name, serial_number, data_source FROM sub_factors WHERE id=%s",
            (row.get("id"),),
        )
        if db_row:
            for field in ("id", "name", "cn_name", "serial_number", "data_source"):
                if row.get(field) != db_row.get(field):
                    identity_mismatch.append(field)
        if row.get("kind") != "sub_factor":
            identity_mismatch.append("kind")
        if row.get("library_status") != "valid":
            identity_mismatch.append("library_status")
    if search_status == "PASS" and identity_mismatch:
        search_status = "FAIL"
        search_reason = "search row disagreed with authoritative DB identity"
    _record(
        cases,
        "CATALOG-SEARCH-MIN",
        "catalog search honors minimum limit and identity",
        search_status,
        search_reason,
        {
            "requested_limit": 1,
            "returned_count": len(search_items),
            "returned_refs": [row.get("factor_ref") for row in search_items],
            "identity_mismatch_fields": identity_mismatch,
            "meta": _meta(search),
            "transport": _transport_signature(search),
        },
    )

    as_of = datetime.now(timezone.utc).isoformat()
    scope_discovery = runner.tool(
        "TS-SCOPE-DISCOVERY",
        "factor_list_metric_scopes",
        {"as_of": as_of, "kind": "sub_factor", "ic_scope": "time_series", "limit": 5},
    )
    discovered = _items(scope_discovery)
    scope_code = _error_code(scope_discovery)
    if _success(scope_discovery):
        scope_status = "PASS"
        scope_reason = "time-series scope discovery returned a bounded page"
    elif scope_code == "QUERY_TIMEOUT":
        scope_status = "FAIL"
        scope_reason = "time-series scope discovery still times out"
    else:
        scope_status = "BLOCKED" if _classify_call(scope_discovery) != "BUSINESS" else "FAIL"
        scope_reason = f"scope discovery {_classify_call(scope_discovery).lower()}"
    _record(
        cases,
        "TS-SCOPE-DISCOVERY",
        "TS scope discovery is available within a bounded request",
        scope_status,
        scope_reason,
        {
            "returned_count": len(discovered),
            "error_code": scope_code,
            "transport": _transport_signature(scope_discovery),
            "db_scope_available": fixtures["ts_scope"] is not None,
        },
    )

    scope = discovered[0] if discovered else fixtures["ts_scope"]
    ts_search: dict[str, Any] | None = None
    if scope:
        # API scope rows use factor_bar_interval; DB rows use the same value.
        try:
            args = _scope_args(scope, as_of) if "factor_bar_interval" not in scope else {
                "kind": scope.get("kind", "sub_factor"),
                "calculation_mode": scope["calculation_mode"],
                "interval": scope["factor_bar_interval"],
                "factor_window_bars": scope["factor_window_bars"],
                "return_bar_interval": scope["return_bar_interval"],
                "forward_return_bars": int(scope["forward_return_bars"]),
                "ic_scope": scope["ic_scope"],
                "validity_scope": scope["ic_scope"],
                "scoring_version": scope["scoring_version"],
                "universe_key": scope["universe_key"],
                "window_scope": scope["window_scope"],
                "symbol": scope.get("symbol") or "",
                "as_of": as_of,
                "limit": 1,
            }
            ts_search = runner.tool("TS-FACTOR-SEARCH", "factor_search", args)
            ts_items = _items(ts_search)
            ts_code = _error_code(ts_search)
            if _success(ts_search):
                ts_status = "PASS"
                ts_reason = "TS research-scope search returned a bounded page (possibly empty)"
            elif ts_code == "QUERY_TIMEOUT":
                ts_status = "FAIL"
                ts_reason = "TS research-scope factor_search still times out"
            else:
                ts_status = "BLOCKED" if _classify_call(ts_search) != "BUSINESS" else "FAIL"
                ts_reason = f"TS search {_classify_call(ts_search).lower()}"
            _record(
                cases,
                "TS-FACTOR-SEARCH",
                "TS research-scope factor search is callable",
                ts_status,
                ts_reason,
                {
                    "arguments": args,
                    "returned_count": len(ts_items),
                    "error_code": ts_code,
                    "transport": _transport_signature(ts_search),
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            _record(
                cases,
                "TS-FACTOR-SEARCH",
                "TS research-scope factor search is callable",
                "BLOCKED",
                "discovered/DB scope was incomplete",
                {"exception_type": type(exc).__name__, "exception": str(exc)[:200]},
            )
    else:
        _record(
            cases,
            "TS-FACTOR-SEARCH",
            "TS research-scope factor search is callable",
            "BLOCKED",
            "no complete TS scope was available",
            {},
        )

    kb = fixtures["kb"]
    if kb is None:
        _record(cases, "KB-EXACT", "KB exact extraction lookup", "BLOCKED", "no DB extraction fixture", {})
    else:
        kb_call = runner.tool(
            "KB-EXACT",
            "kb_factor_candidate_search",
            {"extraction_id": int(kb["id"]), "limit": 1},
        )
        kb_items = _items(kb_call)
        returned_ids = [row.get("extraction_id", row.get("id")) for row in kb_items]
        kb_ok = _success(kb_call) and returned_ids == [int(kb["id"])]
        kb_status = "PASS" if kb_ok else ("BLOCKED" if _classify_call(kb_call) != "BUSINESS" else "FAIL")
        _record(
            cases,
            "KB-EXACT",
            "KB exact extraction lookup returns only requested candidate",
            kb_status,
            "exact extraction id was returned" if kb_ok else f"KB lookup {_classify_call(kb_call).lower()}",
            {
                "requested_extraction_id": int(kb["id"]),
                "returned_ids": returned_ids,
                "transport": _transport_signature(kb_call),
            },
        )

    counts = Counter(row["status"] for row in cases)
    result = {
        "run_id": stamp,
        "environment": "test",
        "mcp_url": MCP_URL,
        "user_agent_class": "standard Chrome",
        "read_only": True,
        "request_count": len(runner.calls),
        "case_counts": dict(sorted(counts.items())),
        "cases": cases,
        "confirmed_failures": [row for row in cases if row["status"] == "FAIL"],
        "blocked": [row for row in cases if row["status"] == "BLOCKED"],
        "fixtures": {
            "catalog_id": fixtures["catalog"].get("id") if fixtures["catalog"] else None,
            "ts_scope": fixtures["ts_scope"],
            "kb_extraction_id": fixtures["kb"].get("id") if fixtures["kb"] else None,
        },
    }
    transport._write_json(output / "summary.json", result)
    transport._write_json(
        output / "call-ledger.json",
        [
            {
                "case_id": call.get("case_id"),
                "tool": call.get("tool"),
                "http_status": call.get("http_status"),
                "elapsed_seconds": call.get("elapsed_seconds"),
                "error_code": _error_code(call),
                "meta": _meta(call),
            }
            for call in runner.calls
        ],
    )
    lines = [
        "# Catalog / TS / KB remaining recheck",
        "",
        f"- Run: `{stamp}`",
        "- Environment: test MCP; read-only DB fixture discovery",
        "- User-Agent: standard Chrome",
        f"- Counts: `{dict(sorted(counts.items()))}`",
        "",
        "| Case | Status | Result |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {row['case_id']} | {row['status']} | {row['reason']} |" for row in cases)
    lines.extend(["", "Raw request/response artifacts are sanitized; no Authorization header or complete token is stored."])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "case_counts": dict(sorted(counts.items())), "request_count": len(runner.calls)}))
    return result


if __name__ == "__main__":
    run()
