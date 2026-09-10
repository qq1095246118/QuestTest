"""Factor 4.0 可重复只读验收：编排端点读取与 DB 最终结果对账。

没有 pytest、SQL、旧报告读取或 tmp 脚本依赖。所有不一致只返回结构化结果；
前置缺失和依赖阻断用明确异常表示，不把空样本或错误 envelope 算作成功。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from api.factor_data_mcp_api import MCPResponse
from api.factor4_read_api import CatalogFilter, Factor4ReadAPI, LabelKind
from db.factor4_read_repository import (CatalogSubset, DailyReadSnapshot, EnvironmentMetricSample,
                                       EnvironmentReadMatrixSnapshot, Factor4ReadRepository)

LABELS = ("UNILATERAL_UP", "CHOPPY_UP", "NARROW_RANGE", "WIDE_RANGE",
          "UNILATERAL_DOWN", "CHOPPY_DOWN")
_LOCAL = ZoneInfo("Asia/Shanghai")
_DEPENDENCY_CODES = frozenset({"DEPENDENCY_UNAVAILABLE", "EXPORT_BUDGET_EXCEEDED",
                             "RATE_LIMITED", "SERVICE_UNAVAILABLE", "QUERY_TIMEOUT"})
_METRIC_FIELDS = (
    "id", "eval_batch_id", "factor_ref", "factor_type", "factor_id", "factor_version",
    "market_scope", "label_kind", "label_code", "evaluation_type", "interval",
    "return_bar_interval", "forward_return_bars", "window_scope", "sample_start_date",
    "sample_end_date", "total_sample_count", "valid_sample_count", "coverage_rate",
    "mean_ic", "mean_rank_ic", "icir", "rank_icir", "t_stat", "oos_retention",
    "net_return", "sharpe", "max_drawdown", "turnover_rate", "time_series_score",
    "cross_sectional_score", "routing_score", "confidence", "metric_status", "is_valid",
    "scoring_version", "metric_payload", "error_code", "error_message",
)
_ROUTE_FIELDS = (
    "id", "eval_batch_id", "metric_id", "factor_ref", "factor_type", "factor_id",
    "factor_version", "market_scope", "label_kind", "label_code", "rank_no",
    "routing_score", "confidence", "time_series_score", "cross_sectional_score",
    "is_active", "is_eligible", "publication_uid", "publish_version", "score_rule_version",
    "evidence", "reject_reason_code",
)
_DAILY_FIELDS = (
    "id", "environment_date", "label_kind", "label_code", "label_status", "revision",
    "is_current", "available_at", "features", "probabilities", "confidence",
    "confidence_level", "effective_from", "effective_to", "model_version",
    "schema_version", "raw_payload",
    "created_at", "updated_at", "created_by", "updated_by", "request_id",
)
_CATALOG_FIELDS = ("id", "name", "cn_name", "serial_number", "data_source")
_JSON_FIELDS = frozenset({"metric_payload", "evidence", "features", "probabilities", "raw_payload"})
_DETAIL_IDENTITY_FIELDS = ("factor_ref", "kind", "id", "name", "cn_name", "serial_number")
_DETAIL_DEFINITION_COMMON_FIELDS = ("calc_logic", "formula_summary", "metadata", "params", "data_source_metadata")
_DETAIL_SHARED_FIELDS = (
    "factor_version", "formula_version", "source_detail_id", "formula_available", "data_source",
    "library_status", "library_coin_categories", "themes", "tags", "factor_bar_interval",
)


class ReadPrecondition(RuntimeError):
    """缺少自然样本或共享依赖；异常文字只由本地固定原因/白名单错误码构造。"""


class ReadContractError(RuntimeError):
    """合法请求未得到可验证业务响应；不附带远端响应正文或认证信息。"""


@dataclass(frozen=True)
class ReadCheck:
    """一项业务比较结果；issues 只存本地字段名、数字主键和固定错误码。"""

    checked_count: int
    issues: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolPage:
    """经 envelope 验证的业务页；正文和游标不出现在 repr 或报告中。"""

    data: dict[str, Any] = field(repr=False)
    meta: dict[str, Any] = field(repr=False)

    @property
    def items(self) -> tuple[dict[str, Any], ...]:
        """返回严格 object 数组；字段缺失或含非 object 项时抛安全契约错误。"""
        rows = self.data.get("items")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ReadContractError("business items must be an object array")
        return tuple(rows)


@dataclass(frozen=True)
class PageTraversal:
    """分页结果及终态；正文/游标不进入 repr，受限或阻塞不代表完整读取。"""

    rows: tuple[dict[str, Any], ...] = field(repr=False)
    pages: tuple[ToolPage, ...] = field(repr=False)
    issues: tuple[str, ...] = ()
    termination: str = "complete"
    blocked: tuple[str, ...] = ()


def read_tool_body(response: MCPResponse) -> dict[str, Any]:
    """返回业务对象；两种表示可解析时核对内容，text截断只用完整structured。

    不证明text表示完整，该兼容断言由独立deferred协议Case负责。
    缺少可用业务对象或可解析的双表示不一致时抛安全异常，不附带正文。
    """
    result = response.result
    if not isinstance(result, Mapping):
        raise ReadContractError("MCP result is not an object")
    content = result.get("content")
    if not isinstance(content, list):
        raise ReadContractError("MCP content must be an array")
    texts = [item.get("text") for item in content
             if isinstance(item, Mapping) and item.get("type") == "text"]
    if len(texts) != 1 or not isinstance(texts[0], str):
        raise ReadContractError("MCP must expose one JSON text representation")
    try:
        body = json.loads(texts[0])
    except (TypeError, ValueError):
        # Large read responses may truncate the human-readable text part while
        # structuredContent remains complete. Prefer it only when it is a mapping;
        # callers still validate the returned business shape below.
        structured = response.structured_content
        if isinstance(structured, dict):
            body = structured
        else:
            raise ReadContractError("MCP text is not complete JSON") from None
    if not isinstance(body, dict) or body != response.structured_content:
        raise ReadContractError("MCP content and structuredContent differ")
    return body


def read_tool_page(response: MCPResponse) -> ToolPage:
    """解析可用业务表示并校验业务错误后返回页；阻断/契约错误抛安全异常。"""
    body = read_tool_body(response)
    error = body.get("error")
    if response.is_tool_error or error is not None:
        code = error.get("code") if isinstance(error, Mapping) else None
        if response.is_tool_error and isinstance(code, str) and code in _DEPENDENCY_CODES:
            raise ReadPrecondition(f"BLOCKED_DEPENDENCY: {code}")
        safe_code = code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code) else "UNKNOWN"
        raise ReadContractError(f"legal MCP request returned a business error: {safe_code}")
    if not isinstance(body.get("data"), dict) or not isinstance(body.get("meta"), dict):
        raise ReadContractError("MCP success requires data and meta objects")
    return ToolPage(body["data"], body["meta"])


def compare_rows(
    actual: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]],
    fields: Sequence[str], *, daily_timestamps: bool = False,
) -> ReadCheck:
    """按整数 id 对账集合/指定字段，保留 null；返回差异字段，不输出远端值，不执行 I/O。"""
    issues: list[str] = []
    def indexed(rows: Sequence[Mapping[str, Any]], side: str) -> dict[int, Mapping[str, Any]]:
        output: dict[int, Mapping[str, Any]] = {}
        for row in rows:
            identifier = row.get("id")
            if isinstance(identifier, bool) or not isinstance(identifier, int):
                issues.append(f"{side}:invalid_id")
            elif identifier in output:
                issues.append(f"{side}:duplicate_id={identifier}")
            else:
                output[identifier] = row
        return output
    left, right = indexed(actual, "api"), indexed(expected, "db")
    for identifier in sorted(set(right) - set(left)):
        issues.append(f"missing_id={identifier}")
    for identifier in sorted(set(left) - set(right)):
        issues.append(f"unexpected_id={identifier}")
    for identifier in sorted(set(left) & set(right)):
        for name in fields:
            if name not in left[identifier]:
                issues.append(f"id={identifier}:missing_field={name}")
                continue
            try:
                av, ev = left[identifier][name], right[identifier].get(name)
                if daily_timestamps and name in {"available_at", "effective_from", "effective_to", "created_at", "updated_at"}:
                    a = _time(av) if av is not None else None
                    b = _time(ev, db_local=True) if ev is not None else None
                else:
                    a, b = _value(av, name), _value(ev, name)
                if a != b:
                    issues.append(f"id={identifier}:field={name}")
            except (TypeError, ValueError, ArithmeticError):
                issues.append(f"id={identifier}:unparseable_field={name}")
    return ReadCheck(len(expected), tuple(issues), {"expected_count": len(expected), "actual_count": len(actual)})


def visible_daily_rows(
    snapshot: DailyReadSnapshot, label_kind: LabelKind, *, as_of: datetime | None = None,
    environment_date: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """用全部 DB 修订独立选取 available_at<=as_of 的最高 revision；无样本返回空 tuple。"""
    selected: dict[str, dict[str, Any]] = {}
    for row in snapshot.rows:
        day = str(row["environment_date"])
        if row["label_kind"] != label_kind or (environment_date is not None and day != environment_date):
            continue
        if _time(row["available_at"], db_local=True) > (as_of or snapshot.as_of):
            continue
        prior = selected.get(day)
        if prior is None or (row["revision"], row["id"]) > (prior["revision"], prior["id"]):
            selected[day] = row
    return tuple(sorted(selected.values(), key=lambda row: (str(row["environment_date"]), row["id"]), reverse=True))


class Factor4ReadService:
    """复用一次受门禁保护的 MCP 会话；每次公开检查只执行所需业务动作。"""

    def __init__(self, api: Factor4ReadAPI) -> None:
        """输入只读语义 API；不自动请求、不读取旧报告、不启动计算。"""
        self.api = api

    def catalog_pages(self, subset: CatalogSubset, *, updated_after: datetime | None = None) -> PageTraversal:
        """读取目录分区及可选更新时间筛选，返回已读页和预算/阻塞终态；契约错误透传。"""
        filters = CatalogFilter(subset.kind, subset.status, subset.category)
        return self._traverse(lambda cursor: self.api.search_catalog(
            filters, limit=3, cursor=cursor,
            updated_after=updated_after.isoformat() if updated_after else None,
        ), page_size=3, max_pages=len(subset.rows) // 3 + 3, catalog_budget=True)

    def catalog_query(self, subset: CatalogSubset, query: str) -> PageTraversal:
        """Search one discovered catalog partition by exact query text and paginate it.

        Blank query is rejected before I/O; returned pages retain the same status/category
        filters as the discovered partition. Declared budgets stop the chain; dependency
        blocks retain earlier pages for validation. Contract errors propagate.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        filters = CatalogFilter(subset.kind, subset.status, subset.category)
        return self._traverse(lambda cursor: self.api.search_catalog(
            filters, limit=3, cursor=cursor, query=query,
        ), page_size=3, max_pages=len(subset.rows) // 3 + 3, catalog_budget=True)

    def check_catalog_members(self, subset: CatalogSubset, traversal: PageTraversal) -> ReadCheck:
        """核对目录页的实体和筛选，只有自然结束核对完整集合；返回差异及覆盖范围。"""
        return self._check_catalog_selection(subset, traversal, subset.rows)

    def _check_catalog_selection(
        self, subset: CatalogSubset, traversal: PageTraversal, expected: Sequence[Mapping[str, Any]],
    ) -> ReadCheck:
        actual_ids = {row["id"] for row in traversal.rows
                      if isinstance(row.get("id"), int) and not isinstance(row["id"], bool)}
        complete = traversal.termination == "complete"
        compared = expected if complete else [row for row in expected if row["id"] in actual_ids]
        result = compare_rows(traversal.rows, compared, _CATALOG_FIELDS)
        issues = [*traversal.issues, *result.issues]
        for row in traversal.rows:
            if row.get("factor_ref") != f"{subset.kind}:{row.get('id')}" or row.get("kind") != subset.kind:
                issues.append("catalog:factor_identity")
            if row.get("library_status") != subset.status:
                issues.append("catalog:status_filter")
            if subset.category not in (row.get("library_coin_categories") or []):
                issues.append("catalog:category_filter")
        full_membership = complete and not any(
            issue.startswith(("missing_id=", "unexpected_id=", "api:invalid_id", "api:duplicate_id="))
            for issue in result.issues
        )
        evidence = {
            **result.evidence, "blocked": traversal.blocked,
            "catalog_traversal": traversal.termination, "catalog_page_count": len(traversal.pages),
            "catalog_returned_count": len(traversal.rows), "catalog_returned_unique_count": len(actual_ids),
            "catalog_database_unique_count": len({row["id"] for row in expected}),
            "catalog_full_membership_verified": full_membership,
            "catalog_completeness": "verified" if full_membership else "not_verified",
            "catalog_budget_code": "CATALOG_CURSOR_BUDGET_REACHED" if traversal.termination == "bounded" else None,
        }
        return ReadCheck(max(1, result.checked_count), tuple(dict.fromkeys(issues)), evidence)

    def check_catalog_stats(self, subset: CatalogSubset) -> ReadCheck:
        """读取同一目录能力的统计并核对 total/group 自洽；统计口径由服务返回值决定。

        ``factor_catalog_stats`` 的契约统计的是完成指标的 distinct factor_ref，
        而不是 ``factor_search`` 的 library status 实体数；因此这里不把两个不同
        口径错误相减，避免临时脚本中的错误前提进入正式 Case。
        """
        page = read_tool_page(self.api.catalog_stats(CatalogFilter(subset.kind, subset.status, subset.category)))
        total = page.data.get("total")
        groups = page.data.get("groups")
        issues: list[str] = []
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            issues.append("catalog:stats_total_type")
        if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
            issues.append("catalog:stats_groups_type")
        elif any(isinstance(group.get("count"), bool) or not isinstance(group.get("count"), int)
                 or group["count"] < 0 for group in groups):
            issues.append("catalog:stats_group_count_type")
        elif sum(group["count"] for group in groups) != total:
            issues.append("catalog:stats_group_sum")
        return ReadCheck(max(1, len(subset.rows)), tuple(issues))

    def check_catalog_replay(self, subset: CatalogSubset, initial: PageTraversal) -> ReadCheck:
        """重读目录核对已返回前缀；只有两次自然完结证明全量稳定，异常按分页契约处理。"""
        repeated = self.catalog_pages(subset)
        first = self.check_catalog_members(subset, initial)
        second = self.check_catalog_members(subset, repeated)
        issues = [*first.issues, *second.issues]
        full_replay = initial.termination == repeated.termination == "complete"
        length = min(len(initial.rows), len(repeated.rows))
        if (full_replay and repeated.rows != initial.rows
                or not full_replay and repeated.rows[:length] != initial.rows[:length]):
            issues.append("catalog:repeat_read_changed")
        evidence = {**second.evidence, "blocked": (*initial.blocked, *repeated.blocked),
                    "catalog_initial_traversal": initial.termination,
                    "catalog_initial_returned_count": len(initial.rows),
                    "catalog_repeat_full_verified": full_replay and not issues}
        if not full_replay:
            terminal = ("invalid" if "invalid" in {initial.termination, repeated.termination}
                        else "blocked" if evidence["blocked"] else "bounded")
            evidence.update(catalog_traversal=terminal, catalog_full_membership_verified=False,
                            catalog_completeness="not_verified",
                            catalog_budget_code=first.evidence["catalog_budget_code"] or second.evidence["catalog_budget_code"])
        return ReadCheck(max(first.checked_count, second.checked_count), tuple(dict.fromkeys(issues)), evidence)

    def check_catalog_updated_boundary(self, subset: CatalogSubset, offset: int) -> ReadCheck:
        """对动态 updated_at 的前/等/后三个秒级点验证严格大于筛选；offset 仅允许 -1/0/1。"""
        if offset not in {-1, 0, 1}:
            raise ValueError("updated boundary offset must be -1, 0 or 1")
        times = [_time(row["updated_at"], db_local=True) for row in subset.rows if row.get("updated_at")]
        if not times:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: catalog updated_at missing")
        boundary = sorted(times)[len(times) // 2] + timedelta(seconds=offset)
        expected = [row for row in subset.rows if row.get("updated_at") and _time(row["updated_at"], db_local=True) > boundary]
        actual = self.catalog_pages(subset, updated_after=boundary)
        check = self._check_catalog_selection(subset, actual, expected)
        return ReadCheck(check.checked_count, check.issues, {**check.evidence, "offset_seconds": offset})

    def check_details_batch(self, subset: CatalogSubset, detail_level: str) -> ReadCheck:
        """Compare required identity and level-specific common single/batch fields.

        Identity and the established executable fields are required in both responses.
        Other common definition fields are compared when either endpoint exposes them,
        without inventing mandatory optional fields. Nested children/relations and
        unspecified derived fields are excluded. Invalid levels raise ValueError;
        API/contract errors propagate.
        """
        if detail_level not in {"summary", "definition", "executable"}:
            raise ValueError("unsupported detail level")
        refs = [f"{subset.kind}:{row['id']}" for row in subset.rows[:2]]
        batch = read_tool_page(self.api.mcp.get_factor_details_batch(refs, detail_level=detail_level))
        issues: list[str] = []
        indexed = {item.get("factor_ref"): item for item in batch.items}
        if set(indexed) != set(refs) or len(batch.items) != len(refs):
            issues.append("detail:batch_membership")
        for ref, row in zip(refs, subset.rows):
            single = read_tool_page(self.api.mcp.get_factor_detail(ref, detail_level=detail_level))
            wrapper = indexed.get(ref, {})
            batch_data = wrapper.get("data")
            if wrapper.get("success") is not True or not isinstance(batch_data, Mapping):
                issues.append("detail:single_batch_mismatch")
                continue
            required = (*_DETAIL_IDENTITY_FIELDS,
                        *(("calc_logic", "calc_function", "formula_summary", "formula_available")
                          if detail_level == "executable" else ()))
            common_fields = (*_DETAIL_SHARED_FIELDS,
                             *(_DETAIL_DEFINITION_COMMON_FIELDS if detail_level != "summary" else ()))
            fields = set(required) | (set(common_fields) & (single.data.keys() | batch_data.keys()))
            # Extra batch fields remain comparable when single also exposes them;
            # endpoint-specific additions are not invented as required projections.
            fields.update(single.data.keys() & batch_data.keys() - {
                "children", "relations", "children_next_cursor", "children_truncated",
            })
            for field in sorted(fields):
                if field not in single.data or field not in batch_data:
                    issues.append(f"detail:{detail_level}_missing={field}")
                elif _value(single.data[field], field) != _value(batch_data[field], field):
                    issues.append(f"detail:{detail_level}_field={field}")
            for data in (single.data, batch_data):
                if data.get("factor_ref") != ref or data.get("kind") != subset.kind or data.get("id") != row["id"]:
                    issues.append("detail:factor_identity")
                for field in ("name", "cn_name", "serial_number"):
                    if field in row and (field not in data or _value(data[field]) != _value(row[field])):
                        issues.append(f"detail:database_field={field}")
        return ReadCheck(len(refs), tuple(dict.fromkeys(issues)))

    def check_catalog_query(self, subset: CatalogSubset, query: str, traversal: PageTraversal) -> ReadCheck:
        """核对非空 query 的发现实体及原筛选；预算只限制集合完整性，空 query 抛 ValueError。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        expected = [row for row in subset.rows if query in {row.get("name"), row.get("cn_name")}]
        return self._check_catalog_selection(subset, traversal, expected)

    def daily_pages(
        self, snapshot: DailyReadSnapshot, label_kind: LabelKind, *,
        as_of: datetime | None = None, environment_date: str | None = None, page_size: int = 100,
    ) -> PageTraversal:
        """按固定时点有界完整遍历环境；返回分页证据，达到安全页数上限时明确阻断。"""
        expected = visible_daily_rows(snapshot, label_kind, as_of=as_of, environment_date=environment_date)
        return self._traverse(lambda cursor: self.api.daily(
            label_kind, as_of=(as_of or snapshot.as_of).isoformat(), limit=page_size,
            environment_date=environment_date, cursor=cursor,
        ), page_size=page_size, max_pages=min(max(len(expected) + 1, 3), 1000))

    def check_daily(
        self, snapshot: DailyReadSnapshot, label_kind: LabelKind, traversal: PageTraversal, *,
        as_of: datetime | None = None, environment_date: str | None = None,
    ) -> ReadCheck:
        """对账最高可见修订、字段、时区与排序；返回结果，不要求日历连续或虚构上游分类。"""
        expected = visible_daily_rows(snapshot, label_kind, as_of=as_of, environment_date=environment_date)
        check = compare_rows(traversal.rows, expected, _DAILY_FIELDS, daily_timestamps=True)
        issues = [*check.issues, *traversal.issues]
        if [row.get("id") for row in traversal.rows] != [row["id"] for row in expected]:
            issues.append("daily:revision_order")
        for row in traversal.rows:
            if row.get("label_status") == "ready" and row.get("label_code") not in LABELS:
                issues.append("daily:ready_unknown_label")
            try:
                if _time(row.get("available_at")) > (as_of or snapshot.as_of):
                    issues.append("daily:future_revision_leak")
            except (TypeError, ValueError):
                issues.append("daily:available_at_not_timezone_aware")
        return ReadCheck(max(1, check.checked_count), tuple(issues), check.evidence)

    def check_daily_visibility_horizon(self, snapshot: DailyReadSnapshot, label_kind: LabelKind, horizon: str) -> ReadCheck:
        """Verify legal before-history/future as-of against persisted revisions, not wall-clock guesses.

        Uses one dynamically selected date to keep reads bounded. Missing rows raise
        ReadPrecondition; unsupported horizon raises ValueError; API errors propagate.
        """
        candidates = [row for row in snapshot.rows if row.get("label_kind") == label_kind and row.get("available_at")]
        if not candidates:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no dated daily revision")
        if horizon == "before-history":
            seed = min(candidates, key=lambda row: _time(row["available_at"], db_local=True))
            as_of = _time(seed["available_at"], db_local=True) - timedelta(microseconds=1)
        elif horizon == "future":
            seed = max(candidates, key=lambda row: _time(row["available_at"], db_local=True))
            as_of = max(snapshot.as_of, _time(seed["available_at"], db_local=True)) + timedelta(days=3650)
        else:
            raise ValueError("unknown visibility horizon")
        day = str(seed["environment_date"])
        traversal = self.daily_pages(snapshot, label_kind, as_of=as_of, environment_date=day)
        return self.check_daily(snapshot, label_kind, traversal, as_of=as_of, environment_date=day)

    def check_current_uniqueness(self, snapshot: DailyReadSnapshot, label_kind: LabelKind) -> ReadCheck:
        """检查 DB 同日同 kind current 指针不重复；没有该类数据则明确数据阻断。"""
        counts: dict[str, int] = defaultdict(int)
        revisions: dict[tuple[str, int], int] = defaultdict(int)
        for row in snapshot.rows:
            if row["label_kind"] == label_kind:
                counts[str(row["environment_date"])] += int(row["is_current"])
                revisions[(str(row["environment_date"]), row["revision"])] += 1
        if not counts:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: daily label kind missing")
        issues = ["daily:multiple_current" for count in counts.values() if count > 1]
        issues.extend("daily:duplicate_revision" for count in revisions.values() if count > 1)
        return ReadCheck(len(counts), tuple(issues))

    def check_revision_boundary(self, snapshot: DailyReadSnapshot, label_kind: LabelKind, offset: int) -> ReadCheck:
        """动态寻找同日期多修订，验证 available_at 前/等/后 1 微秒；缺自然样本则数据阻断。"""
        if offset not in {-1, 0, 1}:
            raise ValueError("revision offset must be -1, 0 or 1")
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in snapshot.rows:
            if row["label_kind"] == label_kind:
                groups[str(row["environment_date"])].append(row)
        for day, rows in groups.items():
            rows.sort(key=lambda row: row["revision"])
            if len({row["revision"] for row in rows}) < 2:
                continue
            newest = _time(rows[-1]["available_at"], db_local=True)
            if not any(_time(row["available_at"], db_local=True) < newest for row in rows[:-1]):
                continue
            as_of = newest + timedelta(microseconds=offset)
            traversal = self.daily_pages(snapshot, label_kind, as_of=as_of, environment_date=day)
            return self.check_daily(snapshot, label_kind, traversal, as_of=as_of, environment_date=day)
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: multiple distinct available_at revisions missing")

    def check_environment_metrics(
        self, sample: EnvironmentMetricSample, *, evaluation_type: str | None = None,
        label_code: str | None = None, auto_batch: bool = False,
    ) -> ReadCheck:
        """按动态因子/批次读取最终环境指标，逐字段核对 TS/CS/六标签筛选；不声称独立计算通过。"""
        batch = sample.batch
        page = read_tool_page(self.api.environment_metrics(
            sample.factor_ref, batch["market_scope"], batch["route_profile_key"],
            batch_uid=None if auto_batch else batch["batch_uid"],
            evaluation_type=evaluation_type, label_code=label_code, limit=1000,
        ))
        expected = [row for row in sample.metrics
                    if (evaluation_type is None or row["evaluation_type"] == evaluation_type)
                    and (label_code is None or row["label_code"] == label_code)]
        check = compare_rows(page.items, expected, _METRIC_FIELDS)
        issues = list(check.issues)
        returned_batch = page.data.get("batch")
        if not isinstance(returned_batch, dict) or returned_batch.get("id") != batch["id"] or returned_batch.get("batch_uid") != batch["batch_uid"]:
            issues.append("metric:batch_identity")
        issues.extend(_environment_publication_mismatches(returned_batch, batch, "metric"))
        if page.data.get("factor_ref") != sample.factor_ref:
            issues.append("metric:factor_identity")
        if page.data.get("returned_count") != len(page.items):
            issues.append("metric:returned_count")
        if page.meta.get("truncated") is True or page.meta.get("next_cursor"):
            issues.append("metric:unexpected_partial_result")
        return ReadCheck(max(1, check.checked_count), tuple(issues), {**check.evidence, "batch_id": batch["id"]})

    def check_environment_tags(self, sample: EnvironmentMetricSample) -> ReadCheck:
        """读取标签并与当前批次 active 路由逐项对账，包括精确 metric 外键和分数；异常透传。"""
        batch = sample.batch
        page = read_tool_page(self.api.environment_tags(sample.factor_ref, batch["market_scope"], batch["route_profile_key"]))
        check = compare_rows(page.items, sample.routes, _ROUTE_FIELDS)
        issues = list(check.issues)
        publication = page.data.get("publication")
        if not isinstance(publication, dict) or publication.get("publication_uid") != batch["publication_uid"]:
            issues.append("tags:publication_identity")
        issues.extend(_environment_publication_mismatches(publication, batch, "tags"))
        if page.data.get("factor_ref") != sample.factor_ref or page.data.get("returned_count") != len(page.items):
            issues.append("tags:identity_or_returned_count")
        return ReadCheck(max(1, check.checked_count), tuple(issues), check.evidence)

    def check_environment_output_matrix(
        self, snapshot: EnvironmentReadMatrixSnapshot, repository: Factor4ReadRepository, *,
        mode: str, evaluation_type: str | None = None, label_code: str | None = None,
    ) -> ReadCheck:
        """Run representative MCP result/output checks in every active market/profile.

        Modes dimensions/labels/implicit/tags extend existing Cases, not a second
        overlapping suite. Each partition and parent/subfactor kind gets positive
        label/scope representatives and a real empty-output sample where available.
        Missing natural shapes are returned in blocked evidence after all detectable
        failures. The endpoints have no as_of selector: captured DB publication
        identity and before/after pointers guard implicit/tags reads from drift.
        Invalid selector combinations raise ValueError; transport exceptions propagate.
        """
        if mode not in {"dimensions", "labels", "implicit", "tags"}:
            raise ValueError("unsupported environment output matrix mode")
        if evaluation_type not in {None, "time_series", "cross_sectional"} or label_code not in {None, *LABELS}:
            raise ValueError("unsupported environment matrix filter")
        if mode == "labels" and label_code is None:
            raise ValueError("label matrix requires a label")
        blocked: list[str] = []
        issues: list[str] = []
        if not snapshot.batches:
            return ReadCheck(0, (), {"blocked": ["no_active_publication"], "request_count": 0})
        guarded = mode in {"implicit", "tags"}
        expected_pointers = _environment_partition_fingerprints(snapshot.batches)
        changed_partitions: set[tuple[Any, Any]] = set()
        if guarded:
            current_pointers = _environment_partition_fingerprints(repository.active_environment_publications())
            changed_partitions.update(key for key in expected_pointers if current_pointers.get(key) != expected_pointers[key])
        requests: dict[tuple[Any, ...], tuple[EnvironmentMetricSample, str | None, str | None]] = {}
        coverage: list[str] = []
        partitions = [(batch["market_scope"], batch["route_profile_key"]) for batch in snapshot.batches]
        if len(partitions) != len(set(partitions)):
            issues.append("environment_matrix:duplicate_active_partition")
        for batch in snapshot.batches:
            prefix = f"batch={batch['id']}"
            # Incremental per-factor publication may be active while the overall
            # batch is still running; only publication state is required here.
            if batch.get("publish_status") != "published":
                issues.append(prefix + ":active_publication_not_published")
            if (batch["market_scope"], batch["route_profile_key"]) in changed_partitions:
                blocked.append(prefix + ":SNAPSHOT_DRIFT: active publication changed before matrix")
                continue
            for kind in ("factor", "sub_factor"):
                samples = [sample for sample in snapshot.samples if sample.batch["id"] == batch["id"]
                           and sample.factor_ref.startswith(kind + ":")]
                selected_labels = (label_code,) if mode == "labels" else LABELS
                scopes = (evaluation_type,) if evaluation_type else ("time_series", "cross_sectional")
                cells = [(label, scope) for label in selected_labels for scope in scopes] if mode != "tags" else [(label, None) for label in LABELS]
                for label, scope in cells:
                    def contains(sample: EnvironmentMetricSample) -> bool:
                        rows = sample.routes if mode == "tags" else sample.metrics
                        return any(row.get("label_code") == label and (mode == "tags" or row.get("evaluation_type") == scope) for row in rows)
                    positive = next((sample for sample in samples if contains(sample)), None)
                    cell = f"{prefix}:{kind}:{label}:{scope or 'route'}"
                    if positive is None:
                        blocked.append(cell + ":positive_shape_missing")
                    else:
                        coverage.append(cell)
                        requested_scope = scope if mode == "labels" else evaluation_type
                        requested_label = label if mode == "labels" else None
                        key = (batch["id"], positive.factor_ref, requested_scope, requested_label)
                        requests[key] = (positive, requested_scope, requested_label)
                # A real existing factor with no result exercises a successful empty
                # response. No invented ID or absent label is called a positive shape.
                empty = next((sample for sample in samples if not (sample.routes if mode == "tags" else sample.metrics)), None)
                if empty is None:
                    blocked.append(f"{prefix}:{kind}:empty_{'routes' if mode == 'tags' else 'metrics'}_shape_missing")
                else:
                    coverage.append(f"{prefix}:{kind}:empty_{mode}")
                    empty_scopes = scopes if mode == "labels" else (evaluation_type,)
                    for scope in empty_scopes:
                        requested_label = label_code if mode == "labels" else None
                        requests[(batch["id"], empty.factor_ref, scope, requested_label)] = (empty, scope, requested_label)
        results: list[tuple[EnvironmentMetricSample, ReadCheck]] = []
        for sample, scope, label in requests.values():
            context = f"batch={sample.batch['id']}:{sample.factor_ref}:{scope or 'all'}:{label or 'all'}"
            try:
                check = (self.check_environment_tags(sample) if mode == "tags" else
                         self.check_environment_metrics(sample, evaluation_type=scope, label_code=label, auto_batch=mode == "implicit"))
                results.append((sample, ReadCheck(check.checked_count, tuple(context + ":" + issue for issue in check.issues))))
            except ReadPrecondition as error:
                blocked.append(context + ":" + str(error))
            except ReadContractError as error:
                # The malformed response is still a confirmed contract failure even
                # if a publication switches; it cannot be explained by old rows.
                issues.append(context + ":" + str(error))
        if guarded:
            current_pointers = _environment_partition_fingerprints(repository.active_environment_publications())
            changed_partitions.update(key for key in expected_pointers if current_pointers.get(key) != expected_pointers[key])
            for batch in snapshot.batches:
                if (batch["market_scope"], batch["route_profile_key"]) in changed_partitions:
                    blocked.append(f"batch={batch['id']}:SNAPSHOT_DRIFT: active publication changed during matrix")
        checked = 0
        for sample, check in results:
            if (sample.batch["market_scope"], sample.batch["route_profile_key"]) not in changed_partitions:
                checked += check.checked_count
                issues.extend(check.issues)
        return ReadCheck(checked, tuple(issues), {"blocked": sorted(set(blocked)), "discovered_shapes": sorted(set(coverage)),
            "request_count": len(requests), "partition_count": len(snapshot.batches), "captured_at": snapshot.as_of.isoformat(),
            "scope": "representative_shape_matrix_not_all_entities"})

    def _traverse(
        self, fetch: Callable[[str | None], MCPResponse], *, page_size: int, max_pages: int,
        catalog_budget: bool = False,
    ) -> PageTraversal:
        rows: list[dict[str, Any]] = []
        pages: list[ToolPage] = []
        issues: list[str] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(max_pages):
            try:
                page = read_tool_page(fetch(cursor))
            except ReadPrecondition as error:
                if not catalog_budget:
                    raise
                return PageTraversal(tuple(rows), tuple(pages), tuple(issues), "blocked", (str(error),))
            items = page.items
            pages.append(page)
            rows.extend(items)
            next_cursor = page.meta.get("next_cursor")
            if len(items) > page_size:
                issues.append("pagination:limit_exceeded")
            if "returned_count" in page.data and page.data["returned_count"] != len(items):
                issues.append("pagination:returned_count")
            warnings = page.meta.get("warnings")
            declared_budget = catalog_budget and isinstance(warnings, list) and any(
                warning == "CATALOG_CURSOR_BUDGET_REACHED"
                or isinstance(warning, Mapping) and warning.get("code") == "CATALOG_CURSOR_BUDGET_REACHED"
                for warning in warnings
            )
            if declared_budget:
                valid_terminal = (page.meta.get("truncated") is True
                                  and "next_cursor" in page.meta and next_cursor is None)
                if not valid_terminal:
                    issues.append("pagination:budget_terminal_contract")
                return PageTraversal(tuple(rows), tuple(pages), tuple(issues), "bounded" if valid_terminal else "invalid")
            if page.meta.get("truncated") is not bool(next_cursor):
                issues.append("pagination:truncated_cursor_mismatch")
            if not next_cursor:
                terminal = "complete" if next_cursor is None and page.meta.get("truncated") is False else "invalid"
                if next_cursor is not None:
                    issues.append("pagination:invalid_terminal_cursor")
                return PageTraversal(tuple(rows), tuple(pages), tuple(issues), terminal)
            if not isinstance(next_cursor, str) or next_cursor in seen or not items:
                raise ReadContractError("pagination cursor loop or empty continuation page")
            seen.add(next_cursor)
            cursor = next_cursor
        reason = "BLOCKED_DATA_PRECONDITION: traversal safety cap reached; not a complete read"
        if catalog_budget:
            return PageTraversal(tuple(rows), tuple(pages), tuple(issues), "blocked", (reason,))
        raise ReadPrecondition(reason)


def _environment_publication_fingerprint(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    names = ("id", "batch_uid", "market_scope", "route_profile_key", "publication_uid", "publish_version",
             "is_active", "status", "publish_status", "score_rule_version", "as_of_time", "label_kind")
    return tuple(sorted(tuple(str(row.get(name)) for name in names) for row in rows))


def _environment_partition_fingerprints(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, Any], tuple[tuple[str, ...], ...]]:
    partitions: dict[tuple[Any, Any], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        partitions[(row.get("market_scope"), row.get("route_profile_key"))].append(row)
    return {key: _environment_publication_fingerprint(values) for key, values in partitions.items()}


def _environment_publication_mismatches(
    actual: Any, expected: Mapping[str, Any], prefix: str,
) -> list[str]:
    if not isinstance(actual, Mapping):
        return [prefix + ":publication_object_missing"]
    issues: list[str] = []
    for name in ("id", "batch_uid", "market_scope", "route_profile_key", "publication_uid", "publish_version",
                 "score_rule_version", "label_kind", "as_of_time", "published_at"):
        if name not in expected:
            continue
        if name not in actual:
            issues.append(f"{prefix}:publication_missing={name}")
            continue
        try:
            if name in {"as_of_time", "published_at"} and expected[name] is not None and actual[name] is not None:
                mismatch = _time(actual[name]) != _time(expected[name], db_local=True)
            else:
                mismatch = _value(actual[name]) != _value(expected[name])
            if mismatch:
                issues.append(f"{prefix}:publication_field={name}")
        except (TypeError, ValueError):
            issues.append(f"{prefix}:publication_invalid={name}")
    return issues


def _time(value: Any, *, db_local: bool = False) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if not db_local:
            raise ValueError("API timestamp has no timezone")
        parsed = parsed.replace(tzinfo=_LOCAL)
    return parsed.astimezone(timezone.utc)


def _value(value: Any, name: str = "") -> Any:
    if name in _JSON_FIELDS and isinstance(value, (str, bytes)):
        value = json.loads(value, parse_float=Decimal)
    if isinstance(value, dict):
        return {key: _value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if isinstance(value, date):
        return value.isoformat()
    return value
