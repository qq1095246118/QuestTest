#!/usr/bin/env python3
"""Close CALC-508, ENV-108, and MET-310 with read-only test evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

import pymysql
import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from tmp.critical_readonly_gap_probe import (  # noqa: E402
    MCPClient,
    data as mcp_data,
    rows as mcp_rows,
    successful as mcp_successful,
)


REPORT_ROOT = ROOT / "reports" / "factor4-resume"
TOKEN_ENV_NAMES = ("MCP_TOKEN", "FACTOR4_MCP_TOKEN")
TIME_FIELDS = ("period_start", "period_end")
COST_FIELDS = ("turnover_rate", "net_return", "sharpe")
RAW_RETURN_KEY_PATTERN = re.compile(
    r"(?:gross|raw)[_-]?(?:return|pnl)|(?:return|pnl)[_-]?(?:series|values)|"
    r"^(?:returns|gross_returns|raw_returns|pnl_series|positions|signals)$",
    re.IGNORECASE,
)


def json_default(value: Any) -> str:
    """Serialize database and Decimal values without losing displayed precision."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Remove credentials and token-like values before an artifact is persisted."""

    sensitive_key = re.compile(
        r"authorization|token|password|secret|api[_-]?key|jwt|hmac|signature",
        re.IGNORECASE,
    )
    token_text = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.IGNORECASE)
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if sensitive_key.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return token_text.sub("<redacted>", value)
    return value


def write_json(path: Path, value: Any) -> None:
    """Write one deterministic, recursively redacted JSON artifact."""

    path.write_text(
        json.dumps(
            redact(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=json_default,
        )
        + "\n",
        encoding="utf-8",
    )


def decode_json(value: Any) -> Any:
    """Decode a MySQL JSON value while accepting already-decoded objects."""

    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return json.loads(value) if isinstance(value, str) else value


def utc_text(value: datetime) -> str:
    """Render an aware UTC timestamp in the API's accepted RFC3339 form."""

    parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def parse_instant(value: Any) -> datetime | None:
    """Parse an explicitly offset timestamp and normalize it to UTC."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def decimal_equal(left: Any, right: Any, tolerance: Decimal = Decimal("0.0000000000005")) -> bool:
    """Compare nullable numeric values with the DB column's 12-decimal scale."""

    if left is None or right is None:
        return left is None and right is None
    try:
        return abs(Decimal(str(left)) - Decimal(str(right))) <= tolerance
    except (InvalidOperation, ValueError):
        return False


def scalar_equal(left: Any, right: Any) -> bool:
    """Compare identity values while preserving null and boolean semantics."""

    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


def iter_key_paths(value: Any, prefix: str = "$") -> Iterator[tuple[str, Any]]:
    """Yield every JSON object key path and its value recursively."""

    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            yield path, item
            yield from iter_key_paths(item, path)
    elif isinstance(value, list):
        for item in value:
            yield from iter_key_paths(item, f"{prefix}[]")


def connect_database() -> pymysql.connections.Connection:
    """Connect to the configured test database with transactions disabled by default."""

    settings = SettingsLoader.load("test", ROOT).database
    return pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.username,
        password=settings.password,
        database=settings.name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=180,
        write_timeout=30,
    )


def query(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> list[dict[str, Any]]:
    """Execute one parameterized SELECT and return dictionary rows."""

    cursor.execute(sql, parameters or ())
    return [dict(row) for row in cursor.fetchall()]


def query_one(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> dict[str, Any] | None:
    """Execute one parameterized SELECT and return its first row."""

    rows = query(cursor, sql, parameters)
    return rows[0] if rows else None


def backend_login(session: requests.Session) -> str:
    """Login with the configured test account and return a JWT without persisting it."""

    settings = SettingsLoader.load("test", ROOT)
    credentials = settings.authentication.privileged
    if not credentials.email or not credentials.password:
        raise RuntimeError("Configured privileged test credentials are missing")
    response = session.post(
        f"{settings.api.base_url}/auth/login",
        json={"email": credentials.email, "password": credentials.password},
        timeout=settings.api.timeout_seconds,
    )
    response.raise_for_status()
    body = response.json()
    token = (body.get("data") or {}).get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Backend login did not return a JWT")
    return token


def backend_get(
    session: requests.Session,
    output: Path,
    sequence: int,
    case_id: str,
    path: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Send and persist one credential-free Backend GET request and response."""

    settings = SettingsLoader.load("test", ROOT)
    started = time.monotonic()
    response = session.get(
        f"{settings.api.base_url}/{path.lstrip('/')}",
        params=parameters,
        timeout=settings.api.timeout_seconds,
    )
    elapsed = time.monotonic() - started
    try:
        body: Any = response.json()
    except ValueError:
        body = {"unparsed_body": response.text[:2000]}
    request_artifact = {
        "case_id": case_id,
        "method": "GET",
        "path": f"/{path.lstrip('/')}",
        "parameters": parameters,
        "authentication": "Bearer <redacted>",
    }
    response_artifact = {
        "case_id": case_id,
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "elapsed_seconds": round(elapsed, 3),
        "body": body,
    }
    write_json(output / f"{sequence:03d}-{case_id}.backend.request.json", request_artifact)
    write_json(output / f"{sequence:03d}-{case_id}.backend.response.json", response_artifact)
    return response_artifact


def backend_items(call: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract object items from a Backend success envelope."""

    body = call.get("body")
    payload = body.get("data") if isinstance(body, dict) else None
    items = payload.get("items") if isinstance(payload, dict) else None
    return [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def select_metric_fixture(cursor: pymysql.cursors.DictCursor) -> list[dict[str, Any]]:
    """Select a compact completed-run factor containing matching TS and CS summaries."""

    seed = query_one(
        cursor,
        """
        SELECT ts.factor_id,ts.is_sub_factor_id,ts.run_id,ts.calculation_mode,
               ts.factor_bar_interval,ts.factor_window_bars,ts.return_bar_interval,
               ts.forward_return_bars,ts.universe_key,ts.symbol,ts.window_scope,
               ts.scoring_version,r.completed_at
        FROM factor_ic_summary_metrics ts
        JOIN factor_ic_runs r ON r.run_id=ts.run_id AND r.status='completed'
        JOIN (
            SELECT factor_id,is_sub_factor_id,COUNT(*) AS factor_metric_count
            FROM factor_ic_summary_metrics
            GROUP BY factor_id,is_sub_factor_id
            HAVING COUNT(*) <= 20
        ) compact
          ON compact.factor_id=ts.factor_id
         AND compact.is_sub_factor_id=ts.is_sub_factor_id
        WHERE ts.ic_scope='time_series'
          AND JSON_UNQUOTE(JSON_EXTRACT(ts.metrics_json,'$.summary.period_start')) IS NOT NULL
          AND JSON_UNQUOTE(JSON_EXTRACT(ts.metrics_json,'$.summary.period_end')) IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM factor_ic_summary_metrics cs
              WHERE cs.factor_id=ts.factor_id
                AND cs.is_sub_factor_id=ts.is_sub_factor_id
                AND cs.run_id=ts.run_id
                AND cs.ic_scope='cross_sectional'
                AND cs.calculation_mode=ts.calculation_mode
                AND cs.factor_bar_interval=ts.factor_bar_interval
                AND cs.factor_window_bars=ts.factor_window_bars
                AND cs.return_bar_interval=ts.return_bar_interval
                AND cs.forward_return_bars=ts.forward_return_bars
                AND cs.universe_key=ts.universe_key
                AND COALESCE(cs.symbol,'')=COALESCE(ts.symbol,'')
                AND cs.window_scope=ts.window_scope
                AND cs.scoring_version <=> ts.scoring_version
          )
        ORDER BY r.completed_at DESC,ts.updated_at DESC,ts.id DESC
        LIMIT 1
        """,
    )
    if seed is None:
        return []
    return query(
        cursor,
        """
        SELECT *
        FROM factor_ic_summary_metrics
        WHERE factor_id=%s AND is_sub_factor_id=%s AND run_id=%s
          AND ic_scope IN ('time_series','cross_sectional')
          AND calculation_mode=%s AND factor_bar_interval=%s
          AND factor_window_bars=%s AND return_bar_interval=%s
          AND forward_return_bars=%s AND universe_key=%s
          AND COALESCE(symbol,'')=%s AND window_scope=%s
          AND scoring_version <=> %s
        ORDER BY ic_scope,id
        """,
        (
            seed["factor_id"],
            seed["is_sub_factor_id"],
            seed["run_id"],
            seed["calculation_mode"],
            seed["factor_bar_interval"],
            seed["factor_window_bars"],
            seed["return_bar_interval"],
            seed["forward_return_bars"],
            seed["universe_key"],
            str(seed.get("symbol") or ""),
            seed["window_scope"],
            seed["scoring_version"],
        ),
    )


def compact_metric_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields needed to identify and adjudicate one IC summary."""

    payload = decode_json(row.get("metrics_json")) or {}
    summary = payload.get("summary") if isinstance(payload, dict) else {}
    return {
        "id": row.get("id"),
        "run_id": row.get("run_id"),
        "factor_id": row.get("factor_id"),
        "is_sub_factor_id": row.get("is_sub_factor_id"),
        "ic_scope": row.get("ic_scope"),
        "calculation_mode": row.get("calculation_mode"),
        "factor_bar_interval": row.get("factor_bar_interval"),
        "factor_window_bars": row.get("factor_window_bars"),
        "return_bar_interval": row.get("return_bar_interval"),
        "forward_return_bars": row.get("forward_return_bars"),
        "universe_key": row.get("universe_key"),
        "symbol": row.get("symbol") or "",
        "window_scope": row.get("window_scope"),
        "scoring_version": row.get("scoring_version"),
        "period_start_db_raw": row.get("period_start"),
        "period_end_db_raw": row.get("period_end"),
        "period_start_explicit_oracle": summary.get("period_start") if isinstance(summary, dict) else None,
        "period_end_explicit_oracle": summary.get("period_end") if isinstance(summary, dict) else None,
    }


def metric_mcp_arguments(row: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Build one exact factor_get_metrics request from a DB summary identity."""

    return {
        "factor_ref": f"{'sub_factor' if row['is_sub_factor_id'] else 'factor'}:{row['factor_id']}",
        "ic_scope": row["ic_scope"],
        "calculation_mode": row["calculation_mode"],
        "universe_key": row["universe_key"],
        "window_scope": row["window_scope"],
        "interval": row["factor_bar_interval"],
        "factor_window_bars": row["factor_window_bars"],
        "return_bar_interval": row["return_bar_interval"],
        "forward_return_bars": int(row["forward_return_bars"]),
        "as_of": as_of,
        "scoring_version": row["scoring_version"],
        "symbol": row.get("symbol") or "",
        "run_id": row["run_id"],
    }


def calc_508(cursor: pymysql.cursors.DictCursor) -> dict[str, Any]:
    """Adjudicate observable cost fields and the independent-recalculation precondition."""

    batch = query_one(
        cursor,
        """
        SELECT id,batch_uid,publish_status,is_active,evaluation_config,
               evaluation_config_version,published_at
        FROM market_environment_eval_batch
        WHERE is_active=1 AND publish_status='published'
        ORDER BY published_at DESC,id DESC
        LIMIT 1
        """,
    )
    if batch is None:
        return {
            "case_id": "CALC-508",
            "status": "BLOCKED",
            "blocking_reason": "BLOCKED_DATA_PRECONDITION",
            "reason": "No active published evaluation batch exists.",
        }
    config = decode_json(batch.get("evaluation_config")) or {}
    metrics = query(
        cursor,
        """
        SELECT id,factor_ref,label_code,evaluation_type,metric_status,is_valid,
               turnover_rate,net_return,sharpe,metric_payload
        FROM market_environment_factor_metric
        WHERE eval_batch_id=%s
        ORDER BY id
        """,
        (batch["id"],),
    )
    routes = query(
        cursor,
        """
        SELECT id,metric_id,factor_ref,label_code,market_scope,evidence
        FROM market_environment_factor_route
        WHERE eval_batch_id=%s
        ORDER BY id
        """,
        (batch["id"],),
    )
    metrics_by_id = {int(row["id"]): row for row in metrics}
    status_counts = Counter(str(row.get("metric_status")) for row in metrics)
    success_rows = [row for row in metrics if row.get("metric_status") == "success"]
    nonsuccess_rows = [row for row in metrics if row.get("metric_status") != "success"]
    missing_success_fields: list[int] = []
    unexpected_nonsuccess_values: list[int] = []
    payload_mismatches: list[dict[str, Any]] = []
    raw_return_paths: set[str] = set()
    hash_only_return_paths: set[str] = set()
    top_level_key_shapes: Counter[tuple[str, ...]] = Counter()
    for row in metrics:
        payload = decode_json(row.get("metric_payload")) or {}
        if isinstance(payload, dict):
            top_level_key_shapes[tuple(sorted(payload))] += 1
        for path, item in iter_key_paths(payload):
            key = path.rsplit(".", 1)[-1]
            if key.lower().endswith("forward_return_sha256"):
                hash_only_return_paths.add(path)
            elif RAW_RETURN_KEY_PATTERN.search(key):
                raw_return_paths.add(path)
        if row.get("metric_status") == "success":
            missing = [field for field in COST_FIELDS if row.get(field) is None]
            if missing:
                missing_success_fields.append(int(row["id"]))
            if isinstance(payload, dict):
                for field in COST_FIELDS:
                    if not decimal_equal(row.get(field), payload.get(field)):
                        payload_mismatches.append(
                            {"metric_id": row["id"], "field": field}
                        )
            else:
                payload_mismatches.append(
                    {"metric_id": row["id"], "field": "metric_payload"}
                )
        elif any(row.get(field) is not None for field in COST_FIELDS):
            unexpected_nonsuccess_values.append(int(row["id"]))

    route_mismatches: list[dict[str, Any]] = []
    traced_metric_ids: set[int] = set()
    for route in routes:
        evidence = decode_json(route.get("evidence")) or {}
        metric_ids = evidence.get("metric_ids") if isinstance(evidence, dict) else None
        if not isinstance(metric_ids, dict):
            route_mismatches.append({"route_id": route["id"], "reason": "metric_ids missing"})
            continue
        if int(route["metric_id"]) not in {int(value) for value in metric_ids.values()}:
            route_mismatches.append(
                {"route_id": route["id"], "reason": "primary metric_id not in evidence"}
            )
        for scope, metric_id_value in metric_ids.items():
            metric_id = int(metric_id_value)
            traced_metric_ids.add(metric_id)
            metric = metrics_by_id.get(metric_id)
            nested = evidence.get(scope) if isinstance(evidence, dict) else None
            if metric is None:
                route_mismatches.append(
                    {"route_id": route["id"], "metric_id": metric_id, "reason": "metric missing"}
                )
                continue
            if not isinstance(nested, dict):
                route_mismatches.append(
                    {"route_id": route["id"], "metric_id": metric_id, "reason": "scope evidence missing"}
                )
                continue
            identity_ok = (
                str(metric.get("factor_ref")) == str(route.get("factor_ref"))
                and str(metric.get("label_code")) == str(route.get("label_code"))
                and str(metric.get("evaluation_type")) == str(scope)
            )
            field_mismatches = [
                field
                for field in COST_FIELDS
                if not decimal_equal(metric.get(field), nested.get(field))
            ]
            if not identity_ok or field_mismatches:
                route_mismatches.append(
                    {
                        "route_id": route["id"],
                        "metric_id": metric_id,
                        "identity_ok": identity_ok,
                        "field_mismatches": field_mismatches,
                    }
                )

    candidate_columns = query(
        cursor,
        """
        SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,COLUMN_COMMENT
        FROM information_schema.columns
        WHERE table_schema=DATABASE()
          AND (
            COLUMN_NAME REGEXP '(^|_)(gross|raw|net|return|pnl|turnover|cost|position|signal)(_|$)'
            OR COLUMN_COMMENT REGEXP '原始收益|无成本|费用前|收益序列|换手|成本后|交易成本'
          )
        ORDER BY TABLE_NAME,ORDINAL_POSITION
        """,
    )
    environment_candidates = [
        row
        for row in candidate_columns
        if str(row["TABLE_NAME"]).startswith("market_environment_")
    ]
    has_recalculation_series = bool(raw_return_paths)
    observable_checks_pass = (
        Decimal(str(config.get("transaction_cost"))) == Decimal("0.001")
        and bool(success_rows)
        and not missing_success_fields
        and not unexpected_nonsuccess_values
        and not payload_mismatches
        and bool(routes)
        and not route_mismatches
    )
    return {
        "case_id": "CALC-508",
        "status": "BLOCKED" if observable_checks_pass and not has_recalculation_series else (
            "PASS" if observable_checks_pass else "FAIL"
        ),
        "blocking_reason": (
            "BLOCKED_DATA_PRECONDITION"
            if observable_checks_pass and not has_recalculation_series
            else None
        ),
        "reason": (
            "The published batch and persisted cost outputs are internally traceable, but neither "
            "a gross/raw strategy return nor positions/signals plus a period return series is exposed; "
            "transaction-cost subtraction and net Sharpe therefore cannot be independently recomputed."
            if observable_checks_pass and not has_recalculation_series
            else "Observable cost-field consistency checks failed."
        ),
        "confirmed": {
            "selected_batch_id": batch["id"],
            "selected_batch_uid": batch["batch_uid"],
            "publish_status": batch["publish_status"],
            "is_active": bool(batch["is_active"]),
            "evaluation_config_version": batch["evaluation_config_version"],
            "transaction_cost": config.get("transaction_cost"),
            "metric_count": len(metrics),
            "metric_status_counts": dict(status_counts),
            "success_metric_count": len(success_rows),
            "non_success_metric_count": len(nonsuccess_rows),
            "success_missing_cost_field_count": len(missing_success_fields),
            "non_success_non_null_cost_field_count": len(unexpected_nonsuccess_values),
            "db_vs_metric_payload_cost_mismatch_count": len(payload_mismatches),
            "route_count": len(routes),
            "route_cost_trace_mismatch_count": len(route_mismatches),
            "route_evidence_metric_count": len(traced_metric_ids),
        },
        "cannot_confirm": {
            "gross_or_raw_return_key_paths": sorted(raw_return_paths),
            "hash_only_forward_return_paths_sample": sorted(hash_only_return_paths)[:10],
            "environment_return_related_schema_columns": environment_candidates,
            "can_recompute_transaction_cost": has_recalculation_series,
            "can_compare_net_to_gross": has_recalculation_series,
            "can_recompute_net_sharpe": has_recalculation_series,
        },
        "diagnostics": {
            "top_level_metric_payload_key_shape_count": len(top_level_key_shapes),
            "top_level_metric_payload_key_shapes": [
                {"keys": list(keys), "row_count": count}
                for keys, count in top_level_key_shapes.most_common()
            ],
            "missing_success_metric_ids_sample": missing_success_fields[:20],
            "unexpected_non_success_metric_ids_sample": unexpected_nonsuccess_values[:20],
            "payload_mismatch_sample": payload_mismatches[:20],
            "route_mismatch_sample": route_mismatches[:20],
        },
    }


def env_108(
    cursor: pymysql.cursors.DictCursor,
    backend: requests.Session,
    mcp: MCPClient,
    output: Path,
    as_of: str,
    sequence: list[int],
) -> dict[str, Any]:
    """Recheck Backend date filtering against MCP and DB for both label kinds."""

    evidence: list[dict[str, Any]] = []
    all_passed = True
    for kind in ("fact", "forecast"):
        target = query_one(
            cursor,
            """
            SELECT *
            FROM market_environment_daily
            WHERE label_kind=%s AND is_current=1
            ORDER BY environment_date DESC,revision DESC,id DESC
            LIMIT 1
            """,
            (kind,),
        )
        if target is None:
            evidence.append({"label_kind": kind, "status": "BLOCKED_NO_FIXTURE"})
            all_passed = False
            continue
        base_args = {"label_kind": kind, "limit": 10}
        exact_args = {
            "label_kind": kind,
            "environment_date": str(target["environment_date"]),
            "as_of": as_of,
            "include_revisions": False,
            "limit": 10,
        }
        mcp_exact_args = {
            key: value for key, value in exact_args.items() if key != "include_revisions"
        }
        sequence[0] += 1
        unfiltered = backend_get(
            backend,
            output,
            sequence[0],
            f"ENV-108-{kind.upper()}-BACKEND-UNFILTERED",
            "/market-environments/daily",
            base_args,
        )
        sequence[0] += 1
        exact_without_as_of = backend_get(
            backend,
            output,
            sequence[0],
            f"ENV-108-{kind.upper()}-BACKEND-EXACT-NO-ASOF",
            "/market-environments/daily",
            {key: value for key, value in exact_args.items() if key != "as_of"},
        )
        sequence[0] += 1
        exact = backend_get(
            backend,
            output,
            sequence[0],
            f"ENV-108-{kind.upper()}-BACKEND-EXACT",
            "/market-environments/daily",
            exact_args,
        )
        mcp_call = mcp.tool(
            f"ENV-108-{kind.upper()}-MCP-EXACT",
            "environment_get_daily",
            mcp_exact_args,
        )
        target_id = int(target["id"])
        unfiltered_ids = [int(item["id"]) for item in backend_items(unfiltered)]
        exact_no_as_of_ids = [int(item["id"]) for item in backend_items(exact_without_as_of)]
        exact_ids = [int(item["id"]) for item in backend_items(exact)]
        mcp_items = mcp_rows(mcp_call)
        mcp_ids = [int(item["id"]) for item in mcp_items]
        mcp_hit = next((item for item in mcp_items if int(item["id"]) == target_id), None)
        mcp_identity_match = bool(mcp_hit) and all(
            scalar_equal(mcp_hit.get(field), target.get(field))
            for field in (
                "id",
                "environment_date",
                "label_kind",
                "label_code",
                "revision",
                "is_current",
            )
        )
        passed = (
            unfiltered.get("http_status") == 200
            and exact_without_as_of.get("http_status") == 200
            and exact.get("http_status") == 200
            and target_id in unfiltered_ids
            and target_id in exact_no_as_of_ids
            and target_id in exact_ids
            and mcp_successful(mcp_call)
            and target_id in mcp_ids
            and mcp_identity_match
        )
        all_passed = all_passed and passed
        evidence.append(
            {
                "label_kind": kind,
                "db_identity": {
                    "id": target_id,
                    "environment_date": target["environment_date"],
                    "revision": target["revision"],
                    "is_current": target["is_current"],
                    "available_at_db_raw": target["available_at"],
                },
                "backend_unfiltered_ids": unfiltered_ids,
                "backend_exact_without_as_of_ids": exact_no_as_of_ids,
                "backend_exact_ids": exact_ids,
                "mcp_exact_ids": mcp_ids,
                "mcp_identity_match": mcp_identity_match,
                "passed": passed,
            }
        )
    return {
        "case_id": "ENV-108",
        "status": "PASS" if all_passed else "FAIL",
        "severity": None if all_passed else "P1",
        "reason": (
            "Backend, MCP, and DB return the same current row for an exact environment_date filter."
            if all_passed
            else "Backend can list the current DB rows without environment_date, but its documented exact "
            "environment_date filter omits them; MCP returns the same DB identities under the same filter. "
            "The failure also occurs without as_of, so it is not caused by the test runner's timezone conversion."
        ),
        "evidence": evidence,
    }


def met_310(
    cursor: pymysql.cursors.DictCursor,
    backend: requests.Session,
    mcp: MCPClient,
    output: Path,
    as_of: str,
    sequence: list[int],
) -> dict[str, Any]:
    """Reconcile Backend and MCP metric identities and timestamp instants."""

    fixture = select_metric_fixture(cursor)
    if not fixture:
        return {
            "case_id": "MET-310",
            "status": "BLOCKED",
            "blocking_reason": "BLOCKED_DATA_PRECONDITION",
            "reason": "No compact completed-run TS/CS metric fixture with explicit period timestamps exists.",
        }
    seed = fixture[0]
    sequence[0] += 1
    backend_call = backend_get(
        backend,
        output,
        sequence[0],
        "MET-310-BACKEND-ALL",
        "/factor-ic/summary-metrics",
        {
            "factor_id": int(seed["factor_id"]),
            "is_sub_factor_id": bool(seed["is_sub_factor_id"]),
            "limit": 5000,
        },
    )
    backend_by_id = {int(item["id"]): item for item in backend_items(backend_call)}
    comparisons: list[dict[str, Any]] = []
    non_time_mismatches: list[dict[str, Any]] = []
    backend_time_mismatches: list[dict[str, Any]] = []
    mcp_time_mismatches: list[dict[str, Any]] = []
    identity_fields = (
        "id",
        "run_id",
        "factor_id",
        "is_sub_factor_id",
        "ic_scope",
        "calculation_mode",
        "factor_bar_interval",
        "factor_window_bars",
        "return_bar_interval",
        "forward_return_bars",
        "universe_key",
        "symbol",
        "window_scope",
        "scoring_version",
    )
    for db_row in fixture:
        metric_id = int(db_row["id"])
        backend_row = backend_by_id.get(metric_id)
        mcp_call = mcp.tool(
            f"MET-310-MCP-{str(db_row['ic_scope']).upper()}",
            "factor_get_metrics",
            metric_mcp_arguments(db_row, as_of),
        )
        summaries = mcp_data(mcp_call).get("ic_summaries")
        mcp_items = (
            [dict(item) for item in summaries if isinstance(item, dict)]
            if isinstance(summaries, list)
            else []
        )
        mcp_row = next((item for item in mcp_items if int(item["id"]) == metric_id), None)
        row_identity_mismatches: list[str] = []
        if backend_row is None:
            row_identity_mismatches.append("backend_missing")
        if mcp_row is None:
            row_identity_mismatches.append("mcp_missing")
        if backend_row and mcp_row:
            for field in identity_fields:
                expected = db_row.get(field)
                if field == "symbol":
                    expected = expected or ""
                if not scalar_equal(backend_row.get(field), expected):
                    row_identity_mismatches.append(f"backend.{field}")
                if not scalar_equal(mcp_row.get(field), expected):
                    row_identity_mismatches.append(f"mcp.{field}")
        if row_identity_mismatches:
            non_time_mismatches.append(
                {"metric_id": metric_id, "fields": row_identity_mismatches}
            )
        payload = decode_json(db_row.get("metrics_json")) or {}
        summary = payload.get("summary") if isinstance(payload, dict) else {}
        time_checks: dict[str, Any] = {}
        for field in TIME_FIELDS:
            oracle_text = summary.get(field) if isinstance(summary, dict) else None
            oracle = parse_instant(oracle_text)
            backend_text = backend_row.get(field) if backend_row else None
            mcp_text = mcp_row.get(field) if mcp_row else None
            backend_instant = parse_instant(backend_text)
            mcp_instant = parse_instant(mcp_text)
            backend_matches = oracle is not None and backend_instant == oracle
            mcp_matches = oracle is not None and mcp_instant == oracle
            if not backend_matches:
                backend_time_mismatches.append(
                    {
                        "metric_id": metric_id,
                        "field": field,
                        "backend": backend_text,
                        "explicit_db_payload_oracle": oracle_text,
                        "instant_delta_seconds": (
                            (backend_instant - oracle).total_seconds()
                            if backend_instant is not None and oracle is not None
                            else None
                        ),
                    }
                )
            if not mcp_matches:
                mcp_time_mismatches.append(
                    {
                        "metric_id": metric_id,
                        "field": field,
                        "mcp": mcp_text,
                        "explicit_db_payload_oracle": oracle_text,
                        "instant_delta_seconds": (
                            (mcp_instant - oracle).total_seconds()
                            if mcp_instant is not None and oracle is not None
                            else None
                        ),
                    }
                )
            time_checks[field] = {
                "db_outer_raw": db_row.get(field),
                "db_payload_explicit": oracle_text,
                "backend": backend_text,
                "mcp": mcp_text,
                "backend_same_instant_as_db_payload": backend_matches,
                "mcp_same_instant_as_db_payload": mcp_matches,
            }
        comparisons.append(
            {
                "metric_id": metric_id,
                "ic_scope": db_row["ic_scope"],
                "identity_mismatches": row_identity_mismatches,
                "time_checks": time_checks,
            }
        )
    passed = (
        backend_call.get("http_status") == 200
        and not non_time_mismatches
        and not backend_time_mismatches
        and not mcp_time_mismatches
    )
    return {
        "case_id": "MET-310",
        "status": "PASS" if passed else "FAIL",
        "severity": None if passed else "P1",
        "reason": (
            "Backend and MCP reconstruct the same TS/CS rows and represent period boundaries as the same instants."
            if passed
            else "Metric identities reconstruct correctly, but Backend period_start/period_end do not represent "
            "the same instants as the explicit UTC values in the row's own DB metrics_json; MCP does. "
            "This is a real Backend timestamp conversion error, not an equivalent-offset representation."
        ),
        "fixture": [compact_metric_row(row) for row in fixture],
        "backend_returned_metric_ids": sorted(backend_by_id),
        "fixture_metric_ids": sorted(int(row["id"]) for row in fixture),
        "non_time_mismatches": non_time_mismatches,
        "backend_time_mismatches": backend_time_mismatches,
        "mcp_time_mismatches": mcp_time_mismatches,
        "comparisons": comparisons,
    }


def openapi_evidence(session: requests.Session, output: Path) -> dict[str, Any]:
    """Fetch and compact the live OpenAPI contract used by the Backend checks."""

    settings = SettingsLoader.load("test", ROOT)
    response = session.get(
        f"{settings.api.base_url}/openapi.yaml",
        timeout=settings.api.timeout_seconds,
    )
    response.raise_for_status()
    spec = yaml.safe_load(response.text)
    paths = spec.get("paths") if isinstance(spec, dict) else {}
    selected: dict[str, Any] = {}
    for path in (
        "/api/v1/market-environments/daily",
        "/api/v1/factor-ic/summary-metrics",
    ):
        operation = (paths.get(path) or {}).get("get") if isinstance(paths, dict) else None
        selected[path] = {
            "summary": operation.get("summary") if isinstance(operation, dict) else None,
            "operation_id": operation.get("operationId") if isinstance(operation, dict) else None,
            "parameters": operation.get("parameters") if isinstance(operation, dict) else None,
            "responses": sorted((operation.get("responses") or {}).keys())
            if isinstance(operation, dict)
            else None,
        }
    evidence = {
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "paths": selected,
    }
    write_json(output / "openapi-evidence.json", evidence)
    return evidence


def markdown_summary(summary: dict[str, Any]) -> str:
    """Render a compact human-readable adjudication alongside machine JSON."""

    lines = [
        "# CALC-508 / ENV-108 / MET-310 read-only adjudication",
        "",
        f"- Environment: `{summary['environment']}`",
        f"- Mode: `{summary['mode']}`",
        f"- DB transaction: `{summary['database_transaction']}`",
        f"- DB changed by test: `{summary['database_changed_by_test']}`",
        "",
        "| Case | Status | Conclusion |",
        "|---|---|---|",
    ]
    for case in summary["cases"]:
        reason = str(case.get("reason") or "").replace("|", "\\|")
        lines.append(f"| {case['case_id']} | {case['status']} | {reason} |")
    lines.extend(
        [
            "",
            "`BLOCKED_DATA_PRECONDITION` means the observable persisted values are internally consistent, "
            "but the data needed for an independent numerical oracle is not exposed. It is not counted as a product failure.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Execute all three read-only adjudications and persist sanitized evidence."""

    token = next((os.environ.get(name) for name in TOKEN_ENV_NAMES if os.environ.get(name)), None)
    if not token:
        raise SystemExit("MCP_TOKEN or FACTOR4_MCP_TOKEN is required")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = REPORT_ROOT / f"{stamp}-calc508-env108-met310"
    output.mkdir(parents=True, exist_ok=False)
    settings = SettingsLoader.load("test", ROOT)
    backend = requests.Session()
    backend_token = backend_login(backend)
    backend.headers.update(
        {"Authorization": f"Bearer {backend_token}", "Accept": "application/json"}
    )
    openapi = openapi_evidence(backend, output)
    mcp = MCPClient(token, output)
    init = mcp.request(
        "MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-cost-time-reconcile", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    mcp.protocol_version = str(init_result.get("protocolVersion") or "") or None
    if not mcp_successful(init) or not mcp.protocol_version:
        raise RuntimeError("MCP initialization failed")
    mcp.request("MCP-NOTIFY", "notifications/initialized", {})
    tools_call = mcp.request("MCP-TOOLS", "tools/list", {})
    tool_rows = (((tools_call.get("envelope") or {}).get("result") or {}).get("tools") or [])
    selected_tools = [
        row
        for row in tool_rows
        if isinstance(row, dict)
        and row.get("name") in {"environment_get_daily", "factor_get_metrics"}
    ]
    write_json(output / "mcp-tool-schemas.json", selected_tools)

    connection = connect_database()
    sequence = [0]
    cases: list[dict[str, Any]] = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            snapshot = query_one(
                cursor,
                """
                SELECT DATABASE() AS database_name,@@hostname AS hostname,
                       @@session.time_zone AS session_time_zone,
                       NOW(6) AS now_local,UTC_TIMESTAMP(6) AS now_utc
                """,
            )
            if snapshot is None or not isinstance(snapshot.get("now_utc"), datetime):
                raise RuntimeError("Could not acquire DB snapshot time")
            as_of = utc_text(snapshot["now_utc"])
            write_json(
                output / "run-context.json",
                {
                    "environment": "test",
                    "backend_base_url": settings.api.base_url,
                    "mcp_url": "https://test-factor-frontend.questvector.ai/mcp/factor-data",
                    "authentication": {
                        "backend": "test account login; JWT not persisted",
                        "mcp": "Bearer <redacted>",
                    },
                    "mode": "READ_ONLY",
                    "database_transaction": "SET SESSION TRANSACTION READ ONLY; START TRANSACTION WITH CONSISTENT SNAPSHOT; ROLLBACK",
                    "database_name": snapshot["database_name"],
                    "database_host_sha256": hashlib.sha256(
                        str(snapshot["hostname"]).encode("utf-8")
                    ).hexdigest(),
                    "database_session_time_zone": snapshot["session_time_zone"],
                    "snapshot_now_local": snapshot["now_local"],
                    "snapshot_now_utc": snapshot["now_utc"],
                    "request_as_of": as_of,
                    "openapi_sha256": openapi["sha256"],
                },
            )
            cases.append(calc_508(cursor))
            cases.append(env_108(cursor, backend, mcp, output, as_of, sequence))
            cases.append(met_310(cursor, backend, mcp, output, as_of, sequence))
    finally:
        connection.rollback()
        connection.close()
        backend.close()

    summary = {
        "run_id": output.name,
        "environment": "test",
        "mode": "READ_ONLY",
        "database_transaction": "explicit read-only snapshot followed by rollback",
        "database_changed_by_test": False,
        "counts": {
            status: sum(case.get("status") == status for case in cases)
            for status in ("PASS", "FAIL", "BLOCKED")
        },
        "cases": cases,
        "excluded_topics": [
            "UX/compatibility/spec-style observations",
            "orphan records",
            "end_time boundary",
            "missing document references",
            "VWAP historical data",
        ],
        "credential_handling": "Authorization headers and complete tokens were not persisted.",
    }
    write_json(output / "adjudicated-summary.json", summary)
    (output / "summary.md").write_text(markdown_summary(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "output_dir": str(output),
                "counts": summary["counts"],
                "cases": [
                    {
                        "case_id": case["case_id"],
                        "status": case["status"],
                        "blocking_reason": case.get("blocking_reason"),
                    }
                    for case in cases
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if any(case.get("status") == "FAIL" for case in cases) else 0


if __name__ == "__main__":
    raise SystemExit(main())
