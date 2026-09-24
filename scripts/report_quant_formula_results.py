"""Reconcile explicitly chosen real JUnit runs against the current scenario registry.

Does not call the product, change fixtures, or infer that local references passed
on the server. Known bug titles are stable; unmapped failures remain untriaged.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from scripts.report_quant_formula_coverage import build_inventory


def reconcile_runs(root: Path, reports: list[Path]) -> dict[str, Any]:
    """Select latest timestamp per ready scenario from explicit integration reports.

    Missing files, invalid timestamps and equal-time conflicting outcomes fail
    rather than silently choosing a favorable result. Out-of-scope cases are
    reported separately, never added to controlled-input acceptance totals.
    """
    inventory = build_inventory(root)
    ready = {case["id"]: case for case in inventory["scenarios"] + inventory["implementation_profiles"]
             if case["automation_state"] == "source_adapter_ready"}
    latest: dict[str, dict[str, Any]] = {}
    completed: dict[str, dict[str, Any]] = {}
    completed_timestamps: dict[str, datetime] = {}
    timestamps: dict[str, datetime] = {}
    ignored: set[str] = set()
    natural: dict[str, dict[str, Any]] = {}
    current_overview: dict[str, dict[str, Any]] = {}
    current_overview_queries: dict[str, dict[str, Any]] = {}
    for path in reports:
        for suite in ET.parse(path).getroot().iter("testsuite"):
            stamp_text = suite.get("timestamp", "")
            stamp = datetime.fromisoformat(stamp_text)
            if stamp.tzinfo is None:
                raise ValueError(f"JUnit timestamp needs explicit timezone: {path}")
            for case in suite.findall("testcase"):
                name = case.get("name", "")
                identity = name.rsplit("[", 1)[-1].removesuffix("]")
                classname = case.get("classname", "")
                if "test_source_" not in classname:
                    ignored.add(f"{path.name}:{name}")
                    continue
                state = ("failed" if case.find("failure") is not None else
                         "error" if case.find("error") is not None else
                         "skipped" if case.find("skipped") is not None else "passed")
                try:
                    relative = str(path.resolve().relative_to(root.resolve()))
                except ValueError:
                    relative = str(path.resolve())
                module = classname.rsplit(".", 1)[-1]
                is_natural = module == "test_source_risk_statistics"
                is_current_overview = module == "test_source_current_overview"
                is_overview_query = module == "test_source_current_overview_queries"
                # The same metric is checked at account, portfolio and coverage
                # levels. Retain the function name to keep those checks distinct.
                if is_current_overview or is_overview_query:
                    identity = name
                row = {"id": identity, "result": state, "executed_at": stamp_text,
                       "junit_report": relative, "test_name": name}
                if is_current_overview or is_overview_query:
                    row["entrypoint"] = module + ".py"
                    skipped = case.find("skipped")
                    if skipped is not None:
                        reason = " ".join(filter(None, (skipped.get("message", ""), skipped.text or "")))
                        row["skip_reason"] = reason
                        row["sample_gap"] = "SAMPLE_GAP" in reason or "SOURCE_SUBTOTAL_UNAVAILABLE" in reason
                target = (current_overview_queries if is_overview_query else
                          current_overview if is_current_overview else natural if is_natural else latest)
                group = ("current_overview_query:" if is_overview_query else
                         "current_overview:" if is_current_overview else "natural:" if is_natural else "controlled:")
                time_key = group + identity
                is_controlled = not (is_natural or is_current_overview or is_overview_query)
                if is_controlled and identity not in ready:
                    ignored.add(f"{path.name}:{name}")
                    continue
                previous = timestamps.get(time_key)
                if previous is not None and stamp == previous and target[identity]["result"] != state:
                    raise ValueError(f"Conflicting same-time outcomes for {identity}")
                if previous is None or stamp > previous:
                    timestamps[time_key], target[identity] = stamp, row
                if is_controlled and state in {"passed", "failed"}:
                    if identity not in completed_timestamps or stamp > completed_timestamps[identity]:
                        completed_timestamps[identity], completed[identity] = stamp, row
    bugs = json.loads((root / "tests/data/strategy_dashboard/formula_bug_registry.json").read_text(encoding="utf-8"))
    mapped: set[str] = set()
    active = []
    for bug in bugs:
        matching = [identity for identity in bug["scenario_ids"]
                    if completed.get(identity, {}).get("result") == "failed"]
        if matching:
            mapped.update(matching)
            active.append({**bug, "failed_scenarios_in_selected_runs": matching,
                           "basis": "last_completed_business_assertion_not_latest_connectivity_attempt"})
    counts = Counter(row["result"] for row in latest.values())
    rows = [{"id": identity, "title": case["title"],
             **latest.get(identity, {"result": "not_run", "executed_at": None, "junit_report": None}),
             "last_completed_business_assertion": completed.get(identity)}
            for identity, case in sorted(ready.items())]
    return {
        "scope": inventory["scope"],
        "counting_rule": "Latest selected integration result per currently bound scenario; not a fresh execution.",
        "current_distinct_fixed_scenarios": inventory["all_distinct_fixed_input_scenarios"],
        "source_ready": len(ready), "executed_distinct": len(latest),
        "results": dict(sorted(counts.items())), "not_run": sorted(set(ready) - set(latest)),
        "last_completed_business_assertion_counts": dict(sorted(Counter(row["result"] for row in completed.values()).items())),
        "source_unbound": inventory["automation_states"].get("source_adapter_unbound", 0),
        "contract_pending": inventory["automation_states"].get("contract_pending", 0),
        "local_reference_only": inventory["implementation_profile_states"].get("local_reference_only", 0),
        "known_bug_matches": active,
        "untriaged_failures": sorted(identity for identity, row in completed.items()
                                     if row["result"] == "failed" and identity not in mapped),
        "controlled_input_results": rows,
        "natural_source_checks": [row for _, row in sorted(natural.items())],
        "current_overview_source_checks": [row for _, row in sorted(current_overview.items())],
        "current_overview_results": dict(sorted(Counter(row["result"] for row in current_overview.values()).items())),
        "current_overview_query_checks": [row for _, row in sorted(current_overview_queries.items())],
        "current_overview_query_results": dict(sorted(Counter(row["result"] for row in current_overview_queries.values()).items())),
        "current_overview_combined_results": dict(sorted(Counter(
            row["result"] for row in list(current_overview.values()) + list(current_overview_queries.values())
        ).items())),
        "current_overview_sample_gaps": [row for row in
                                         list(current_overview.values()) + list(current_overview_queries.values())
                                         if row.get("sample_gap")],
        "ignored_out_of_scope_results": sorted(ignored),
    }


def main() -> None:
    """Write a reproducible deduplicated report from user-selected JUnit files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = reconcile_runs(Path(__file__).resolve().parents[1], args.reports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {"controlled_input_results", "known_bug_matches", "natural_source_checks",
                                     "current_overview_source_checks", "current_overview_query_checks",
                                     "ignored_out_of_scope_results"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
