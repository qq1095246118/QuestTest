"""Factor 4.0 反馈提交的只读状态端点，不公开提交/写入口。"""

from api.factor_data_mcp_api import FactorDataMCPAPI, MCPResponse


class Factor4FeedbackStatusAPI:
    """按提交业务 ID 读取当前调用主体可见的状态。"""

    def __init__(self, mcp: FactorDataMCPAPI) -> None:
        """保存已门禁的 MCP；无请求或返回值。"""
        self.mcp = mcp

    def get_status(self, submission_id: str) -> MCPResponse:
        """返回状态工具原始回包；网络/协议异常透传，业务 NOT_FOUND 保留在回包中。"""
        return self.mcp.call_tool("get_feedback_submission_status", {"submission_id": submission_id})

    def get_status_arguments(self, arguments: dict[str, object]) -> MCPResponse:
        """负向契约读取，原样发送非法/缺失参数；只调用状态工具，不提供写操作。"""
        return self.mcp.call_tool("get_feedback_submission_status", arguments)
