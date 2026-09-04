"""Run targeted no-catalog-export rechecks for the rank expansion."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports" / "factor4-deep" / "20260902T200653+0800-rank-functional"
URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN = os.environ.get("FACTOR4_MCP_TOKEN")
if not TOKEN:
    raise SystemExit("FACTOR4_MCP_TOKEN is required")


def load(path: Path) -> dict[str, Any]:
    """Load one JSON evidence file."""

    return json.loads(path.read_text(encoding="utf-8"))


def invoke(case_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke MCP once and persist a credential-free request and response."""

    payload = {
        "jsonrpc": "2.0",
        "id": case_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    response = requests.post(
        URL,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json=payload,
        timeout=90,
    )
    envelope = response.json()
    (SOURCE / f"{case_id}.request.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (SOURCE / f"{case_id}.response.json").write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result = envelope.get("result") or {}
    business = result.get("structuredContent")
    if not isinstance(business, dict):
        business = json.loads((result.get("content") or [{}])[0].get("text") or "{}")
    return {
        "http_status": response.status_code,
        "is_error": result.get("isError"),
        "business": business,
    }


def main() -> None:
    """Recheck the zero-size rank contract and formula completion boundary."""

    zero_request = load(SOURCE / "RANK-CS-ZERO.request.json")["params"]
    results: dict[str, Any] = {}
    for index in (1, 2):
        case_id = f"RANK-CS-ZERO-REPRO-{index}"
        results[case_id] = invoke(case_id, zero_request["name"], zero_request["arguments"])

    formula_request = load(SOURCE / "PIT-FORMULA-EQUAL.request.json")["params"]
    run_id = formula_request["arguments"]["run_id"]
    config = yaml.safe_load((ROOT / "config" / "test.yaml").read_text(encoding="utf-8"))["database"]
    connection = pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["username"],
        password=config["password"],
        database=config["name"],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute("SELECT completed_at FROM factor_ic_runs WHERE run_id=%s", (run_id,))
            row = cursor.fetchone()
            if not row or not row["completed_at"]:
                raise RuntimeError("target run has no completed_at")
            completed_at = row["completed_at"].replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            connection.rollback()
    finally:
        connection.close()

    for label, instant in (
        ("COMPLETION-BEFORE", completed_at - timedelta(microseconds=1)),
        ("COMPLETION-EQUAL", completed_at),
        ("COMPLETION-AFTER", completed_at + timedelta(microseconds=1)),
    ):
        args = dict(formula_request["arguments"])
        args["as_of"] = instant.isoformat()
        results[f"PIT-FORMULA-{label}"] = invoke(
            f"PIT-FORMULA-{label}", formula_request["name"], args
        )

    summary: dict[str, Any] = {"run_id": run_id, "completed_at": completed_at.isoformat(), "results": {}}
    for case_id, result in results.items():
        business = result["business"]
        summary["results"][case_id] = {
            "http_status": result["http_status"],
            "is_error": result["is_error"],
            "error_code": (business.get("error") or {}).get("code"),
            "returned_run_id": (business.get("data") or {}).get("run_id"),
        }
    (SOURCE / "targeted-recheck.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
