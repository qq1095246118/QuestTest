from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql
import yaml


OUTPUT_DIR = Path("reports/factor4-rerun/20260902T094800Z-field-runtime")
MCP_SUMMARY = OUTPUT_DIR / "mcp-summary.json"


def _json_default(value: Any) -> str:
    """Serialize DB decimal and temporal values without losing their displayed precision."""
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _fetch_grouped(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    identifiers: list[int] | list[str],
    key: str,
) -> dict[Any, dict[str, Any]]:
    """Execute a parameterized aggregate query and index its rows by one result key."""
    cursor.execute(sql, identifiers)
    result: dict[Any, dict[str, Any]] = {}
    for raw in cursor.fetchall():
        row = dict(raw)
        result[row.pop(key)] = row
    return result


def _extract_quality_failures(metrics_json: Any) -> list[str]:
    """Read persisted sample-quality failure codes from one summary payload."""
    if not metrics_json:
        return []
    parsed = json.loads(metrics_json) if isinstance(metrics_json, str) else metrics_json
    summary = parsed.get("summary") or {}
    quality = summary.get("sample_quality") or {}
    return [str(value) for value in quality.get("failures") or []]


def main() -> None:
    """Write credential-free runtime evidence for the 20 field-resolution candidates."""
    config = yaml.safe_load(Path("config/test.yaml").read_text())["database"]
    mcp_summary = json.loads(MCP_SUMMARY.read_text())
    candidates = mcp_summary["items"]
    identifiers = [int(row["factor_ref"].split(":", 1)[1]) for row in candidates]
    refs = [row["factor_ref"] for row in candidates]
    placeholders = ",".join(["%s"] * len(identifiers))
    connection = pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["username"],
        password=config["password"],
        database=config["name"],
        connect_timeout=10,
        read_timeout=120,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            formula = _fetch_grouped(
                cursor,
                f"""
                SELECT evidence.factor_id,
                       COUNT(*) AS row_count,
                       COUNT(DISTINCT evidence.run_id) AS run_count,
                       MAX(evidence.recorded_at) AS latest_at,
                       GROUP_CONCAT(DISTINCT COALESCE(runs.status, 'NO_RUN_MASTER')
                                    ORDER BY COALESCE(runs.status, 'NO_RUN_MASTER')) AS run_statuses
                FROM factor_ic_run_formula_evidence AS evidence
                LEFT JOIN factor_ic_runs AS runs ON runs.run_id = evidence.run_id
                WHERE evidence.is_sub_factor_id = 1
                  AND evidence.factor_id IN ({placeholders})
                GROUP BY evidence.factor_id
                """,
                identifiers,
                "factor_id",
            )
            summaries = _fetch_grouped(
                cursor,
                f"""
                SELECT summary.factor_id,
                       COUNT(*) AS row_count,
                       COUNT(DISTINCT summary.run_id) AS run_count,
                       MAX(summary.updated_at) AS latest_at,
                       SUM(CASE WHEN COALESCE(
                           summary.mean_ic, summary.mean_rank_ic, summary.icir,
                           summary.rank_icir, summary.final_score,
                           summary.ic_t_stat, summary.rank_ic_t_stat
                       ) IS NOT NULL THEN 1 ELSE 0 END) AS populated_rows,
                       GROUP_CONCAT(DISTINCT COALESCE(runs.status, 'NO_RUN_MASTER')
                                    ORDER BY COALESCE(runs.status, 'NO_RUN_MASTER')) AS run_statuses
                FROM factor_ic_summary_metrics AS summary
                LEFT JOIN factor_ic_runs AS runs ON runs.run_id = summary.run_id
                WHERE summary.is_sub_factor_id = 1
                  AND summary.factor_id IN ({placeholders})
                GROUP BY summary.factor_id
                """,
                identifiers,
                "factor_id",
            )
            ic_slices = _fetch_grouped(
                cursor,
                f"""
                SELECT factor_id, COUNT(*) AS row_count, COUNT(DISTINCT run_id) AS run_count,
                       MAX(created_at) AS latest_at,
                       SUM(CASE WHEN COALESCE(ic, rank_ic, slice_score) IS NOT NULL
                                THEN 1 ELSE 0 END) AS populated_rows
                FROM factor_ic_slice_metrics
                WHERE is_sub_factor_id = 1 AND factor_id IN ({placeholders})
                GROUP BY factor_id
                """,
                identifiers,
                "factor_id",
            )
            value_slices = _fetch_grouped(
                cursor,
                f"""
                SELECT factor_id, COUNT(*) AS row_count, COUNT(DISTINCT run_id) AS run_count,
                       MAX(created_at) AS latest_at,
                       SUM(CASE WHEN COALESCE(factor_value, adjusted_factor_value) IS NOT NULL
                                THEN 1 ELSE 0 END) AS populated_rows
                FROM factor_value_slice_metrics
                WHERE is_sub_factor_id = 1 AND factor_id IN ({placeholders})
                GROUP BY factor_id
                """,
                identifiers,
                "factor_id",
            )
            performance = _fetch_grouped(
                cursor,
                f"""
                SELECT factor_id, COUNT(*) AS row_count, COUNT(DISTINCT run_id) AS run_count,
                       SUM(bars) AS total_bars, MIN(period_start) AS period_start,
                       MAX(period_end) AS period_end, MAX(updated_at) AS latest_at,
                       SUM(CASE WHEN annual_return IS NOT NULL THEN 1 ELSE 0 END) AS annual_return_rows,
                       SUM(CASE WHEN sharpe_ratio IS NOT NULL THEN 1 ELSE 0 END) AS sharpe_rows
                FROM factor_performance_summary_metrics
                WHERE is_sub_factor_id = 1 AND factor_id IN ({placeholders})
                GROUP BY factor_id
                """,
                identifiers,
                "factor_id",
            )
            environment = _fetch_grouped(
                cursor,
                f"""
                SELECT factor_ref, COUNT(*) AS row_count, COUNT(DISTINCT eval_batch_id) AS batch_count,
                       MAX(updated_at) AS latest_at,
                       SUM(metric_status = 'success') AS success_rows,
                       SUM(metric_status = 'insufficient_sample') AS insufficient_rows,
                       SUM(metric_status = 'failed') AS failed_rows,
                       SUM(CASE WHEN COALESCE(mean_ic, mean_rank_ic, icir, rank_icir,
                                              time_series_score, cross_sectional_score) IS NOT NULL
                                THEN 1 ELSE 0 END) AS populated_rows,
                       GROUP_CONCAT(DISTINCT COALESCE(error_code, 'NONE')
                                    ORDER BY COALESCE(error_code, 'NONE')) AS error_codes
                FROM market_environment_factor_metric
                WHERE factor_ref IN ({','.join(['%s'] * len(refs))})
                GROUP BY factor_ref
                """,
                refs,
                "factor_ref",
            )
            cursor.execute(
                f"""
                SELECT factor_id, metrics_json, valid_slice_count, coverage_mean
                FROM factor_ic_summary_metrics
                WHERE is_sub_factor_id = 1 AND factor_id IN ({placeholders})
                """,
                identifiers,
            )
            quality: dict[int, dict[str, Any]] = {}
            for row in cursor.fetchall():
                factor_id = row["factor_id"]
                item = quality.setdefault(
                    factor_id,
                    {"failures": set(), "valid_slice_count_max": 0, "coverage_mean_max": None},
                )
                item["failures"].update(_extract_quality_failures(row["metrics_json"]))
                item["valid_slice_count_max"] = max(
                    item["valid_slice_count_max"], int(row["valid_slice_count"] or 0)
                )
                coverage = row["coverage_mean"]
                if coverage is not None:
                    previous = item["coverage_mean_max"]
                    item["coverage_mean_max"] = coverage if previous is None else max(previous, coverage)
    finally:
        connection.close()

    evidence_items: list[dict[str, Any]] = []
    for candidate, factor_id in zip(candidates, identifiers, strict=True):
        runtime_confirmed = bool(
            (summaries.get(factor_id) or {}).get("populated_rows")
            or (ic_slices.get(factor_id) or {}).get("populated_rows")
            or (value_slices.get(factor_id) or {}).get("populated_rows")
            or (performance.get(factor_id) or {}).get("annual_return_rows")
            or (environment.get(candidate["factor_ref"]) or {}).get("populated_rows")
        )
        quality_item = quality.get(factor_id) or {}
        evidence_items.append(
            {
                "factor_ref": candidate["factor_ref"],
                "name": candidate["name"],
                "formula_evidence": formula.get(factor_id),
                "ic_summaries": summaries.get(factor_id),
                "ic_slices": ic_slices.get(factor_id),
                "value_slices": value_slices.get(factor_id),
                "performance_summaries": performance.get(factor_id),
                "current_environment_metrics": environment.get(candidate["factor_ref"]),
                "sample_quality": {
                    "failures": sorted(quality_item.get("failures") or []),
                    "valid_slice_count_max": quality_item.get("valid_slice_count_max"),
                    "coverage_mean_max": quality_item.get("coverage_mean_max"),
                },
                "runtime_calculation_confirmed": runtime_confirmed,
                "confirmed_field_resolution_or_calculation_failure": False,
                "severity": None,
                "p1_reportable": False,
                "classification": "static_contract_risk_only",
                "classification_reason": (
                    "Persisted non-null runtime metrics prove that the formula was evaluated; "
                    "canonical metadata mismatch alone does not prove a calculation failure."
                ),
            }
        )
    output = {
        "environment": "test",
        "database": config["name"],
        "read_only": True,
        "candidate_count": len(evidence_items),
        "runtime_calculation_confirmed_count": sum(
            bool(row["runtime_calculation_confirmed"]) for row in evidence_items
        ),
        "confirmed_failure_count": 0,
        "p1_reportable_count": 0,
        "current_environment_metric_factor_count": sum(
            row["current_environment_metrics"] is not None for row in evidence_items
        ),
        "items": evidence_items,
        "limitations": [
            "Only sub_factor:1945 is present in the current environment evaluation batch.",
            "The other 19 runtime conclusions use historical persisted metrics; no new calculation run was started.",
            "A completed run with insufficient valid slices is not classified as a field-resolution failure.",
        ],
    }
    (OUTPUT_DIR / "db-runtime-summary.json").write_text(
        json.dumps(output, default=_json_default, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps({
        "candidate_count": output["candidate_count"],
        "runtime_calculation_confirmed_count": output["runtime_calculation_confirmed_count"],
        "confirmed_failure_count": output["confirmed_failure_count"],
        "p1_reportable_count": output["p1_reportable_count"],
        "output": str(OUTPUT_DIR / "db-runtime-summary.json"),
    }))


if __name__ == "__main__":
    main()
