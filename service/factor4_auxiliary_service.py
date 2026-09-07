"""候选来源检索和 universe 最终结果对账，不触发计算或写入。"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from api.factor4_auxiliary_api import Factor4AuxiliaryAPI
from db.factor4_auxiliary_repository import CandidateTaskSample
from service.factor4_protocol_service import Factor4ProtocolService
from service.factor4_read_service import ReadCheck, ReadPrecondition, read_tool_page


def _cell(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _contains_private_task_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in {"claim_token", "lease_token", "authorization", "password", "api_key"}
                   or _contains_private_task_field(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_private_task_field(item) for item in value)
    return False


def visible_universe_rows(rows: tuple[dict[str, Any], ...], universe_key: str, as_of: datetime) -> tuple[dict[str, Any], ...]:
    """按 active 和 [valid_from, valid_to) 返回精确排序成员，NULL 无界；无 I/O，日期错误透传。"""
    instant = as_of.astimezone(timezone.utc)
    def stamp(value: Any) -> datetime:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    result = [row for row in rows if row.get("universe_key") == universe_key and row.get("is_active") == 1
              and (row.get("valid_from") is None or stamp(row["valid_from"]) <= instant)
              and (row.get("valid_to") is None or stamp(row["valid_to"]) > instant)]
    return tuple(sorted(result, key=lambda row: (int(row["sort_order"]), str(row["symbol"]))))


class Factor4AuxiliaryService:
    """对业务身份、字段和筛选结果进行逐项对账并返回差异。"""

    def __init__(self, api: Factor4AuxiliaryAPI) -> None:
        """保存 API；无返回与 I/O。"""
        self.api = api

    def check_candidate(self, row: dict[str, Any], *, filter_name: str | None = None, matching: bool = True, query: bool = False, combined: bool = False) -> ReadCheck:
        """精确候选与 DB 字段对账，并验证一个合法筛选；缺自然筛选值抛前置异常，协议错误透传。

        非匹配状态值从服务实时声明的枚举选取，不能用非法枚举把参数错误当作筛选通过。
        """
        filters: dict[str, Any] = {}
        if filter_name:
            value = self._filter_value(row, filter_name, matching)
            filters[filter_name] = value
        if combined:
            filters = {name: self._filter_value(row, name, True) for name in ("validation_status", "mapping_status", "min_confidence", "target_asset_class")}
        query_text = str(row.get("factor_name") or "") if query else None
        if query and not matching:
            query_text = "questtest-deliberately-not-the-candidate-name"
        if query and (not query_text or len(query_text) > 200):
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: candidate name outside documented query range")
        response = read_tool_page(self.api.candidate(int(row["id"]), filters=filters, query=query_text))
        items = response.items
        issues: list[str] = []
        if any(_contains_private_task_field(item) for item in items):
            issues.append("kb:private_task_field_exposed")
        expected = [int(row["id"])] if matching else []
        if [item.get("extraction_id", item.get("id")) for item in items] != expected:
            issues.append("kb:exact_filter_membership")
        if matching and len(items) == 1:
            actual = items[0]
            for field in ("factor_name", "validation_status", "mapping_status", "target_asset_class", "confidence_score"):
                if field not in actual:
                    issues.append(f"kb:missing={field}")
                    continue
                left, right = _cell(actual[field]), _cell(row[field])
                if field == "confidence_score" and left is not None and right is not None:
                    left, right = Decimal(str(left)), Decimal(str(right))
                if left != right:
                    issues.append(f"kb:field={field}")
            for field in ("mapped_factor_id", "is_sub_factor_id"):
                if field in row and (field not in actual or actual[field] != row[field]):
                    issues.append(f"kb:field={field}")
            if row.get("pipeline_sub_factor_id") is not None and actual.get("result_sub_factor_id") != row["pipeline_sub_factor_id"]:
                issues.append("kb:result_subfactor_identity")
        return ReadCheck(1, tuple(issues))

    def check_candidate_name_discovery(self, row: dict[str, Any]) -> ReadCheck:
        """仅按完整名称检索必须能发现种子；合法结果已截断且种子未出页则明确前置阻断。"""
        name = row.get("factor_name")
        if not isinstance(name, str) or not 1 <= len(name) <= 200:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: candidate has no bounded query name")
        page = read_tool_page(self.api.candidate(None, query=name))
        found = any(item.get("extraction_id", item.get("id")) == row["id"] for item in page.items)
        if not found and page.meta.get("truncated") is True:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: candidate not observed within bounded name search")
        return ReadCheck(1, () if found else ("kb:name_seed_not_found",))

    def check_mapped_identity(self, row: dict[str, Any], entity: dict[str, Any] | None) -> ReadCheck:
        """候选映射→类型化详情→DB 实体链；缺映射实体是失败不是 skip，网络/协议错误透传。"""
        baseline = self.check_candidate(row)
        kind = "sub_factor" if row["is_sub_factor_id"] else "factor"
        ref = f"{kind}:{row['mapped_factor_id']}"
        detail = read_tool_page(self.api.mcp.get_factor_detail(ref, detail_level="summary")).data
        issues = list(baseline.issues)
        if entity is None:
            issues.append("kb:mapped_entity_missing")
        else:
            for field in ("id", "name", "serial_number"):
                if detail.get(field) != entity.get(field):
                    issues.append(f"kb:mapped_detail={field}")
        if detail.get("factor_ref") != ref or detail.get("kind") != kind:
            issues.append("kb:mapped_typed_identity")
        return ReadCheck(1, tuple(issues))

    def check_candidate_task(self, sample: CandidateTaskSample) -> ReadCheck:
        """当前任务身份、失败信息、尝试次数与 lease 投影和 DB 对账；临近 lease 切换明确阻断。"""
        items = read_tool_page(self.api.candidate(sample.extraction_id)).items
        if len(items) != 1 or items[0].get("extraction_id") != sample.extraction_id:
            return ReadCheck(1, ("kb:task_candidate_identity",))
        item = items[0]
        task = sample.task
        expected: dict[str, Any] = {}
        if task is None:
            expected = dict.fromkeys(("task_id", "task_status", "active_task_id", "pipeline_run_id", "result_sub_factor_id", "result_validity"))
        else:
            mapping = {"task_id": "id", "task_status": "status", "attempt_count": "attempt_count", "task_max_attempts": "max_attempts",
                       **{field: field for field in ("pipeline_run_id", "result_sub_factor_id", "result_validity", "last_error_stage", "last_error_class", "last_error_code", "last_error_message", "retryable")}}
            expected = {field: task[column] for field, column in mapping.items()}
            lease = task.get("lease_until")
            if isinstance(lease, datetime):
                lease = lease.replace(tzinfo=timezone.utc) if lease.tzinfo is None else lease.astimezone(timezone.utc)
                if sample.as_of <= lease <= datetime.now(timezone.utc):
                    raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: lease changed across DB/MCP observation")
            active = task["status"] in {"claimed", "running"} and isinstance(lease, datetime) and lease > sample.as_of
            expected["active_task_id"] = task["id"] if active else None
        issues = tuple(f"kb:task_field={field}" for field, value in expected.items() if field not in item or item[field] != value)
        return ReadCheck(1, issues)

    def _filter_value(self, row: dict[str, Any], name: str, matching: bool) -> Any:
        if name not in {"validation_status", "mapping_status", "min_confidence", "target_asset_class"}:
            raise ValueError("unsupported KB filter")
        value = row.get("confidence_score" if name == "min_confidence" else name)
        value = _cell(value)
        if isinstance(value, list):
            value = value[0] if value else None
        if value is None:
            raise ReadPrecondition(f"BLOCKED_DATA_PRECONDITION: candidate has no {name}")
        if matching:
            return float(value) if name == "min_confidence" else value
        if name == "min_confidence":
            if Decimal(str(value)) >= 1:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no legal confidence threshold above sample")
            return float((Decimal(str(value)) + 1) / 2)
        if name == "target_asset_class":
            return "questtest_nonmatching_asset"
        descriptors = Factor4ProtocolService(self.api.mcp).tool_descriptors()
        descriptor = next(item for item in descriptors if item.get("name") == "kb_factor_candidate_search")
        definition = descriptor["inputSchema"]["properties"][name]
        choices = definition.get("enum", [])
        if not choices:
            choices = [item for branch in definition.get("anyOf", []) for item in branch.get("enum", [])]
        alternate = next((item for item in choices if isinstance(item, str) and item != value), None)
        if alternate is None:
            raise ReadPrecondition(f"BLOCKED_CONTRACT: no declared alternate {name} value")
        return alternate

    def check_universe(self, rows: tuple[dict[str, Any], ...], universe_key: str, as_of: datetime) -> ReadCheck:
        """固定时点读取集合并核对身份、排序、重复及所有静态属性；无集合样本抛前置异常。"""
        if not any(row.get("universe_key") == universe_key for row in rows):
            raise ReadPrecondition(f"BLOCKED_DATA_PRECONDITION: no universe {universe_key}")
        expected = visible_universe_rows(rows, universe_key, as_of)
        actual = read_tool_page(self.api.universe(universe_key, as_of)).items
        issues: list[str] = []
        symbols = [row.get("symbol") for row in actual]
        if len(symbols) != len(set(symbols)):
            issues.append("universe:duplicate_symbol")
        if symbols != [row["symbol"] for row in expected]:
            issues.append("universe:membership_or_order")
        fields = ("universe_key", "symbol", "base_asset", "quote_asset", "market", "exchange_name", "instrument_type", "sort_order")
        indexed = {row["symbol"]: row for row in expected}
        for row in actual:
            baseline = indexed.get(row.get("symbol"))
            if baseline is None:
                continue
            for field in fields:
                if field not in row or row[field] != baseline[field]:
                    issues.append(f"universe:field={field}")
        return ReadCheck(max(1, len(expected)), tuple(dict.fromkeys(issues)))

    def check_universe_partition(self, rows: tuple[dict[str, Any], ...], as_of: datetime) -> ReadCheck:
        """核对同一时点 main/altcoin 不相交且并集为 all；逐集合同时 DB 对账，前置异常透传。"""
        checks = [self.check_universe(rows, key, as_of) for key in ("all", "main", "altcoin")]
        groups = {key: {row["symbol"] for row in visible_universe_rows(rows, key, as_of)} for key in ("all", "main", "altcoin")}
        issues = [issue for check in checks for issue in check.issues]
        if groups["main"] & groups["altcoin"]:
            issues.append("universe:partition_overlap")
        if groups["main"] | groups["altcoin"] != groups["all"]:
            issues.append("universe:partition_union")
        return ReadCheck(sum(check.checked_count for check in checks), tuple(dict.fromkeys(issues)))
