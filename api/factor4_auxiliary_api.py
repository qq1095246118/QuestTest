"""Factor 4.0 候选来源与资产集合的只读端点语义。"""

from datetime import datetime
from typing import Any

from api.factor_data_mcp_api import FactorDataMCPAPI, MCPResponse


class Factor4AuxiliaryAPI:
    """封装 KB 检索和 universe 读取，不包含业务断言。"""

    def __init__(self, mcp: FactorDataMCPAPI) -> None:
        """保存已通过环境门禁的 MCP；无返回与 I/O。"""
        self.mcp = mcp

    def candidate(self, extraction_id: int | None, *, filters: dict[str, Any] | None = None, query: str | None = None) -> MCPResponse:
        """按 extraction ID/可选名称和合法筛选取候选；返回 MCP，网络/协议错误透传。"""
        arguments: dict[str, Any] = {"limit": 10, **(filters or {})}
        if extraction_id is not None:
            arguments["extraction_id"] = extraction_id
        if query is not None:
            arguments["query"] = query
        return self.mcp.call_tool("kb_factor_candidate_search", arguments)

    def universe(self, universe_key: str, as_of: datetime) -> MCPResponse:
        """按指定 key 和固定 UTC 时点读取成员；返回 MCP，网络/协议错误透传。"""
        return self.mcp.call_tool("universe_list_symbols", {"universe_key": universe_key, "as_of": as_of.isoformat()})

    def search_catalog(self, *, kind: str, limit: int = 50, filters: dict[str, Any] | None = None) -> MCPResponse:
        """按实体类别和目录业务筛选读取有界页；返回 MCP，网络/协议错误透传。"""
        return self.mcp.call_tool("factor_search", {"kind": kind, "limit": limit, **(filters or {})})

    def catalog_stats(self, kind: str | None = None, *, filters: dict[str, Any] | None = None) -> MCPResponse:
        """读取全目录或单类别统计；返回 MCP，网络/协议错误透传。"""
        return self.mcp.call_tool("factor_catalog_stats", {**({} if kind is None else {"kind": kind}), **(filters or {})})
