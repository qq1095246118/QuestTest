"""反馈状态所有权和授权断言离线反例。"""

import json
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from service.factor4_feedback_status_service import Factor4FeedbackStatusService, FeedbackCaller

pytestmark = pytest.mark.unit


def caller(scopes: list[str]) -> FeedbackCaller:
    """构造无真实凭据的权限审计上下文，无 I/O。"""
    return FeedbackCaller("mcp-user:7", "absent", {"key_status": "active", "owner_user_id": 7, "caller_user_id": 7, "required_scope": "strategy.feedback.read", "scopes_json": scopes, "error_code": "NOT_FOUND"})


@pytest.mark.parametrize("scopes,valid", [(["mcp.full_access"], True), (["strategy.feedback.read"], True), (["factor.read"], False), ([], False)])
def test_read_scope_grants_include_only_explicit_scope_or_full_access(scopes: list[str], valid: bool) -> None:
    service = Factor4FeedbackStatusService(None, None)  # type: ignore[arg-type]
    assert (not service.check_caller_scope(caller(scopes)).issues) is valid


def test_not_found_error_cannot_contain_other_owner_data() -> None:
    class API:
        def get_status(self, submission_id: str) -> MCPResponse:
            """模拟错误标记却泄漏 data 的远端结果；不联网。"""
            body = {"error": {"code": "NOT_FOUND"}, "data": {"status": "accepted"}}
            return MCPResponse(200, None, {"result": {"isError": True, "structuredContent": body, "content": [{"type": "text", "text": json.dumps(body)}]}}, None)
    service = Factor4FeedbackStatusService(API(), None)  # type: ignore[arg-type]
    result = service.check_unavailable(caller(["mcp.full_access"]), other_owner=False)
    assert result.issues == ("feedback:unavailable_data_leak",)
