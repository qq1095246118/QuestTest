"""Admin 模块 DB 只读查询服务。"""

from __future__ import annotations

from typing import Any


class AdminDBQueryService:
    """Admin 模块 DB 只读查询服务。

    请求参数:
        不需要实例化，直接通过静态方法接收只读 DB client。
    返回值:
        提供用户、权限、角色、量化账户和评估标准查询能力。
    """

    @staticmethod
    def fetch_user_by_email(client: Any, email: str) -> dict[str, Any] | None:
        """按邮箱查询用户。

        请求参数:
            client: 只读 DB client。
            email: 用户邮箱。
        返回值:
            用户记录；不存在时返回 None。
        """
        return client.fetch_one(
            """
            SELECT *
            FROM app_users
            WHERE email = %(email)s
            """,
            {"email": email},
        )

    @staticmethod
    def fetch_quant_account_by_id(client: Any, account_id: int) -> dict[str, Any] | None:
        """按 ID 查询量化账户。

        请求参数:
            client: 只读 DB client。
            account_id: 量化账户 ID。
        返回值:
            量化账户记录；不存在时返回 None。
        """
        return client.fetch_one(
            """
            SELECT *
            FROM quant_accounts
            WHERE id = %(account_id)s
            """,
            {"account_id": account_id},
        )
