"""Factor 4.0 正常只读协议流程；异常、并发和性能用例不在本模块冒充覆盖。"""

from __future__ import annotations

from typing import Any

from api.factor_data_mcp_api import FactorDataMCPAPI, JSONRPCId
from service.factor4_read_service import ReadCheck, ReadContractError, read_tool_page


class Factor4ProtocolService:
    """编排协商、目录枚举和串行重读，只返回结构化差异。"""

    def __init__(self, api: FactorDataMCPAPI) -> None:
        """接收已通过测试环境门禁的 API；不访问网络，不返回值。"""
        self.api = api

    def check_handshake(self, protocol_version: str) -> ReadCheck:
        """按配置版本协商并发送通知；返回状态差异，网络/协议异常原样抛出。"""
        response = self.api.initialize(protocol_version=protocol_version)
        result = response.result
        issues: list[str] = []
        if not isinstance(result, dict):
            raise ReadContractError("initialize result is not an object")
        if result.get("protocolVersion") != protocol_version:
            issues.append("protocol:negotiated_version")
        server = result.get("serverInfo")
        if not isinstance(server, dict) or not all(isinstance(server.get(key), str) and server[key] for key in ("name", "version")):
            issues.append("protocol:server_identity")
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
            issues.append("protocol:tools_capability")
        notification = self.api.notify_initialized()
        if notification.status_code not in {200, 202, 204}:
            issues.append("protocol:notification_status")
        return ReadCheck(1, tuple(issues))

    def tool_descriptors(self) -> tuple[dict[str, Any], ...]:
        """分页取得所有声明；返回非空唯一工具表，非法列表/循环游标抛安全契约异常。"""
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(20):
            response = self.api.list_tools(cursor=cursor)
            result = response.result
            page = result.get("tools") if isinstance(result, dict) else None
            if not isinstance(page, list) or not page or any(not isinstance(row, dict) for row in page):
                raise ReadContractError("tools/list requires nonempty tool objects")
            rows.extend(page)
            cursor = result.get("nextCursor")
            if cursor is None:
                return tuple(rows)
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ReadContractError("tools/list returned invalid or repeated cursor")
            seen.add(cursor)
        raise ReadContractError("tools/list exceeds bounded 20-page traversal")

    def check_descriptors(self) -> ReadCheck:
        """核对声明名称唯一及 schema 形态；返回差异，读取/契约异常透传。"""
        rows = self.tool_descriptors()
        issues: list[str] = []
        names = [row.get("name") for row in rows]
        if any(not isinstance(name, str) or not name for name in names):
            issues.append("protocol:tool_name")
        if len(set(str(name) for name in names)) != len(rows):
            issues.append("protocol:duplicate_tool_name")
        required = {
            "factor_search", "factor_catalog_stats", "factor_get_detail", "factor_get_details_batch",
            "environment_get_daily", "factor_get_environment_metrics", "factor_get_environment_tags",
            "environment_get_recommendations", "factor_list_metric_scopes",
            "schema_get_factor_fields", "schema_get_raw_data", "universe_list_symbols",
        }
        if not required <= set(str(name) for name in names):
            issues.append("protocol:missing_read_tool")
        for row in rows:
            schema = row.get("inputSchema")
            if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
                issues.append("protocol:input_schema_shape")
        return ReadCheck(len(rows), tuple(dict.fromkeys(issues)))

    def check_reconnect(self, protocol_version: str) -> ReadCheck:
        """新独立连接重新握手并读取相同目录/raw schema；自动关闭新连接，网络/协议异常透传。"""
        original_tools = self.tool_descriptors()
        original_schema = read_tool_page(self.api.call_tool("schema_get_raw_data", {})).data
        fresh = self.api.new_connection()
        try:
            service = Factor4ProtocolService(fresh)
            check = service.check_handshake(protocol_version)
            tools = service.tool_descriptors()
            schema = read_tool_page(fresh.call_tool("schema_get_raw_data", {})).data
            issues = list(check.issues)
            if tools != original_tools:
                issues.append("protocol:reconnected_tool_inventory")
            if not schema or schema != original_schema:
                issues.append("protocol:reconnected_schema")
            return ReadCheck(2, tuple(issues))
        finally:
            fresh.close()

    def check_schema_replay(self, tool: str, request_id: JSONRPCId) -> ReadCheck:
        """串行两次读取固定 schema，含重复 RPC ID；核对业务 data 和回包 ID。

        只允许两个无参 schema 工具。返回字段级问题；非法工具 ValueError，网络/协议错误透传。
        不比较 request_id/quota 等易变 meta，也不把串行重读称作并发或严格双表示测试。
        """
        if tool not in {"schema_get_factor_fields", "schema_get_raw_data"}:
            raise ValueError("only readonly schema tools are allowed")
        first = self.api.call_tool(tool, {}, request_id=request_id)
        second = self.api.call_tool(tool, {}, request_id=request_id)
        first_page, second_page = read_tool_page(first), read_tool_page(second)
        issues: list[str] = []
        if not first_page.data or not second_page.data:
            issues.append("protocol:empty_schema_data")
        if first_page.data != second_page.data:
            issues.append("protocol:schema_replay_difference")
        for response in (first, second):
            if not isinstance(response.envelope, dict) or response.envelope.get("id") != request_id:
                issues.append("protocol:request_id_correlation")
        return ReadCheck(2, tuple(dict.fromkeys(issues)))
