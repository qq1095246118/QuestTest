"""Resolve strategy-name overviews without collapsing exchange/account list rows.

The management list and the detail page have different identities: a list row
retains its exchange/account relationship, whereas a detail includes every
authorized relationship with the exact same strategy name. Account names are
never used to merge accounts. Runtime precedence for mixed states is not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def strategy_name(row: dict[str, Any]) -> str | None:
    """Return the exact displayed name, falling back to source name/code.

    Missing names return ``None``; prefix, case and whitespace normalization are
    intentionally not used to invent equivalence between different strategies.
    """
    for field in ("name", "strategy_name", "code"):
        value = row.get(field)
        if isinstance(value, str) and value:
            return value
    return None


@dataclass(frozen=True)
class StrategyOverviewScope:
    """Describe the full same-name population and all stable detail entry IDs."""

    name: str | None
    definition_ids: tuple[str, ...]
    account_ids: frozenset[str]
    account_ids_complete: bool
    runtime_statuses: frozenset[str]
    runtime_statuses_complete: bool


def strategy_overview_scopes(rows: list[dict[str, Any]]) -> tuple[StrategyOverviewScope, ...]:
    """Group complete, unfiltered list rows by exact strategy name.

    IDs remain detail entry points, including different IDs with the same name.
    Repeated relationships do not duplicate members. Nameless rows are isolated
    by definition ID, never grouped into one unnamed overview. Rows without a
    stable definition ID are ignored; callers retain their discovery diagnostics.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        ident = row.get("strategy_definition_id") or row.get("id")
        if ident is None:
            continue
        name = strategy_name(row)
        key = ("name", name) if name is not None else ("id", str(ident))
        grouped.setdefault(key, []).append(row)
    scopes = []
    for members in grouped.values():
        complete = all(
            isinstance(row.get("account_ids"), list)
            and all(value is not None for value in row["account_ids"])
            and (row.get("account_count") is None
                 or row["account_count"] == len({str(value) for value in row["account_ids"]}))
            for row in members
        )
        accounts = frozenset(
            str(value) for row in members
            for value in (row.get("account_ids") if isinstance(row.get("account_ids"), list) else [])
            if value is not None
        )
        scopes.append(StrategyOverviewScope(
            name=strategy_name(members[0]),
            definition_ids=tuple(dict.fromkeys(
                str(row.get("strategy_definition_id") or row["id"]) for row in members
            )),
            account_ids=accounts,
            account_ids_complete=complete,
            runtime_statuses=frozenset(str(row["runtime_status"]) for row in members
                                       if row.get("runtime_status") is not None),
            runtime_statuses_complete=all(row.get("runtime_status") is not None for row in members),
        ))
    return tuple(scopes)


def strategy_overview_differences(
    scope: StrategyOverviewScope, detail: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return result-level discrepancies for one unscoped overview response.

    Membership is the deduplicated union across exchanges, not a clicked row.
    Missing membership is a sample gap; mixed runtime states are an observation
    until an aggregation precedence is confirmed, not a single-row status bug.
    No monetary or risk-ratio aggregation formula is inferred by this comparison.
    """
    findings: list[dict[str, Any]] = []
    returned_name = strategy_name(detail)
    if scope.name is not None and returned_name is not None and returned_name != scope.name:
        findings.append({"kind": "BUG_CANDIDATE", "title": "策略详情加载了其他名称的策略",
                         "detail": f"expected name={scope.name!r}, actual={returned_name!r}"})
    ids = detail.get("account_ids")
    if not scope.account_ids_complete or not isinstance(ids, list):
        findings.append({"kind": "DATA_GAP", "title": "策略总览关联账号集合缺少对账依据",
                         "detail": "列表或详情未提供完整账号 ID 集合，不能当作空集合通过"})
    else:
        actual = [str(value) for value in ids]
        actual_set = set(actual)
        if actual_set != scope.account_ids:
            findings.append({"kind": "BUG_CANDIDATE", "title": "策略总览关联账号漏汇总或混入其他策略",
                             "detail": f"missing={sorted(scope.account_ids - actual_set)}, "
                                       f"unexpected={sorted(actual_set - scope.account_ids)}"})
        if len(actual) != len(actual_set):
            findings.append({"kind": "BUG_CANDIDATE", "title": "策略总览关联账号重复汇总",
                             "detail": f"account_ids={actual}"})
        count = detail.get("account_count")
        if count is not None and count != len(actual_set):
            findings.append({"kind": "BUG_CANDIDATE", "title": "策略详情关联账号数量与账号 ID 列表长度不一致",
                             "detail": f"account_count={count}, distinct account_ids={len(actual_set)}"})
    if len(scope.runtime_statuses) > 1:
        findings.append({"kind": "OBSERVATION", "title": "策略总览混合运行状态按聚合口径核对",
                         "detail": f"member states={sorted(scope.runtime_statuses)}, "
                                   f"overview={detail.get('runtime_status')}; 不与单行状态直接比较"})
    elif (scope.runtime_statuses_complete and len(scope.runtime_statuses) == 1
          and detail.get("runtime_status") is not None):
        expected = next(iter(scope.runtime_statuses))
        if detail["runtime_status"] != expected:
            findings.append({"kind": "BUG_CANDIDATE", "title": "策略总览与全部关联记录运行状态不一致",
                             "detail": f"all member states={expected}, overview={detail['runtime_status']}"})
    return findings
