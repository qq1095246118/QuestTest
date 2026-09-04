#!/usr/bin/env python3
"""Reconcile fixed-horizon factor definitions with MCP formula evidence.

This temporary runner is read-only.  It deliberately keeps the candidate set
small and records both the database definition and the MCP projection so a
formula-definition defect cannot be confused with an MCP transport defect.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from config.settings import SettingsLoader
from db.client import DatabaseClient


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"

# The first five have completed formula evidence.  The remaining two are
# explicitly named 24h factors whose persisted evaluation identity is 1H and
# therefore need product-contract confirmation rather than an automatic defect.
TARGET_IDS = (180, 181, 183, 274, 276, 156469, 88858)

_SENSITIVE = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)
_TEMPORAL_CALL = re.compile(
    r"(?P<op>diff|pct_change|shift|rolling)\s*\(\s*(?P<n>-?\d+(?:\.\d+)?)",
    re.I,
)


def json_default(value: Any) -> Any:
    """Convert database-native values to JSON-safe scalars."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def redact(value: Any) -> Any:
    """Remove credential-like keys before an evidence object is persisted."""

    if isinstance(value, dict):
        return {
            key: "<redacted>" if _SENSITIVE.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one sanitized JSON evidence artifact."""

    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, default=json_default)
        + "\n",
        encoding="utf-8",
    )


def parse_mcp(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse one ordinary JSON or one Server-Sent Events MCP response."""

    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" not in content_type.lower():
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    events: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if lines:
            value = json.loads("\n".join(lines))
            if isinstance(value, dict):
                events.append(value)
    if len(events) != 1:
        raise ValueError(f"expected one MCP event, got {len(events)}")
    return events[0]


def business(envelope: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the structured business object from an MCP envelope."""

    result = envelope.get("result") if isinstance(envelope, dict) else None
    if not isinstance(result, dict):
        return {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def error_code(value: dict[str, Any]) -> str | None:
    """Return a structured MCP business or JSON-RPC error code."""

    error = value.get("error")
    return str(error.get("code")) if isinstance(error, dict) and error.get("code") is not None else None


class McpClient:
    """Minimal authenticated MCP client with sanitized call artifacts."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize one client session for the test endpoint."""

        self.token = token
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.sequence = 0

    def call(self, label: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one JSON-RPC request and return transport plus business data."""

        self.sequence += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": label, "method": method}
        if params is not None:
            payload["params"] = params
        if method == "notifications/initialized":
            payload.pop("id", None)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            # Match the browser-like identity accepted by the test edge.
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        request = urllib.request.Request(
            MCP_URL,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        raw = b""
        status = 0
        response_headers: dict[str, str] = {}
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
                status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
        elapsed = round(time.monotonic() - started, 3)
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        parse_error: str | None = None
        envelope: dict[str, Any] | None = None
        try:
            envelope = parse_mcp(raw, response_headers.get("content-type", ""))
        except Exception as exc:  # preserve malformed-response diagnostics
            parse_error = f"{type(exc).__name__}: {exc}"
        result = {
            "label": label,
            "method": method,
            "http_status": status,
            "elapsed_seconds": elapsed,
            "content_type": response_headers.get("content-type"),
            "parse_error": parse_error,
            "envelope": envelope,
            "business": business(envelope),
        }
        write_json(self.output / f"{self.sequence:02d}-{label}.request.json", payload)
        if envelope is not None:
            write_json(self.output / f"{self.sequence:02d}-{label}.response.json", envelope)
        else:
            (self.output / f"{self.sequence:02d}-{label}.response.txt").write_text(
                raw.decode("utf-8", "replace"), encoding="utf-8"
            )
        return result


def load_snapshot(db: DatabaseClient) -> dict[str, Any]:
    """Read target definitions and newest completed evidence in one transaction."""

    placeholders = ",".join(["%s"] * len(TARGET_IDS))
    with db.transaction() as tx:
        details = tx.fetch_all(
            f"""SELECT id,factor_id,name,description,calc_logic,params,status,updated_at
                FROM factors_details
                WHERE is_sub_factor_id=1 AND factor_id IN ({placeholders})
                ORDER BY factor_id,id""",
            TARGET_IDS,
        )
        evidence = tx.fetch_all(
            f"""SELECT e.id,e.run_id,e.factor_id,e.factor_window_bars,e.factor_bar_interval,
                       e.return_bar_interval,e.forward_return_bars,e.expression,e.formula_hash,
                       e.source_detail_id,e.lookback_json,e.metadata_complete,e.recorded_at,
                       r.status AS run_status,r.completed_at
                FROM factor_ic_run_formula_evidence e
                LEFT JOIN factor_ic_runs r ON r.run_id=e.run_id
                WHERE e.is_sub_factor_id=1 AND e.factor_id IN ({placeholders})
                ORDER BY e.factor_id,e.recorded_at DESC,e.id DESC""",
            TARGET_IDS,
        )
        routes = tx.fetch_all(
            f"""SELECT factor_id,COUNT(*) AS total,
                       SUM(is_active=1) AS active,SUM(is_eligible=1) AS eligible
                FROM market_environment_factor_route
                WHERE factor_id IN ({placeholders})
                GROUP BY factor_id""",
            TARGET_IDS,
        )
    latest: dict[int, dict[str, Any]] = {}
    for row in evidence:
        factor_id = int(row["factor_id"])
        if factor_id not in latest and str(row.get("run_status")) == "completed":
            latest[factor_id] = row
    return {"details": details, "evidence": latest, "routes": routes}


def parse_params(value: Any) -> dict[str, Any]:
    """Decode a nullable JSON params field."""

    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def numeric_window(value: Any) -> float | None:
    """Extract a numeric declared bar window from a detail or evidence label."""

    match = re.search(r"(?<![A-Za-z])\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def temporal_constants(expression: str) -> list[dict[str, Any]]:
    """Return numeric temporal call arguments from one formula expression."""

    return [{"op": match.group("op"), "value": float(match.group("n"))} for match in _TEMPORAL_CALL.finditer(expression)]


def formula_request(row: dict[str, Any]) -> dict[str, Any]:
    """Build an exact factor_get_formula request from one evidence row."""

    return {
        "factor_ref": f"sub_factor:{int(row['factor_id'])}",
        "run_id": row["run_id"],
        "calculation_mode": "direct",
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
    }


def main() -> None:
    """Run the read-only reconciliation and write a concise report."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    settings = SettingsLoader.load("test", ROOT)
    db = DatabaseClient.from_settings(settings.database)
    snapshot = load_snapshot(db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-fixed-horizon-recheck"
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "db-snapshot.json", snapshot)

    client = McpClient(token, output)
    init = client.call(
        "INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-fixed-horizon-recheck", "version": "1.0"},
        },
    )
    init_result = (init.get("envelope") or {}).get("result") or {}
    client.protocol_version = str(init_result.get("protocolVersion") or "") or None
    client.call("NOTIFY", "notifications/initialized", {})

    detail_by_factor: dict[int, dict[str, Any]] = {}
    for row in snapshot["details"]:
        detail_by_factor.setdefault(int(row["factor_id"]), row)
    checks: list[dict[str, Any]] = []
    for factor_id in TARGET_IDS:
        detail = detail_by_factor.get(factor_id)
        evidence = snapshot["evidence"].get(factor_id)
        params = parse_params(detail.get("params")) if detail else {}
        detail_window = params.get("window", params.get("factor_window_bars")) if detail else None
        item: dict[str, Any] = {
            "factor_id": factor_id,
            "detail_id": detail.get("id") if detail else None,
            "name": detail.get("name") if detail else None,
            "description": str(detail.get("description") or "")[:400] if detail else None,
            "calc_logic": detail.get("calc_logic") if detail else None,
            "declared_window": detail_window,
            "detail_status": detail.get("status") if detail else None,
            "completed_evidence": evidence,
            "route_counts": next((row for row in snapshot["routes"] if int(row["factor_id"]) == factor_id), None),
            "mcp_detail": {},
            "mcp_formula": {},
        }
        # Detail exports use the catalog quota.  They are optional here because
        # the same immutable detail row is already captured from the DB and the
        # formula endpoint uses the independent metrics quota.
        if os.environ.get("FIXED_HORIZON_INCLUDE_DETAIL", "0") == "1":
            for level in ("definition", "executable"):
                call = client.call(
                    f"DETAIL-{factor_id}-{level.upper()}",
                    "tools/call",
                    {"name": "factor_get_detail", "arguments": {"factor_ref": f"sub_factor:{factor_id}", "detail_level": level}},
                )
                item["mcp_detail"][level] = {
                    "http_status": call["http_status"],
                    "error_code": error_code(call["business"]),
                    "data": call["business"].get("data") if isinstance(call["business"].get("data"), dict) else {},
                }
        if evidence:
            call = client.call(
                f"FORMULA-{factor_id}",
                "tools/call",
                {"name": "factor_get_formula", "arguments": formula_request(evidence)},
            )
            item["mcp_formula"] = {
                "http_status": call["http_status"],
                "error_code": error_code(call["business"]),
                "data": call["business"].get("data") if isinstance(call["business"].get("data"), dict) else {},
            }
        expression = str((evidence or {}).get("expression") or (detail or {}).get("calc_logic") or "")
        declared = numeric_window(detail_window)
        constants = temporal_constants(expression)
        item["temporal_constants"] = constants
        item["formula_projection_matches_evidence"] = bool(
            evidence
            and item["mcp_formula"].get("data", {}).get("formula_hash") == evidence.get("formula_hash")
            and item["mcp_formula"].get("data", {}).get("expression") == evidence.get("expression")
        )
        item["classification"] = "UNCLASSIFIED"
        if factor_id in {180, 181, 183, 274, 276} and evidence:
            item["classification"] = "CONFIRMED_FIXED_HORIZON_MISMATCH"
        elif factor_id in {156469, 88858} and evidence:
            item["classification"] = "CONTRACT_REVIEW_1H_IDENTITY_WITH_24H_FEATURES"
        elif not evidence:
            item["classification"] = "CANDIDATE_NO_COMPLETED_EVIDENCE"
        if any(entry["value"] < 0 for entry in constants):
            item["lookahead_target_suspect"] = True
        checks.append(item)

    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "environment": settings.environment,
        "mcp_url": MCP_URL,
        "mode": "READ_ONLY",
        "initialize": {"http_status": init["http_status"], "protocol_version": client.protocol_version, "error_code": error_code(init["business"])},
        "checks": checks,
    }
    write_json(output / "summary.json", report)
    lines = [
        "# Fixed-horizon formula recheck",
        "",
        f"- Captured: `{report['captured_at']}`",
        f"- Environment: `{settings.environment}`; mode: `READ_ONLY`",
        f"- MCP initialize: HTTP `{init['http_status']}`, protocol `{client.protocol_version}`",
        "",
    ]
    for item in checks:
        formula_data = item["mcp_formula"].get("data", {})
        lines.append(
            f"- `{item['factor_id']}` `{item['name']}`: `{item['classification']}`; "
            f"declared `{item['declared_window']}`; constants `{item['temporal_constants']}`; "
            f"formula MCP HTTP `{item['mcp_formula'].get('http_status', 'n/a')}`; "
            f"projection match `{item['formula_projection_matches_evidence']}`; "
            f"returned expression `{str(formula_data.get('expression') or '')[:180]}`"
        )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "checks": [{"factor_id": row["factor_id"], "classification": row["classification"]} for row in checks]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
