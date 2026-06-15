"""Approval 模块自动化测试数据组装服务。"""

from __future__ import annotations

from typing import Any


class ApprovalTestDataService:
    """Approval 模块测试数据构造服务。

    请求参数:
        不需要实例化，直接通过静态方法构造审批请求 payload。
    返回值:
        提供创建审批、更新审批和状态审批 payload。
    """

    @staticmethod
    def update_request_type(target_type: str) -> str:
        """返回更新审批的后端 request_type。

        请求参数:
            target_type: factor、sub_factor 或 theme。
        返回值:
            后端创建审批接口认可的更新审批类型字符串。
        """
        mapping = {
            "factor": "edit_factor",
            "sub_factor": "edit_sub_factor",
            "theme": "edit_theme",
        }
        return mapping[target_type]

    @staticmethod
    def status_request_type(target_type: str) -> str:
        """返回状态审批的后端 request_type。

        请求参数:
            target_type: factor、sub_factor 或 theme。
        返回值:
            后端创建审批接口认可的状态审批类型字符串。
        """
        mapping = {
            "factor": "status_change_factor",
            "sub_factor": "status_change_sub_factor",
            "theme": "status_change_theme",
        }
        return mapping[target_type]

    @staticmethod
    def build_update_approval_payload(
        target_type: str,
        target_id: int,
        target_name: str,
        before_data: dict[str, Any],
        after_data: dict[str, Any],
    ) -> dict[str, Any]:
        """构造通用更新审批 payload。

        请求参数:
            target_type: factor、sub_factor 或 theme。
            target_id: 目标业务对象 ID。
            target_name: 目标业务对象名称。
            before_data: 更新前数据快照。
            after_data: 更新后数据快照。
        返回值:
            可提交给 POST /api/v1/approvals 的 JSON body。
        """
        return {
            "request_type": ApprovalTestDataService.update_request_type(target_type),
            "target_type": target_type,
            "target_id": target_id,
            "target_name": target_name,
            "operation_desc": f"auto update {target_type}",
            "before_data": before_data,
            "after_data": after_data,
            "change_summary": f"auto update {target_type}",
        }

    @staticmethod
    def build_status_approval_payload(
        target_type: str,
        target_id: int,
        target_name: str,
        before_status: int,
        after_status: int,
    ) -> dict[str, Any]:
        """构造通用状态审批 payload。

        请求参数:
            target_type: factor、sub_factor 或 theme。
            target_id: 目标业务对象 ID。
            target_name: 目标业务对象名称。
            before_status: 当前状态。
            after_status: 目标状态。
        返回值:
            可提交给 POST /api/v1/approvals 的 JSON body。
        """
        return {
            "request_type": ApprovalTestDataService.status_request_type(target_type),
            "target_type": target_type,
            "target_id": target_id,
            "target_name": target_name,
            "operation_desc": f"auto status {target_type}",
            "before_data": {"status": before_status},
            "after_data": {"status": after_status},
            "change_summary": f"status {before_status} -> {after_status}",
        }

    @staticmethod
    def extract_approval_id(body: dict[str, Any]) -> int | None:
        """从审批相关响应体中提取审批 ID。

        请求参数:
            body: 创建审批、with-approval 或批量审批接口返回的 JSON 字典。
        返回值:
            审批 ID；响应中不存在可识别 ID 时返回 None。
        """
        data = body.get("data")
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("id"), int):
            return data["id"]
        if isinstance(data.get("approval_id"), int):
            return data["approval_id"]
        approval = data.get("approval")
        if isinstance(approval, dict) and isinstance(approval.get("id"), int):
            return approval["id"]
        return None
