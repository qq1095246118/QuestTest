#!/usr/bin/env python3
"""Run a read-only input and ownership matrix for feedback status.

The probe deliberately avoids the feedback submission/write tool.  It checks
that malformed identifiers never produce a server error, that unknown or
other-user identifiers do not reveal a submission, and that the database is
unchanged after all reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SettingsLoader  # noqa: E402
from db.client import DatabaseClient, DatabaseTransaction, QueryParameters  # noqa: E402
from tmp.catalog_deep_readonly import (  # noqa: E402
    MCP_URL,
    Runner,
    _data,
    _error_code,
    _rejected,
    _success,
    _write_json,
)


TOKEN_ENV = "FEEDBACK_MCP_TOKEN"
BLOCKING_CODES = {
    "AUTH_REQUIRED",
    "FORBIDDEN",
    "INSUFFICIENT_SCOPE",
    "QUERY_TIMEOUT",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE",
}


@contextmanager
def read_only_transaction(db: DatabaseClient) -> Iterator[DatabaseTransaction]:
    """Open an explicit read-only transaction and always roll it back."""

    connection = db._connection_factory()  # Temporary probe needs an explicit rollback boundary.
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION")
        finally:
            cursor.close()
        yield DatabaseTransaction(connection)
    finally:
        connection.rollback()
        connection.close()


def stable_feedback_snapshot(db: DatabaseClient) -> dict[str, Any]:
    """Read non-payload feedback rows and stable table markers.

    The snapshot intentionally excludes ``raw_payload`` and hashes submission
    identifiers so reports do not duplicate potentially sensitive feedback
    content.
    """

    with read_only_transaction(db) as transaction:
        rows = transaction.fetch_all(
            """
            SELECT id, source_system, submission_id, status, accepted_count,
                   rejected_count, error_code, created_at, updated_at
            FROM market_environment_strategy_feedback_submissions
            ORDER BY id
            """
        )
        marker = transaction.fetch_one(
            """
            SELECT COUNT(*) AS row_count, MAX(id) AS max_id,
                   MAX(updated_at) AS max_updated_at
            FROM market_environment_strategy_feedback_submissions
            """
        ) or {}
    safe_rows = []
    for row in rows:
        safe = dict(row)
        if safe.get("submission_id") is not None:
            safe["submission_id_sha256"] = hashlib.sha256(
                str(safe.pop("submission_id")).encode()
            ).hexdigest()
        safe_rows.append(safe)
    return {"rows": safe_rows, "marker": marker}


def caller_subject(db: DatabaseClient, request_id: str | None = None) -> dict[str, Any] | None:
    """Return the caller identity logged for one status read, or the latest read."""

    suffix = " AND request_id=%s" if request_id else ""
    parameters = (request_id,) if request_id else None
    with read_only_transaction(db) as transaction:
        return transaction.fetch_one(
            f"""
            SELECT caller_subject, caller_user_id, caller_role, api_key_id
            FROM agent_data_access_logs
            WHERE tool_name='get_feedback_submission_status'
            {suffix}
            ORDER BY id DESC
            LIMIT 1
            """,
            parameters,
        )


def response_summary(call: dict[str, Any], submitted: Any) -> dict[str, Any]:
    """Extract safe transport and error fields from one MCP call."""

    data = _data(call)
    error = (call.get("business") or {}).get("error") if isinstance(call.get("business"), dict) else None
    returned_id = data.get("submission_id") if isinstance(data, dict) else None
    return {
        "http_status": call.get("http_status"),
        "error_code": _error_code(call),
        "is_error": call.get("is_error"),
        "rejected": _rejected(call),
        "success": _success(call),
        "returned_submission_id_matches": (
            returned_id is not None and str(returned_id) == str(submitted)
        ),
        "error_keys": sorted(error) if isinstance(error, dict) else [],
    }


def run_matrix() -> None:
    """Execute the feedback status matrix and write a sanitized report."""

    token = os.environ.get(TOKEN_ENV) or os.environ.get("FACTOR4_MCP_TOKEN")
    if not token:
        raise SystemExit(f"{TOKEN_ENV} or FACTOR4_MCP_TOKEN is required")
    settings = SettingsLoader.load("test", PROJECT_ROOT)
    if settings.environment != "test" or not MCP_URL.startswith("https://test-factor-frontend.questvector.ai/"):
        raise SystemExit("test environment gate failed")

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = PROJECT_ROOT / "reports" / "factor4-deep" / f"{run_stamp}-feedback-status-input-matrix"
    db = DatabaseClient.from_settings(settings.database)
    before = stable_feedback_snapshot(db)

    runner = Runner(token, output, db)
    cases: list[dict[str, Any]] = []
    init = runner.request(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-feedback-matrix", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    runner.protocol_version = init_result.get("protocolVersion")
    init_ok = init.get("http_status") == 200 and runner.protocol_version == "2025-06-18"
    if not init_ok:
        cases.append({
            "case_id": "MCP-INIT",
            "status": "BLOCKED" if _error_code(init) in BLOCKING_CODES or init.get("http_status") in {401, 403} else "FAIL",
            "reason": "MCP initialization failed",
            "error_code": _error_code(init),
            "http_status": init.get("http_status"),
        })
        _write_json(output / "summary.json", {"environment": "test", "cases": cases})
        print(json.dumps({"output_dir": str(output), "counts": dict(Counter(x["status"] for x in cases))}))
        return
    runner.notify_initialized("MCP-NOTIFY")

    discovery_value = str(uuid4())
    discovery = runner.tool(
        "FB-CALLER-DISCOVERY",
        "get_feedback_submission_status",
        {"submission_id": discovery_value},
    )
    discovery_business = discovery.get("business") if isinstance(discovery.get("business"), dict) else {}
    discovery_error = discovery_business.get("error") if isinstance(discovery_business, dict) else {}
    discovery_request_id = (
        discovery_error.get("request_id") if isinstance(discovery_error, dict) else None
    )
    caller_current = caller_subject(db, str(discovery_request_id)) if discovery_request_id else None
    caller_id = caller_current.get("caller_user_id") if caller_current else None
    owner_parameter: QueryParameters = (
        (f"mcp-user:{caller_id}",) if caller_id is not None else ("__no_caller__",)
    )
    with read_only_transaction(db) as transaction:
        owned_rows = transaction.fetch_all(
            """
            SELECT submission_id, status, accepted_count, rejected_count, error_code
            FROM market_environment_strategy_feedback_submissions
            WHERE source_system=%s
            ORDER BY id DESC
            """,
            owner_parameter,
        )
        other_rows = transaction.fetch_all(
            """
            SELECT submission_id
            FROM market_environment_strategy_feedback_submissions
            WHERE source_system<>%s
            ORDER BY id DESC
            LIMIT 3
            """,
            owner_parameter,
        )

    # The schema advertises a string with maxLength=128.  Values below test
    # both type validation and the distinction between blank and unknown IDs.
    matrix: list[tuple[str, Any, str]] = [
        ("INTEGER", 7, "numeric identifier must not reach a successful read"),
        ("FLOAT", 7.5, "numeric identifier must not reach a successful read"),
        ("BOOLEAN", True, "boolean identifier must not reach a successful read"),
        ("BLANK", "   ", "blank identifier must not reveal a submission"),
        ("WHITESPACE", "\t\n", "whitespace identifier must not reveal a submission"),
        ("RANDOM-UUID", str(uuid4()), "random UUID must be isolated as not found"),
        ("TOO-LONG", "x" * 129, "identifier over maxLength must be rejected"),
        ("VERY-LONG", "x" * 4096, "extreme identifier must be rejected without 5xx"),
        ("UNICODE", "不存在提交-" + str(uuid4()), "unknown Unicode identifier must be isolated"),
        ("SQL-LIKE", "' OR 1=1 --", "SQL-like identifier must not broaden the lookup"),
        ("NULL", None, "null identifier must be rejected"),
    ]
    for case_id, value, reason in matrix:
        call = runner.tool(f"FB-MATRIX-{case_id}", "get_feedback_submission_status", {"submission_id": value})
        summary = response_summary(call, value)
        no_5xx = isinstance(summary["http_status"], int) and summary["http_status"] < 500
        no_wrong_success = not summary["success"] or summary["returned_submission_id_matches"]
        ok = no_5xx and summary["rejected"] and no_wrong_success
        status = "PASS" if ok else ("BLOCKED" if _error_code(call) in BLOCKING_CODES else "FAIL")
        cases.append({
            "case_id": f"FB-MATRIX-{case_id}",
            "status": status,
            "reason": reason if ok else "input produced 5xx, unstructured success, or a mismatched submission",
            "value_type": type(value).__name__,
            "value_length": len(value) if isinstance(value, str) else None,
            "response": summary,
        })

    # Missing argument and wrong container types exercise JSON object/schema
    # validation separately from a null value.
    for case_id, arguments, reason in (
        ("MISSING", {}, "missing required submission_id must be rejected"),
        ("LIST", {"submission_id": ["x"]}, "array identifier must be rejected"),
        ("OBJECT", {"submission_id": {"id": "x"}}, "object identifier must be rejected"),
    ):
        call = runner.tool(f"FB-MATRIX-{case_id}", "get_feedback_submission_status", arguments)
        summary = response_summary(call, arguments.get("submission_id"))
        no_5xx = isinstance(summary["http_status"], int) and summary["http_status"] < 500
        ok = no_5xx and summary["rejected"]
        cases.append({
            "case_id": f"FB-MATRIX-{case_id}",
            "status": "PASS" if ok else ("BLOCKED" if _error_code(call) in BLOCKING_CODES else "FAIL"),
            "reason": reason if ok else "schema-invalid input produced 5xx or business success",
            "response": summary,
        })

    # Existing rows owned by another source are an explicit confidentiality
    # check.  A caller-owned row, when available, also verifies the success
    # shape and identity without recording its payload.
    for index, row in enumerate(other_rows, 1):
        value = str(row["submission_id"])
        call = runner.tool(f"FB-OTHER-{index}", "get_feedback_submission_status", {"submission_id": value})
        summary = response_summary(call, value)
        no_leak = summary["rejected"]
        no_5xx = isinstance(summary["http_status"], int) and summary["http_status"] < 500
        ok = no_5xx and no_leak
        cases.append({
            "case_id": f"FB-OTHER-{index}",
            "status": "PASS" if ok else ("BLOCKED" if _error_code(call) in BLOCKING_CODES else "FAIL"),
            "reason": "other-source submission is hidden" if ok else "other-source submission leaked or caused 5xx",
            "response": summary,
        })

    owned_success_count = 0
    owned_blocked_count = 0
    for index, row in enumerate(owned_rows[:3], 1):
        value = str(row["submission_id"])
        call = runner.tool(f"FB-OWNED-{index}", "get_feedback_submission_status", {"submission_id": value})
        summary = response_summary(call, value)
        returned = _data(call)
        fields_match = (
            summary["returned_submission_id_matches"]
            and str(returned.get("status")) == str(row.get("status"))
            and returned.get("accepted_count") == row.get("accepted_count")
            and returned.get("rejected_count") == row.get("rejected_count")
            and returned.get("error_code") == row.get("error_code")
        )
        if fields_match and summary["success"]:
            status = "PASS"
            owned_success_count += 1
            reason = "caller-owned submission status and counters match the database"
        elif _error_code(call) in BLOCKING_CODES:
            status = "BLOCKED"
            owned_blocked_count += 1
            reason = f"caller-owned submission could not be read because {_error_code(call)}"
        else:
            status = "FAIL"
            reason = "caller-owned submission was rejected or its status fields differ from the database"
        cases.append({
            "case_id": f"FB-OWNED-{index}",
            "status": status,
            "reason": reason,
            "response": summary,
            "db_expected": {
                "status": row.get("status"),
                "accepted_count": row.get("accepted_count"),
                "rejected_count": row.get("rejected_count"),
                "error_code": row.get("error_code"),
            },
        })

    after = stable_feedback_snapshot(db)
    snapshot_ok = before == after
    cases.append({
        "case_id": "FB-READONLY-SNAPSHOT",
        "status": "PASS" if snapshot_ok else "FAIL",
        "reason": "feedback table is unchanged after all status reads" if snapshot_ok else "feedback table changed during read-only matrix",
        "before_marker": before["marker"],
        "after_marker": after["marker"],
        "row_count": len(after["rows"]),
    })

    if not owned_rows:
        cases.append({
            "case_id": "FB-OWNED-SUCCESS-PRECONDITION",
            "status": "BLOCKED",
            "reason": "no submission row is owned by the authenticated caller; successful status shape cannot be verified",
            "caller_subject": caller_current.get("caller_subject") if caller_current else None,
            "caller_user_id": caller_id,
            "db_owned_row_count": 0,
        })
    elif owned_success_count == 0 and owned_blocked_count == 0:
        cases.append({
            "case_id": "FB-OWNED-SUCCESS",
            "status": "FAIL",
            "reason": "caller-owned rows exist but none produced a valid status response",
            "db_owned_row_count": len(owned_rows),
        })

    counts = Counter(case["status"] for case in cases)
    result = {
        "run_id": run_stamp,
        "environment": "test",
        "mcp_url": MCP_URL,
        "read_only": True,
        "database_mode": "READ_ONLY_ROLLBACK",
        "caller": caller_current,
        "db_owned_row_count": len(owned_rows),
        "db_other_row_count_sampled": len(other_rows),
        "cases": cases,
        "case_counts": dict(counts),
    }
    _write_json(output / "summary.json", result)
    lines = [
        "# Feedback status input matrix",
        "",
        f"- Environment: `test`; read-only: `true`",
        f"- Counts: `{dict(counts)}`",
        f"- Authenticated caller owned rows: `{len(owned_rows)}`",
        "",
        "| Case | Status | Result |",
        "|---|---|---|",
    ]
    for case in cases:
        lines.append(f"| {case['case_id']} | {case['status']} | {case['reason']} |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "counts": dict(counts)}, ensure_ascii=False))


if __name__ == "__main__":
    run_matrix()
