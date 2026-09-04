#!/usr/bin/env python3
"""Revalidate DB-613 against one stable published batch in a read-only transaction."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import pymysql


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402


REPORT_ROOT = ROOT / "reports" / "factor4-resume"
SHANGHAI = ZoneInfo("Asia/Shanghai")
TOKEN_PATTERN = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.IGNORECASE)
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
SENSITIVE_KEY = re.compile(
    r"authorization|password|secret|api[_-]?key|key_plaintext|ciphertext|nonce|signature|"
    r"(?:^|_)(?:jwt|hmac|access_token|refresh_token|auth_token|token|token_value)$",
    re.IGNORECASE,
)


def json_default(value: Any) -> str:
    """Serialize temporal, decimal, and byte database values."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Recursively redact sensitive fields and complete token patterns."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return JWT_PATTERN.sub(
            "<redacted-jwt>", TOKEN_PATTERN.sub("<redacted-mcp-token>", value)
        )
    return value


def json_text(value: Any) -> str:
    """Return stable redacted JSON text."""

    return (
        json.dumps(
            redact(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=json_default,
        )
        + "\n"
    )


def scan_contents(
    contents: Mapping[str, str], forbidden_values: Sequence[str]
) -> dict[str, Any]:
    """Scan in-memory artifacts before any report content is written."""

    exact_matches: dict[str, int] = {}
    token_files: list[str] = []
    jwt_files: list[str] = []
    for name, content in contents.items():
        for index, forbidden in enumerate(forbidden_values):
            if forbidden and forbidden in content:
                exact_matches[f"forbidden_value_{index}"] = content.count(forbidden)
        if TOKEN_PATTERN.search(content):
            token_files.append(name)
        if JWT_PATTERN.search(content):
            jwt_files.append(name)
    return {
        "files_scanned": len(contents),
        "exact_credential_match_counts": exact_matches,
        "complete_mcp_token_pattern_files": sorted(token_files),
        "complete_jwt_pattern_files": sorted(jwt_files),
        "passed": not exact_matches and not token_files and not jwt_files,
    }


def decode_json(value: Any) -> dict[str, Any]:
    """Decode a MySQL JSON object or return an empty object."""

    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def query_one(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    parameters: tuple[Any, ...] = (),
) -> dict[str, Any]:
    """Execute one parameterized SELECT and require exactly one row."""

    cursor.execute(sql, parameters)
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"Expected one row, received {len(rows)}")
    return dict(rows[0])


def read_batch(
    cursor: pymysql.cursors.DictCursor, batch_id: int | None = None
) -> dict[str, Any]:
    """Read the unique active published success batch, optionally by fixed ID."""

    if batch_id is None:
        cursor.execute(
            """
            SELECT id,batch_uid,status,publish_status,is_active,publication_uid,
                   publish_version,market_scope,label_kind,route_profile_key,as_of_time,
                   published_at,environment_status,updated_at
            FROM market_environment_eval_batch
            WHERE status='success' AND publish_status='published' AND is_active=1
            ORDER BY published_at DESC,id DESC
            """
        )
        rows = cursor.fetchall()
        if len(rows) != 1:
            raise RuntimeError(f"Expected one active published success batch, got {len(rows)}")
        return dict(rows[0])
    return query_one(
        cursor,
        """
        SELECT id,batch_uid,status,publish_status,is_active,publication_uid,
               publish_version,market_scope,label_kind,route_profile_key,as_of_time,
               published_at,environment_status,updated_at
        FROM market_environment_eval_batch
        WHERE id=%s
        """,
        (batch_id,),
    )


def route_count(
    cursor: pymysql.cursors.DictCursor,
    batch: Mapping[str, Any],
    label_code: str,
) -> dict[str, Any]:
    """Count active eligible routes for the exact published batch identity."""

    return query_one(
        cursor,
        """
        SELECT COUNT(*) AS active_eligible_route_count,
               COUNT(DISTINCT id) AS distinct_route_id_count,
               COUNT(DISTINCT factor_ref) AS distinct_factor_ref_count,
               MIN(rank_no) AS minimum_rank,MAX(rank_no) AS maximum_rank,
               MIN(updated_at) AS earliest_route_updated_at,
               MAX(updated_at) AS latest_route_updated_at
        FROM market_environment_factor_route
        WHERE eval_batch_id=%s AND publication_uid=%s AND publish_version=%s
          AND market_scope=%s AND label_code=%s AND is_active=1 AND is_eligible=1
        """,
        (
            batch["id"],
            batch["publication_uid"],
            batch["publish_version"],
            batch["market_scope"],
            label_code,
        ),
    )


def compact_batch(batch: Mapping[str, Any]) -> dict[str, Any]:
    """Return a report-safe batch identity without the full JSON summary."""

    return {key: value for key, value in batch.items() if key != "environment_status"}


def read_evidence() -> dict[str, Any]:
    """Read DB-613 twice in one consistent read-only transaction and roll it back."""

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
        read_timeout=60,
        write_timeout=30,
    )
    transaction = {
        "start_statement": "START TRANSACTION READ ONLY",
        "select_only": True,
        "rollback_attempted": False,
        "rolled_back": False,
    }
    evidence: dict[str, Any] = {"transaction": transaction}
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            evidence["database_identity"] = query_one(
                cursor,
                """
                SELECT DATABASE() AS database_name,CURRENT_USER() AS current_user_name,
                       @@session.transaction_isolation AS transaction_isolation,
                       @@session.transaction_read_only AS transaction_read_only,NOW(6) AS observed_at
                """,
            )
            first_batch = read_batch(cursor)
            first_status = decode_json(first_batch["environment_status"])
            label_code = "WIDE_RANGE"
            first_summary = first_status.get(label_code)
            if not isinstance(first_summary, dict):
                raise RuntimeError(f"environment_status has no object for {label_code}")
            first_routes = route_count(cursor, first_batch, label_code)

            identity_mismatches = query_one(
                cursor,
                """
                SELECT COUNT(*) AS route_identity_mismatch_count
                FROM market_environment_factor_route
                WHERE eval_batch_id=%s AND label_code=%s
                  AND is_active=1 AND is_eligible=1
                  AND (
                    NOT (publication_uid <=> %s)
                    OR NOT (publish_version <=> %s)
                    OR NOT (market_scope <=> %s)
                  )
                """,
                (
                    first_batch["id"],
                    label_code,
                    first_batch["publication_uid"],
                    first_batch["publish_version"],
                    first_batch["market_scope"],
                ),
            )

            second_batch = read_batch(cursor, int(first_batch["id"]))
            second_status = decode_json(second_batch["environment_status"])
            second_summary = second_status.get(label_code)
            if not isinstance(second_summary, dict):
                raise RuntimeError(f"second environment_status has no object for {label_code}")
            second_routes = route_count(cursor, second_batch, label_code)

            stable_fields = (
                "id",
                "batch_uid",
                "status",
                "publish_status",
                "is_active",
                "publication_uid",
                "publish_version",
                "market_scope",
                "updated_at",
            )
            evidence.update(
                {
                    "label_code": label_code,
                    "first_read": {
                        "batch": compact_batch(first_batch),
                        "environment_summary": first_summary,
                        "route_query": first_routes,
                    },
                    "second_read": {
                        "batch": compact_batch(second_batch),
                        "environment_summary": second_summary,
                        "route_query": second_routes,
                    },
                    "route_identity_mismatch_count": int(
                        identity_mismatches["route_identity_mismatch_count"]
                    ),
                    "snapshot_stability": {
                        "batch_identity_equal": all(
                            first_batch.get(field) == second_batch.get(field)
                            for field in stable_fields
                        ),
                        "environment_summary_equal": first_summary == second_summary,
                        "route_query_equal": first_routes == second_routes,
                    },
                }
            )
    finally:
        transaction["rollback_attempted"] = True
        try:
            connection.rollback()
            transaction["rolled_back"] = True
        finally:
            connection.close()
    return evidence


def adjudicate(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return the strict DB-613 targeted verdict."""

    first = evidence["first_read"]
    second = evidence["second_read"]
    first_stored = int(first["environment_summary"].get("route_count", -1))
    second_stored = int(second["environment_summary"].get("route_count", -1))
    first_actual = int(first["route_query"]["active_eligible_route_count"])
    second_actual = int(second["route_query"]["active_eligible_route_count"])
    stability = evidence["snapshot_stability"]
    stable = all(bool(value) for value in stability.values())
    identity_clean = int(evidence["route_identity_mismatch_count"]) == 0

    if stable and identity_clean and first_stored == second_stored == 86 and first_actual == second_actual == 86:
        status = "PASS"
        classification = "FIXED"
        reason = (
            "The stable active published batch reports WIDE_RANGE.route_count=86, and an exact "
            "eval_batch_id + publication_uid + publish_version + market_scope query finds 86 active "
            "eligible routes on both reads. The former 0-versus-86 invariant violation is fixed."
        )
    elif stable and identity_clean and first_stored != first_actual:
        status = "FAIL"
        classification = "PRODUCT_DEFECT_PUBLISHED_SUMMARY_DRIFT"
        reason = (
            f"The stable published summary reports {first_stored} WIDE_RANGE routes while the exact "
            f"active eligible route query returns {first_actual}."
        )
    else:
        status = "BLOCKED"
        classification = "BLOCKED_ASYNC_STATE_MOVING"
        reason = (
            "The two reads or route identities were not stable enough to adjudicate DB-613 without "
            "risking a false result."
        )
    return {
        "case_id": "DB-613",
        "title": "批次、发布与 active route 状态不变量（只读）",
        "status": status,
        "classification": classification,
        "severity": "P1" if status == "FAIL" else None,
        "reason": reason,
        "assertions": {
            "snapshot_stable": stable,
            "route_identity_mismatch_count": evidence["route_identity_mismatch_count"],
            "stored_route_count_first": first_stored,
            "stored_route_count_second": second_stored,
            "actual_route_count_first": first_actual,
            "actual_route_count_second": second_actual,
            "expected_fixed_equality": "86 == 86",
        },
    }


def markdown(report: Mapping[str, Any]) -> str:
    """Render the targeted authority summary."""

    case = report["case"]
    assertions = case["assertions"]
    return "\n".join(
        [
            "# DB-613 定向验收",
            "",
            f"- 状态：`{case['status']}`",
            f"- 分类：`{case['classification']}`",
            "- 环境：`test`",
            "- 数据库：`START TRANSACTION READ ONLY`，最终 `ROLLBACK`",
            "- MCP/HTTP 调用：`0`",
            "",
            "## 结果",
            "",
            f"- 第一次：environment_status.WIDE_RANGE.route_count=`{assertions['stored_route_count_first']}`；精确 route 统计=`{assertions['actual_route_count_first']}`。",
            f"- 第二次：environment_status.WIDE_RANGE.route_count=`{assertions['stored_route_count_second']}`；精确 route 统计=`{assertions['actual_route_count_second']}`。",
            f"- 快照稳定：`{assertions['snapshot_stable']}`；route 身份错配：`{assertions['route_identity_mismatch_count']}`。",
            f"- 裁决：{case['reason']}",
            "",
        ]
    )


def main() -> int:
    """Execute the targeted closure and write pre-scanned evidence artifacts."""

    settings = SettingsLoader.load("test", ROOT)
    forbidden_values = [
        settings.api.auth_token or "",
        settings.database.password or "",
        settings.authentication.privileged.password or "",
        settings.authentication.restricted.password or "",
        settings.authentication.non_owner.password or "",
    ]
    evidence = read_evidence()
    case = adjudicate(evidence)
    generated_at = datetime.now(SHANGHAI)
    report: dict[str, Any] = {
        "authority": "This adjudicated-summary.json is the authoritative DB-613 verdict for this targeted run.",
        "generated_at": generated_at.isoformat(),
        "environment": "test",
        "mode": "READ_ONLY",
        "scope": ["DB-613"],
        "case": case,
        "confirmed_defects": ["DB-613"] if case["status"] == "FAIL" else [],
        "mcp_calls": [],
        "http_calls": [],
        "mcp_write_tools_called": [],
        "database_transaction": evidence["transaction"],
        "security": {},
    }
    contents = {
        "db-evidence.json": json_text(evidence),
        "adjudicated-summary.json": json_text(report),
        "summary.md": str(redact(markdown(report))),
    }
    security = scan_contents(contents, forbidden_values)
    if not security["passed"]:
        raise RuntimeError("Sensitive content detected before report write")
    report["security"] = security
    contents["adjudicated-summary.json"] = json_text(report)
    contents["sensitive-scan.json"] = json_text(security)
    final_security = scan_contents(contents, forbidden_values)
    if not final_security["passed"]:
        raise RuntimeError("Sensitive content detected in final report set")

    output = REPORT_ROOT / f"{generated_at.strftime('%Y%m%dT%H%M%S%z')}-db613-targeted-closure"
    output.mkdir(parents=True, exist_ok=False)
    for name, content in contents.items():
        (output / name).write_text(content, encoding="utf-8")
    print(output)
    print(json.dumps({"DB-613": case["status"]}, sort_keys=True))
    return 1 if case["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
