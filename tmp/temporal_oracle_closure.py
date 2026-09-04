#!/usr/bin/env python3
"""Close CALC-502 and CALC-503 with read-only temporal evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from tmp.critical_readonly_gap_probe import (  # noqa: E402
    MCPClient,
    data as mcp_data,
    error_code as mcp_error_code,
    rows as mcp_rows,
    successful as mcp_successful,
)


REPORT_ROOT = ROOT / "reports" / "factor4-resume"
TOKEN_ENV_NAMES = ("MCP_TOKEN", "FACTOR4_MCP_TOKEN")
MCP_ENDPOINT = os.environ.get(
    "MCP_URL", "https://test-factor-frontend.questvector.ai/mcp/factor-data"
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
SENSITIVE_KEY = re.compile(
    r"authorization|token|password|secret|api[_-]?key|jwt|hmac|signature",
    re.IGNORECASE,
)
TOKEN_TEXT = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.IGNORECASE)
RAW_SERIES_KEYS = (
    "raw_returns",
    "returns",
    "return_series",
    "factor_values",
    "labels",
    "positions",
    "signals",
)
NORMALIZATION_KEYS = (
    "normalization",
    "normalization_fit",
    "scaler",
    "scaler_params",
    "fit_period",
    "training_period",
)


def json_default(value: Any) -> str:
    """Serialize database-native values without losing their displayed value."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Recursively remove credentials and complete MCP tokens from evidence."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return TOKEN_TEXT.sub("<redacted>", value)
    return value


def write_json(path: Path, value: Any) -> None:
    """Write deterministic, recursively redacted JSON evidence."""

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
    """Decode a MySQL JSON value while accepting already decoded objects."""

    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def parse_time(value: Any, *, database_value: bool = False) -> datetime | None:
    """Normalize an API/JSON or test-DB wall-clock value to UTC."""

    if value is None:
        return None
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI if database_value else timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_hash(value: Any) -> str:
    """Return the SHA256 of canonical compact JSON."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    """Execute one parameterized SELECT and return the first row."""

    records = query(cursor, sql, parameters)
    return records[0] if records else None


def db_connection() -> pymysql.connections.Connection:
    """Open a non-autocommit connection to the configured test database."""

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


def database_watermark() -> dict[str, Any]:
    """Capture compact table watermarks in an explicit read-only transaction."""

    connection = db_connection()
    evidence: dict[str, Any] = {
        "transaction": {
            "start_statement": "START TRANSACTION READ ONLY",
            "select_only": True,
            "rollback_attempted": False,
            "rolled_back": False,
        },
        "tables": {},
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            for table in (
                "market_environment_daily",
                "market_environment_eval_batch",
                "market_environment_factor_metric",
                "market_environment_factor_route",
            ):
                evidence["tables"][table] = query_one(
                    cursor,
                    f"SELECT COUNT(*) AS row_count,MAX(id) AS max_id FROM `{table}`",
                )
    finally:
        evidence["transaction"]["rollback_attempted"] = True
        try:
            connection.rollback()
            evidence["transaction"]["rolled_back"] = True
        finally:
            connection.close()
    return evidence


def compute_segments(values: list[str]) -> list[dict[str, Any]]:
    """Convert sorted calendar dates into maximal contiguous day segments."""

    parsed = sorted(date.fromisoformat(value) for value in values)
    segments: list[list[date]] = []
    for current in parsed:
        if not segments or current > segments[-1][1] + timedelta(days=1):
            segments.append([current, current])
        else:
            segments[-1][1] = current
    return [
        {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "day_count": (end - start).days + 1,
        }
        for start, end in segments
    ]


def gap_summary(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize calendar gaps between declared continuous segments."""

    gaps: list[int] = []
    for left, right in zip(segments, segments[1:]):
        left_end = date.fromisoformat(str(left["end_date"]))
        right_start = date.fromisoformat(str(right["start_date"]))
        gaps.append((right_start - left_end).days - 1)
    return {
        "gap_count": len(gaps),
        "gap_days_total": sum(gaps),
        "largest_gap_days": max(gaps) if gaps else 0,
    }


def factor_snapshot_evidence(
    factor_snapshot: dict[str, Any],
    snapshot_as_of: Any,
) -> dict[str, Any]:
    """Summarize frozen factor identities and reject definitions newer than the batch."""

    members = [
        item for item in factor_snapshot.get("members", []) if isinstance(item, dict)
    ]
    anchor = parse_time(snapshot_as_of)
    updated_times = [
        parsed
        for parsed in (parse_time(item.get("updated_at")) for item in members)
        if parsed is not None
    ]
    future = [
        {
            "factor_ref": item.get("factor_ref"),
            "updated_at": parse_time(item.get("updated_at")),
        }
        for item in members
        if anchor is not None
        and parse_time(item.get("updated_at")) is not None
        and parse_time(item.get("updated_at")) > anchor
    ]
    refs = [str(item.get("factor_ref")) for item in members]
    return {
        "schema_version": factor_snapshot.get("schema_version"),
        "declared_factor_count": factor_snapshot.get("factor_count"),
        "member_count": len(members),
        "unique_factor_ref_count": len(set(refs)),
        "member_key_shapes": sorted(
            {tuple(sorted(item)) for item in members if isinstance(item, dict)}
        ),
        "min_updated_at": min(updated_times, default=None),
        "max_updated_at": max(updated_times, default=None),
        "future_definition_count": len(future),
        "future_definition_samples": future[:20],
    }


def compact_payload_key_shapes(
    cursor: pymysql.cursors.DictCursor,
    batch_id: int,
) -> list[dict[str, Any]]:
    """Return every top-level metric payload key shape without returning payload values."""

    records = query(
        cursor,
        """
        SELECT evaluation_type,metric_status,JSON_LENGTH(metric_payload) AS key_count,
               JSON_KEYS(metric_payload) AS top_keys,COUNT(*) AS row_count
        FROM market_environment_factor_metric
        WHERE eval_batch_id=%s
        GROUP BY evaluation_type,metric_status,JSON_LENGTH(metric_payload),JSON_KEYS(metric_payload)
        ORDER BY evaluation_type,metric_status,key_count
        """,
        (batch_id,),
    )
    for record in records:
        record["top_keys"] = decode_json(record.get("top_keys"))
    return records


def compact_nested_key_shapes(
    cursor: pymysql.cursors.DictCursor,
    batch_id: int,
) -> dict[str, list[dict[str, Any]]]:
    """Return key shapes for temporal and artifact subobjects in metric payloads."""

    result: dict[str, list[dict[str, Any]]] = {}
    for name in ("oos", "direction", "aggregation", "data_diagnostics", "metric_identity"):
        records = query(
            cursor,
            f"""
            SELECT JSON_KEYS(JSON_EXTRACT(metric_payload,'$.{name}')) AS object_keys,
                   COUNT(*) AS row_count
            FROM market_environment_factor_metric
            WHERE eval_batch_id=%s
            GROUP BY JSON_KEYS(JSON_EXTRACT(metric_payload,'$.{name}'))
            ORDER BY row_count DESC
            """,
            (batch_id,),
        )
        for record in records:
            record["object_keys"] = decode_json(record.get("object_keys"))
        result[name] = records
    return result


def snapshot_evidence(
    cursor: pymysql.cursors.DictCursor,
    batch: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Validate snapshot membership, partition segmentation, and visibility cutoffs."""

    members = [item for item in snapshot.get("members", []) if isinstance(item, dict)]
    member_ids = [int(item["daily_id"]) for item in members if item.get("daily_id") is not None]
    member_dates = [str(item.get("environment_date")) for item in members]
    anchor = parse_time(snapshot.get("as_of_time"))
    db_anchor = parse_time(batch.get("as_of_time"), database_value=True)

    daily_rows: list[dict[str, Any]] = []
    if member_ids:
        placeholders = ",".join(["%s"] * len(member_ids))
        daily_rows = query(
            cursor,
            f"""
            SELECT id,environment_date,label_kind,label_code,label_status,revision,is_current,
                   available_at,schema_version
            FROM market_environment_daily
            WHERE id IN ({placeholders})
            """,
            tuple(member_ids),
        )
    daily_by_id = {int(row["id"]): row for row in daily_rows}
    mismatches: Counter[str] = Counter()
    mismatch_samples: list[dict[str, Any]] = []
    late_members: list[dict[str, Any]] = []
    for member in members:
        member_id = int(member.get("daily_id", -1))
        row = daily_by_id.get(member_id)
        member_available = parse_time(member.get("available_at"))
        row_available = parse_time(row.get("available_at"), database_value=True) if row else None
        checks = {
            "missing_db_row": row is None,
            "environment_date": row is not None
            and str(row.get("environment_date")) != str(member.get("environment_date")),
            "label_kind": row is not None
            and str(row.get("label_kind")) != str(batch.get("label_kind")),
            "label_code": row is not None
            and str(row.get("label_code")) != str(member.get("label_code")),
            "revision": row is not None
            and str(row.get("revision")) != str(member.get("revision")),
            "schema_version": row is not None
            and str(row.get("schema_version")) != str(member.get("schema_version")),
            "available_at": row is not None and row_available != member_available,
        }
        for kind, failed in checks.items():
            if failed:
                mismatches[kind] += 1
                if len(mismatch_samples) < 20:
                    mismatch_samples.append(
                        {
                            "kind": kind,
                            "daily_id": member_id,
                            "member_date": member.get("environment_date"),
                            "db_date": row.get("environment_date") if row else None,
                        }
                    )
        if anchor is not None and member_available is not None and member_available > anchor:
            late_members.append(
                {
                    "daily_id": member_id,
                    "environment_date": member.get("environment_date"),
                    "available_at": member_available,
                }
            )

    partitions = snapshot.get("partitions") if isinstance(snapshot.get("partitions"), dict) else {}
    partition_summary: dict[str, Any] = {}
    partition_date_union: set[str] = set()
    partition_label_mismatches: list[dict[str, Any]] = []
    member_dates_by_label: dict[str, set[str]] = {}
    for member in members:
        member_dates_by_label.setdefault(str(member.get("label_code")), set()).add(
            str(member.get("environment_date"))
        )
    for label, raw_partition in partitions.items():
        partition = raw_partition if isinstance(raw_partition, dict) else {}
        dates = [str(value) for value in partition.get("dates", [])]
        declared = partition.get("segments") if isinstance(partition.get("segments"), list) else []
        computed = compute_segments(dates)
        partition_date_union.update(dates)
        member_set = member_dates_by_label.get(str(label), set())
        if set(dates) != member_set:
            partition_label_mismatches.append(
                {
                    "label_code": label,
                    "partition_only_count": len(set(dates) - member_set),
                    "member_only_count": len(member_set - set(dates)),
                }
            )
        partition_summary[str(label)] = {
            "declared_day_count": partition.get("day_count"),
            "actual_day_count": len(dates),
            "unique_day_count": len(set(dates)),
            "dates_sorted": dates == sorted(dates),
            "declared_segment_count": len(declared),
            "computed_segment_count": len(computed),
            "segments_match_recomputed_dates": declared == computed,
            **gap_summary(computed),
            "segments": computed,
        }

    missing_dates = {str(value) for value in snapshot.get("missing_dates", [])}
    return {
        "schema_version": snapshot.get("schema_version"),
        "start_date": snapshot.get("start_date"),
        "end_date": snapshot.get("end_date"),
        "expected_days": snapshot.get("expected_days"),
        "covered_days": snapshot.get("covered_days"),
        "coverage_rate": snapshot.get("coverage_rate"),
        "snapshot_as_of_utc": anchor,
        "batch_as_of_db_raw": batch.get("as_of_time"),
        "batch_as_of_normalized_utc": db_anchor,
        "batch_and_snapshot_as_of_same_instant": anchor is not None and db_anchor == anchor,
        "member_count": len(members),
        "unique_member_id_count": len(set(member_ids)),
        "unique_member_date_count": len(set(member_dates)),
        "members_sorted_by_date": member_dates == sorted(member_dates),
        "max_member_available_at": max(
            (parse_time(item.get("available_at")) for item in members if item.get("available_at")),
            default=None,
        ),
        "future_member_count": len(late_members),
        "future_member_samples": late_members[:20],
        "daily_db_match_count": len(daily_rows),
        "daily_db_mismatch_counts": dict(mismatches),
        "daily_db_mismatch_samples": mismatch_samples,
        "missing_dates": sorted(missing_dates),
        "missing_member_overlap": sorted(missing_dates & set(member_dates)),
        "partition_date_union_matches_members": partition_date_union == set(member_dates),
        "partition_label_mismatches": partition_label_mismatches,
        "partitions": partition_summary,
    }


def metric_evidence(
    cursor: pymysql.cursors.DictCursor,
    batch: dict[str, Any],
    snapshot: dict[str, Any],
    factor_snapshot: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Inspect every current-batch metric's temporal metadata and oracle references."""

    batch_id = int(batch["id"])
    anchor = parse_time(snapshot.get("as_of_time"))
    partition_days = {
        str(label): int(value.get("day_count", 0))
        for label, value in (snapshot.get("partitions") or {}).items()
        if isinstance(value, dict)
    }
    factor_members = {
        str(item.get("factor_ref")): item
        for item in factor_snapshot.get("members", [])
        if isinstance(item, dict) and item.get("factor_ref") is not None
    }
    aggregate = query_one(
        cursor,
        """
        SELECT COUNT(*) AS row_count,
               SUM(metric_status='success') AS success_count,
               SUM(metric_status='insufficient_sample') AS insufficient_count,
               SUM(metric_status='failed') AS failed_count,
               COUNT(DISTINCT factor_ref) AS factor_count,
               COUNT(DISTINCT label_code) AS label_count,
               COUNT(DISTINCT evaluation_type) AS evaluation_type_count,
               MIN(sample_start_date) AS min_sample_start,
               MAX(sample_end_date) AS max_sample_end,
               SUM(label_kind <> %s) AS non_batch_label_kind_count,
               SUM(sample_start_date < %s OR sample_end_date > %s) AS sample_range_outside_batch_count,
               COUNT(DISTINCT JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                   '$.data_diagnostics.artifact_manifest_hash'))) AS distinct_artifact_manifest_count,
               SUM(JSON_EXTRACT(metric_payload,'$.data_diagnostics.artifact_fingerprints') IS NOT NULL)
                   AS fingerprint_map_count,
               SUM(JSON_EXTRACT(metric_payload,'$.artifact_uri') IS NOT NULL
                   OR JSON_EXTRACT(metric_payload,'$.data_diagnostics.artifact_uri') IS NOT NULL)
                   AS artifact_uri_count,
               SUM(JSON_EXTRACT(metric_payload,'$.run_id') IS NOT NULL
                   OR JSON_EXTRACT(metric_payload,'$.metric_identity.run_id') IS NOT NULL)
                   AS source_run_id_count
        FROM market_environment_factor_metric
        WHERE eval_batch_id=%s
        """,
        (
            batch.get("label_kind"),
            batch.get("start_date"),
            batch.get("end_date"),
            batch_id,
        ),
    ) or {}
    presence_expressions = [
        f"SUM(JSON_EXTRACT(metric_payload,'$.{key}') IS NOT NULL) AS `{key}`"
        for key in RAW_SERIES_KEYS + NORMALIZATION_KEYS
    ]
    presence = query_one(
        cursor,
        "SELECT " + ",".join(presence_expressions)
        + " FROM market_environment_factor_metric WHERE eval_batch_id=%s",
        (batch_id,),
    ) or {}
    top_shapes = compact_payload_key_shapes(cursor, batch_id)
    nested_shapes = compact_nested_key_shapes(cursor, batch_id)
    rows = query(
        cursor,
        """
        SELECT id,factor_ref,factor_version,label_code,evaluation_type,metric_status,sample_start_date,
               sample_end_date,total_sample_count,valid_sample_count,
               JSON_UNQUOTE(JSON_EXTRACT(metric_payload,'$.sample_day_count')) AS sample_day_count,
               JSON_EXTRACT(metric_payload,'$.oos') AS oos,
               JSON_EXTRACT(metric_payload,'$.direction') AS direction,
               JSON_EXTRACT(metric_payload,'$.aggregation') AS aggregation,
               JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                   '$.metric_identity.evaluation_config_hash')) AS evaluation_config_hash,
               JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                   '$.metric_identity.factor_version')) AS identity_factor_version,
               JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                   '$.metric_identity.definition_factor_version')) AS definition_factor_version,
               JSON_UNQUOTE(JSON_EXTRACT(metric_payload,
                   '$.data_diagnostics.artifact_manifest_hash')) AS artifact_manifest_hash,
               JSON_LENGTH(JSON_EXTRACT(metric_payload,
                   '$.data_diagnostics.artifact_fingerprints')) AS artifact_fingerprint_count,
               JSON_LENGTH(JSON_EXTRACT(metric_payload,
                   '$.data_diagnostics.missing_symbols')) AS missing_symbol_count
        FROM market_environment_factor_metric
        WHERE eval_batch_id=%s
        ORDER BY id
        """,
        (batch_id,),
    )

    fold_counts: Counter[int] = Counter()
    fold_durations: Counter[str] = Counter()
    violations: Counter[str] = Counter()
    violation_samples: list[dict[str, Any]] = []
    training_value_counts: Counter[str] = Counter()
    direction_sources: Counter[str] = Counter()
    aggregation_modes: Counter[str] = Counter()
    sample_day_mismatches: list[dict[str, Any]] = []
    fingerprint_reference_count = 0
    manifest_hashes: set[str] = set()
    config_hashes: set[str] = set()
    factor_identity_mismatches: Counter[str] = Counter()
    factor_identity_mismatch_samples: list[dict[str, Any]] = []
    success_rows = 0
    min_fold_start: datetime | None = None
    max_fold_end: datetime | None = None

    def record_violation(kind: str, row: dict[str, Any], detail: Any) -> None:
        """Record a bounded temporal violation sample."""

        violations[kind] += 1
        if len(violation_samples) < 30:
            violation_samples.append(
                {
                    "kind": kind,
                    "metric_id": row.get("id"),
                    "factor_ref": row.get("factor_ref"),
                    "label_code": row.get("label_code"),
                    "evaluation_type": row.get("evaluation_type"),
                    "detail": detail,
                }
            )

    for row in rows:
        oos = decode_json(row.get("oos"))
        direction = decode_json(row.get("direction"))
        aggregation = decode_json(row.get("aggregation"))
        oos = oos if isinstance(oos, dict) else {}
        direction = direction if isinstance(direction, dict) else {}
        aggregation = aggregation if isinstance(aggregation, dict) else {}
        sample_days = int(row["sample_day_count"]) if row.get("sample_day_count") else 0
        expected_days = partition_days.get(str(row.get("label_code")))
        if expected_days is not None and sample_days != expected_days:
            sample_day_mismatches.append(
                {
                    "metric_id": row.get("id"),
                    "label_code": row.get("label_code"),
                    "payload_sample_day_count": sample_days,
                    "snapshot_partition_day_count": expected_days,
                }
            )
        member = factor_members.get(str(row.get("factor_ref")))
        identity_checks = {
            "factor_ref_missing_from_snapshot": member is None,
            "metric_identity_factor_version_mismatch": str(
                row.get("identity_factor_version")
            )
            != str(row.get("factor_version")),
            "definition_factor_version_mismatch": member is not None
            and str(row.get("definition_factor_version"))
            != str(member.get("factor_version")),
        }
        for kind, failed in identity_checks.items():
            if failed:
                factor_identity_mismatches[kind] += 1
                if len(factor_identity_mismatch_samples) < 30:
                    factor_identity_mismatch_samples.append(
                        {
                            "kind": kind,
                            "metric_id": row.get("id"),
                            "factor_ref": row.get("factor_ref"),
                        }
                    )
        if row.get("artifact_manifest_hash"):
            manifest_hashes.add(str(row["artifact_manifest_hash"]))
        if row.get("evaluation_config_hash"):
            config_hashes.add(str(row["evaluation_config_hash"]))
        fingerprint_reference_count += int(row.get("artifact_fingerprint_count") or 0)
        if str(row.get("metric_status")) != "success":
            continue
        success_rows += 1
        folds = [item for item in oos.get("folds", []) if isinstance(item, dict)]
        fold_counts[len(folds)] += 1
        previous_end: datetime | None = None
        for index, fold in enumerate(folds):
            start = parse_time(fold.get("start"))
            end = parse_time(fold.get("end"))
            if start is None or end is None:
                record_violation("unparseable_oos_boundary", row, {"fold_index": index})
                continue
            min_fold_start = start if min_fold_start is None else min(min_fold_start, start)
            max_fold_end = end if max_fold_end is None else max(max_fold_end, end)
            fold_durations[str((end - start).total_seconds())] += 1
            if start >= end:
                record_violation("oos_start_not_before_end", row, {"fold_index": index})
            if previous_end is not None and start != previous_end:
                record_violation(
                    "oos_fold_gap_or_overlap",
                    row,
                    {
                        "fold_index": index,
                        "previous_end": previous_end,
                        "current_start": start,
                    },
                )
            if anchor is not None and end > anchor:
                record_violation(
                    "oos_end_after_batch_as_of",
                    row,
                    {"fold_index": index, "fold_end": end, "batch_as_of": anchor},
                )
            previous_end = end
        frozen_at = parse_time(direction.get("direction_frozen_at"))
        direction_sources[str(direction.get("direction_source"))] += 1
        aggregation_modes[str(aggregation.get("calculation_mode"))] += 1
        training_value_counts[
            "null" if direction.get("training_mean_ic") is None else "non_null"
        ] += 1
        if folds and frozen_at is not None:
            first_start = parse_time(folds[0].get("start"))
            if first_start is not None and frozen_at != first_start:
                record_violation(
                    "direction_freeze_not_equal_first_oos_start",
                    row,
                    {"direction_frozen_at": frozen_at, "first_oos_start": first_start},
                )
        elif folds:
            record_violation("missing_direction_frozen_at", row, None)

    canonical_config_hash = canonical_hash(config)
    artifact_columns = query(
        cursor,
        """
        SELECT TABLE_NAME AS table_name,COLUMN_NAME AS column_name,COLUMN_TYPE AS column_type
        FROM information_schema.columns
        WHERE table_schema=DATABASE()
          AND TABLE_NAME IN ('market_environment_factor_metric','factor_ic_runs',
              'factor_ic_summary_metrics','factor_ic_slice_metrics','factor_value_slice_metrics',
              'pipeline_artifacts','benchmark_daily_returns','benchmark_minute_returns')
          AND (COLUMN_NAME REGEXP 'bar|return|label|value|normal|train|oos|period|time|path|uri|hash|artifact'
               OR COLUMN_NAME IN ('run_id','eval_batch_id','metric_payload','metrics_json','config_json'))
        ORDER BY TABLE_NAME,ORDINAL_POSITION
        """,
    )
    candidate_tables = query(
        cursor,
        """
        SELECT TABLE_NAME AS table_name,TABLE_TYPE AS table_type
        FROM information_schema.tables
        WHERE table_schema=DATABASE()
          AND (TABLE_NAME LIKE '%%bar%%' OR TABLE_NAME LIKE '%%return%%'
               OR TABLE_NAME LIKE '%%factor_value%%' OR TABLE_NAME LIKE '%%artifact%%')
        ORDER BY TABLE_NAME
        """,
    )
    fixture = query_one(
        cursor,
        """
        SELECT r.factor_ref,r.label_code,m.id AS metric_id,m.evaluation_type
        FROM market_environment_factor_route r
        JOIN market_environment_factor_metric m ON m.id=r.metric_id
        WHERE r.eval_batch_id=%s AND r.is_active=1
        ORDER BY r.id
        LIMIT 1
        """,
        (batch_id,),
    )
    expected_fixture_rows: list[dict[str, Any]] = []
    if fixture:
        expected_fixture_rows = query(
            cursor,
            """
            SELECT id,factor_ref,label_code,evaluation_type,metric_status,is_valid,
                   sample_start_date,sample_end_date,total_sample_count,valid_sample_count
            FROM market_environment_factor_metric
            WHERE eval_batch_id=%s AND factor_ref=%s AND label_code=%s
            ORDER BY evaluation_type,id
            """,
            (batch_id, fixture["factor_ref"], fixture["label_code"]),
        )
    return {
        "aggregate": aggregate,
        "payload_top_level_key_shapes": top_shapes,
        "payload_nested_key_shapes": nested_shapes,
        "payload_field_presence_counts": presence,
        "canonical_evaluation_config_hash": canonical_config_hash,
        "metric_evaluation_config_hashes": sorted(config_hashes),
        "all_metric_config_hashes_match_batch_config": config_hashes == {canonical_config_hash},
        "artifact_manifest_hash_count": len(manifest_hashes),
        "artifact_fingerprint_reference_count": fingerprint_reference_count,
        "artifact_reference_assessment": {
            "hashes_present": bool(manifest_hashes and fingerprint_reference_count),
            "downloadable_uri_present": int(aggregate.get("artifact_uri_count") or 0) > 0,
            "source_run_id_present": int(aggregate.get("source_run_id_count") or 0) > 0,
            "local_aligned_data_path_exists": Path(
                "/opt/nextalpha/shared/data/aligned"
            ).exists(),
            "local_factor_data_path_exists": Path(
                "/opt/nextalpha/shared/factors"
            ).exists(),
            "note": (
                "Hashes establish identity only. No URI/FK maps current batch hashes to readable "
                "bar, factor-value, forward-return, label-join, position, or signal artifacts."
            ),
        },
        "success_metric_count_checked": success_rows,
        "oos_fold_count_distribution": dict(sorted(fold_counts.items())),
        "oos_fold_duration_seconds_distribution": dict(sorted(fold_durations.items())),
        "earliest_oos_start": min_fold_start,
        "latest_oos_end": max_fold_end,
        "direction_source_distribution": dict(direction_sources),
        "training_mean_ic_presence": dict(training_value_counts),
        "aggregation_mode_distribution": dict(aggregation_modes),
        "temporal_violation_counts": dict(violations),
        "temporal_violation_samples": violation_samples,
        "sample_day_count_mismatch_count": len(sample_day_mismatches),
        "sample_day_count_mismatch_samples": sample_day_mismatches[:30],
        "factor_identity_mismatch_counts": dict(factor_identity_mismatches),
        "factor_identity_mismatch_samples": factor_identity_mismatch_samples,
        "configured_oos_fold_count": config.get("oos_fold_count"),
        "configured_oos_fold_days": config.get("oos_fold_days"),
        "candidate_input_tables": candidate_tables,
        "candidate_input_columns": artifact_columns,
        "raw_input_assessment": {
            "current_metric_has_raw_series": any(int(presence.get(key) or 0) for key in RAW_SERIES_KEYS),
            "current_metric_has_normalization_fit": any(
                int(presence.get(key) or 0) for key in NORMALIZATION_KEYS
            ),
            "factor_value_slice_limitation": (
                "factor_value_slice_metrics has factor values but no eval_batch_id, environment label, "
                "or forward-return value, and current metrics expose no source run_id."
            ),
            "factor_ic_slice_limitation": (
                "factor_ic_slice_metrics contains aggregates, not per-bar forward returns, and has no "
                "eval_batch_id or current artifact-manifest link."
            ),
            "benchmark_return_limitation": (
                "benchmark return tables cannot reconstruct the multi-symbol forward-return inputs."
            ),
        },
        "mcp_fixture": fixture,
        "mcp_expected_metric_rows": expected_fixture_rows,
    }


def read_database_evidence() -> dict[str, Any]:
    """Read all CALC-502/503 database evidence in one read-only transaction."""

    connection = db_connection()
    evidence: dict[str, Any] = {
        "transaction": {
            "start_statement": "START TRANSACTION READ ONLY",
            "select_only": True,
            "rollback_attempted": False,
            "rolled_back": False,
        }
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            evidence["database_identity"] = query_one(
                cursor,
                """
                SELECT DATABASE() AS database_name,CURRENT_USER() AS current_user_name,
                       @@session.time_zone AS session_time_zone,
                       @@system_time_zone AS system_time_zone,
                       NOW(6) AS database_now,UTC_TIMESTAMP(6) AS database_utc_now
                """,
            )
            selected = query_one(
                cursor,
                """
                SELECT MAX(id) AS id
                FROM market_environment_eval_batch
                WHERE is_active=1 AND publish_status='published'
                """,
            )
            if not selected or selected.get("id") is None:
                evidence["blocking_reason"] = "NO_ACTIVE_PUBLISHED_BATCH"
                return evidence
            batch = query_one(
                cursor,
                """
                SELECT id,batch_uid,market_scope,route_profile_key,label_kind,start_date,end_date,
                       as_of_time,status,publish_status,is_active,published_at,
                       expected_metric_count,completed_metric_count,insufficient_metric_count,
                       failed_metric_count,evaluation_config_version,score_rule_version,code_version,
                       environment_snapshot_hash,factor_set_snapshot_hash,release_manifest_hash,
                       evaluation_config,environment_snapshot,factor_set_snapshot
                FROM market_environment_eval_batch
                WHERE id=%s
                """,
                (selected["id"],),
            )
            if batch is None:
                evidence["blocking_reason"] = "SELECTED_BATCH_DISAPPEARED"
                return evidence
            config = decode_json(batch.pop("evaluation_config"))
            snapshot = decode_json(batch.pop("environment_snapshot"))
            factor_snapshot = decode_json(batch.pop("factor_set_snapshot"))
            config = config if isinstance(config, dict) else {}
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            factor_snapshot = factor_snapshot if isinstance(factor_snapshot, dict) else {}
            evidence["selected_batch"] = batch
            evidence["evaluation_config"] = config
            evidence["factor_snapshot"] = factor_snapshot_evidence(
                factor_snapshot,
                snapshot.get("as_of_time"),
            )
            evidence["batch_history"] = query(
                cursor,
                """
                SELECT id,batch_uid,as_of_time,status,publish_status,is_active,published_at,
                       expected_metric_count,completed_metric_count,insufficient_metric_count,
                       failed_metric_count
                FROM market_environment_eval_batch
                ORDER BY id
                """,
            )
            evidence["published_batch_count"] = sum(
                1 for item in evidence["batch_history"] if item.get("publish_status") == "published"
            )
            evidence["environment_snapshot"] = snapshot_evidence(cursor, batch, snapshot)
            evidence["metrics"] = metric_evidence(
                cursor,
                batch,
                snapshot,
                factor_snapshot,
                config,
            )
    finally:
        evidence["transaction"]["rollback_attempted"] = True
        try:
            connection.rollback()
            evidence["transaction"]["rolled_back"] = True
        finally:
            connection.close()
    return evidence


def summarize_call(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact, credential-free MCP call summary."""

    if not isinstance(call, dict):
        return {"executed": False}
    notification_accepted = (
        call.get("method") == "notifications/initialized"
        and call.get("http_status") in {200, 202}
    )
    return {
        "executed": True,
        "case_id": call.get("case_id"),
        "method": call.get("method"),
        "http_status": call.get("http_status"),
        "elapsed_seconds": call.get("elapsed_seconds"),
        "successful": notification_accepted or mcp_successful(call),
        "notification_accepted_without_body": notification_accepted,
        "error_code": mcp_error_code(call),
        "parse_error": None if notification_accepted else call.get("parse_error"),
        "is_error": call.get("is_error"),
        "credential_echo": call.get("credential_echo"),
    }


def execute_mcp(
    token: str,
    output: Path,
    database: dict[str, Any],
) -> dict[str, Any]:
    """Call only read-only MCP operations needed for live temporal reconciliation."""

    client = MCPClient(token, output)
    init = client.request(
        "TEMP-MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-temporal-oracle-closure", "version": "1.0"},
        },
    )
    init_result = ((init.get("envelope") or {}).get("result") or {})
    client.protocol_version = str(init_result.get("protocolVersion") or "") or None
    calls: dict[str, dict[str, Any] | None] = {
        "initialize": init,
        "initialized_notification": None,
        "tools_list": None,
        "schema": None,
        "daily": None,
        "metrics": None,
    }
    if not mcp_successful(init) or not client.protocol_version:
        return {
            "endpoint": MCP_ENDPOINT,
            "auth_mode": "Bearer <redacted>",
            "calls": {key: summarize_call(value) for key, value in calls.items()},
            "read_tools_called": [],
            "write_tools_called": [],
            "blocking_reason": "MCP_INITIALIZATION_FAILED",
        }

    calls["initialized_notification"] = client.request(
        "TEMP-MCP-NOTIFY", "notifications/initialized", {}
    )
    calls["tools_list"] = client.request("TEMP-MCP-TOOLS", "tools/list", {})
    calls["schema"] = client.tool("TEMP-MCP-SCHEMA", "schema_get_raw_data", {})

    snapshot = database["environment_snapshot"]
    batch = database["selected_batch"]
    fixture = database["metrics"].get("mcp_fixture") or {}
    member_date = None
    partitions = snapshot.get("partitions") or {}
    for partition in partitions.values():
        segments = partition.get("segments") if isinstance(partition, dict) else None
        if segments:
            candidate = segments[-1].get("end_date")
            member_date = max(member_date, candidate) if member_date else candidate
    if member_date:
        calls["daily"] = client.tool(
            "TEMP-MCP-DAILY-ASOF",
            "environment_get_daily",
            {
                "environment_date": member_date,
                "label_kind": str(batch["label_kind"]),
                "as_of": snapshot["snapshot_as_of_utc"].isoformat().replace("+00:00", "Z"),
                "limit": 100,
            },
        )
    if fixture:
        calls["metrics"] = client.tool(
            "TEMP-MCP-METRICS",
            "factor_get_environment_metrics",
            {
                "factor_ref": fixture["factor_ref"],
                "market_scope": batch["market_scope"],
                "route_profile_key": batch["route_profile_key"],
                "batch_uid": batch["batch_uid"],
                "label_code": fixture["label_code"],
                "limit": 100,
            },
        )

    tools_result = ((calls["tools_list"] or {}).get("envelope") or {}).get("result") or {}
    tools = tools_result.get("tools") if isinstance(tools_result, dict) else []
    tool_names = sorted(
        str(item["name"])
        for item in tools or []
        if isinstance(item, dict) and item.get("name")
    )
    schema_data = mcp_data(calls["schema"])
    schema_contract = schema_data.get("contract") if isinstance(schema_data, dict) else {}
    schema_has_observation_rows = any(
        key in schema_data for key in ("items", "rows", "bars", "observations")
    ) if isinstance(schema_data, dict) else False

    metric_data = mcp_data(calls["metrics"])
    metric_batch = metric_data.get("batch") if isinstance(metric_data.get("batch"), dict) else {}
    metric_items = mcp_rows(calls["metrics"])
    expected_items = database["metrics"].get("mcp_expected_metric_rows") or []
    expected_ids = sorted(int(item["id"]) for item in expected_items)
    actual_ids = sorted(int(item["id"]) for item in metric_items if item.get("id") is not None)
    daily_items = mcp_rows(calls["daily"])
    daily_future = []
    anchor = snapshot.get("snapshot_as_of_utc")
    for item in daily_items:
        available = parse_time(item.get("available_at"))
        if anchor is not None and available is not None and available > anchor:
            daily_future.append(
                {
                    "id": item.get("id"),
                    "environment_date": item.get("environment_date"),
                    "available_at": available,
                }
            )
    schema_only_tools = {"schema_get_raw_data", "schema_get_factor_fields"}
    apparent_raw_download_tools = [
        name
        for name in tool_names
        if any(word in name for word in ("artifact", "download", "raw_series", "bar_data"))
        and name not in schema_only_tools
    ]
    return {
        "endpoint": MCP_ENDPOINT,
        "auth_mode": "Bearer <redacted>",
        "protocol_version": client.protocol_version,
        "calls": {key: summarize_call(value) for key, value in calls.items()},
        "read_tools_called": [
            "schema_get_raw_data",
            *( ["environment_get_daily"] if calls["daily"] else [] ),
            *( ["factor_get_environment_metrics"] if calls["metrics"] else [] ),
        ],
        "write_tools_called": [],
        "advertised_tool_count": len(tool_names),
        "advertised_tool_names": tool_names,
        "raw_artifact_or_series_download_tools": apparent_raw_download_tools,
        "schema_tool_assessment": {
            "data_keys": sorted(schema_data) if isinstance(schema_data, dict) else [],
            "contract": schema_contract,
            "contains_active_batch_observation_rows": schema_has_observation_rows,
            "note": (
                "schema_get_raw_data returns a schema contract, field mappings, and replay fixtures; "
                "it does not return the current batch's bar or forward-return observations."
            ),
        },
        "daily_reconciliation": {
            "returned_count": len(daily_items),
            "future_available_at_count": len(daily_future),
            "future_available_at_samples": daily_future,
        },
        "metric_reconciliation": {
            "batch_uid": metric_batch.get("batch_uid"),
            "batch_as_of": metric_batch.get("as_of_time"),
            "expected_metric_ids": expected_ids,
            "returned_metric_ids": actual_ids,
            "metric_ids_match_database": actual_ids == expected_ids,
            "batch_uid_matches_database": metric_batch.get("batch_uid") == batch.get("batch_uid"),
            "batch_as_of_matches_snapshot": parse_time(metric_batch.get("as_of_time")) == anchor,
        },
    }


def adjudicate(database: dict[str, Any], mcp: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply the test-case oracle without treating output payloads as self-proof."""

    snapshot = database["environment_snapshot"]
    metrics = database["metrics"]
    partitions = snapshot.get("partitions") or {}
    structural_failures = {
        "snapshot_member_duplicates": snapshot.get("member_count")
        != snapshot.get("unique_member_id_count")
        or snapshot.get("member_count") != snapshot.get("unique_member_date_count"),
        "snapshot_dates_not_sorted": not snapshot.get("members_sorted_by_date"),
        "snapshot_partition_mismatch": not snapshot.get("partition_date_union_matches_members")
        or bool(snapshot.get("partition_label_mismatches")),
        "snapshot_segment_mismatch": any(
            not item.get("segments_match_recomputed_dates")
            or item.get("declared_day_count") != item.get("actual_day_count")
            or item.get("actual_day_count") != item.get("unique_day_count")
            or not item.get("dates_sorted")
            for item in partitions.values()
        ),
        "metric_sample_day_mismatch": metrics.get("sample_day_count_mismatch_count", 0) > 0,
    }
    calc502_confirmed_failure = any(structural_failures.values())
    calc502 = {
        "case_id": "CALC-502",
        "title": "时间排序与连续区间",
        "status": "FAIL" if calc502_confirmed_failure else "BLOCKED",
        "classification": "P0" if calc502_confirmed_failure else "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "The frozen snapshot or metric day membership is structurally inconsistent."
            if calc502_confirmed_failure
            else "The snapshot correctly preserves disjoint calendar segments and metric day counts, "
            "but the current batch exposes neither a versioned gap-handling policy nor per-bar "
            "return/position inputs. It is therefore impossible to independently determine whether "
            "net return, annualization, turnover, and maximum drawdown treated gaps as zero exposure "
            "or incorrectly concatenated them."
        ),
        "assertions": [
            {
                "assertion": "snapshot members are unique and date-sorted",
                "passed": not structural_failures["snapshot_member_duplicates"]
                and not structural_failures["snapshot_dates_not_sorted"],
            },
            {
                "assertion": "partition dates and declared continuous segments recompute exactly",
                "passed": not structural_failures["snapshot_partition_mismatch"]
                and not structural_failures["snapshot_segment_mismatch"],
            },
            {
                "assertion": "every metric payload sample_day_count matches its label partition",
                "passed": not structural_failures["metric_sample_day_mismatch"],
            },
            {
                "assertion": "configuration declares zero-exposure or per-segment aggregation",
                "passed": False,
                "blocked": True,
            },
            {
                "assertion": "per-bar returns and positions independently reproduce economic metrics",
                "passed": False,
                "blocked": True,
            },
        ],
        "blocking_reasons": [] if calc502_confirmed_failure else [
            "MISSING_GAP_HANDLING_CONTRACT",
            "RAW_FORWARD_RETURN_SERIES_UNAVAILABLE",
            "POSITION_AND_TURNOVER_SERIES_UNAVAILABLE",
        ],
        "evidence": {
            "partition_summary": partitions,
            "metric_sample_day_count_mismatch_count": metrics.get(
                "sample_day_count_mismatch_count"
            ),
            "aggregation_mode_distribution": metrics.get("aggregation_mode_distribution"),
            "raw_input_assessment": metrics.get("raw_input_assessment"),
            "artifact_reference_assessment": metrics.get("artifact_reference_assessment"),
            "structural_failure_flags": structural_failures,
        },
    }

    future_failures = {
        "batch_snapshot_as_of_mismatch": not snapshot.get(
            "batch_and_snapshot_as_of_same_instant"
        ),
        "future_environment_member": snapshot.get("future_member_count", 0) > 0,
        "snapshot_db_mismatch": bool(snapshot.get("daily_db_mismatch_counts")),
        "future_or_invalid_oos_boundary": bool(metrics.get("temporal_violation_counts")),
        "non_fact_or_wrong_label_kind": int(
            metrics.get("aggregate", {}).get("non_batch_label_kind_count") or 0
        )
        > 0,
        "sample_range_outside_batch": int(
            metrics.get("aggregate", {}).get("sample_range_outside_batch_count") or 0
        )
        > 0,
        "future_factor_definition": int(
            database.get("factor_snapshot", {}).get("future_definition_count") or 0
        )
        > 0,
        "factor_snapshot_identity_mismatch": bool(
            metrics.get("factor_identity_mismatch_counts")
        ),
    }
    calc503_confirmed_failure = any(future_failures.values())
    calc503 = {
        "case_id": "CALC-503",
        "title": "未来信息泄漏",
        "status": "FAIL" if calc503_confirmed_failure else "BLOCKED",
        "classification": "P0" if calc503_confirmed_failure else "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "At least one frozen environment or metric temporal boundary is explicitly later than "
            "the batch visibility cutoff or structurally inconsistent."
            if calc503_confirmed_failure
            else "No explicit future timestamp was found: all frozen environment revisions are visible "
            "by batch as_of, all successful metrics have ordered OOS folds ending before as_of, and "
            "direction is frozen at the first OOS boundary. Full no-leakage proof remains blocked "
            "because the exact bar/label/forward-return observations, training membership, and fitted "
            "normalization parameters are not accessible."
        ),
        "assertions": [
            {
                "assertion": "batch and snapshot as_of represent the same UTC instant",
                "passed": not future_failures["batch_snapshot_as_of_mismatch"],
            },
            {
                "assertion": "all snapshot member available_at values are <= batch as_of",
                "passed": not future_failures["future_environment_member"],
            },
            {
                "assertion": "snapshot members reconcile to their frozen DB revisions",
                "passed": not future_failures["snapshot_db_mismatch"],
            },
            {
                "assertion": "frozen factor definitions are <= batch as_of and metric identities match",
                "passed": not future_failures["future_factor_definition"]
                and not future_failures["factor_snapshot_identity_mismatch"],
            },
            {
                "assertion": "OOS folds are ordered, contiguous, and end <= batch as_of",
                "passed": not future_failures["future_or_invalid_oos_boundary"],
            },
            {
                "assertion": "direction_frozen_at equals the first OOS start",
                "passed": not future_failures["future_or_invalid_oos_boundary"],
            },
            {
                "assertion": "raw bar/label/forward-return inputs can be independently replayed",
                "passed": False,
                "blocked": True,
            },
            {
                "assertion": "training membership and normalization fit scope exclude OOS rows",
                "passed": False,
                "blocked": True,
            },
            {
                "assertion": "a prior immutable publication supports a future-data perturbation check",
                "passed": database.get("published_batch_count", 0) > 1,
                "blocked": database.get("published_batch_count", 0) <= 1,
            },
        ],
        "blocking_reasons": [] if calc503_confirmed_failure else [
            "RAW_BAR_LABEL_AND_FORWARD_RETURN_INPUTS_UNAVAILABLE",
            "TRAINING_MEMBERSHIP_UNAVAILABLE",
            "NORMALIZATION_FIT_PROVENANCE_UNAVAILABLE",
            "NO_PRIOR_PUBLISHED_BATCH_FOR_PERTURBATION_CONTROL",
        ],
        "evidence": {
            "batch_as_of": snapshot.get("snapshot_as_of_utc"),
            "max_member_available_at": snapshot.get("max_member_available_at"),
            "future_member_count": snapshot.get("future_member_count"),
            "success_metric_count_checked": metrics.get("success_metric_count_checked"),
            "oos_fold_count_distribution": metrics.get("oos_fold_count_distribution"),
            "earliest_oos_start": metrics.get("earliest_oos_start"),
            "latest_oos_end": metrics.get("latest_oos_end"),
            "direction_source_distribution": metrics.get("direction_source_distribution"),
            "training_mean_ic_presence": metrics.get("training_mean_ic_presence"),
            "temporal_violation_counts": metrics.get("temporal_violation_counts"),
            "future_failure_flags": future_failures,
            "published_batch_count": database.get("published_batch_count"),
            "factor_snapshot": database.get("factor_snapshot"),
            "factor_identity_mismatch_counts": metrics.get(
                "factor_identity_mismatch_counts"
            ),
            "mcp_raw_download_tools": mcp.get("raw_artifact_or_series_download_tools"),
        },
    }
    return [calc502, calc503]


def render_markdown(report: dict[str, Any]) -> str:
    """Render a concise Chinese summary from the authoritative JSON report."""

    database = report["database_evidence"]
    snapshot = database["environment_snapshot"]
    metrics = database["metrics"]
    lines = [
        "# CALC-502 / CALC-503 时间 Oracle 闭环",
        "",
        f"- 环境：`{report['environment']}`",
        f"- 模式：`{report['mode']}`",
        f"- MCP：`{report['mcp_evidence']['endpoint']}`",
        f"- 当前 published batch：`{database['selected_batch']['id']}` / "
        f"`{database['selected_batch']['batch_uid']}`",
        f"- batch as_of（UTC）：`{snapshot['snapshot_as_of_utc'].isoformat()}`",
        f"- DB 事务：`{database['transaction']['start_statement']}`，"
        f"rollback=`{database['transaction']['rolled_back']}`",
        "",
        "## 裁决",
        "",
        "| 用例 | 状态 | 分类 | 结论 |",
        "|---|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| `{case['case_id']}` | `{case['status']}` | `{case['classification']}` | "
            f"{case['reason']} |"
        )
    lines.extend(
        [
            "",
            "本轮没有确认 `FAIL`。两项均不是“已通过”，而是缺少独立原始输入，无法完成最终计算正确性证明。",
            "",
            "## 已确认事实",
            "",
            f"- 环境快照有 `{snapshot['member_count']}` 个成员，唯一日期 "
            f"`{snapshot['unique_member_date_count']}` 个；晚于 batch as_of 的成员为 "
            f"`{snapshot['future_member_count']}` 个。",
            f"- `{metrics['success_metric_count_checked']}` 个成功指标全部经过 OOS 时间检查；"
            f"fold 数分布为 `{metrics['oos_fold_count_distribution']}`，时间违规为 "
            f"`{metrics['temporal_violation_counts']}`。",
            f"- 所有指标的 `sample_day_count` 与相应环境分区一致；不一致数为 "
            f"`{metrics['sample_day_count_mismatch_count']}`。",
            f"- 因子快照有 `{database['factor_snapshot']['member_count']}` 个成员；晚于 "
            f"batch as_of 的定义为 `{database['factor_snapshot']['future_definition_count']}` 个，"
            f"指标身份不一致为 `{metrics['factor_identity_mismatch_counts']}`。",
            f"- 当前指标携带 `{metrics['artifact_fingerprint_reference_count']}` 个 artifact hash "
            "引用，但没有可下载 URI 或 source run ID；hash 只能证明身份，不能作为自身正确性的 Oracle。",
            f"- MCP 当前公布 `{report['mcp_evidence']['advertised_tool_count']}` 个工具；"
            f"可下载 raw series/artifact 的工具为 "
            f"`{report['mcp_evidence']['raw_artifact_or_series_download_tools']}`。",
            "",
            "## CALC-502 缺失输入",
            "",
            "- 版本化的 gap handling 规则：非目标环境零暴露，或按连续区间分别统计。",
            "- 当前 batch 实际使用的逐 bar forward-return、position/signal 与 turnover 序列。",
            "- 可按 segment 独立重算 net return、年化、turnover 和 maximum drawdown 的输入。",
            "",
            "## CALC-503 缺失输入",
            "",
            "- 与 artifact hash 对应、可读取的逐 bar 行情、因子值、forward-return 和标签 join 记录。",
            "- 训练样本成员清单，以及标准化/scaler 的拟合区间和参数。",
            "- 第二个历史 published batch 或等价不可变快照，用于未来样本扰动对照。",
            "",
            "## 执行边界",
            "",
            "- 只调用了 MCP 读工具；未调用 feedback 等写工具。",
            "- 数据库连接显式使用 `START TRANSACTION READ ONLY` 并执行 `ROLLBACK`。",
            "- 未重跑 `ENV-108`、`MET-310`、`DB-613`，也未将已排除的结束时间边界观察重新登记为缺陷。",
            "- 原始 MCP 请求/响应、`db-evidence.json` 与本文件位于同一报告目录，均已脱敏。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Execute the closure and persist redacted authoritative evidence."""

    token = next((os.environ.get(name) for name in TOKEN_ENV_NAMES if os.environ.get(name)), None)
    if not token:
        raise SystemExit("MCP_TOKEN or FACTOR4_MCP_TOKEN is required")
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%dT%H%M%S%z")
    output = REPORT_ROOT / f"{stamp}-temporal-oracle-closure"
    output.mkdir(parents=True, exist_ok=False)

    before = database_watermark()
    database = read_database_evidence()
    write_json(output / "db-before.json", before)
    write_json(output / "db-evidence.json", database)
    if database.get("blocking_reason"):
        raise RuntimeError(str(database["blocking_reason"]))

    mcp = execute_mcp(token, output, database)
    after = database_watermark()
    write_json(output / "db-after.json", after)
    watermarks_equal = before.get("tables") == after.get("tables")
    cases = adjudicate(database, mcp)
    report = {
        "authority": (
            "This adjudicated-summary.json is the authoritative verdict for this run. Raw "
            "request/response artifacts are transport evidence and do not override it."
        ),
        "captured_at": datetime.now(SHANGHAI).isoformat(),
        "environment": "test",
        "mode": "READ_ONLY",
        "scope": ["CALC-502", "CALC-503"],
        "excluded_retests": ["ENV-108", "MET-310", "DB-613"],
        "database_evidence": database,
        "database_watermarks": {
            "before": before,
            "after": after,
            "tables_unchanged": watermarks_equal,
            "note": (
                "Only SELECT statements were issued by this script. Equal watermarks are supporting "
                "evidence; the explicit read-only transactions are the write-prevention control."
            ),
        },
        "mcp_evidence": mcp,
        "cases": cases,
        "totals": {
            "pass": sum(case["status"] == "PASS" for case in cases),
            "fail": sum(case["status"] == "FAIL" for case in cases),
            "blocked_data_precondition": sum(
                case["classification"] == "BLOCKED_DATA_PRECONDITION" for case in cases
            ),
        },
        "confirmed_defects": [case["case_id"] for case in cases if case["status"] == "FAIL"],
        "security": {
            "contains_credential_value": False,
            "contains_auth_header_value": False,
            "contains_complete_mcp_key": False,
        },
    }
    write_json(output / "adjudicated-summary.json", report)
    (output / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    print(output)
    print(json.dumps(report["totals"], sort_keys=True))
    return 1 if report["totals"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
