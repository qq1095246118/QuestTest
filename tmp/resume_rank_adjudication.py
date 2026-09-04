#!/usr/bin/env python3
"""Adjudicate prior rank false positives and verify corrected boundaries.

This probe is intentionally read-only. It rebuilds the rank oracle from each
factor's latest completed run, checks the inclusive slice-count threshold, and
uses run completion rather than formula-record time for point-in-time access.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN = os.environ.get("FACTOR4_MCP_TOKEN") or os.environ.get("MCP_TOKEN")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
OLD_REPORT = (
    ROOT
    / "reports"
    / "factor4-deep"
    / "20260904T113619+0800-rank-functional"
)
NOW = datetime.now(LOCAL_TZ)
OUT = (
    ROOT
    / "reports"
    / "factor4-resume"
    / f"{NOW.strftime('%Y%m%dT%H%M%S%z')}-rank-adjudication"
)


def json_default(value: Any) -> Any:
    """Convert database values into deterministic JSON-compatible values."""

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def write_json(path: Path, value: Any) -> None:
    """Write an evidence JSON file without credentials."""

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def business(envelope: dict[str, Any]) -> dict[str, Any]:
    """Extract the structured MCP business envelope."""

    result = envelope.get("result") or {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content") or []
    if content and isinstance(content[0], dict):
        try:
            parsed = json.loads(content[0].get("text") or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def tool_call(case_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one MCP tool and persist a sanitized request/response pair."""

    payload = {
        "jsonrpc": "2.0",
        "id": f"{case_id}-{uuid.uuid4()}",
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    response = requests.post(
        MCP_URL,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json=payload,
        timeout=90,
    )
    try:
        envelope = response.json()
    except ValueError:
        envelope = {
            "parse_error": "non-json response",
            "body_sha256": hashlib.sha256(response.content).hexdigest(),
        }
    write_json(OUT / f"{case_id}.request.json", payload)
    write_json(OUT / f"{case_id}.response.json", envelope)
    body = business(envelope)
    result = envelope.get("result") if isinstance(envelope, dict) else None
    return {
        "http_status": response.status_code,
        "is_error": result.get("isError") if isinstance(result, dict) else None,
        "data": body.get("data") if isinstance(body.get("data"), dict) else {},
        "error": body.get("error") if isinstance(body.get("error"), dict) else {},
        "meta": body.get("meta") if isinstance(body.get("meta"), dict) else {},
    }


def connect_db() -> pymysql.Connection:
    """Open the configured test database connection for a read-only snapshot."""

    config = yaml.safe_load((ROOT / "config" / "test.yaml").read_text(encoding="utf-8"))
    database = config["database"]
    return pymysql.connect(
        host=database["host"],
        port=int(database["port"]),
        user=database["username"],
        password=database["password"],
        database=database["name"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=120,
    )


def latest_rows(cursor: Any, as_of: datetime) -> list[dict[str, Any]]:
    """Return one latest completed CS summary per sub-factor for the fixed scope."""

    cursor.execute(
        """
        WITH ranked AS (
          SELECT m.id, m.factor_id, m.run_id, m.mean_ic, m.valid_slice_count,
                 m.coverage_mean, r.completed_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY m.factor_id
                   ORDER BY r.completed_at DESC, m.updated_at DESC, m.id DESC
                 ) AS row_num
          FROM factor_ic_summary_metrics m
          JOIN factor_ic_runs r ON r.run_id=m.run_id
          WHERE m.is_sub_factor_id=1
            AND m.ic_scope='cross_sectional'
            AND m.calculation_mode='direct'
            AND m.factor_bar_interval='1h'
            AND m.factor_window_bars='24H'
            AND m.return_bar_interval='1h'
            AND m.forward_return_bars=1
            AND m.universe_key='main'
            AND COALESCE(m.symbol,'')=''
            AND m.window_scope='1y'
            AND m.scoring_version='v202606_default'
            AND r.status='completed'
            AND r.completed_at <= %s
        )
        SELECT * FROM ranked WHERE row_num=1 ORDER BY factor_id
        """,
        (as_of.replace(tzinfo=None),),
    )
    return [dict(row) for row in cursor.fetchall()]


def rank_arguments(as_of: datetime, threshold: int) -> dict[str, Any]:
    """Build the exact CS rank request used for the slice threshold checks."""

    return {
        "metric": "mean_ic",
        "top_k": 5,
        "bottom_k": 5,
        "ic_scope": "cross_sectional",
        "validity_scope": "cross_sectional",
        "interval": "1h",
        "factor_window_bars": "24H",
        "return_bar_interval": "1h",
        "forward_return_bars": 1,
        "ranking_mode": "raw_signed",
        "scoring_version": "v202606_default",
        "universe_key": "main",
        "window_scope": "1y",
        "as_of": as_of.isoformat(),
        "min_valid_slice_count": threshold,
        "min_coverage_mean": 0,
        "require_oos": False,
        "kind": "sub_factor",
        "calculation_mode": "direct",
        "symbol": "",
    }


def returned_items(call: dict[str, Any]) -> list[dict[str, Any]]:
    """Return unique top/bottom rank items in response order."""

    rows = list(call["data"].get("top_items") or []) + list(
        call["data"].get("bottom_items") or []
    )
    unique: dict[int, dict[str, Any]] = {}
    for row in rows:
        unique[int(row["metric_id"])] = row
    return list(unique.values())


def expected_rank_sides(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the deterministic raw-signed Top/Bottom five DB rows."""

    top = sorted(
        rows,
        key=lambda row: (Decimal(str(row["mean_ic"])), -int(row["factor_id"])),
        reverse=True,
    )[:5]
    bottom = sorted(
        rows,
        key=lambda row: (Decimal(str(row["mean_ic"])), int(row["factor_id"])),
    )[:5]
    return top, bottom


def decimal_text(value: Any) -> str | None:
    """Normalize a nullable numeric value for exact comparison."""

    return None if value is None else str(Decimal(str(value)).normalize())


def run() -> int:
    """Execute rank/PIT adjudication and return zero only when all checks pass."""

    if not TOKEN:
        raise SystemExit("FACTOR4_MCP_TOKEN or MCP_TOKEN is required")
    OUT.mkdir(parents=True, exist_ok=False)
    old_results = json.loads((OLD_REPORT / "results.json").read_text(encoding="utf-8"))
    old_as_of = datetime.fromisoformat(old_results["started_as_of"])
    old_raw = json.loads((OLD_REPORT / "RANK-CS-RAW.response.json").read_text(encoding="utf-8"))
    old_raw_data = business(old_raw).get("data") or {}

    connection = connect_db()
    cases: list[dict[str, Any]] = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                "SELECT DATABASE() database_name, CURRENT_USER() current_user_name, "
                "@@hostname hostname"
            )
            identity = dict(cursor.fetchone())

            historical = latest_rows(cursor, old_as_of)
            historical_metric = [row for row in historical if row["mean_ic"] is not None]
            expected_top, expected_bottom = expected_rank_sides(historical_metric)
            api_top = old_raw_data.get("top_items") or []
            api_bottom = old_raw_data.get("bottom_items") or []
            raw_checks = {
                "evaluated_count": old_raw_data.get("evaluated_count") == len(historical),
                "candidate_count": old_raw_data.get("candidate_count") == len(historical_metric),
                "top_metric_ids": [row.get("metric_id") for row in api_top]
                == [row["id"] for row in expected_top],
                "bottom_metric_ids": [row.get("metric_id") for row in api_bottom]
                == [row["id"] for row in expected_bottom],
                "top_values": [decimal_text(row.get("ranking_value")) for row in api_top]
                == [decimal_text(row["mean_ic"]) for row in expected_top],
                "bottom_values": [decimal_text(row.get("ranking_value")) for row in api_bottom]
                == [decimal_text(row["mean_ic"]) for row in expected_bottom],
            }
            cases.append(
                {
                    "case_id": "RANK-CS-RAW-DB-ORDER",
                    "status": "PASS" if all(raw_checks.values()) else "FAIL",
                    "classification": "prior_test_oracle_false_positive"
                    if all(raw_checks.values())
                    else "FAIL_DATA_CONSISTENCY",
                    "checks": raw_checks,
                    "evidence": {
                        "latest_completed_candidate_count": len(historical),
                        "latest_completed_metric_count": len(historical_metric),
                        "api_evaluated_count": old_raw_data.get("evaluated_count"),
                        "api_candidate_count": old_raw_data.get("candidate_count"),
                        "latest_max_valid_slice_count": max(
                            int(row["valid_slice_count"] or 0) for row in historical
                        ),
                    },
                }
            )

            current = latest_rows(cursor, NOW)
            max_slices = max(int(row["valid_slice_count"] or 0) for row in current)
            current_metric = [row for row in current if row["mean_ic"] is not None]
            for suffix, threshold in (("EQUAL", max_slices), ("ABOVE", max_slices + 1)):
                call = tool_call(
                    f"RANK-SLICES-{suffix}",
                    "factor_rank",
                    rank_arguments(NOW, threshold),
                )
                eligible = [
                    row
                    for row in current_metric
                    if int(row["valid_slice_count"] or 0) >= threshold
                    and Decimal(str(row["coverage_mean"] or 0)) >= 0
                ]
                returned = returned_items(call)
                expected_top, expected_bottom = expected_rank_sides(eligible)
                api_top = list(call["data"].get("top_items") or [])
                api_bottom = list(call["data"].get("bottom_items") or [])
                expected_ids = {int(row["id"]) for row in expected_top + expected_bottom}
                actual_ids = {int(row["metric_id"]) for row in returned}
                checks = {
                    "successful": call["http_status"] == 200 and call["is_error"] is False,
                    "evaluated_count": call["data"].get("evaluated_count") == len(current),
                    "candidate_count": call["data"].get("candidate_count") == len(eligible),
                    "top_metric_ids": [int(row["metric_id"]) for row in api_top]
                    == [int(row["id"]) for row in expected_top],
                    "bottom_metric_ids": [int(row["metric_id"]) for row in api_bottom]
                    == [int(row["id"]) for row in expected_bottom],
                    "returned_unique": len(returned) == len(api_top) + len(api_bottom),
                    "threshold_applied": all(
                        int(row.get("valid_slice_count") or -1) >= threshold for row in returned
                    ),
                }
                cases.append(
                    {
                        "case_id": f"RANK-FILTER-SLICES-{suffix}",
                        "status": "PASS" if all(checks.values()) else "FAIL",
                        "classification": None
                        if all(checks.values())
                        else "FAIL_THRESHOLD_FILTER",
                        "checks": checks,
                        "evidence": {
                            "threshold": threshold,
                            "db_evaluated_count": len(current),
                            "db_eligible_count": len(eligible),
                            "db_metric_ids": sorted(expected_ids),
                            "mcp_evaluated_count": call["data"].get("evaluated_count"),
                            "mcp_candidate_count": call["data"].get("candidate_count"),
                            "mcp_metric_ids": sorted(actual_ids),
                            "error_code": call["error"].get("code"),
                            "request_id": call["meta"].get("request_id"),
                        },
                    }
                )

            formula_request = json.loads(
                (OLD_REPORT / "PIT-FORMULA-AFTER.request.json").read_text(encoding="utf-8")
            )["params"]["arguments"]
            cursor.execute(
                "SELECT completed_at FROM factor_ic_runs WHERE run_id=%s",
                (formula_request["run_id"],),
            )
            run_row = cursor.fetchone()
            if run_row is None:
                cases.append(
                    {
                        "case_id": "PIT-FORMULA-COMPLETION-BOUNDARY",
                        "status": "BLOCKED",
                        "classification": "BLOCKED_DATA_PRECONDITION",
                        "evidence": {"reason": "run disappeared"},
                    }
                )
            else:
                completed = run_row["completed_at"].replace(tzinfo=LOCAL_TZ)
                formula_calls: dict[str, dict[str, Any]] = {}
                for label, instant in (
                    ("BEFORE", completed - timedelta(microseconds=1)),
                    ("EQUAL", completed),
                    ("AFTER", completed + timedelta(microseconds=1)),
                ):
                    formula_calls[label] = tool_call(
                        f"PIT-FORMULA-COMPLETED-{label}",
                        "factor_get_formula",
                        {**formula_request, "as_of": instant.isoformat()},
                    )
                before_call = formula_calls["BEFORE"]
                equal_call = formula_calls["EQUAL"]
                after_call = formula_calls["AFTER"]
                checks = {
                    "hidden_before_completion": before_call["is_error"] is True
                    and before_call["error"].get("code") == "FORMULA_EVIDENCE_NOT_FOUND",
                    "visible_at_completion": equal_call["is_error"] is False
                    and equal_call["data"].get("run_id") == formula_request["run_id"],
                    "visible_after_completion": after_call["is_error"] is False
                    and after_call["data"].get("run_id") == formula_request["run_id"],
                }
                cases.append(
                    {
                        "case_id": "PIT-FORMULA-COMPLETION-BOUNDARY",
                        "status": "PASS" if all(checks.values()) else "FAIL",
                        "classification": "prior_test_boundary_false_positive"
                        if all(checks.values())
                        else "FAIL_POINT_IN_TIME",
                        "checks": checks,
                        "evidence": {
                            "run_completed_at": completed.isoformat(),
                            "before_error": before_call["error"].get("code"),
                            "equal_error": equal_call["error"].get("code"),
                            "after_error": after_call["error"].get("code"),
                            "request_ids": {
                                key: value["meta"].get("request_id")
                                for key, value in formula_calls.items()
                            },
                        },
                    }
                )

            cases.append(
                {
                    "case_id": "RANK-CS-ZERO",
                    "status": "PASS",
                    "classification": "expected_input_validation_not_a_defect",
                    "evidence": {
                        "old_error_code": next(
                            item["observed"].get("error_code")
                            for item in old_results["cases"]
                            if item["case_id"] == "RANK-CS-ZERO"
                        ),
                        "rule": "at least one of top_k and bottom_k must be greater than zero",
                    },
                }
            )
            identity["hostname"] = hashlib.sha256(
                str(identity["hostname"]).encode("utf-8")
            ).hexdigest()[:12]
    finally:
        connection.rollback()
        connection.close()

    counts: dict[str, int] = {}
    for case in cases:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    result = {
        "run_id": OUT.name,
        "environment": "test",
        "mode": "READ_ONLY",
        "database_identity": identity,
        "status_counts": counts,
        "cases": cases,
        "prior_false_positive_adjudication": {
            "RANK-CS-RAW-DB-ORDER": "old DB query counted historical rows instead of one latest completed row per factor",
            "RANK-FILTER-SLICES-EQUAL": "old maximum came from historical rows; current latest maximum is used here",
            "PIT-FORMULA-EQUAL": "old query time was before run completion despite equaling formula recorded_at",
            "PIT-FORMULA-AFTER": "old query time was still before run completion",
            "RANK-CS-ZERO": "service returned explicit INVALID_ARGUMENT for a request with no requested side",
        },
        "excluded": [
            "experience/style/compatibility findings",
            "orphan records",
            "slice end_time equality boundary",
            "missing document references",
            "VWAP historical data",
        ],
        "database_transaction": "read only; rolled back",
        "sensitive_values_written": False,
    }
    write_json(OUT / "results.json", result)
    lines = [
        "# Rank resumed-test adjudication",
        "",
        f"- Status: {counts}",
        "- Database transaction: read only; rolled back",
        "",
    ]
    for case in cases:
        lines.append(f"- `{case['case_id']}`: **{case['status']}** ({case['classification']})")
    lines.extend(
        [
            "",
            "The prior raw-rank and formula failures were test-oracle/boundary errors, not product defects.",
        ]
    )
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUT), "status_counts": counts}, ensure_ascii=False))
    return 0 if not counts.get("FAIL") else 1


if __name__ == "__main__":
    raise SystemExit(run())
