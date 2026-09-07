"""Factor 4.0 只读端点语义；复用 MCP 传输，不承载业务判断。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from api.factor_data_mcp_api import FactorDataMCPAPI, MCPResponse

FactorKind = Literal["factor", "sub_factor"]
LabelKind = Literal["fact", "forecast"]


@dataclass(frozen=True)
class CatalogFilter:
    """目录业务筛选；None 字段不发送，不与指标 scope 混用。"""

    kind: FactorKind
    library_status: str | None = None
    library_coin_category: str | None = None


class Factor4ReadAPI:
    """封装历史只读脚本使用的目录、环境、指标和标签工具。"""

    def __init__(self, mcp: FactorDataMCPAPI) -> None:
        """输入已受环境门禁保护的 MCP 客户端；不执行 I/O，不抛业务异常。"""
        self.mcp = mcp

    def search_catalog(
        self, filters: CatalogFilter, *, limit: int = 20, cursor: str | None = None,
        query: str | None = None, updated_after: str | None = None,
    ) -> MCPResponse:
        """按目录筛选分页；返回原始 MCP 结果，网络/协议异常透传。"""
        args = {key: value for key, value in asdict(filters).items() if value is not None}
        args["limit"] = limit
        for key, value in {"cursor": cursor, "query": query, "updated_after": updated_after}.items():
            if value is not None:
                args[key] = value
        return self.mcp.call_tool("factor_search", args)

    def catalog_stats(self, filters: CatalogFilter) -> MCPResponse:
        """按相同目录筛选读取统计；返回原始 MCP 结果，网络/协议异常透传。"""
        return self.mcp.call_tool(
            "factor_catalog_stats", {k: v for k, v in asdict(filters).items() if v is not None}
        )

    def daily(
        self, label_kind: LabelKind, *, as_of: str, limit: int = 100,
        environment_date: str | None = None, cursor: str | None = None,
    ) -> MCPResponse:
        """读取指定类型/日期/as-of 的环境页；返回 MCP 结果，传输异常透传。"""
        args = {"label_kind": label_kind, "as_of": as_of, "limit": limit}
        if environment_date is not None:
            args["environment_date"] = environment_date
        if cursor is not None:
            args["cursor"] = cursor
        return self.mcp.call_tool("environment_get_daily", args)

    def environment_metrics(
        self, factor_ref: str, market_scope: str, route_profile_key: str, *,
        batch_uid: str | None = None, evaluation_type: str | None = None,
        label_code: str | None = None, limit: int = 100,
    ) -> MCPResponse:
        """读取精确因子/分区的环境指标；可选 batch/TS-CS/label 不做回退；异常透传。"""
        args = {"factor_ref": factor_ref, "market_scope": market_scope,
                "route_profile_key": route_profile_key, "limit": limit}
        for key, value in {"batch_uid": batch_uid, "evaluation_type": evaluation_type,
                           "label_code": label_code}.items():
            if value is not None:
                args[key] = value
        return self.mcp.call_tool("factor_get_environment_metrics", args)

    def environment_tags(
        self, factor_ref: str, market_scope: str, route_profile_key: str,
    ) -> MCPResponse:
        """读取因子当前 publication 的标签；返回 MCP 结果，传输异常透传。"""
        return self.mcp.call_tool("factor_get_environment_tags", {
            "factor_ref": factor_ref, "market_scope": market_scope,
            "route_profile_key": route_profile_key,
        })

    def recommendations(
        self, market_scope: str, route_profile_key: str, *, as_of: str, limit: int = 20,
    ) -> MCPResponse:
        """读取指定时点的在线推荐；不触发计算，返回 MCP 结果，传输异常透传。"""
        return self.mcp.call_tool("environment_get_recommendations", {
            "market_scope": market_scope, "route_profile_key": route_profile_key,
            "as_of": as_of, "limit": limit,
        })
