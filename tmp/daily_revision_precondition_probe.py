#!/usr/bin/env python3
"""Record the natural-data precondition for the Factor 4 daily PIT test."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient  # noqa: E402


def json_default(value: Any) -> str:
    """Serialize database-native scalars without exposing credentials."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def read_revision_state(db: DatabaseClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read revision statistics in an explicit read-only transaction and roll it back."""

    connection = db._connection_factory()  # Temporary probe needs an explicit rollback boundary.
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION")
            cursor.execute(
                """
                SELECT label_kind, COUNT(*) AS row_count,
                       COUNT(DISTINCT revision) AS distinct_revision_values,
                       MIN(revision) AS min_revision, MAX(revision) AS max_revision,
                       COUNT(DISTINCT environment_date) AS distinct_dates
                FROM market_environment_daily
                GROUP BY label_kind
                ORDER BY label_kind
                """
            )
            stats = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT environment_date, label_kind, COUNT(*) AS row_count,
                       COUNT(DISTINCT revision) AS revision_count,
                       MIN(revision) AS min_revision, MAX(revision) AS max_revision,
                       MIN(available_at) AS first_available_at,
                       MAX(available_at) AS last_available_at
                FROM market_environment_daily
                GROUP BY environment_date, label_kind
                HAVING COUNT(DISTINCT revision) > 1
                ORDER BY last_available_at DESC
                """
            )
            pairs = [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
    finally:
        connection.rollback()
        connection.close()
    return stats, pairs


def main() -> None:
    """Run the precondition check and write a credential-free report."""

    settings = SettingsLoader.load("test", ROOT)
    if settings.environment != "test" or settings.database.name != "factor_db":
        raise SystemExit("test factor_db environment gate failed")
    db = DatabaseClient.from_settings(settings.database)
    stats, pairs = read_revision_state(db)
    status = "READY" if pairs else "BLOCKED"
    reason = (
        "natural multi-revision keys exist; before/equal/after MCP reconciliation is required"
        if pairs
        else "BLOCKED_DATA_PRECONDITION: no environment_date + label_kind has multiple revisions"
    )
    captured_at = datetime.now(timezone.utc)
    report = {
        "captured_at": captured_at.isoformat(),
        "environment": "test",
        "database": "factor_db",
        "mode": "READ_ONLY_ROLLBACK",
        "case_id": "ENV-104",
        "status": status,
        "reason": reason,
        "revision_stats": stats,
        "multi_revision_key_count": len(pairs),
        "multi_revision_keys": pairs,
        "mcp_calls_executed": 0,
        "mcp_skip_reason": None if pairs else "before/equal/after has no valid natural fixture",
    }
    stamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports" / "factor4-deep" / f"{stamp}-daily-revision-precondition"
    output.mkdir(parents=True, exist_ok=False)
    (output / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )
    (output / "summary.md").write_text(
        "# Daily revision PIT precondition\n\n"
        f"- ENV-104: `{status}`\n"
        f"- Reason: `{reason}`\n"
        f"- Multi-revision keys: `{len(pairs)}`\n"
        "- Database mode: `READ_ONLY_ROLLBACK`\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "status": status, "multi_revision_keys": len(pairs)}))


if __name__ == "__main__":
    main()
