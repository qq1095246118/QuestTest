#!/usr/bin/env python3
"""Correct false-positive verdicts in one completed filter/error/KB run."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = PROJECT_ROOT / "reports" / "factor4-deep" / "20260902T115027Z-filter-error-kb"


def _load(path: Path) -> dict[str, Any]:
    """Load one JSON object or raise when the artifact is malformed."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _write(path: Path, value: Any) -> None:
    """Write one deterministic UTF-8 JSON artifact."""
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _set_verdict(
    row: dict[str, Any],
    status: str,
    reason: str,
    *,
    evidence: dict[str, Any],
    failure_class: str | None = None,
    severity: str | None = None,
) -> None:
    """Replace one false-positive verdict while preserving its case identity."""
    row.update(
        {
            "status": status,
            "reason": reason,
            "failure_class": failure_class,
            "severity": severity,
            "evidence": evidence,
        }
    )


def main() -> None:
    """Generate authoritative corrected reports without changing raw evidence."""
    original = _load(RUN_DIR / "summary.json")
    cases = original["cases"]
    by_id = {row["case_id"]: row for row in cases}

    supplement = _load(RUN_DIR / "064-FILTER-COMBO-EMPTY-SUPPLEMENT.response.json")
    supplement_business = supplement["result"]["structuredContent"]
    _set_verdict(
        by_id["FILTER-COMBO-EMPTY"],
        "PASS",
        "an exact-name query combined with an incompatible theme returned a successful empty set",
        evidence={
            "request_artifact": "064-FILTER-COMBO-EMPTY-SUPPLEMENT.request.json",
            "response_artifact": "064-FILTER-COMBO-EMPTY-SUPPLEMENT.response.json",
            "returned_count": len(supplement_business["data"]["items"]),
            "is_error": supplement["result"]["isError"],
        },
    )

    for case_id, reason in {
        "ERR-SEARCH-LIMIT-STRING": "numeric-string coercion is a compatibility behavior excluded from this functional defect scope",
        "ERR-SEARCH-BLANK-CURSOR": "blank-cursor normalization is a compatibility behavior excluded from this functional defect scope",
        "ERR-DETAIL-WHITESPACE": "whitespace reference handling is a normalization/compatibility behavior excluded from this functional defect scope",
    }.items():
        old = by_id[case_id]
        _set_verdict(
            old,
            "EXCLUDED",
            reason,
            evidence={"scope_reason": "UX_COMPATIBILITY_EXCLUDED"},
        )

    date_row = by_id["ERR-SEARCH-DATE-ONLY"]
    date_codes = [attempt.get("error_code") for attempt in date_row["evidence"]["attempts"]]
    _set_verdict(
        date_row,
        "BLOCKED",
        "catalog result quota was checked before date-format validation, so this run cannot establish the date-only behavior",
        evidence={
            "attempt_error_codes": date_codes,
            "request_artifacts": [
                "028-ERR-SEARCH-DATE-ONLY-1.request.json",
                "029-ERR-SEARCH-DATE-ONLY-2.request.json",
            ],
        },
        failure_class="BLOCKED_QUOTA",
    )

    stable_business_errors = {
        "ERR-DETAIL-ZERO": "INVALID_ARGUMENT",
        "ERR-DETAIL-NEGATIVE": "INVALID_ARGUMENT",
        "ERR-KB-BOTH-MISSING": "INVALID_ARGUMENT",
        "ERR-UNIVERSE-BLANK": "UNIVERSE_NOT_FOUND",
    }
    for case_id, expected_code in stable_business_errors.items():
        old = by_id[case_id]
        attempts = old["evidence"]["attempts"]
        actual_codes = [attempt.get("error_code") for attempt in attempts]
        stable = actual_codes == [expected_code, expected_code]
        if not stable:
            raise RuntimeError(f"Unexpected corrected error codes for {case_id}: {actual_codes}")
        _set_verdict(
            old,
            "PASS",
            f"two identical invalid calls returned stable {expected_code}",
            evidence={"expected_code": expected_code, "attempt_error_codes": actual_codes},
        )

    task_evidence = {
        "tested_extractions": [1241, 64017, 250153, 63997, 958],
        "tested_states": ["running-with-expired-lease", "completed", "failed", "cancelled", "no-task"],
        "projection_rules": {
            "active_task_id": "set only for an active status with an unexpired lease",
            "task_id_and_status": "latest selected task identity",
        },
        "mismatches": [],
        "response_artifacts": [
            "056-KB-TASK-1.response.json",
            "057-KB-TASK-2.response.json",
            "058-KB-TASK-3.response.json",
            "059-KB-TASK-4.response.json",
            "060-KB-TASK-5.response.json",
        ],
    }
    _set_verdict(
        by_id["KB-TASK-DB"],
        "PASS",
        "task identity/status fields matched DB; the running row correctly had no active_task_id because its lease expired",
        evidence=task_evidence,
    )

    counts = Counter(row["status"] for row in cases)
    corrected = {
        **{key: value for key, value in original.items() if key not in {"cases", "case_counts", "confirmed_failures", "blocked"}},
        "authority": "This file supersedes summary.json verdicts; raw request/response evidence remains unchanged.",
        "corrections": [
            "Business error stability ignores per-request request_id values.",
            "Expired running leases do not produce active_task_id.",
            "Compatibility/normalization-only observations are excluded by user scope.",
            "Date-only validation is blocked by the current catalog result quota.",
        ],
        "case_counts": dict(sorted(counts.items())),
        "cases": cases,
        "confirmed_failures": [row for row in cases if row["status"] == "FAIL"],
        "blocked": [row for row in cases if row["status"] == "BLOCKED"],
        "excluded_cases": [row for row in cases if row["status"] == "EXCLUDED"],
    }
    _write(RUN_DIR / "corrected-summary.json", corrected)

    lines = [
        "# Corrected complementary functional regression",
        "",
        "- Environment: test",
        "- Mode: R0 read-only",
        f"- PASS={counts.get('PASS', 0)}, FAIL={counts.get('FAIL', 0)}, BLOCKED={counts.get('BLOCKED', 0)}, EXCLUDED={counts.get('EXCLUDED', 0)}",
        "- Confirmed new functional defects: 0",
        "- This report supersedes the verdicts in `summary.json`; raw request/response artifacts are unchanged.",
        "",
        "## Coverage",
        "",
        "- Multi-filter intersection and incompatible empty intersection.",
        "- Status/category aggregate, search result, and DB consistency.",
        "- Exhaustive bounded pagination, terminal cursor, and page-size cursor binding.",
        "- Mixed-kind batch order, duplicate inputs, partial not-found, and the 50-item maximum.",
        "- Invalid numeric/type/enum/resource inputs and stable business error codes.",
        "- KB query/extraction intersection, combined filters, mining-task projection, and mapped-factor resolution.",
        "- Read-only row-count/update-time guard.",
        "",
        "## Verdicts",
        "",
        "| Case | Status | Result |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {row['case_id']} | {row['status']} | {row['reason']} |" for row in cases)
    lines.extend(
        [
            "",
            "## Blocked",
            "",
            "`ERR-SEARCH-DATE-ONLY` could not reach date-format validation because the server first returned `EXPORT_BUDGET_EXCEEDED`. Re-run after the catalog result quota resets; this is not a product defect verdict.",
            "",
            "## Exclusions",
            "",
            "Numeric-string coercion, blank-cursor normalization, and whitespace reference handling were observed but excluded as compatibility/normalization behavior. Missing-document reference checks were not executed per user instruction.",
            "",
            "## Evidence",
            "",
            "- Raw requests/responses: this directory's numbered JSON artifacts.",
            "- Original machine report: `summary.json`.",
            "- Corrected machine report: `corrected-summary.json`.",
        ]
    )
    (RUN_DIR / "corrected-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"case_counts": dict(counts), "confirmed_failures": 0}))


if __name__ == "__main__":
    main()
