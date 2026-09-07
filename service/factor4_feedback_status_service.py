"""本轮调用主体发现、反馈状态所有权与最终字段校验；不写反馈/DB。"""

import json
from dataclasses import dataclass, field
from time import sleep
from typing import Any
from uuid import uuid4

from api.factor4_feedback_status_api import Factor4FeedbackStatusAPI
from api.factor_data_mcp_api import MCPJSONRPCError
from db.factor4_feedback_repository import Factor4FeedbackRepository
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_body, read_tool_page


@dataclass(frozen=True)
class FeedbackCaller:
    """精确审计请求的调用上下文；主体及 ID 不进入默认 repr。"""
    source_system: str = field(repr=False)
    absent_id: str = field(repr=False)
    audit: dict[str, Any] = field(repr=False)


class Factor4FeedbackStatusService:
    """只编排状态读取，不能凭最近任意一条访问日志推断当前 token 的主体。"""

    def __init__(self, api: Factor4FeedbackStatusAPI, repository: Factor4FeedbackRepository) -> None:
        """保存只读 API/Repository；无请求与返回值。"""
        self.api, self.repository = api, repository

    def discover_caller(self) -> FeedbackCaller:
        """用已确认不存在的 UUID 安全读取并关联同 request_id 审计；审计缺失/身份异常失败。"""
        absent_id = str(uuid4())
        if self.repository.submission_exists(absent_id):
            raise ReadContractError("generated feedback discovery ID unexpectedly exists")
        body = read_tool_body(self.api.get_status(absent_id))
        error = body.get("error")
        if not isinstance(error, dict) or error.get("code") != "NOT_FOUND":
            raise ReadContractError("feedback caller discovery did not return NOT_FOUND")
        request_id = error.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ReadContractError("feedback discovery lacks auditable request_id")
        audit = None
        for attempt in range(5):
            audit = self.repository.caller_for_request(request_id, "get_feedback_submission_status")
            if audit:
                break
            if attempt < 4:
                sleep(.1)
        if not audit or audit.get("caller_user_id") is None:
            raise ReadContractError("feedback caller cannot be correlated to exact request audit")
        return FeedbackCaller(f"mcp-user:{audit['caller_user_id']}", absent_id, audit)

    def check_caller_scope(self, caller: FeedbackCaller) -> ReadCheck:
        """核对当前请求所属 active API key、调用者与 required scope 授权；不宣称验证了无权限账号。"""
        audit = caller.audit
        scopes = audit.get("scopes_json")
        if isinstance(scopes, str):
            scopes = json.loads(scopes)
        issues: list[str] = []
        if audit.get("key_status") != "active":
            issues.append("feedback:inactive_caller_key")
        if audit.get("owner_user_id") != audit.get("caller_user_id"):
            issues.append("feedback:caller_owner_identity")
        if not isinstance(scopes, list) or not (
            "mcp.full_access" in scopes or set(str(audit.get("required_scope") or "").split()) <= set(scopes)
        ) or not audit.get("required_scope"):
            issues.append("feedback:required_scope_not_granted")
        if audit.get("error_code") != "NOT_FOUND":
            issues.append("feedback:discovery_audit_result")
        return ReadCheck(1, tuple(issues))

    def check_owned(self, caller: FeedbackCaller) -> ReadCheck:
        """当前主体所有自然提交逐条状态/计数/错误字段对账；无 owned 样本明确数据阻断。"""
        rows = self.repository.submission_samples(caller.source_system, owned=True)
        if not rows:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: current MCP caller owns no feedback submissions")
        issues: list[str] = []
        for row in rows:
            data = read_tool_page(self.api.get_status(row["submission_id"])).data
            for field in ("submission_id", "status", "accepted_count", "rejected_count", "error_code"):
                if field not in data or data[field] != row[field]:
                    issues.append(f"feedback:owned_field={field}")
        return ReadCheck(len(rows), tuple(dict.fromkeys(issues)))

    def check_unavailable(self, caller: FeedbackCaller, *, other_owner: bool) -> ReadCheck:
        """其他主体真实提交与确定不存在 ID 均同样 NOT_FOUND 且不泄漏状态；无其他主体样本阻断。"""
        rows = self.repository.submission_samples(caller.source_system, owned=False) if other_owner else ({"submission_id": caller.absent_id},)
        if not rows:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no other-owner feedback submissions")
        issues: list[str] = []
        for row in rows:
            response = self.api.get_status(row["submission_id"])
            body = read_tool_body(response)
            error = body.get("error")
            if not response.is_tool_error or not isinstance(error, dict) or error.get("code") != "NOT_FOUND":
                issues.append("feedback:unavailable_not_isolated")
            if body.get("data") not in (None, {}):
                issues.append("feedback:unavailable_data_leak")
            details = error.get("details") if isinstance(error, dict) else None
            if isinstance(details, dict) and {"status", "accepted_count", "rejected_count", "source_system"} & details.keys():
                issues.append("feedback:unavailable_error_detail_leak")
        return ReadCheck(len(rows), tuple(dict.fromkeys(issues)))

    def check_invalid_arguments(self, arguments: dict[str, object]) -> ReadCheck:
        """负向输入必须明确拒绝且不得返回其他提交；需异常执行门禁，协议/HTTP 失败直接使 Case 失败。"""
        try:
            response = self.api.get_status_arguments(arguments)
        except MCPJSONRPCError as exc:
            if exc.code == -32602:
                return ReadCheck(1, ())
            raise
        body = read_tool_body(response)
        error = body.get("error")
        issues: list[str] = []
        if not response.is_tool_error or not isinstance(error, dict) or error.get("code") not in {"INVALID_ARGUMENT", "NOT_FOUND"}:
            issues.append("feedback:invalid_input_not_rejected")
        if body.get("data") not in (None, {}):
            issues.append("feedback:invalid_input_data_leak")
        return ReadCheck(1, tuple(issues))
