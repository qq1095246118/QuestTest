"""真实但默认暂缓的协议/输入/游标兼容断言；禁止调用业务写工具。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import json
from typing import Any

from api.factor_data_mcp_api import FactorDataMCPAPI, MCPJSONRPCError, MCPResponse
from db.factor4_auxiliary_repository import Factor4AuxiliaryRepository
from service.factor4_protocol_service import Factor4ProtocolService
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_body, read_tool_page
from service.factor4_summary_service import metric_slice_arguments, summary_scope, validity_arguments

READ_TOOLS = (
    "factor_search", "factor_catalog_stats", "factor_get_detail", "factor_get_details_batch",
    "factor_get_metrics", "factor_get_metrics_batch", "factor_get_validity", "factor_get_validity_batch",
    "factor_get_formula", "factor_rank", "factor_list_metric_scopes", "factor_get_metric_slices",
    "environment_get_daily", "environment_get_recommendations", "factor_get_environment_metrics",
    "factor_get_environment_tags", "universe_list_symbols", "kb_factor_candidate_search",
    "schema_get_factor_fields", "schema_get_raw_data", "get_feedback_submission_status",
)


def check_explicit_rejection(api: FactorDataMCPAPI, tool: str, arguments: dict[str, Any], *, codes: tuple[str, ...] = ("INVALID_ARGUMENT",)) -> ReadCheck:
    """负向只读请求必须返回指定工具错误或 -32602，不能把 HTTP/解析/内部错误计通过；异常透传。"""
    if tool not in READ_TOOLS:
        raise ValueError("only known readonly tools can be probed")
    try:
        response = api.call_tool(tool, arguments)
    except MCPJSONRPCError as exc:
        if exc.code == -32602:
            return ReadCheck(1, ())
        raise
    body = read_tool_body(response)
    error = body.get("error")
    issues = []
    if not response.is_tool_error or not isinstance(error, dict) or error.get("code") not in codes:
        issues.append("protocol:expected_explicit_rejection")
    if body.get("data") not in (None, {}):
        issues.append("protocol:rejected_request_returned_data")
    return ReadCheck(1, tuple(issues))


def check_strict_dual_representation(response: MCPResponse) -> ReadCheck:
    """严格独立解析全部 text JSON 与 structuredContent，截断/缺失/不一致都失败；不采用 structured fallback。"""
    result = response.result
    if not isinstance(result, dict):
        raise ReadContractError("tool result requires object")
    content = result.get("content")
    texts = [item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"] if isinstance(content, list) else []
    issues = []
    if len(texts) != 1 or not isinstance(result.get("structuredContent"), dict):
        return ReadCheck(1, ("protocol:dual_representation_missing",))
    try:
        parsed = json.loads(texts[0])
    except (ValueError, TypeError):
        return ReadCheck(1, ("protocol:text_not_complete_json",))
    if parsed != result["structuredContent"]:
        issues.append("protocol:dual_representation_mismatch")
    return ReadCheck(1, tuple(issues))


class Factor4ProtocolBoundaryService:
    """编排参数及协议边界，无写入口，Case负责执行门禁和结果断言。"""

    def __init__(self, api: FactorDataMCPAPI, repository: Factor4AuxiliaryRepository) -> None:
        """保存已门禁只读连接；无请求与返回值。"""
        self.api, self.repository = api, repository

    def arguments(self, tool: str) -> dict[str, Any]:
        """从自然 DB 实体生成各 read tool 可成功请求；不造不存在 ID，缺前置明确阻断。"""
        if tool not in READ_TOOLS:
            raise ValueError("unknown readonly tool")
        now = datetime.now(timezone.utc).isoformat()
        simple = {"factor_search": {"kind": "sub_factor", "limit": 1}, "factor_catalog_stats": {},
                  "environment_get_daily": {"as_of": now, "limit": 1}, "schema_get_factor_fields": {},
                  "schema_get_raw_data": {}, "universe_list_symbols": {"universe_key": "all", "as_of": now},
                  "factor_list_metric_scopes": {"as_of": now, "limit": 1}}
        if tool in simple:
            return simple[tool]
        if tool == "get_feedback_submission_status":
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: owned feedback baseline is verified in ownership-specific Case")
        if tool == "kb_factor_candidate_search":
            candidate = self.repository.candidate_sample()
            if candidate is None:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no KB candidate")
            return {"extraction_id": candidate["id"], "limit": 1}
        if tool in {"factor_get_detail", "factor_get_details_batch"}:
            rows = self.repository.detail_entities("sub_factor", limit=1)
            if not rows:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no catalog entity")
            ref = f"sub_factor:{rows[0]['id']}"
            return {"factor_ref": ref, "children_limit": 1} if tool == "factor_get_detail" else {"factor_refs": [ref]}
        if tool in {"environment_get_recommendations", "factor_get_environment_metrics", "factor_get_environment_tags"}:
            sample = self.repository.metric_sample("sub_factor")
            if sample is None:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no environment factor")
            args = {"market_scope": sample.batch["market_scope"], "route_profile_key": sample.batch["route_profile_key"]}
            if tool == "environment_get_recommendations":
                return {**args, "as_of": now, "limit": 1}
            return {**args, "factor_ref": sample.factor_ref, **({"limit": 1} if tool.endswith("metrics") else {})}
        if tool in {"factor_get_validity", "factor_get_validity_batch"}:
            sample = self.repository.validity_sample()
            if sample is None:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no complete validity")
            args = validity_arguments(sample, "ts", explicit_run=True)
            if tool.endswith("_batch"):
                args["factor_refs"] = [args.pop("factor_ref")]
            return args
        if tool == "factor_get_metric_slices":
            sample = self.repository.slice_sample()
            if sample is None:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no completed metric slices")
            return metric_slice_arguments(sample, limit=1)
        sample = self.repository.summary_sample("cs_aggregate")
        if sample is None or not sample.rows:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no complete metric scope")
        row = sample.rows[0]
        args = {**asdict(summary_scope(row)), "factor_ref": f"sub_factor:{row['factor_id']}", "as_of": sample.as_of.isoformat(), "run_id": row["run_id"]}
        if tool == "factor_get_formula":
            if not any(item.get("factor_id") == row["factor_id"] and item.get("run_id") == row["run_id"] for item in sample.formulas):
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no exact Run formula evidence")
            return {key: args[key] for key in ("factor_ref", "run_id", "calculation_mode", "interval", "factor_window_bars", "return_bar_interval", "forward_return_bars", "as_of")}
        if tool == "factor_get_metrics_batch":
            args["factor_refs"] = [args.pop("factor_ref")]
        if tool == "factor_rank":
            args.pop("factor_ref")
            args.pop("run_id")
            args.update({"metric": "mean_ic", "top_k": 1, "bottom_k": 1, "validity_scope": "cross_sectional", "ranking_mode": "signed", "min_valid_slice_count": 0, "min_coverage_mean": 0, "require_oos": False})
        return args

    def check_schema_boundaries(self, tool: str) -> ReadCheck:
        """每个read tool先成功baseline，再覆盖所有enum/extra和limit合法min/max/越界/类型；缺fixture不伪PASS。"""
        descriptor = next((row for row in Factor4ProtocolService(self.api).tool_descriptors() if row.get("name") == tool), None)
        if descriptor is None:
            raise ReadContractError("required readonly descriptor is absent")
        schema = descriptor["inputSchema"]
        args = self.arguments(tool)
        read_tool_page(self.api.call_tool(tool, args))
        issues: list[str] = []
        checked = 1
        if schema.get("additionalProperties") is False:
            check = check_explicit_rejection(self.api, tool, {**args, "questtest_unknown_argument": True})
            issues.extend(check.issues)
            checked += 1
        for field_name, definition in schema.get("properties", {}).items():
            if not isinstance(definition, dict):
                continue
            enums = definition.get("enum")
            if enums is None:
                enums = next((part["enum"] for part in definition.get("anyOf", []) if isinstance(part, dict) and "enum" in part), None)
            if enums:
                issues.extend(check_explicit_rejection(self.api, tool, {**args, field_name: "__questtest_invalid_enum__"}).issues)
                checked += 1
            if field_name not in {"limit", "children_limit"}:
                continue
            low, high = definition.get("minimum"), definition.get("maximum")
            if not isinstance(low, int) or not isinstance(high, int):
                issues.append(f"protocol:{field_name}_range_missing")
                continue
            for value in (low, high):
                page = read_tool_page(self.api.call_tool(tool, {**args, field_name: value}))
                rows = page.data.get("children") if field_name == "children_limit" else page.items
                if isinstance(rows, (tuple, list)) and len(rows) > value:
                    issues.append(f"protocol:{field_name}_legal_bound_exceeded")
                checked += 1
            for value in (low-1, high+1, -1, str(low), low+0.5):
                issues.extend(check_explicit_rejection(self.api, tool, {**args, field_name: value}).issues)
                checked += 1
        return ReadCheck(checked, tuple(dict.fromkeys(issues)))

    def check_raw_rejection(self, raw: bytes, code: int) -> ReadCheck:
        """畸形JSON/JSONRPC只接受精确合法错误信封；HTTP5xx、解析失败、成功data均失败。"""
        response = self.api.probe_protocol(raw)
        envelope = response.envelope
        error = envelope.get("error")
        ok = response.status_code < 500 and envelope.get("jsonrpc") == "2.0" and "result" not in envelope and isinstance(error, dict) and error.get("code") == code and isinstance(error.get("message"), str)
        return ReadCheck(1, () if ok else (f"protocol:expected_jsonrpc_error={code}",))

    def check_accept(self, accept: str) -> ReadCheck:
        """已握手独立连接支持JSON/SSE读取；解析后的body与正常工具调用完全一致，错误不当作拒绝通过。"""
        baseline = read_tool_page(self.api.call_tool("schema_get_factor_fields", {})).data
        raw = json.dumps({"jsonrpc": "2.0", "id": "accept-test", "method": "tools/call", "params": {"name": "schema_get_factor_fields", "arguments": {}}}).encode()
        response = self.api.probe_protocol(raw, accept=accept)
        parsed = MCPResponse(response.status_code, response.content_type, response.envelope, self.api.protocol_version)
        page = read_tool_page(parsed)
        ok = response.status_code == 200 and response.envelope.get("id") == "accept-test" and page.data == baseline
        return ReadCheck(1, () if ok else ("protocol:content_negotiation",))

    def check_concurrent(self, tool: str, workers: int) -> ReadCheck:
        """固定as_of并发独立握手只读，前/中/后同业务data；无性能阈值，不比较quota等meta。"""
        args = self.arguments(tool)
        if tool == "factor_search":
            args["as_of"] = datetime.now(timezone.utc).isoformat()
        baseline = read_tool_page(self.api.call_tool(tool, args)).data
        def read(index: int) -> dict[str, Any]:
            fresh = self.api.new_connection()
            try:
                fresh.initialize(protocol_version=self.api.protocol_version or "2025-06-18")
                fresh.notify_initialized()
                return read_tool_page(fresh.call_tool(tool, args, request_id=f"parallel-{index}")).data
            finally:
                fresh.close()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(read, range(workers)))
        after = read_tool_page(self.api.call_tool(tool, args)).data
        return ReadCheck(workers+2, () if all(row == baseline for row in [*results, after]) else ("protocol:parallel_snapshot_changed",))

    def check_unknown_version(self, supported: str) -> ReadCheck:
        """未知版本必须拒绝 -32602 或协商当前支持版本；HTTP/解析/其他RPC错误不能假通过。"""
        fresh = self.api.new_connection()
        try:
            try:
                result = fresh.initialize(protocol_version="2099-01-01").result
            except MCPJSONRPCError as exc:
                if exc.code == -32602:
                    return ReadCheck(1, ())
                raise
            ok = isinstance(result, dict) and result.get("protocolVersion") == supported
            return ReadCheck(1, () if ok else ("protocol:unknown_version_not_negotiated",))
        finally:
            fresh.close()

    def check_cursor(self, mode: str) -> ReadCheck:
        """目录cursor篡改/换kind/filter/page大小/parent必须拒绝；正常重放相同页作为独立对照。"""
        if mode.startswith("children"):
            sample = self.repository.parent_children()
            if sample is None:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no multipage parent")
            args = {"factor_ref": f"factor:{sample[0]}", "children_limit": 1, "detail_level": "summary"}
            tool = "factor_get_detail"
            data = read_tool_page(self.api.call_tool(tool, args)).data
            cursor = data.get("children_next_cursor")
            cursor_key = "children_cursor"
        else:
            tool, args, cursor_key = "factor_search", {"kind": "sub_factor", "limit": 1}, "cursor"
            cursor = read_tool_page(self.api.call_tool(tool, args)).meta.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no continuation cursor")
        valid = {**args, cursor_key: cursor}
        first = read_tool_page(self.api.call_tool(tool, valid)).data
        second = read_tool_page(self.api.call_tool(tool, valid)).data
        issues = [] if first == second else ["protocol:cursor_replay_changed"]
        if mode.endswith("replay"):
            return ReadCheck(2, tuple(issues))
        changed = dict(valid)
        if mode.endswith("tamper"):
            changed[cursor_key] = ("A" if cursor[0] != "A" else "B") + cursor[1:]
        elif mode == "search_kind":
            changed["kind"] = "factor"
        elif mode == "search_filter":
            changed["library_status"] = "valid"
        elif mode == "search_cross_tool":
            tool = "environment_get_daily"
            changed = {"limit": 1, "cursor": cursor, "as_of": datetime.now(timezone.utc).isoformat()}
        elif mode.endswith("limit"):
            changed["children_limit" if mode.startswith("children") else "limit"] = 2
        elif mode == "children_parent":
            others = self.repository.detail_entities("factor", limit=50)
            row = next((row for row in others if f"factor:{row['id']}" != args["factor_ref"]), None)
            if row is None:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no other real parent")
            changed["factor_ref"] = f"factor:{row['id']}"
        else:
            raise ValueError("unknown cursor scenario")
        issues.extend(check_explicit_rejection(self.api, tool, changed).issues)
        return ReadCheck(3, tuple(issues))

    def check_mixed_batch(self) -> ReadCheck:
        """合法+真实不存在+重复混合批量必须逐位置成功/FACTOR_NOT_FOUND，不整体丢失已存在项。"""
        valid = self.arguments("factor_get_details_batch")["factor_refs"][0]
        absent = self.repository.absent_factor_ref()
        refs = [valid, absent, valid]
        items = read_tool_page(self.api.call_tool("factor_get_details_batch", {"factor_refs": refs, "detail_level": "summary"})).items
        issues = []
        if [row.get("factor_ref") for row in items] != refs:
            issues.append("catalog:mixed_batch_order")
        for index, row in enumerate(items):
            if index == 1:
                error = row.get("error")
                if row.get("success") is not False or not isinstance(error, dict) or error.get("code") != "FACTOR_NOT_FOUND" or row.get("data") not in (None, {}):
                    issues.append("catalog:missing_batch_item_not_isolated")
            elif row.get("success") is not True or not isinstance(row.get("data"), dict) or row["data"].get("factor_ref") != valid:
                issues.append("catalog:existing_batch_item_lost")
        return ReadCheck(3, tuple(issues))

    def check_session_binding(self, mode: str) -> ReadCheck:
        """stateful服务对缺失/伪造Session返回明确400/404且无result；stateless按事实标不可适用。"""
        if not self.api.has_session:
            raise ReadPrecondition("NOT_APPLICABLE: initialize exposes no MCP Session ID (stateless service)")
        raw = json.dumps({"jsonrpc": "2.0", "id": "session-check", "method": "tools/list", "params": {}}).encode()
        response = self.api.probe_protocol(raw, session_mode=mode)
        ok = response.status_code in {400, 404} and isinstance(response.envelope.get("error"), dict) and "result" not in response.envelope
        return ReadCheck(1, () if ok else ("protocol:invalid_session_not_rejected",))

    def check_write_schema_only(self) -> ReadCheck:
        """只读取反馈写工具schema，不调用工具；required/property/additionalProperties声明必须完整。"""
        rows = Factor4ProtocolService(self.api).tool_descriptors()
        selected = next((row for row in rows if row.get("name") == "submit_backtest_factor_feedback"), None)
        if selected is None:
            return ReadCheck(1, ("protocol:feedback_write_descriptor_missing",))
        schema = selected.get("inputSchema") or {}
        required, properties = schema.get("required"), schema.get("properties")
        ok = schema.get("additionalProperties") is False and isinstance(required, list) and bool(required) and isinstance(properties, dict) and set(required) <= set(properties)
        return ReadCheck(1, () if ok else ("protocol:feedback_write_schema_invalid",))

    def check_authentication(self, mode: str, method: str) -> ReadCheck:
        """无/假/畸形Bearer的initialize或catalog读必须401/403明确拒绝且无业务数据；不读取别人的token。"""
        if method == "initialize":
            params = {"protocolVersion": self.api.protocol_version or "2025-06-18", "capabilities": {}, "clientInfo": {"name": "QuestTest", "version": "1"}}
        elif method == "tools/call":
            params = {"name": "factor_search", "arguments": {"limit": 1}}
        else:
            raise ValueError("authentication probe method must be initialize or readonly tools/call")
        raw = json.dumps({"jsonrpc": "2.0", "id": "auth-check", "method": method, "params": params}).encode()
        response = self.api.probe_protocol(raw, authentication=mode)
        body = response.envelope
        ok = response.status_code in {401, 403} and isinstance(body.get("error"), dict) and "result" not in body and body.get("data") in (None, {})
        return ReadCheck(1, () if ok else ("protocol:unauthorized_request_not_isolated",))

    def check_untrusted_origin(self) -> ReadCheck:
        """真实Origin安全边界：非许可Origin的只读请求必须403，不能返回工具结果；异常透传。"""
        raw = json.dumps({"jsonrpc": "2.0", "id": "origin-check", "method": "tools/list", "params": {}}).encode()
        response = self.api.probe_protocol(raw, origin="https://questtest-untrusted-origin.invalid")
        ok = response.status_code == 403 and isinstance(response.envelope.get("error"), dict) and "result" not in response.envelope
        return ReadCheck(1, () if ok else ("protocol:untrusted_origin_not_rejected",))

    def check_credential_echo(self, secret: str) -> ReadCheck:
        """握手/目录/schema/详情正文不得回显当前真实认证token；仅返回固定差异文字，不输出token或完整正文。"""
        if not secret:
            raise ReadPrecondition("BLOCKED_ENV: no configured MCP authentication to compare")
        fresh = self.api.new_connection()
        try:
            responses = [fresh.initialize(protocol_version=self.api.protocol_version or "2025-06-18")]
            fresh.notify_initialized()
            responses.append(fresh.list_tools())
            for tool in ("schema_get_factor_fields", "factor_get_detail"):
                response = fresh.call_tool(tool, self.arguments(tool))
                read_tool_page(response)
                responses.append(response)
            leaked = any(secret in json.dumps(response.envelope, ensure_ascii=False) for response in responses)
            return ReadCheck(len(responses), ("protocol:authentication_secret_echoed",) if leaked else ())
        finally:
            fresh.close()
