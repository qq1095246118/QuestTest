#!/usr/bin/env python3
"""Run read-only deep functional checks for the Factor 4.0 MCP catalog tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "CATALOG_MCP_TOKEN"
ERROR_KEY_RE = re.compile(r"(authorization|token|password|secret|claim_token|signature)", re.I)
# Cloudflare in the test environment accepts a normal browser UA; synthetic
# QuestTest-* values are rejected before MCP dispatch and are not useful test
# evidence.
CLIENT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if ERROR_KEY_RE.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(_redact(value), ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)
        + "\n",
        encoding="utf-8",
    )


def _parse_body(raw: bytes, content_type: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" not in content_type.lower():
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("MCP response root is not an object")
        return parsed
    events: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if data_lines:
            parsed = json.loads("\n".join(data_lines))
            if isinstance(parsed, dict):
                events.append(parsed)
    if len(events) != 1:
        raise ValueError(f"Expected one MCP data event, got {len(events)}")
    return events[0]


class Runner:
    """Execute MCP calls, assertions, and credential-free evidence capture."""

    def __init__(self, token: str, output_dir: Path, db: DatabaseClient) -> None:
        self._token = token
        self.output_dir = output_dir
        self.db = db
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict[str, Any]] = []
        self.cases: list[dict[str, Any]] = []
        self._sequence = 0
        self.protocol_version: str | None = None
        self.session_id: str | None = None

    def request(self, case_id: str, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """Send one MCP JSON-RPC request and save a sanitized request/response pair."""
        self._sequence += 1
        request_id = f"{case_id}-{uuid4()}"
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": CLIENT_USER_AGENT,
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        raw_request = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        req = urllib.request.Request(MCP_URL, data=raw_request, headers=headers, method="POST")
        started = time.monotonic()
        response_headers: dict[str, str] = {}
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                raw_response = response.read()
                http_status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raw_response = exc.read()
            http_status = exc.code
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
        elapsed = round(time.monotonic() - started, 3)
        content_type = response_headers.get("content-type", "")
        try:
            envelope = _parse_body(raw_response, content_type)
            parse_error = None
        except Exception as exc:  # diagnostic evidence must survive malformed responses
            envelope = None
            parse_error = f"{type(exc).__name__}: {exc}"
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        safe_headers = {
            key: value
            for key, value in response_headers.items()
            if key in {"content-type", "mcp-session-id", "mcp-protocol-version", "x-request-id", "x-trace-id"}
        }
        if "mcp-session-id" in safe_headers:
            safe_headers["mcp-session-id"] = hashlib.sha256(
                safe_headers["mcp-session-id"].encode()
            ).hexdigest()
        stem = f"{self._sequence:03d}-{case_id}"
        _write_json(self.output_dir / f"{stem}.request.json", payload)
        if envelope is not None:
            _write_json(self.output_dir / f"{stem}.response.json", envelope)
        else:
            (self.output_dir / f"{stem}.response.txt").write_text(
                raw_response.decode("utf-8", errors="replace"), encoding="utf-8"
            )
        call = {
            "case_id": case_id,
            "method": method,
            "http_status": http_status,
            "elapsed_seconds": elapsed,
            "content_type": content_type,
            "response_headers": safe_headers,
            "parse_error": parse_error,
            "envelope": envelope,
        }
        self.calls.append(call)
        return call

    def notify_initialized(self, case_id: str) -> dict[str, Any]:
        """Send the initialized notification without waiting for a JSON-RPC result."""
        self._sequence += 1
        payload = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": CLIENT_USER_AGENT,
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        req = urllib.request.Request(
            MCP_URL,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        elapsed = round(time.monotonic() - started, 3)
        stem = f"{self._sequence:03d}-{case_id}"
        _write_json(self.output_dir / f"{stem}.request.json", payload)
        (self.output_dir / f"{stem}.response.txt").write_text(
            raw.decode("utf-8", errors="replace"), encoding="utf-8"
        )
        call = {
            "case_id": case_id,
            "method": "notifications/initialized",
            "http_status": status,
            "elapsed_seconds": elapsed,
            "envelope": None,
        }
        self.calls.append(call)
        return call

    def tool(self, case_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one MCP tool and return normalized transport and business data."""
        call = self.request(case_id, "tools/call", {"name": name, "arguments": arguments})
        envelope = call.get("envelope") or {}
        result = envelope.get("result") if isinstance(envelope, dict) else None
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        parsed_text = None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    parsed_text = json.loads(text)
                except json.JSONDecodeError:
                    parsed_text = None
        call.update(
            {
                "tool": name,
                "arguments": arguments,
                "is_error": result.get("isError") if isinstance(result, dict) else None,
                "business": structured if isinstance(structured, dict) else parsed_text,
                "representations_equal": (
                    structured == parsed_text
                    if structured is not None and parsed_text is not None
                    else None
                ),
            }
        )
        return call

    def record(
        self,
        case_id: str,
        title: str,
        status: str,
        reason: str,
        *,
        evidence: dict[str, Any] | None = None,
        failure_class: str | None = None,
        severity: str | None = None,
    ) -> None:
        """Append one case-level verdict to the report ledger."""
        self.cases.append(
            {
                "case_id": case_id,
                "title": title,
                "status": status,
                "reason": reason,
                "failure_class": failure_class,
                "severity": severity,
                "evidence": evidence or {},
            }
        )


def _data(call: dict[str, Any]) -> dict[str, Any]:
    business = call.get("business")
    if not isinstance(business, dict):
        return {}
    data = business.get("data")
    return data if isinstance(data, dict) else {}


def _meta(call: dict[str, Any]) -> dict[str, Any]:
    business = call.get("business")
    if not isinstance(business, dict):
        return {}
    meta = business.get("meta")
    return meta if isinstance(meta, dict) else {}


def _items(call: dict[str, Any]) -> list[dict[str, Any]]:
    items = _data(call).get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _error_code(call: dict[str, Any]) -> str | None:
    envelope = call.get("envelope") or {}
    if isinstance(envelope.get("error"), dict):
        code = envelope["error"].get("code")
        return str(code) if code is not None else None
    business = call.get("business") or {}
    error = business.get("error") if isinstance(business, dict) else None
    if isinstance(error, dict):
        for key in ("code", "error_code", "type"):
            if error.get(key) is not None:
                return str(error[key])
    return None


def _success(call: dict[str, Any]) -> bool:
    return (
        call.get("http_status") == 200
        and call.get("parse_error") is None
        and isinstance(call.get("envelope"), dict)
        and "result" in call["envelope"]
        and call.get("is_error") is not True
        and isinstance(call.get("business"), dict)
        and "error" not in call["business"]
    )


def _rejected(call: dict[str, Any]) -> bool:
    envelope = call.get("envelope") or {}
    return bool(
        call.get("http_status", 0) >= 400
        or (isinstance(envelope, dict) and "error" in envelope)
        or call.get("is_error") is True
        or (isinstance(call.get("business"), dict) and "error" in call["business"])
    )


def _assert_rejections(runner: Runner, cases: list[tuple[str, str, dict[str, Any]]]) -> None:
    for case_id, tool, arguments in cases:
        call = runner.tool(case_id, tool, arguments)
        runner.record(
            case_id,
            f"{tool} rejects invalid arguments",
            "PASS" if _rejected(call) else "FAIL",
            "invalid input was rejected" if _rejected(call) else "invalid input returned business success",
            evidence={"arguments": arguments, "http_status": call["http_status"], "error_code": _error_code(call)},
            failure_class=None if _rejected(call) else "FAIL_CONTRACT",
            severity=None if _rejected(call) else "P1",
        )


def _db_state(runner: Runner) -> dict[str, Any]:
    rows = runner.db.fetch_all(
        """
        SELECT 'factors' AS entity, COUNT(*) AS row_count, MAX(updated_at) AS max_updated_at FROM factors
        UNION ALL
        SELECT 'sub_factors', COUNT(*), MAX(updated_at) FROM sub_factors
        UNION ALL
        SELECT 'relations', COUNT(*), MAX(updated_at) FROM factor_sub_factor_relations
        UNION ALL
        SELECT 'kb_extractions', COUNT(*), MAX(updated_at) FROM kb_factor_extractions
        UNION ALL
        SELECT 'kb_tasks', COUNT(*), MAX(updated_at) FROM kb_factor_mining_tasks
        UNION ALL
        SELECT 'universe', COUNT(*), MAX(updated_at) FROM coin_universe_symbols
        """
    )
    return {str(row["entity"]): {"row_count": row["row_count"], "max_updated_at": row["max_updated_at"]} for row in rows}


def _run_catalog_stats(runner: Runner) -> dict[str, Any]:
    baseline = runner.tool("CAT-001", "factor_catalog_stats", {})
    runner.record(
        "CAT-001",
        "catalog baseline statistics",
        "PASS" if _success(baseline) else "FAIL",
        "baseline statistics returned normally" if _success(baseline) else "baseline statistics failed",
        evidence={"data": _data(baseline), "meta": _meta(baseline)},
        failure_class=None if _success(baseline) else "FAIL_BUSINESS",
        severity=None if _success(baseline) else "P1",
    )
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in ("factor", "sub_factor"):
        call = runner.tool(f"CAT-002-{kind}", "factor_catalog_stats", {"kind": kind})
        by_kind[kind] = _data(call)
        runner.record(
            f"CAT-002-{kind}",
            f"catalog statistics kind={kind}",
            "PASS" if _success(call) else "FAIL",
            "kind filter returned normally" if _success(call) else "kind filter failed",
            evidence={"data": _data(call)},
            failure_class=None if _success(call) else "FAIL_BUSINESS",
            severity=None if _success(call) else "P1",
        )
    return {"baseline": _data(baseline), "by_kind": by_kind}


def _find_count(data: dict[str, Any]) -> int | None:
    for key in ("count", "total", "total_count", "factor_count", "matched_count"):
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _run_library_search(runner: Runner, stats: dict[str, Any]) -> dict[str, Any]:
    factor_call = runner.tool(
        "SEARCH-001-factor", "factor_search", {"kind": "factor", "library_status": "valid", "limit": 20}
    )
    child_call = runner.tool(
        "SEARCH-001-child", "factor_search", {"kind": "sub_factor", "library_status": "valid", "limit": 20}
    )
    factors = _items(factor_call)
    children = _items(child_call)
    basic_ok = (
        _success(factor_call)
        and _success(child_call)
        and factors
        and children
        and all(item.get("kind") == "factor" and item.get("library_status") == "valid" for item in factors)
        and all(item.get("kind") == "sub_factor" and item.get("library_status") == "valid" for item in children)
    )
    runner.record(
        "SEARCH-001",
        "library mode kind and status filtering",
        "PASS" if basic_ok else "FAIL",
        "factor and sub-factor pages contain only requested kind/status" if basic_ok else "kind/status filtering was incorrect or empty",
        evidence={"factor_count": len(factors), "sub_factor_count": len(children)},
        failure_class=None if basic_ok else "FAIL_BUSINESS",
        severity=None if basic_ok else "P1",
    )
    seed = children[0] if children else (factors[0] if factors else {})
    filters: list[tuple[str, dict[str, Any], Callable[[dict[str, Any]], bool]]] = []
    if seed.get("name"):
        filters.append(("query", {"query": seed["name"], "limit": 10}, lambda x: seed["name"].casefold() in json.dumps(x, ensure_ascii=False).casefold()))
    themes = seed.get("themes") or []
    if themes:
        filters.append(("theme", {"theme": themes[0], "limit": 10}, lambda x: themes[0] in (x.get("themes") or [])))
    tags = seed.get("tags") or []
    if tags:
        filters.append(("tags", {"tags": [tags[0]], "limit": 10}, lambda x: tags[0] in (x.get("tags") or [])))
    if seed.get("data_source"):
        filters.append(("data_source", {"data_source": seed["data_source"], "limit": 10}, lambda x: x.get("data_source") == seed["data_source"]))
    if seed.get("factor_bar_interval"):
        filters.append(("interval", {"interval": seed["factor_bar_interval"], "limit": 10}, lambda x: x.get("factor_bar_interval") == seed["factor_bar_interval"]))
    categories = seed.get("library_coin_categories") or []
    if categories:
        category = categories[0]
        filters.append(("coin_category", {"library_status": "valid", "library_coin_category": category, "limit": 10}, lambda x: category in (x.get("library_coin_categories") or [])))
    for index, (name, arguments, predicate) in enumerate(filters, 1):
        call = runner.tool(f"SEARCH-002-{index}-{name}", "factor_search", arguments)
        items = _items(call)
        ok = _success(call) and bool(items) and all(predicate(item) for item in items)
        runner.record(
            f"SEARCH-002-{index}-{name}",
            f"library search {name} filter",
            "PASS" if ok else "FAIL",
            "all returned rows satisfy the requested filter" if ok else "filter returned no seed match or leaked non-matching rows",
            evidence={"arguments": arguments, "returned_count": len(items), "refs": [x.get("factor_ref") for x in items]},
            failure_class=None if ok else "FAIL_BUSINESS",
            severity=None if ok else "P1",
        )
    page1 = runner.tool(
        "SEARCH-003-page1", "factor_search", {"kind": "sub_factor", "library_status": "valid", "limit": 3}
    )
    cursor = _meta(page1).get("next_cursor")
    page2 = runner.tool(
        "SEARCH-003-page2",
        "factor_search",
        {"kind": "sub_factor", "library_status": "valid", "limit": 3, "cursor": cursor},
    ) if cursor else None
    refs1 = [x.get("factor_ref") for x in _items(page1)]
    refs2 = [x.get("factor_ref") for x in _items(page2)] if page2 else []
    page_ok = bool(cursor and page2 and _success(page2) and len(refs1) == 3 and len(refs2) == 3 and not set(refs1) & set(refs2))
    runner.record(
        "SEARCH-003",
        "library search signed cursor continuation",
        "PASS" if page_ok else "FAIL",
        "second page is non-overlapping and retains filters" if page_ok else "cursor continuation missing, failed, or repeated rows",
        evidence={"page1": refs1, "page2": refs2, "cursor_present": bool(cursor)},
        failure_class=None if page_ok else "FAIL_PAGINATION",
        severity=None if page_ok else "P1",
    )
    if cursor:
        tampered = cursor[:-1] + ("A" if cursor[-1:] != "A" else "B")
        altered = runner.tool(
            "SEARCH-004-tampered",
            "factor_search",
            {"kind": "sub_factor", "library_status": "valid", "limit": 3, "cursor": tampered},
        )
        changed = runner.tool(
            "SEARCH-004-filter-change",
            "factor_search",
            {"kind": "factor", "library_status": "valid", "limit": 3, "cursor": cursor},
        )
        ok = _rejected(altered) and _rejected(changed)
        runner.record(
            "SEARCH-004",
            "catalog cursor integrity and filter binding",
            "PASS" if ok else "FAIL",
            "tampered and cross-filter cursors were rejected" if ok else "a tampered or cross-filter cursor was accepted",
            evidence={"tampered_rejected": _rejected(altered), "filter_change_rejected": _rejected(changed)},
            failure_class=None if ok else "FAIL_CURSOR_BINDING",
            severity=None if ok else "P1",
        )
    updated_at = seed.get("updated_at")
    if isinstance(updated_at, str):
        threshold = (datetime.fromisoformat(updated_at.replace("Z", "+00:00")) - timedelta(seconds=1)).isoformat()
        updated_call = runner.tool("SEARCH-005", "factor_search", {"updated_after": threshold, "limit": 20})
        rows = _items(updated_call)
        ok = _success(updated_call) and all(
            datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")) > datetime.fromisoformat(threshold)
            for row in rows if row.get("updated_at")
        )
        runner.record(
            "SEARCH-005",
            "catalog updated_after filter",
            "PASS" if ok else "FAIL",
            "returned timestamps are strictly after the requested point" if ok else "updated_after leaked an older row",
            evidence={"threshold": threshold, "returned_count": len(rows)},
            failure_class=None if ok else "FAIL_BUSINESS",
            severity=None if ok else "P1",
        )
    all_stats_count = _find_count(stats.get("baseline") or {})
    factor_stats_count = _find_count((stats.get("by_kind") or {}).get("factor") or {})
    child_stats_count = _find_count((stats.get("by_kind") or {}).get("sub_factor") or {})
    if all(value is not None for value in (all_stats_count, factor_stats_count, child_stats_count)):
        additive = all_stats_count == factor_stats_count + child_stats_count
        runner.record(
            "CAT-003",
            "catalog total equals factor plus sub-factor totals",
            "PASS" if additive else "FAIL",
            "catalog totals are additive" if additive else "catalog total conflicts with per-kind totals",
            evidence={"total": all_stats_count, "factor": factor_stats_count, "sub_factor": child_stats_count},
            failure_class=None if additive else "FAIL_DATA_CONSISTENCY",
            severity=None if additive else "P1",
        )
    else:
        runner.record("CAT-003", "catalog statistics additive identity", "BLOCKED", "response does not expose a documented scalar count", failure_class="BLOCKED_CONTRACT")
    return {"factors": factors, "children": children, "seed": seed, "page_cursor": cursor}


def _metric_scope_arguments(scope: dict[str, Any]) -> dict[str, Any] | None:
    aliases = {
        "interval": ("interval", "factor_bar_interval", "interval_value"),
        "factor_window_bars": ("factor_window_bars",),
        "return_bar_interval": ("return_bar_interval",),
        "forward_return_bars": ("forward_return_bars",),
        "universe_key": ("universe_key",),
        "window_scope": ("window_scope",),
        "ic_scope": ("ic_scope",),
        "calculation_mode": ("calculation_mode",),
        "scoring_version": ("scoring_version",),
        "symbol": ("symbol",),
    }
    result: dict[str, Any] = {}
    for target, sources in aliases.items():
        for source in sources:
            if scope.get(source) is not None:
                result[target] = scope[source]
                break
    required = set(aliases)
    return result if required <= set(result) else None


def _run_metric_search(runner: Runner, search_data: dict[str, Any]) -> dict[str, Any]:
    as_of = datetime.now(timezone.utc).isoformat()
    scopes_call = runner.tool("SEARCH-100-scopes", "factor_list_metric_scopes", {"as_of": as_of, "limit": 100})
    scopes = _items(scopes_call)
    scope_args = next((_metric_scope_arguments(row) for row in scopes if _metric_scope_arguments(row)), None)
    if not _success(scopes_call) or scope_args is None:
        runner.record(
            "SEARCH-100",
            "discover a complete metric scope",
            "BLOCKED",
            "no complete metric scope was returned",
            evidence={"scope_count": len(scopes), "meta": _meta(scopes_call)},
            failure_class="BLOCKED_DATA_PRECONDITION",
        )
        return {"scope": None, "items": []}
    full = {**scope_args, "as_of": as_of, "validity": "valid", "validity_scope": scope_args["ic_scope"], "limit": 20}
    metric_call = runner.tool("SEARCH-101", "factor_search", full)
    metric_items = _items(metric_call)
    scope_match = all(
        item.get("validity_status") in {"valid", None}
        and (item.get("scoring_version") in {scope_args["scoring_version"], None})
        for item in metric_items
    )
    ok = _success(metric_call) and scope_match
    runner.record(
        "SEARCH-101",
        "factor search with a complete point-in-time metric scope",
        "PASS" if ok else "FAIL",
        "complete metric search returned only scope-consistent rows" if ok else "complete metric search failed or mixed metric identity",
        evidence={"scope": full, "returned_count": len(metric_items), "refs": [x.get("factor_ref") for x in metric_items]},
        failure_class=None if ok else "FAIL_BUSINESS",
        severity=None if ok else "P1",
    )
    stats_args = {key: value for key, value in full.items() if key not in {"limit", "validity_scope"}}
    stats_args["validity_scope"] = full["validity_scope"]
    stats_call = runner.tool("SEARCH-102-stats", "factor_catalog_stats", stats_args)
    count = _find_count(_data(stats_call))
    consistent = _success(stats_call) and (count is None or count >= len(metric_items))
    runner.record(
        "SEARCH-102",
        "metric-scope catalog statistics and search consistency",
        "PASS" if consistent else "FAIL",
        "stats count is not smaller than the first search page" if consistent else "stats count conflicts with search page",
        evidence={"stats": _data(stats_call), "search_returned": len(metric_items)},
        failure_class=None if consistent else "FAIL_DATA_CONSISTENCY",
        severity=None if consistent else "P1",
    )
    if metric_items:
        thresholds: list[tuple[str, str]] = []
        for field, param in (("icir", "min_icir"), ("rank_icir", "min_rank_icir"), ("final_score", "min_score")):
            values = [Decimal(str(item[field])) for item in metric_items if item.get(field) is not None]
            if values:
                thresholds.append((param, str(min(values))))
        for index, (param, value) in enumerate(thresholds, 1):
            args = {**full, param: float(value)}
            call = runner.tool(f"SEARCH-103-{index}-{param}", "factor_search", args)
            items = _items(call)
            field = {"min_icir": "icir", "min_rank_icir": "rank_icir", "min_score": "final_score"}[param]
            valid = _success(call) and all(item.get(field) is not None and Decimal(str(item[field])) >= Decimal(value) for item in items)
            runner.record(
                f"SEARCH-103-{index}-{param}",
                f"metric search {param} threshold",
                "PASS" if valid else "FAIL",
                "all returned metric values satisfy the threshold" if valid else "threshold leaked null or lower metric values",
                evidence={"threshold": value, "returned_count": len(items)},
                failure_class=None if valid else "FAIL_BUSINESS",
                severity=None if valid else "P1",
            )
    return {"scope": full, "items": metric_items, "scope_rows": scopes}


def _run_details(runner: Runner, search_data: dict[str, Any]) -> dict[str, Any]:
    candidates = (search_data.get("factors") or []) + (search_data.get("children") or [])
    if not candidates:
        runner.record("DETAIL-001", "factor detail levels", "BLOCKED", "no catalog seed was available", failure_class="BLOCKED_DATA_PRECONDITION")
        return {}
    seed = candidates[0]
    ref = seed["factor_ref"]
    levels: dict[str, dict[str, Any]] = {}
    level_calls: dict[str, dict[str, Any]] = {}
    for level in ("summary", "definition", "executable"):
        call = runner.tool(f"DETAIL-001-{level}", "factor_get_detail", {"factor_ref": ref, "detail_level": level})
        level_calls[level] = call
        levels[level] = _data(call)
    identity_keys = ("factor_ref", "id", "kind", "name", "serial_number")
    identity_ok = all(
        _success(level_calls[level]) and all(levels[level].get(key) == levels["summary"].get(key) for key in identity_keys)
        for level in levels
    )
    exec_ok = bool(levels["executable"].get("formula_available") is not None and "calc_logic" in levels["executable"])
    runner.record(
        "DETAIL-001",
        "summary, definition, and executable detail identity",
        "PASS" if identity_ok and exec_ok else "FAIL",
        "all levels identify the same factor and executable exposes formula evidence" if identity_ok and exec_ok else "detail levels changed identity or executable evidence is missing",
        evidence={"factor_ref": ref, "keys_by_level": {level: sorted(data) for level, data in levels.items()}},
        failure_class=None if identity_ok and exec_ok else "FAIL_DATA_CONSISTENCY",
        severity=None if identity_ok and exec_ok else "P1",
    )
    db_row = None
    if levels["summary"].get("kind") == "factor":
        db_row = runner.db.fetch_one("SELECT id, factor_name AS name, serial_number, cn_name FROM factors WHERE id=%s", (levels["summary"]["id"],))
    elif levels["summary"].get("kind") == "sub_factor":
        db_row = runner.db.fetch_one("SELECT id, sub_factor_name AS name, serial_number, cn_name FROM sub_factors WHERE id=%s", (levels["summary"]["id"],))
    db_ok = bool(db_row and all(str(db_row[key]) == str(levels["summary"].get(key)) for key in ("id", "name", "serial_number", "cn_name")))
    runner.record(
        "DETAIL-002",
        "factor detail core identity matches database",
        "PASS" if db_ok else "FAIL",
        "detail identity matches the authoritative row" if db_ok else "detail identity differs from database",
        evidence={"factor_ref": ref, "db_row": db_row, "mcp": {key: levels["summary"].get(key) for key in ("id", "name", "serial_number", "cn_name")}},
        failure_class=None if db_ok else "FAIL_DATA_CONSISTENCY",
        severity=None if db_ok else "P1",
    )
    parent_seed = next((item for item in search_data.get("factors") or [] if int(item.get("child_factor_count") or 0) > 2), None)
    if parent_seed is None:
        rows = runner.db.fetch_all(
            """
            SELECT f.id, COUNT(*) AS child_count
            FROM factors f JOIN factor_sub_factor_relations r ON r.factor_id=f.id
            GROUP BY f.id HAVING COUNT(*) > 2 ORDER BY COUNT(*) DESC, f.id LIMIT 5
            """
        )
        if rows:
            parent_seed = {"factor_ref": f"factor:{rows[0]['id']}"}
    parent_result: dict[str, Any] = {}
    if parent_seed:
        parent_ref = parent_seed["factor_ref"]
        first = runner.tool("DETAIL-010-page1", "factor_get_detail", {"factor_ref": parent_ref, "detail_level": "summary", "children_limit": 2})
        parent_result = _data(first)
        children = parent_result.get("children") or []
        cursor = _meta(first).get("next_cursor")
        if not cursor:
            cursor = parent_result.get("children_cursor") or parent_result.get("next_cursor")
        second = runner.tool("DETAIL-010-page2", "factor_get_detail", {"factor_ref": parent_ref, "detail_level": "summary", "children_limit": 2, "children_cursor": cursor}) if cursor else None
        children2 = (_data(second).get("children") or []) if second else []
        refs1 = [row.get("factor_ref") for row in children if isinstance(row, dict)]
        refs2 = [row.get("factor_ref") for row in children2 if isinstance(row, dict)]
        db_children = runner.db.fetch_all("SELECT sub_factor_id FROM factor_sub_factor_relations WHERE factor_id=%s ORDER BY sub_factor_id", (int(parent_ref.split(":", 1)[1]),))
        db_set = {f"sub_factor:{row['sub_factor_id']}" for row in db_children}
        ok = _success(first) and len(refs1) <= 2 and set(refs1) <= db_set
        if cursor:
            ok = ok and bool(second and _success(second) and not set(refs1) & set(refs2) and set(refs2) <= db_set)
        runner.record(
            "DETAIL-010",
            "parent children pagination and relationship identity",
            "PASS" if ok else "FAIL",
            "children pages are bounded, non-overlapping, and backed by DB relations" if ok else "children pagination or relationship identity is inconsistent",
            evidence={"parent_ref": parent_ref, "page1": refs1, "page2": refs2, "db_child_count": len(db_set), "cursor_present": bool(cursor), "parent_keys": sorted(parent_result)},
            failure_class=None if ok else "FAIL_DATA_CONSISTENCY",
            severity=None if ok else "P1",
        )
        if refs1:
            child_call = runner.tool("DETAIL-011", "factor_get_detail", {"factor_ref": refs1[0], "detail_level": "summary"})
            child = _data(child_call)
            child_ok = _success(child_call) and child.get("factor_ref") == refs1[0]
            runner.record(
                "DETAIL-011",
                "parent child reference resolves to the same child",
                "PASS" if child_ok else "FAIL",
                "embedded child reference resolves with stable identity" if child_ok else "embedded child cannot be resolved consistently",
                evidence={"parent_ref": parent_ref, "child_ref": refs1[0], "resolved_ref": child.get("factor_ref")},
                failure_class=None if child_ok else "FAIL_DATA_CONSISTENCY",
                severity=None if child_ok else "P1",
            )
    else:
        runner.record("DETAIL-010", "parent children pagination", "BLOCKED", "no parent with more than two children exists", failure_class="BLOCKED_DATA_PRECONDITION")
    batch_refs = [item["factor_ref"] for item in candidates[:3]]
    nonexistent = f"sub_factor:{9_000_000_000 + int(time.time())}"
    mixed_refs = batch_refs + [nonexistent]
    batch = runner.tool("DETAIL-020", "factor_get_details_batch", {"factor_refs": mixed_refs, "detail_level": "summary"})
    batch_items = _items(batch)
    by_ref = {item.get("factor_ref"): item for item in batch_items}
    valid_ok = all(by_ref.get(value, {}).get("success") is True for value in batch_refs)
    failures_ok = by_ref.get(nonexistent, {}).get("success") is False
    partial_ok = _success(batch) and valid_ok and failures_ok and len(batch_items) == len(mixed_refs)
    runner.record(
        "DETAIL-020",
        "batch detail preserves valid rows and per-item failures",
        "PASS" if partial_ok else "FAIL",
        "mixed batch returned successes and explicit per-item errors" if partial_ok else "one invalid item failed the batch or per-item status was incorrect",
        evidence={"requested": mixed_refs, "returned": [{"factor_ref": x.get("factor_ref"), "success": x.get("success"), "error": x.get("error")} for x in batch_items]},
        failure_class=None if partial_ok else "FAIL_PARTIAL_RESULT",
        severity=None if partial_ok else "P1",
    )
    malformed = runner.tool(
        "DETAIL-020-malformed",
        "factor_get_details_batch",
        {"factor_refs": batch_refs + ["bad-ref"], "detail_level": "summary"},
    )
    runner.record(
        "DETAIL-020-malformed",
        "batch detail rejects a syntactically invalid factor reference",
        "PASS" if _rejected(malformed) else "FAIL",
        "malformed factor_ref was rejected at request level" if _rejected(malformed) else "malformed factor_ref was accepted",
        evidence={"http_status": malformed["http_status"], "error_code": _error_code(malformed)},
        failure_class=None if _rejected(malformed) else "FAIL_CONTRACT",
        severity=None if _rejected(malformed) else "P1",
    )
    if batch_refs:
        individual = runner.tool("DETAIL-021-single", "factor_get_detail", {"factor_ref": batch_refs[0], "detail_level": "summary"})
        batch_data = by_ref.get(batch_refs[0], {}).get("data") or {}
        individual_data = _data(individual)
        keys = ("factor_ref", "id", "kind", "name", "serial_number", "cn_name")
        equal = _success(individual) and all(batch_data.get(key) == individual_data.get(key) for key in keys)
        runner.record(
            "DETAIL-021",
            "batch and single detail core identity consistency",
            "PASS" if equal else "FAIL",
            "batch and single detail identify the same object" if equal else "batch detail differs from single detail",
            evidence={"factor_ref": batch_refs[0], "compared_keys": list(keys)},
            failure_class=None if equal else "FAIL_DATA_CONSISTENCY",
            severity=None if equal else "P1",
        )
    return {"seed_ref": ref, "parent": parent_result, "batch_refs": batch_refs}


def _formula_oracle(runner: Runner) -> dict[str, Any] | None:
    row = runner.db.fetch_one(
        """
        SELECT e.run_id, e.factor_id, e.is_sub_factor_id, e.calculation_mode,
               e.factor_bar_interval, e.factor_window_bars, e.return_bar_interval,
               e.forward_return_bars, e.formula_version, e.formula_hash, e.expression,
               e.required_fields, e.recorded_at, r.status
        FROM factor_ic_run_formula_evidence e
        JOIN factor_ic_runs r ON r.run_id=e.run_id
        WHERE r.status='completed' AND e.calculation_mode='direct'
        ORDER BY e.recorded_at DESC, e.id DESC LIMIT 1
        """
    )
    if not row:
        return None
    row["factor_ref"] = f"{'sub_factor' if int(row['is_sub_factor_id']) else 'factor'}:{row['factor_id']}"
    return row


def _run_formula(runner: Runner) -> None:
    oracle = _formula_oracle(runner)
    if oracle is None:
        runner.record("FORMULA-001", "exact-run formula evidence", "BLOCKED", "no completed direct formula evidence exists", failure_class="BLOCKED_DATA_PRECONDITION")
        return
    arguments = {
        "factor_ref": oracle["factor_ref"],
        "run_id": oracle["run_id"],
        "calculation_mode": "direct",
        "interval": oracle["factor_bar_interval"],
        "factor_window_bars": oracle["factor_window_bars"],
        "return_bar_interval": oracle["return_bar_interval"],
        "forward_return_bars": int(oracle["forward_return_bars"]),
    }
    call = runner.tool("FORMULA-001", "factor_get_formula", arguments)
    data = _data(call)
    expression = data.get("expression") or data.get("formula") or data.get("calc_logic")
    returned_hash = data.get("formula_hash")
    metric_identity = data.get("metric_identity") or {}
    identity_ok = (
        _success(call)
        and str(data.get("factor_ref")) == str(oracle["factor_ref"])
        and str(data.get("run_id")) == str(oracle["run_id"])
        and str(metric_identity.get("calculation_mode")) == "direct"
        and str(metric_identity.get("factor_bar_interval")) == str(oracle["factor_bar_interval"])
        and str(metric_identity.get("factor_window_bars")) == str(oracle["factor_window_bars"])
        and str(metric_identity.get("return_bar_interval")) == str(oracle["return_bar_interval"])
        and int(metric_identity.get("forward_return_bars")) == int(oracle["forward_return_bars"])
    )
    formula_ok = expression == oracle["expression"] and (returned_hash in {None, oracle["formula_hash"]})
    runner.record(
        "FORMULA-001",
        "immutable formula evidence by exact completed run",
        "PASS" if identity_ok and formula_ok else "FAIL",
        "MCP formula identity and expression match DB evidence" if identity_ok and formula_ok else "formula identity or expression differs from exact DB run",
        evidence={"arguments": arguments, "returned_keys": sorted(data), "db_formula_hash": oracle["formula_hash"], "mcp_formula_hash": returned_hash},
        failure_class=None if identity_ok and formula_ok else "FAIL_DATA_CONSISTENCY",
        severity=None if identity_ok and formula_ok else "P1",
    )
    wrong_run = runner.tool("FORMULA-002-run", "factor_get_formula", {**arguments, "run_id": str(uuid4())})
    wrong_scope = runner.tool(
        "FORMULA-002-scope",
        "factor_get_formula",
        {**arguments, "factor_window_bars": f"{oracle['factor_window_bars']}-mismatch"},
    )
    wrong_factor = runner.tool(
        "FORMULA-002-factor",
        "factor_get_formula",
        {**arguments, "factor_ref": f"sub_factor:{9_000_000_000 + int(time.time())}"},
    )
    exact_rejection = all(_rejected(item) for item in (wrong_run, wrong_scope, wrong_factor))
    runner.record(
        "FORMULA-002",
        "formula lookup is bound to run, factor, and metric identity",
        "PASS" if exact_rejection else "FAIL",
        "all mismatched exact identities were rejected" if exact_rejection else "formula lookup silently fell back for a mismatched identity",
        evidence={"wrong_run_rejected": _rejected(wrong_run), "wrong_scope_rejected": _rejected(wrong_scope), "wrong_factor_rejected": _rejected(wrong_factor)},
        failure_class=None if exact_rejection else "FAIL_POINT_IN_TIME",
        severity=None if exact_rejection else "P1",
    )


def _parse_json_cell(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _run_kb(runner: Runner) -> None:
    seed = runner.db.fetch_one(
        """
        SELECT id, factor_name, validation_status, mapping_status, target_asset_class,
               confidence_score, updated_at
        FROM kb_factor_extractions
        ORDER BY updated_at DESC, id DESC LIMIT 1
        """
    )
    if seed is None:
        runner.record("KB-001", "KB candidate exact lookup", "BLOCKED", "no KB extraction exists", failure_class="BLOCKED_DATA_PRECONDITION")
        return
    by_id = runner.tool("KB-001-id", "kb_factor_candidate_search", {"extraction_id": int(seed["id"]), "limit": 10})
    id_items = _items(by_id)
    id_ok = _success(by_id) and len(id_items) == 1 and int(id_items[0].get("extraction_id", id_items[0].get("id", -1))) == int(seed["id"])
    runner.record(
        "KB-001",
        "KB candidate exact extraction lookup",
        "PASS" if id_ok else "FAIL",
        "exact extraction id returns only the requested candidate" if id_ok else "exact extraction lookup is empty or returns another candidate",
        evidence={"extraction_id": seed["id"], "returned_ids": [x.get("extraction_id", x.get("id")) for x in id_items]},
        failure_class=None if id_ok else "FAIL_DATA_CONSISTENCY",
        severity=None if id_ok else "P1",
    )
    query = str(seed.get("factor_name") or "").strip()
    if query:
        by_query = runner.tool("KB-002-query", "kb_factor_candidate_search", {"query": query[:200], "limit": 20})
        query_items = _items(by_query)
        query_ok = _success(by_query) and any(int(x.get("extraction_id", x.get("id", -1))) == int(seed["id"]) for x in query_items)
        runner.record(
            "KB-002",
            "KB candidate name search",
            "PASS" if query_ok else "FAIL",
            "candidate name search includes the exact seed" if query_ok else "exact candidate name is not discoverable",
            evidence={"query": query[:200], "returned_ids": [x.get("extraction_id", x.get("id")) for x in query_items]},
            failure_class=None if query_ok else "FAIL_SEARCH",
            severity=None if query_ok else "P1",
        )
    filters = {
        "validation_status": seed.get("validation_status"),
        "mapping_status": seed.get("mapping_status"),
        "min_confidence": float(seed["confidence_score"]) if seed.get("confidence_score") is not None else None,
    }
    assets = _parse_json_cell(seed.get("target_asset_class"))
    if isinstance(assets, list) and assets:
        filters["target_asset_class"] = str(assets[0])
    elif isinstance(assets, str) and assets:
        filters["target_asset_class"] = assets
    for index, (name, value) in enumerate([(k, v) for k, v in filters.items() if v is not None], 1):
        call = runner.tool(
            f"KB-003-{index}-{name}",
            "kb_factor_candidate_search",
            {"extraction_id": int(seed["id"]), name: value, "limit": 20},
        )
        rows = _items(call)
        if name == "validation_status":
            predicate = lambda x: x.get("validation_status") == value
        elif name == "mapping_status":
            predicate = lambda x: x.get("mapping_status") == value
        elif name == "min_confidence":
            predicate = lambda x: x.get("confidence_score", x.get("confidence")) is not None and Decimal(str(x.get("confidence_score", x.get("confidence")))) >= Decimal(str(value))
        else:
            predicate = lambda x: value in (_parse_json_cell(x.get("target_asset_class")) if isinstance(_parse_json_cell(x.get("target_asset_class")), list) else [x.get("target_asset_class")])
        ok = _success(call) and all(predicate(row) for row in rows)
        runner.record(
            f"KB-003-{index}-{name}",
            f"KB candidate {name} filter",
            "PASS" if ok else "FAIL",
            "all returned candidates satisfy the filter" if ok else "KB filter leaked a non-matching candidate",
            evidence={"filter": value, "returned_count": len(rows)},
            failure_class=None if ok else "FAIL_BUSINESS",
            severity=None if ok else "P1",
        )
    exposed = []
    for call in (by_id,):
        for item in _items(call):
            for key in item:
                if ERROR_KEY_RE.search(str(key)):
                    exposed.append(key)
    no_claim = "claim_token" not in exposed
    runner.record(
        "KB-004",
        "KB read-only result excludes mining claim token",
        "PASS" if no_claim else "FAIL",
        "no claim token was exposed" if no_claim else "candidate response exposed a mining claim token",
        evidence={"sensitive_keys_found": sorted(set(exposed))},
        failure_class=None if no_claim else "FAIL_SECURITY",
        severity=None if no_claim else "P0",
    )
    missing = runner.tool("KB-005-empty", "kb_factor_candidate_search", {})
    unknown = runner.tool("KB-005-notfound", "kb_factor_candidate_search", {"extraction_id": 9_000_000_000 + int(time.time())})
    empty_ok = _rejected(missing)
    unknown_ok = _success(unknown) and len(_items(unknown)) == 0
    runner.record(
        "KB-005",
        "KB requires a selector and handles unknown extraction",
        "PASS" if empty_ok and unknown_ok else "FAIL",
        "missing selector is rejected and unknown id returns an empty result" if empty_ok and unknown_ok else "KB selector/not-found behavior is incorrect",
        evidence={"missing_rejected": empty_ok, "unknown_empty": unknown_ok, "unknown_error": _error_code(unknown)},
        failure_class=None if empty_ok and unknown_ok else "FAIL_CONTRACT",
        severity=None if empty_ok and unknown_ok else "P1",
    )


def _universe_db_rows(runner: Runner, universe_key: str, as_of: datetime) -> list[dict[str, Any]]:
    return runner.db.fetch_all(
        """
        SELECT symbol, base_asset, quote_asset, market, exchange_name, instrument_type, sort_order
        FROM coin_universe_symbols
        WHERE universe_key=%s AND is_active=1 AND (valid_from IS NULL OR valid_from <= %s)
          AND (valid_to IS NULL OR valid_to > %s)
        ORDER BY sort_order, symbol
        """,
        (universe_key, as_of.replace(tzinfo=None), as_of.replace(tzinfo=None)),
    )


def _run_universe(runner: Runner, metric_data: dict[str, Any]) -> None:
    keys = [row.get("universe_key") for row in metric_data.get("scope_rows") or [] if row.get("universe_key")]
    if not keys:
        rows = runner.db.fetch_all("SELECT universe_key, COUNT(*) AS cnt FROM coin_universe_symbols GROUP BY universe_key ORDER BY cnt DESC, universe_key LIMIT 2")
        keys = [row["universe_key"] for row in rows]
    if not keys:
        runner.record("UNIVERSE-001", "authoritative universe current membership", "BLOCKED", "no universe exists", failure_class="BLOCKED_DATA_PRECONDITION")
        return
    key = str(keys[0])
    current_as_of = datetime.now(timezone.utc)
    current = runner.tool("UNIVERSE-001", "universe_list_symbols", {"universe_key": key, "as_of": current_as_of.isoformat()})
    data = _data(current)
    rows = data.get("items") or data.get("symbols") or []
    if rows and isinstance(rows[0], str):
        mcp_symbols = [str(value) for value in rows]
    else:
        mcp_symbols = [str(value.get("symbol")) for value in rows if isinstance(value, dict) and value.get("symbol")]
    db_rows = _universe_db_rows(runner, key, current_as_of)
    db_symbols = [str(row["symbol"]) for row in db_rows]
    current_ok = _success(current) and len(mcp_symbols) == len(set(mcp_symbols)) and set(mcp_symbols) == set(db_symbols)
    runner.record(
        "UNIVERSE-001",
        "current universe membership equals database",
        "PASS" if current_ok else "FAIL",
        "MCP returns the exact active symbol set without duplicates" if current_ok else "MCP universe differs from active database membership",
        evidence={"universe_key": key, "mcp_count": len(mcp_symbols), "db_count": len(db_symbols), "missing_in_mcp": sorted(set(db_symbols) - set(mcp_symbols)), "extra_in_mcp": sorted(set(mcp_symbols) - set(db_symbols))},
        failure_class=None if current_ok else "FAIL_DATA_CONSISTENCY",
        severity=None if current_ok else "P1",
    )
    period = runner.db.fetch_one(
        """
        SELECT MIN(valid_from) AS min_from, MAX(valid_from) AS max_from
        FROM coin_universe_symbols WHERE universe_key=%s AND is_active=1
        """,
        (key,),
    )
    if period:
        raw_time = (period.get("min_from") or current_as_of.replace(tzinfo=None)) - timedelta(days=30)
        historical_as_of = raw_time.replace(tzinfo=timezone.utc)
        historical = runner.tool("UNIVERSE-002", "universe_list_symbols", {"universe_key": key, "as_of": historical_as_of.isoformat()})
        hdata = _data(historical)
        hrows = hdata.get("items") or hdata.get("symbols") or []
        hmcp = [str(x) for x in hrows] if hrows and isinstance(hrows[0], str) else [str(x.get("symbol")) for x in hrows if isinstance(x, dict) and x.get("symbol")]
        hdb = [str(row["symbol"]) for row in _universe_db_rows(runner, key, historical_as_of)]
        historical_ok = _success(historical) and set(hmcp) == set(hdb) and len(hmcp) == len(set(hmcp))
        runner.record(
            "UNIVERSE-002",
            "historical point-in-time universe membership",
            "PASS" if historical_ok else "FAIL",
            "historical MCP membership matches the DB visibility window" if historical_ok else "historical universe leaks or omits symbols",
            evidence={"universe_key": key, "as_of": historical_as_of.isoformat(), "mcp_count": len(hmcp), "db_count": len(hdb), "missing_in_mcp": sorted(set(hdb) - set(hmcp)), "extra_in_mcp": sorted(set(hmcp) - set(hdb))},
            failure_class=None if historical_ok else "FAIL_POINT_IN_TIME",
            severity=None if historical_ok else "P0",
        )
    unknown = runner.tool("UNIVERSE-003", "universe_list_symbols", {"universe_key": f"missing-{uuid4()}"})
    unknown_rows = _data(unknown).get("items") or _data(unknown).get("symbols") or []
    unknown_ok = (_success(unknown) and not unknown_rows) or _rejected(unknown)
    runner.record(
        "UNIVERSE-003",
        "unknown universe does not fall back to another universe",
        "PASS" if unknown_ok else "FAIL",
        "unknown key returned empty/not-found" if unknown_ok else "unknown key returned another universe's symbols",
        evidence={"http_status": unknown["http_status"], "returned_count": len(unknown_rows), "error_code": _error_code(unknown)},
        failure_class=None if unknown_ok else "FAIL_SCOPE_ISOLATION",
        severity=None if unknown_ok else "P0",
    )


def _run_representation_checks(runner: Runner) -> None:
    comparable = [call for call in runner.calls if call.get("representations_equal") is not None]
    unequal = [call["case_id"] for call in comparable if call.get("representations_equal") is False]
    runner.record(
        "MCP-017-CATALOG",
        "content and structuredContent business representations",
        "PASS" if not unequal and comparable else "FAIL",
        "all comparable tool responses carry identical representations" if not unequal and comparable else "one or more tool representations differ or none were comparable",
        evidence={"compared_count": len(comparable), "unequal_cases": unequal},
        failure_class=None if not unequal and comparable else "FAIL_CONTRACT",
        severity=None if not unequal and comparable else "P1",
    )


def _run_invalid_arguments(runner: Runner) -> None:
    _assert_rejections(
        runner,
        [
            ("NEG-search-limit0", "factor_search", {"limit": 0}),
            ("NEG-search-limit501", "factor_search", {"limit": 501}),
            ("NEG-search-kind", "factor_search", {"kind": "parent"}),
            ("NEG-search-date", "factor_search", {"as_of": "not-a-date"}),
            ("NEG-search-extra", "factor_search", {"unknown": 1}),
            ("NEG-search-mixed-mode", "factor_search", {"library_status": "valid", "validity": "valid"}),
            ("NEG-search-incomplete-scope", "factor_search", {"validity": "valid", "as_of": datetime.now(timezone.utc).isoformat()}),
            ("NEG-stats-kind", "factor_catalog_stats", {"kind": "parent"}),
            ("NEG-stats-extra", "factor_catalog_stats", {"unknown": 1}),
            ("NEG-stats-mixed-mode", "factor_catalog_stats", {"library_status": "valid", "validity": "valid"}),
            ("NEG-detail-missing", "factor_get_detail", {}),
            ("NEG-detail-level", "factor_get_detail", {"factor_ref": "sub_factor:1", "detail_level": "full"}),
            ("NEG-detail-limit0", "factor_get_detail", {"factor_ref": "factor:1", "children_limit": 0}),
            ("NEG-batch-empty", "factor_get_details_batch", {"factor_refs": []}),
            ("NEG-batch-over50", "factor_get_details_batch", {"factor_refs": [f"sub_factor:{x}" for x in range(1, 52)]}),
            ("NEG-kb-query-blank", "kb_factor_candidate_search", {"query": ""}),
            ("NEG-kb-query-long", "kb_factor_candidate_search", {"query": "x" * 201}),
            ("NEG-kb-confidence", "kb_factor_candidate_search", {"query": "x", "min_confidence": 1.01}),
            ("NEG-kb-status", "kb_factor_candidate_search", {"query": "x", "validation_status": "valid"}),
            ("NEG-universe-missing", "universe_list_symbols", {}),
            ("NEG-universe-date", "universe_list_symbols", {"universe_key": "x", "as_of": "bad-date"}),
            ("NEG-universe-extra", "universe_list_symbols", {"universe_key": "x", "unknown": 1}),
        ],
    )


def main() -> None:
    """Execute all catalog deep checks and write machine-readable and Markdown summaries."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = PROJECT_ROOT / "reports" / "factor4-deep" / f"{run_stamp}-catalog"
    settings = SettingsLoader.load("test", PROJECT_ROOT)
    db = DatabaseClient.from_settings(settings.database)
    runner = Runner(token, output_dir, db)
    before_state = _db_state(runner)
    init = runner.request(
        "MCP-INIT",
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest-catalog-deep", "version": "1.0"}},
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    runner.protocol_version = init_result.get("protocolVersion")
    init_ok = init["http_status"] == 200 and runner.protocol_version == "2025-06-18"
    runner.record(
        "MCP-INIT",
        "MCP initialization for catalog deep test",
        "PASS" if init_ok else "FAIL",
        "protocol negotiation succeeded" if init_ok else "protocol negotiation failed",
        evidence={"http_status": init["http_status"], "protocol_version": runner.protocol_version, "server_info": init_result.get("serverInfo"), "session_present": bool(runner.session_id)},
        failure_class=None if init_ok else "FAIL_TRANSPORT",
        severity=None if init_ok else "P0",
    )
    runner.notify_initialized("MCP-NOTIFY")
    tools_call = runner.request("MCP-TOOLS", "tools/list", {})
    tools = (((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or [])
    required = {"factor_catalog_stats", "factor_search", "factor_get_detail", "factor_get_details_batch", "factor_get_formula", "kb_factor_candidate_search", "universe_list_symbols", "factor_list_metric_scopes"}
    names = {row.get("name") for row in tools if isinstance(row, dict)}
    tools_ok = required <= names
    runner.record(
        "MCP-TOOLS",
        "catalog deep-test tool discovery",
        "PASS" if tools_ok else "FAIL",
        "all required read-only tools are present" if tools_ok else "one or more required tools are absent",
        evidence={"required": sorted(required), "missing": sorted(required - names), "tool_count": len(names)},
        failure_class=None if tools_ok else "FAIL_CONTRACT",
        severity=None if tools_ok else "P1",
    )
    stats = _run_catalog_stats(runner)
    search_data = _run_library_search(runner, stats)
    metric_data = _run_metric_search(runner, search_data)
    _run_details(runner, search_data)
    _run_formula(runner)
    _run_kb(runner)
    _run_universe(runner, metric_data)
    _run_invalid_arguments(runner)
    _run_representation_checks(runner)
    after_state = _db_state(runner)
    no_mutation = before_state == after_state
    runner.record(
        "READONLY-001",
        "read-only catalog calls have no core table side effects",
        "PASS" if no_mutation else "FAIL",
        "core catalog/KB/universe counts and max update times are unchanged" if no_mutation else "a read-only call coincided with or caused persisted changes",
        evidence={"before": before_state, "after": after_state},
        failure_class=None if no_mutation else "ASYNC_STATE_MOVING",
        severity=None,
    )
    counts = Counter(row["status"] for row in runner.cases)
    summary = {
        "run_id": run_stamp,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "environment": "test",
        "mcp_host": "test-factor-frontend.questvector.ai",
        "database": settings.database.name,
        "read_only": True,
        "case_counts": dict(sorted(counts.items())),
        "call_count": len(runner.calls),
        "cases": runner.cases,
        "failed_cases": [row for row in runner.cases if row["status"] == "FAIL"],
        "blocked_cases": [row for row in runner.cases if row["status"] == "BLOCKED"],
    }
    _write_json(output_dir / "summary.json", summary)
    _write_json(
        output_dir / "call-ledger.json",
        [
            {
                "case_id": call.get("case_id"),
                "tool": call.get("tool"),
                "method": call.get("method"),
                "arguments": call.get("arguments"),
                "http_status": call.get("http_status"),
                "elapsed_seconds": call.get("elapsed_seconds"),
                "is_error": call.get("is_error"),
                "error_code": _error_code(call),
                "request_id": _meta(call).get("request_id"),
                "trace_id": _meta(call).get("trace_id"),
                "representations_equal": call.get("representations_equal"),
            }
            for call in runner.calls
        ],
    )
    lines = [
        "# Factor 4.0 catalog deep read-only regression",
        "",
        f"- Run: `{run_stamp}`",
        "- Environment: `test`",
        f"- Cases: PASS={counts.get('PASS', 0)}, FAIL={counts.get('FAIL', 0)}, BLOCKED={counts.get('BLOCKED', 0)}",
        f"- MCP calls: {len(runner.calls)}",
        "",
        "## Case results",
        "",
        "| Case | Status | Title | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for row in runner.cases:
        lines.append(f"| {row['case_id']} | {row['status']} | {row['title']} | {row['reason']} |")
    lines.extend(["", "## Confirmed failures", ""])
    failures = [row for row in runner.cases if row["status"] == "FAIL"]
    if failures:
        for row in failures:
            lines.append(f"- `{row['case_id']}` ({row.get('severity') or 'unrated'}): {row['reason']}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Blocked", ""])
    blocked = [row for row in runner.cases if row["status"] == "BLOCKED"]
    if blocked:
        for row in blocked:
            lines.append(f"- `{row['case_id']}`: {row['reason']}")
    else:
        lines.append("- None.")
    (output_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "case_counts": dict(counts), "call_count": len(runner.calls)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
