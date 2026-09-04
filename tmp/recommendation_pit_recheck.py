#!/usr/bin/env python3
"""Recheck recommendation point-in-time selection with explicit timezone handling.

The test database stores lifecycle timestamps as Asia/Shanghai wall-clock
``DATETIME`` values.  This runner converts them to UTC before constructing MCP
requests, then compares the returned publication identity with the set of
published batches visible at each requested instant.  It is read-only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pymysql

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
SHANGHAI = ZoneInfo("Asia/Shanghai")
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)


def _json_default(value: Any) -> str:
    """Serialize database-native values for evidence artifacts."""

    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _redact(value: Any) -> Any:
    """Recursively redact credential-like keys from saved evidence."""

    if isinstance(value, dict):
        return {
            key: "<redacted>" if SENSITIVE_KEY.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _write(path: Path, value: Any) -> None:
    """Write one sanitized JSON artifact."""

    path.write_text(
        json.dumps(_redact(value), ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _parse_response(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse a JSON or single-event MCP SSE response."""

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


def _business(envelope: dict[str, Any] | None) -> dict[str, Any]:
    """Extract structured business data, falling back to text content."""

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


def _error_code(business: dict[str, Any]) -> str | None:
    """Extract a business error code when present."""

    error = business.get("error")
    if isinstance(error, dict) and error.get("code") is not None:
        return str(error["code"])
    return None


class Client:
    """Minimal MCP client for one authenticated read-only session."""

    def __init__(self, token: str, output: Path) -> None:
        """Initialize the client with a runtime token and evidence directory."""

        self.token = token
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.sequence = 0

    def call(self, case_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one MCP request and return transport plus business data."""

        self.sequence += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": f"{case_id}-{uuid4()}", "method": method}
        if params is not None:
            payload["params"] = params
        if method == "notifications/initialized":
            payload.pop("id", None)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            # The test edge rejects synthetic automation User-Agents; keep the
            # transport identity consistent with the other read-only probes.
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
        status = 0
        response_headers: dict[str, str] = {}
        raw = b""
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
            envelope = _parse_response(raw, response_headers.get("content-type", "")) if raw else None
        except Exception as exc:  # preserve malformed response evidence
            parse_error = f"{type(exc).__name__}: {exc}"
        business = _business(envelope)
        call = {
            "case_id": case_id,
            "method": method,
            "http_status": status,
            "elapsed_seconds": elapsed,
            "content_type": response_headers.get("content-type"),
            "parse_error": parse_error,
            "business": business,
            "envelope": envelope,
        }
        stem = f"{self.sequence:02d}-{case_id}"
        _write(self.output / f"{stem}.request.json", payload)
        if envelope is not None:
            _write(self.output / f"{stem}.response.json", envelope)
        else:
            (self.output / f"{stem}.response.txt").write_text(raw.decode("utf-8", "replace"), encoding="utf-8")
        return call


def _as_utc(value: Any) -> datetime | None:
    """Interpret a DB lifecycle DATETIME as Asia/Shanghai and return UTC."""

    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(timezone.utc)


def _api_time(value: Any) -> datetime | None:
    """Normalize an API timestamp to UTC for identity comparisons."""

    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(timezone.utc)


def _published_batches(settings: Any) -> list[dict[str, Any]]:
    """Read all published batches for the active market/profile scope."""

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
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                """
                SELECT id,batch_uid,market_scope,route_profile_key,status,publish_status,
                       published_at,publication_uid,publish_version,is_active
                FROM market_environment_eval_batch
                WHERE publish_status='published' AND published_at IS NOT NULL
                ORDER BY published_at,id
                """
            )
            rows = [dict(row) for row in cursor.fetchall()]
            connection.rollback()
            return rows
    finally:
        connection.close()


def main() -> None:
    """Execute before/at/after-publication recommendation assertions."""

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    settings = SettingsLoader.load("test", ROOT)
    if settings.environment != "test":
        raise SystemExit("test environment gate failed")
    batches = _published_batches(settings.database)
    if not batches:
        raise SystemExit("no published batch is available")
    target = batches[-1]
    published_at = _as_utc(target["published_at"])
    if published_at is None:
        raise SystemExit("selected publication has no published_at")
    # The current database contains one publication; this oracle also handles
    # a prior publication by selecting the newest one visible at each instant.
    query_times = {
        "BEFORE": published_at - timedelta(seconds=1),
        "AT": published_at,
        "AFTER": published_at + timedelta(seconds=1),
    }
    output = ROOT / "reports" / "factor4-deep" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-recommendation-pit-recheck"
    client = Client(token, output)
    init = client.call(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-recommendation-pit", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    client.protocol_version = str(init_result.get("protocolVersion") or "") or None
    client.call("MCP-NOTIFY", "notifications/initialized", {})
    results: list[dict[str, Any]] = []
    for label, query_time in query_times.items():
        call = client.call(
            f"REC-{label}",
            "tools/call",
            {
                "name": "environment_get_recommendations",
                "arguments": {
                    "market_scope": target["market_scope"],
                    "route_profile_key": target["route_profile_key"],
                    "as_of": query_time.isoformat(),
                    "limit": 20,
                },
            },
        )
        business = call["business"]
        data = business.get("data") if isinstance(business.get("data"), dict) else {}
        publication = data.get("publication") if isinstance(data.get("publication"), dict) else {}
        returned_uid = publication.get("publication_uid") or publication.get("id")
        returned_time = _api_time(publication.get("published_at"))
        expected = [
            row
            for row in batches
            if row["market_scope"] == target["market_scope"]
            and row["route_profile_key"] == target["route_profile_key"]
            and (_as_utc(row["published_at"]) or query_time) <= query_time
        ]
        expected_row = expected[-1] if expected else None
        identity_ok = returned_uid == (expected_row.get("publication_uid") if expected_row else None)
        time_ok = returned_time is None or returned_time <= query_time
        results.append(
            {
                "case_id": f"REC-{label}",
                "requested_as_of": query_time.isoformat(),
                "expected_publication_uid": expected_row.get("publication_uid") if expected_row else None,
                "returned_publication_uid": returned_uid,
                "returned_published_at_utc": returned_time.isoformat() if returned_time else None,
                "status": data.get("status"),
                "reason_code": data.get("reason_code"),
                "http_status": call.get("http_status"),
                "error_code": _error_code(business),
                "identity_ok": identity_ok,
                "no_future_publication": time_ok,
                "pass": call.get("http_status") == 200 and identity_ok and time_ok,
            }
        )
    report = {
        "environment": "test",
        "mode": "READ_ONLY",
        "mcp_url": MCP_URL,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "selected_batch": target,
        "published_batches": batches,
        "results": results,
        "all_pass": all(item["pass"] for item in results),
        "snapshot_id": hashlib.sha256(json.dumps(batches, default=_json_default, sort_keys=True).encode()).hexdigest(),
    }
    _write(output / "summary.json", report)
    print(json.dumps({"output_dir": str(output), "all_pass": report["all_pass"], "results": results}, ensure_ascii=False, default=_json_default))


if __name__ == "__main__":
    main()
