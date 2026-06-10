from __future__ import annotations

from typing import Any

from service.common.http.json_response_assertion import JSONResponseAssertionService


class AdminAssertionService:
    """Admin 模块响应断言服务。

    请求参数:
        不需要实例化，直接通过静态方法校验用户、权限、角色、量化账户等 Admin 响应。
    返回值:
        错误信息列表；空列表表示响应符合接口自身规则。
    """

    @staticmethod
    def success_errors(status_code: int, body: Any) -> list[str]:
        """校验 Admin 成功响应。

        请求参数:
            status_code: HTTP 状态码。
            body: 接口 JSON 响应体。
        返回值:
            错误信息列表。
        """
        errors = []
        if status_code != 200:
            errors.append(f"status_code must be 200, got {status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        return errors

    @staticmethod
    def forbidden_errors(status_code: int) -> list[str]:
        """校验普通用户访问 Admin 接口被拒绝。

        请求参数:
            status_code: HTTP 状态码。
        返回值:
            错误信息列表。
        """
        return [] if status_code == 403 else [f"status_code must be 403, got {status_code}"]
