"""IC 汇总指标、币种排名与指标 scope 的 MCP 端点语义。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from api.factor_data_mcp_api import FactorDataMCPAPI, MCPResponse

ICScope = Literal["time_series", "cross_sectional"]
RankingMode = Literal["signed", "raw_signed", "absolute_diagnostic"]


@dataclass(frozen=True)
class SummaryScope:
    """一组完整的指标查询维度；interval 为端点参数名，不是 DB 列名。"""

    ic_scope: ICScope
    calculation_mode: str
    interval: str
    factor_window_bars: str
    return_bar_interval: str
    forward_return_bars: int
    universe_key: str
    symbol: str
    window_scope: str
    scoring_version: str


class Factor4SummaryAPI:
    """复用受门禁保护的会话，仅封装请求，不计算业务预期。"""

    def __init__(self, mcp: FactorDataMCPAPI) -> None:
        """输入已握手 MCP 客户端；保存引用，无 I/O 或业务异常。"""
        self.mcp = mcp

    def list_scopes(
        self, *, kind: str, ic_scope: ICScope, interval: str,
        universe_key: str, as_of: str, limit: int = 100,
    ) -> MCPResponse:
        """读取有界 scope 页（此端点无 cursor）；返回原始响应，传输异常透传。"""
        return self.mcp.call_tool("factor_list_metric_scopes", {
            "kind": kind, "ic_scope": ic_scope, "interval": interval,
            "universe_key": universe_key, "as_of": as_of, "limit": limit,
        })

    def metrics(
        self, factor_ref: str, scope: SummaryScope, *, as_of: str, run_id: str | None = None,
    ) -> MCPResponse:
        """读取单实体精确 scope 的最终汇总；省略 run 不做客户端回退，异常透传。"""
        args = {**asdict(scope), "factor_ref": factor_ref, "as_of": as_of}
        if run_id is not None:
            args["run_id"] = run_id
        return self.mcp.call_tool("factor_get_metrics", args)

    def metrics_batch(
        self, factor_refs: tuple[str, ...], scope: SummaryScope, *, as_of: str, run_id: str | None = None,
    ) -> MCPResponse:
        """按一个精确 scope 批量读取汇总；返回原始响应，传输异常透传。"""
        arguments = {
            **asdict(scope), "factor_refs": list(factor_refs), "as_of": as_of,
        }
        if run_id is not None:
            arguments["run_id"] = run_id
        return self.mcp.call_tool("factor_get_metrics_batch", arguments)

    def metrics_query(self, arguments: dict[str, object]) -> MCPResponse:
        """Read metrics with explicit nullable/omitted schema fields for boundary checks.

        Arguments are forwarded unchanged, including intentionally missing values;
        returns the raw endpoint response and propagates transport/protocol errors.
        """
        return self.mcp.call_tool("factor_get_metrics", arguments)

    def rank(
        self, scope: SummaryScope, *, kind: str, as_of: str, metric: str = "mean_ic",
        ranking_mode: RankingMode = "signed", top_k: int = 2, bottom_k: int = 1,
        min_valid_slice_count: int = 0, min_coverage_mean: float = 0,
        require_oos: bool = False, theme: str | None = None,
    ) -> MCPResponse:
        """读取不做 validity 过滤的指标排名；零大小也原样发送，业务/传输异常透传。"""
        arguments = {
            **asdict(scope), "kind": kind, "as_of": as_of, "metric": metric,
            "ranking_mode": ranking_mode, "top_k": top_k, "bottom_k": bottom_k,
            "validity_scope": scope.ic_scope, "min_valid_slice_count": min_valid_slice_count,
            "min_coverage_mean": min_coverage_mean, "require_oos": require_oos,
        }
        if theme is not None:
            arguments["theme"] = theme
        return self.mcp.call_tool("factor_rank", arguments)

    def validity(self, arguments: dict[str, object]) -> MCPResponse:
        """Read one factor validity record using an already-complete argument map."""
        return self.mcp.call_tool("factor_get_validity", arguments)

    def validity_batch(self, arguments: dict[str, object]) -> MCPResponse:
        """Read validity records for a batch of factors."""
        return self.mcp.call_tool("factor_get_validity_batch", arguments)

    def metric_slices(self, arguments: dict[str, object]) -> MCPResponse:
        """Read persisted metric slices for one exact summary scope."""
        return self.mcp.call_tool("factor_get_metric_slices", arguments)

    def research_search(self, scope: SummaryScope, *, kind: str, as_of: str, limit: int = 5,
                        cursor: str | None = None, min_icir: float | None = None,
                        min_rank_icir: float | None = None, min_score: float | None = None,
                        validity: str | None = None, validity_scope: str | None = None, query: str | None = None) -> MCPResponse:
        """Search catalog entities with research metrics, distinct from factor_rank.

        Complete scope and optional thresholds/cursor are forwarded unchanged. Returns
        the raw MCP response; transport/protocol failures propagate without fallback.
        """
        arguments = {**asdict(scope), "kind": kind, "validity_scope": validity_scope or scope.ic_scope,
                     "as_of": as_of, "limit": limit}
        arguments.update({key: value for key, value in {"cursor": cursor, "min_icir": min_icir,
            "min_rank_icir": min_rank_icir, "min_score": min_score, "validity": validity, "query": query}.items() if value is not None})
        return self.mcp.call_tool("factor_search", arguments)

    def research_stats(self, scope: SummaryScope, *, kind: str, as_of: str, validity: str | None = None,
                       validity_scope: str | None = None) -> MCPResponse:
        """Count the base research scope or explicit validity; numeric thresholds are not supported.

        Returns the raw MCP response and propagates transport/protocol errors. The
        published statistics endpoint does not accept min_icir/min_rank_icir/min_score.
        """
        arguments = {**asdict(scope), "kind": kind, "validity_scope": validity_scope or scope.ic_scope, "as_of": as_of}
        if validity is not None:
            arguments["validity"] = validity
        return self.mcp.call_tool("factor_catalog_stats", arguments)
