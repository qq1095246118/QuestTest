"""目录检索交集、详情级别和母子分页的正式流程，独立于临时探针。"""

import re
from typing import Any

from api.factor4_auxiliary_api import Factor4AuxiliaryAPI
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_page


def _catalog_precondition(stage: str, error: ReadPrecondition) -> str:
    reason = str(error)
    if not re.fullmatch(r"BLOCKED_[A-Z_]+: [A-Z][A-Z0-9_]{0,79}", reason):
        reason = "ReadPrecondition"
    return f"catalog:{stage}:{reason}"


class Factor4CatalogExtensionService:
    """编排目录正常业务分支；只输出身份/字段差异，不打印返回正文。"""

    def __init__(self, api: Factor4AuxiliaryAPI) -> None:
        """保存门禁后的 API；无 I/O 与返回值。"""
        self.api = api

    def filter_seed(self) -> dict[str, Any]:
        """读取有完整目录筛选字段的自然种子；缺样本抛前置异常，读取异常透传。"""
        items = read_tool_page(self.api.search_catalog(kind="sub_factor", filters={"library_status": "valid"})).items
        result = next((row for row in items if all(row.get(field) for field in ("factor_ref", "name", "themes", "tags", "data_source", "factor_bar_interval", "library_coin_categories"))), None)
        if result is None:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no catalog seed with complete filter fields")
        return result

    def check_total_additivity(self) -> ReadCheck:
        """目录全部数量等于母/子数量之和；合法 total 必须为非负整数，网络错误透传。"""
        pages = [read_tool_page(self.api.catalog_stats(kind)) for kind in (None, "factor", "sub_factor")]
        totals = [page.data.get("total") for page in pages]
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in totals):
            return ReadCheck(3, ("catalog:invalid_total",))
        return ReadCheck(3, () if totals[0] == totals[1] + totals[2] else ("catalog:total_not_additive",))

    def check_filter(self, seed: dict[str, Any], field: str, *, matching: bool = True) -> ReadCheck:
        """逐项/全交集筛选必须保留种子并排除其他不匹配行；冲突主题必须成功空页。"""
        available = {"query": seed["name"], "theme": seed["themes"][0], "tags": [seed["tags"][0]], "data_source": seed["data_source"],
                     "interval": seed["factor_bar_interval"], "library_coin_category": seed["library_coin_categories"][0]}
        if field != "combined" and field not in available:
            raise ValueError("unknown catalog filter")
        filters = dict(available) if field == "combined" else {field: available[field]}
        # Exact-name additionally bounds broad single filters while preserving
        # their independently falsifiable predicates.
        filters["query"] = seed["name"]
        filters["library_status"] = "valid"
        if not matching:
            target = "theme" if field == "combined" else field
            if target == "interval":
                filters[target] = "1d" if seed["factor_bar_interval"] != "1d" else "1h"
            elif target == "tags":
                filters[target] = ["__questtest_nonmatching_tag__"]
            elif target == "library_coin_category":
                alternative = next((key for key in ("all", "main", "altcoin") if key not in seed["library_coin_categories"]), None)
                if alternative is None:
                    raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: seed belongs to every documented coin category")
                filters[target] = alternative
            else:
                filters[target] = f"__questtest_nonmatching_{target}__"
        page = read_tool_page(self.api.search_catalog(kind="sub_factor", filters=filters))
        issues: list[str] = []
        if not matching:
            if page.items:
                issues.append("catalog:conflicting_filter_not_empty")
            if page.meta.get("next_cursor") is not None or page.meta.get("truncated") is not False:
                issues.append("catalog:empty_result_not_terminal")
            return ReadCheck(1, tuple(issues))
        if seed["factor_ref"] not in {row.get("factor_ref") for row in page.items}:
            issues.append("catalog:matching_seed_missing")
        for row in page.items:
            if row.get("kind") != "sub_factor" or row.get("library_status") != "valid":
                issues.append("catalog:filter_identity")
            for name, value in filters.items():
                if name == "theme":
                    matched = value in (row.get("themes") or [])
                elif name == "tags":
                    matched = set(value) <= set(row.get("tags") or [])
                elif name == "library_coin_category":
                    matched = value in (row.get("library_coin_categories") or [])
                elif name == "query":
                    matched = str(value).casefold() in str(row.get("name", "")).casefold()
                else:
                    matched = row.get("factor_bar_interval" if name == "interval" else name) == value
                if not matched:
                    issues.append(f"catalog:filter={name}")
        return ReadCheck(max(1, len(page.items)), tuple(dict.fromkeys(issues)))

    def check_detail_levels(self, kind: str, row: dict[str, Any]) -> ReadCheck:
        """summary/definition/executable 核对 DB 身份和级别公共投影；读取/契约错误透传。"""
        ref = f"{kind}:{row['id']}"
        levels = {level: read_tool_page(self.api.mcp.get_factor_detail(ref, detail_level=level)).data for level in ("summary", "definition", "executable")}
        issues: list[str] = []
        for level, data in levels.items():
            for field in ("id", "name", "serial_number", "cn_name"):
                if field not in data or data[field] != row[field]:
                    issues.append(f"detail:{level}:identity={field}")
            if data.get("factor_ref") != ref or data.get("kind") != kind:
                issues.append(f"detail:{level}:typed_identity")
        definition_fields = {"calc_logic", "formula_summary", "metadata", "params", "data_source_metadata"}
        if (definition_fields | {"calc_function"}) & levels["summary"].keys():
            issues.append("detail:summary_projection")
        if not definition_fields <= levels["definition"].keys() or "calc_function" in levels["definition"]:
            issues.append("detail:definition_projection")
        if not definition_fields | {"calc_function"} <= levels["executable"].keys():
            issues.append("detail:executable_projection")
        for field in definition_fields:
            if levels["definition"].get(field) != levels["executable"].get(field):
                issues.append(f"detail:definition_executable={field}")
        return ReadCheck(3, tuple(issues))

    def check_batch(self, entities: tuple[tuple[str, dict[str, Any]], ...], *, duplicate: bool = False) -> ReadCheck:
        """真实批量详情保留合法输入顺序/重复位置，并逐实体和 DB 身份核对；API 异常透传。"""
        selected = (*entities, entities[0]) if duplicate else entities
        refs = [f"{kind}:{row['id']}" for kind, row in selected]
        items = read_tool_page(self.api.mcp.get_factor_details_batch(refs, detail_level="summary")).items
        issues: list[str] = []
        if [item.get("factor_ref") for item in items] != refs:
            issues.append("detail:batch_order_or_multiplicity")
        for item, (kind, row) in zip(items, selected):
            data = item.get("data")
            if item.get("success") is not True or not isinstance(data, dict):
                issues.append("detail:valid_batch_item_failed")
                continue
            for field in ("id", "name", "serial_number", "cn_name"):
                if field not in data or data[field] != row[field]:
                    issues.append(f"detail:batch_identity={field}")
            if data.get("kind") != kind:
                issues.append("detail:batch_kind")
        return ReadCheck(len(selected), tuple(dict.fromkeys(issues)))

    def check_children(self, sample: tuple[int, tuple[int, ...]], *, page_size: int) -> ReadCheck:
        """遍历母因子全部 children，核对 DB 关系集合/无重复/末页游标及引用可解；API 错误透传。"""
        parent_id, child_ids = sample
        ref = f"factor:{parent_id}"
        refs: list[str] = []
        cursor: str | None = None
        seen: set[str] = set()
        issues: list[str] = []
        for _ in range(20):
            data = read_tool_page(self.api.mcp.get_factor_detail(ref, detail_level="summary", children_limit=page_size, children_cursor=cursor)).data
            children = data.get("children")
            if not isinstance(children, list) or any(not isinstance(row, dict) for row in children):
                raise ReadContractError("detail children must be objects")
            if len(children) > page_size:
                issues.append("detail:children_limit")
            refs.extend(row.get("factor_ref") for row in children)
            cursor = data.get("children_next_cursor")
            if not cursor:
                if data.get("children_truncated") is not False:
                    issues.append("detail:children_terminal_truncated")
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise ReadContractError("detail children cursor repeated or malformed")
            seen.add(cursor)
        else:
            raise ReadContractError("detail children traversal exceeded bounded pages")
        expected = {f"sub_factor:{child_id}" for child_id in child_ids}
        if set(refs) != expected or len(refs) != len(expected):
            issues.append("detail:children_relation_membership")
        if refs:
            child = read_tool_page(self.api.mcp.get_factor_detail(refs[0], detail_level="summary")).data
            if child.get("factor_ref") != refs[0]:
                issues.append("detail:child_reference_resolution")
        return ReadCheck(max(1, len(refs)), tuple(issues))

    def check_status_category(self, kind: str, status: str, category: str | None, rows: tuple[dict[str, Any], ...]) -> ReadCheck:
        """Check one catalog selection until natural completion or declared cursor budget.

        Inputs are kind/status/optional category and the complete DB selection. Return
        checked returned items plus explicit bounded/complete evidence; only natural
        completion proves full membership. A declared budget never causes a restart or
        retry. Paging/statistics preconditions are returned as independent blocked
        evidence after validating all pages already read. Statistics only check group
        sums. Cursor loops or the local safety cap raise ReadContractError; other API
        errors propagate without printing response bodies.
        """
        filters = {"library_status": status, **({"library_coin_category": category} if category is not None else {})}
        issues: list[str] = []
        blocked: list[str] = []
        by_id = {row["id"]: row for row in rows}
        items: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        termination = "invalid"
        page_count = 0
        for _ in range(max(2, len(by_id) + 1)):
            page_filters = {**filters, **({"cursor": cursor} if cursor is not None else {})}
            try:
                page = read_tool_page(self.api.search_catalog(kind=kind, filters=page_filters, limit=50))
            except ReadPrecondition as error:
                blocked.append(_catalog_precondition("pagination", error))
                termination = "blocked"
                break
            page_count += 1
            items.extend(page.items)
            if len(page.items) > 50:
                issues.append("catalog:status_category_page_limit")
            if "returned_count" in page.data and page.data["returned_count"] != len(page.items):
                issues.append("catalog:status_category_returned_count")
            cursor = page.meta.get("next_cursor")
            warnings = page.meta.get("warnings")
            budget_warning = isinstance(warnings, list) and any(
                warning == "CATALOG_CURSOR_BUDGET_REACHED"
                or isinstance(warning, dict) and warning.get("code") == "CATALOG_CURSOR_BUDGET_REACHED"
                for warning in warnings
            )
            if budget_warning:
                # This is a per-chain delivery boundary, not a daily quota or a
                # request-per-minute limit. Do not reopen or wait to resume it.
                if page.meta.get("truncated") is True and "next_cursor" in page.meta and cursor is None:
                    termination = "bounded"
                else:
                    issues.append("catalog:status_category_budget_terminal_contract")
                break
            if page.meta.get("truncated") is not bool(cursor):
                issues.append("catalog:status_category_cursor_truncation")
            if cursor is None:
                if page.meta.get("truncated") is False:
                    termination = "complete"
                break
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors or not page.items:
                raise ReadContractError("catalog status/category cursor repeated or malformed")
            seen_cursors.add(cursor)
        else:
            raise ReadContractError("catalog status/category traversal did not reach its terminal page")
        actual_ids = [item.get("id") for item in items]
        valid_ids = [identifier for identifier in actual_ids
                     if isinstance(identifier, int) and not isinstance(identifier, bool) and identifier > 0]
        if len(valid_ids) != len(actual_ids):
            issues.append("catalog:status_category_invalid_id")
        if len(set(valid_ids)) != len(valid_ids):
            issues.append("catalog:status_category_duplicate_identity")
        full_membership_verified = (
            termination == "complete" and len(valid_ids) == len(actual_ids)
            and set(valid_ids) == set(by_id) and len(valid_ids) == len(by_id)
        )
        if termination == "complete" and not full_membership_verified:
            issues.append("catalog:status_category_complete_membership")
        for item in items:
            identifier = item.get("id")
            if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
                continue
            expected = by_id.get(identifier)
            if (expected is None or item.get("kind") != kind or item.get("library_status") != status
                    or item.get("factor_ref") != f"{kind}:{item.get('id')}"):
                issues.append("catalog:status_category_identity")
                continue
            if item.get("name") != expected["name"] or item.get("cn_name") != expected["cn_name"]:
                issues.append("catalog:status_category_fields")
            actual_categories = item.get("library_coin_categories")
            expected_categories = {row["coin_category"] for row in rows if row["id"] == item["id"]}
            if not isinstance(actual_categories, list) or not expected_categories <= set(actual_categories):
                issues.append("catalog:status_category_memberships")
        stats_status = "blocked"
        try:
            stats = read_tool_page(self.api.catalog_stats(kind, filters=filters)).data
        except ReadPrecondition as error:
            blocked.append(_catalog_precondition("statistics", error))
        else:
            groups, total = stats.get("groups"), stats.get("total")
            stats_status = "verified"
            if not isinstance(total, int) or isinstance(total, bool) or total < 0 or not isinstance(groups, list):
                issues.append("catalog:stats_shape")
                stats_status = "failed"
            elif any(not isinstance(row, dict) or isinstance(row.get("count"), bool) or not isinstance(row.get("count"), int) or row["count"] < 0 for row in groups) or sum(row["count"] for row in groups) != total:
                issues.append("catalog:stats_group_sum")
                stats_status = "failed"
        checked = max(len(items), int(page_count > 0 or stats_status != "blocked"))
        return ReadCheck(checked, tuple(dict.fromkeys(issues)), {
            "catalog_membership_oracle": "complete_database_set", "stats_oracle": "group_sum_only",
            "catalog_traversal": termination, "catalog_returned_count": len(items),
            "catalog_returned_unique_count": len(set(valid_ids)), "catalog_database_unique_count": len(by_id),
            "catalog_page_count": page_count, "catalog_full_membership_verified": full_membership_verified,
            "catalog_completeness": "verified" if full_membership_verified else "not_verified",
            "catalog_budget_code": "CATALOG_CURSOR_BUDGET_REACHED" if termination == "bounded" else None,
            "catalog_statistics": stats_status, "blocked": tuple(blocked),
        })

    def check_chinese_query(self, kind: str, rows: tuple[dict[str, Any], ...]) -> ReadCheck:
        """cn_name独立查询应能发现真实中文名种子，返回name/cn_name至少一字段匹配；无自然种子阻断。"""
        seed = next((row for row in rows if row.get("cn_name") and any('\u4e00' <= c <= '\u9fff' for c in row["cn_name"])), None)
        if seed is None:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no Chinese catalog name")
        query = seed["cn_name"]
        page = read_tool_page(self.api.search_catalog(kind=kind, filters={"query": query, "library_status": "valid"}))
        issues = []
        if seed["id"] not in {row.get("id") for row in page.items}:
            issues.append("catalog:chinese_query_seed_missing")
        if any(not any(query.casefold() in str(row.get(key) or "").casefold() for key in ("name", "cn_name")) for row in page.items):
            issues.append("catalog:chinese_query_nonmatching_result")
        return ReadCheck(max(1, len(page.items)), tuple(issues))
