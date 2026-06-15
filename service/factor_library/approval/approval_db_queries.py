"""Approval 模块 DB 只读查询服务。"""

from __future__ import annotations

from typing import Any


class ApprovalDBQueryService:
    """审批 DB 只读查询服务。

    请求参数:
        不需要实例化，直接通过静态方法接收只读 DB client。
    返回值:
        提供审批列表、审批详情和 pending 占用查询能力。
    """

    @staticmethod
    def fetch_approval_by_id(client: Any, approval_id: int) -> dict[str, Any] | None:
        """按审批 ID 查询审批记录。

        请求参数:
            client: 只读 DB client。
            approval_id: 审批 ID。
        返回值:
            审批记录字典；不存在时返回 None。
        """
        return client.fetch_one(
            """
            SELECT *
            FROM approval_requests
            WHERE id = %(approval_id)s
            """,
            {"approval_id": approval_id},
        )

    @staticmethod
    def fetch_pending_for_target(client: Any, target_type: str, target_id: int) -> list[dict[str, Any]]:
        """查询指定业务对象的 pending 审批。

        请求参数:
            client: 只读 DB client。
            target_type: factor、sub_factor 或 theme。
            target_id: 目标业务对象 ID。
        返回值:
            pending 审批记录列表。
        """
        return client.fetch_all(
            """
            SELECT *
            FROM approval_requests
            WHERE target_type = %(target_type)s
              AND target_id = %(target_id)s
              AND status = 'pending'
            ORDER BY id DESC
            """,
            {"target_type": target_type, "target_id": target_id},
        )

    @staticmethod
    def fetch_logs(client: Any, approval_id: int) -> list[dict[str, Any]]:
        """查询审批日志。

        请求参数:
            client: 只读 DB client。
            approval_id: 审批 ID。
        返回值:
            审批日志列表。
        """
        return client.fetch_all(
            """
            SELECT *
            FROM approval_request_logs
            WHERE approval_request_id = %(approval_id)s
            ORDER BY id ASC
            """,
            {"approval_id": approval_id},
        )
