#!/usr/bin/env python3
"""Execute a read-only MCP pagination and database reconciliation for daily environments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
PAGE_LIMIT = 1000
DB_TIMEZONE = ZoneInfo("Asia/Shanghai")
CLIENT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
OUTPUT_FIELDS = (
    "id",
    "environment_date",
    "label_kind",
    "label_code",
    "label_status",
    "features",
    "probabilities",
    "confidence",
    "effective_from",
    "effective_to",
    "model_version",
    "schema_version",
    "revision",
    "is_current",
    "raw_payload",
    "available_at",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "request_id",
    "confidence_level",
)


def json_default(value: Any) -> Any:
    """Convert database-specific scalar values into stable JSON-compatible values."""

    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=DB_TIMEZONE)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def write_json(path: Path, value: Any) -> None:
    """Write deterministic UTF-8 JSON evidence without credentials."""

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_json_column(value: Any) -> Any:
    """Parse a MySQL JSON column while preserving already-decoded values."""

    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


def normalize_datetime(value: Any, naive_timezone: tzinfo = timezone.utc) -> str | None:
    """Normalize API or naive UTC database datetimes to a microsecond UTC string."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=naive_timezone)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=naive_timezone)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_decimal(value: Any) -> str | None:
    """Normalize nullable decimal fields without binary floating-point noise."""

    if value is None:
        return None
    normalized = Decimal(str(value)).normalize()
    return format(normalized, "f")


def canonical_item(item: dict[str, Any], naive_timezone: tzinfo = timezone.utc) -> dict[str, Any]:
    """Normalize one MCP or database row for exact semantic comparison."""

    result = {field: item.get(field) for field in OUTPUT_FIELDS}
    result["id"] = int(result["id"])
    result["environment_date"] = str(result["environment_date"])
    result["revision"] = int(result["revision"])
    result["is_current"] = int(result["is_current"])
    result["features"] = parse_json_column(result["features"])
    result["probabilities"] = parse_json_column(result["probabilities"])
    result["raw_payload"] = parse_json_column(result["raw_payload"])
    result["confidence"] = normalize_decimal(result["confidence"])
    for field in ("effective_from", "effective_to", "available_at", "created_at", "updated_at"):
        result[field] = normalize_datetime(result[field], naive_timezone)
    return result


def row_hash(item: dict[str, Any]) -> str:
    """Return a stable SHA-256 digest for a canonical daily row."""

    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def post_mcp(token: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any], float]:
    """Call the MCP endpoint once and return status, decoded envelope, and latency."""

    request = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": CLIENT_USER_AGENT,
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
    elapsed = time.monotonic() - started
    return status, json.loads(body), elapsed


def extract_result(envelope: dict[str, Any]) -> dict[str, Any]:
    """Extract and cross-check the MCP JSON text and structured content forms."""

    result = envelope.get("result")
    if not isinstance(result, dict):
        raise AssertionError(f"Missing MCP result: {envelope.get('error')}")
    if result.get("isError") is True:
        raise AssertionError(f"MCP business error: {result.get('structuredContent')}")
    content = result.get("content")
    if not isinstance(content, list) or not content or content[0].get("type") != "text":
        raise AssertionError("MCP result does not contain a JSON text block")
    parsed = json.loads(content[0]["text"])
    structured = result.get("structuredContent")
    if structured is not None and structured != parsed:
        raise AssertionError("MCP text content differs from structuredContent")
    if "error" in parsed:
        raise AssertionError(f"MCP tool returned an error: {parsed['error']}")
    return parsed


def fetch_db_oracle(client: DatabaseClient, label_kind: str, as_of_db: str) -> list[dict[str, Any]]:
    """Fetch the latest revision visible at the same database UTC timestamp."""

    fields = ", ".join(OUTPUT_FIELDS)
    query = f"""
        SELECT {fields}
        FROM market_environment_daily d
        WHERE d.label_kind = %s
          AND d.available_at <= %s
          AND d.revision = (
              SELECT MAX(d2.revision)
              FROM market_environment_daily d2
              WHERE d2.environment_date = d.environment_date
                AND d2.label_kind = d.label_kind
                AND d2.available_at <= %s
          )
        ORDER BY d.environment_date DESC, d.id DESC
    """
    return client.fetch_all(query, (label_kind, as_of_db, as_of_db))


def state_summary(client: DatabaseClient) -> dict[str, Any]:
    """Read a compact database state summary used to detect concurrent changes."""

    rows = client.fetch_all(
        """
        SELECT label_kind, COUNT(*) AS row_count, MAX(id) AS max_id,
               MAX(available_at) AS max_available_at, MAX(updated_at) AS max_updated_at
        FROM market_environment_daily
        GROUP BY label_kind
        ORDER BY label_kind
        """
    )
    return {str(row["label_kind"]): row for row in rows}


def assert_daily_sequence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return gaps or reversals in the descending daily date sequence."""

    issues: list[dict[str, Any]] = []
    for index, (left, right) in enumerate(zip(items, items[1:]), start=1):
        left_date = date.fromisoformat(str(left["environment_date"]))
        right_date = date.fromisoformat(str(right["environment_date"]))
        delta = (left_date - right_date).days
        if delta != 1:
            issues.append(
                {
                    "left_index": index - 1,
                    "right_index": index,
                    "left_id": left["id"],
                    "right_id": right["id"],
                    "left_date": left_date.isoformat(),
                    "right_date": right_date.isoformat(),
                    "day_delta": delta,
                }
            )
    return issues


def paginate(
    token: str,
    output_dir: Path,
    label_kind: str,
    as_of_api: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Follow all signed cursors for one label kind and persist redacted evidence."""

    all_items: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    cursor: str | None = None
    pages: list[dict[str, Any]] = []
    page_number = 0
    page_data_as_of_values: list[str | None] = []

    while True:
        page_number += 1
        arguments: dict[str, Any] = {
            "label_kind": label_kind,
            "limit": PAGE_LIMIT,
            "as_of": as_of_api,
        }
        if cursor is not None:
            arguments["cursor"] = cursor
        request_id = f"DAILY-{label_kind.upper()}-P{page_number:03d}"
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": "environment_get_daily", "arguments": arguments},
        }
        write_json(output_dir / f"{label_kind}-page-{page_number:03d}.request.json", payload)
        status, envelope, elapsed = post_mcp(token, payload)
        write_json(output_dir / f"{label_kind}-page-{page_number:03d}.response.json", envelope)
        if status != 200:
            raise AssertionError(f"{label_kind} page {page_number} returned HTTP {status}")
        parsed = extract_result(envelope)
        data = parsed.get("data") or {}
        meta = parsed.get("meta") or {}
        items = data.get("items") or []
        if data.get("returned_count") != len(items):
            raise AssertionError(f"{label_kind} page {page_number} returned_count mismatch")
        page_ids = [int(item["id"]) for item in items]
        duplicates = sorted(seen_ids.intersection(page_ids))
        if duplicates:
            raise AssertionError(f"{label_kind} page {page_number} repeats IDs: {duplicates[:10]}")
        if len(set(page_ids)) != len(page_ids):
            raise AssertionError(f"{label_kind} page {page_number} has duplicate IDs within the page")
        if any(item.get("label_kind") != label_kind for item in items):
            raise AssertionError(f"{label_kind} page {page_number} mixes label kinds")
        expected_order = sorted(items, key=lambda item: (str(item["environment_date"]), int(item["id"])), reverse=True)
        if items != expected_order:
            raise AssertionError(f"{label_kind} page {page_number} is not in descending date/id order")
        data_as_of = meta.get("data_as_of")
        if data_as_of is not None and normalize_datetime(data_as_of) > normalize_datetime(as_of_api):
            raise AssertionError(f"{label_kind} page {page_number} exposes data newer than as_of")
        page_data_as_of_values.append(data_as_of)
        next_cursor = meta.get("next_cursor")
        truncated = meta.get("truncated")
        if bool(next_cursor) != bool(truncated):
            raise AssertionError(f"{label_kind} page {page_number} cursor/truncated mismatch")
        pages.append(
            {
                "page": page_number,
                "http_status": status,
                "elapsed_seconds": round(elapsed, 3),
                "returned_count": len(items),
                "first_id": page_ids[0] if page_ids else None,
                "last_id": page_ids[-1] if page_ids else None,
                "first_date": items[0]["environment_date"] if items else None,
                "last_date": items[-1]["environment_date"] if items else None,
                "request_id": meta.get("request_id"),
                "trace_id": meta.get("trace_id"),
                "data_as_of": data_as_of,
                "truncated": truncated,
                "has_next_cursor": bool(next_cursor),
                "warnings": meta.get("warnings") or [],
            }
        )
        all_items.extend(items)
        seen_ids.update(page_ids)
        if not next_cursor:
            break
        cursor = str(next_cursor)
        if page_number > 100:
            raise AssertionError(f"{label_kind} pagination exceeded 100 pages")

    return all_items, {"pages": pages, "page_data_as_of_values": page_data_as_of_values}


def reconcile_kind(
    client: DatabaseClient,
    token: str,
    output_dir: Path,
    label_kind: str,
    as_of_api: str,
    as_of_db: str,
) -> dict[str, Any]:
    """Paginate one kind and compare every returned field with the DB snapshot."""

    api_items, pagination = paginate(token, output_dir, label_kind, as_of_api)
    db_rows = fetch_db_oracle(client, label_kind, as_of_db)
    api_canonical = [canonical_item(item) for item in api_items]
    db_canonical = [canonical_item(row, DB_TIMEZONE) for row in db_rows]
    api_by_id = {item["id"]: item for item in api_canonical}
    db_by_id = {item["id"]: item for item in db_canonical}
    missing_in_mcp = sorted(set(db_by_id) - set(api_by_id))
    extra_in_mcp = sorted(set(api_by_id) - set(db_by_id))
    field_mismatches: list[dict[str, Any]] = []
    for item_id in sorted(set(api_by_id).intersection(db_by_id)):
        api_item = api_by_id[item_id]
        db_item = db_by_id[item_id]
        different_fields = [field for field in OUTPUT_FIELDS if api_item[field] != db_item[field]]
        if different_fields:
            field_mismatches.append(
                {
                    "id": item_id,
                    "environment_date": api_item["environment_date"],
                    "different_fields": different_fields,
                    "mcp_hash": row_hash(api_item),
                    "db_hash": row_hash(db_item),
                }
            )
    global_order_ok = api_items == sorted(
        api_items,
        key=lambda item: (str(item["environment_date"]), int(item["id"])),
        reverse=True,
    )
    sequence_issues = assert_daily_sequence(api_items)
    db_hashes = [{"id": item["id"], "sha256": row_hash(item)} for item in db_canonical]
    write_json(output_dir / f"{label_kind}-db-row-hashes.json", db_hashes)
    result = {
        "label_kind": label_kind,
        "request_arguments": {"label_kind": label_kind, "limit": PAGE_LIMIT, "as_of": as_of_api},
        "page_count": len(pagination["pages"]),
        "pages": pagination["pages"],
        "mcp_count": len(api_items),
        "db_count": len(db_rows),
        "unique_mcp_ids": len(api_by_id),
        "first": {
            "id": api_items[0]["id"],
            "environment_date": api_items[0]["environment_date"],
        } if api_items else None,
        "last": {
            "id": api_items[-1]["id"],
            "environment_date": api_items[-1]["environment_date"],
        } if api_items else None,
        "page_data_as_of_values": pagination["page_data_as_of_values"],
        "global_order_desc_date_id": global_order_ok,
        "daily_sequence_issue_count": len(sequence_issues),
        "daily_sequence_issues": sequence_issues,
        "missing_in_mcp_count": len(missing_in_mcp),
        "missing_in_mcp_ids": missing_in_mcp,
        "extra_in_mcp_count": len(extra_in_mcp),
        "extra_in_mcp_ids": extra_in_mcp,
        "field_mismatch_count": len(field_mismatches),
        "field_mismatches": field_mismatches,
    }
    result["passed"] = all(
        (
            result["mcp_count"] == result["db_count"],
            result["unique_mcp_ids"] == result["mcp_count"],
            result["global_order_desc_date_id"],
            result["daily_sequence_issue_count"] == 0,
            result["missing_in_mcp_count"] == 0,
            result["extra_in_mcp_count"] == 0,
            result["field_mismatch_count"] == 0,
        )
    )
    write_json(output_dir / f"{label_kind}-reconciliation.json", result)
    return result


def main() -> int:
    """Execute the fact and forecast reconciliation and return a shell status."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("MCP_BEARER_TOKEN")
    if not token:
        raise SystemExit("MCP_BEARER_TOKEN is required")
    args.output_dir.mkdir(parents=True, exist_ok=False)

    settings = SettingsLoader.load("test", PROJECT_ROOT)
    client = DatabaseClient.from_settings(settings.database)
    snapshot = client.fetch_one("SELECT NOW(6) AS as_of_db, UTC_TIMESTAMP(6) AS as_of_utc")
    if snapshot is None:
        raise AssertionError("Could not acquire database snapshot time")
    as_of_db_value = snapshot["as_of_db"]
    as_of_utc_value = snapshot["as_of_utc"]
    if not isinstance(as_of_db_value, datetime) or not isinstance(as_of_utc_value, datetime):
        raise AssertionError("Database snapshot time is not a datetime")
    as_of_db = str(as_of_db_value)
    as_of_api = normalize_datetime(as_of_utc_value)
    if as_of_api is None:
        raise AssertionError("Could not normalize database snapshot time")
    before = state_summary(client)
    write_json(
        args.output_dir / "run-context.json",
        {
            "environment": "test",
            "mcp_url": MCP_URL,
            "authentication": "Bearer <redacted>",
            "database": {"name": settings.database.name, "credentials": "<redacted>"},
            "snapshot_as_of_utc": as_of_api,
            "page_limit_requested": PAGE_LIMIT,
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "db_state_before": before,
        },
    )
    results = {
        kind: reconcile_kind(client, token, args.output_dir, kind, as_of_api, as_of_db)
        for kind in ("fact", "forecast")
    }
    after = state_summary(client)
    concurrent_change = before != after
    summary = {
        "status": "PASS" if all(result["passed"] for result in results.values()) else "FAIL",
        "snapshot_as_of_utc": as_of_api,
        "results": results,
        "db_state_before": before,
        "db_state_after": after,
        "database_changed_during_run": concurrent_change,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if concurrent_change:
        summary["state_change_note"] = (
            "The database changed during the run; the explicit as_of parameter and DB available_at filter "
            "were used to retain one point-in-time comparison."
        )
    write_json(args.output_dir / "summary.json", summary)
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "status": summary["status"],
        "database_changed_during_run": concurrent_change,
        "counts": {kind: {"mcp": result["mcp_count"], "db": result["db_count"], "pages": result["page_count"]} for kind, result in results.items()},
        "differences": {kind: {"missing": result["missing_in_mcp_count"], "extra": result["extra_in_mcp_count"], "fields": result["field_mismatch_count"], "sequence": result["daily_sequence_issue_count"]} for kind, result in results.items()},
    }, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
