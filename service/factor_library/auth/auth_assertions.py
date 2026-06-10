from __future__ import annotations

from typing import Any

from service.common.http.json_response_assertion import JSONResponseAssertionService


class AuthAssertionService:
    """Auth 模块响应断言服务。

    请求参数:
        不需要实例化，直接通过静态方法校验注册、登录和当前用户资料接口响应。
    返回值:
        错误信息列表；空列表表示响应符合要求。
    """

    @staticmethod
    def login_success_errors(status_code: int, body: Any, expected_email: str) -> list[str]:
        """校验登录成功响应。

        请求参数:
            status_code: 登录接口 HTTP 状态码。
            body: 登录接口 JSON 响应体。
            expected_email: 期望登录邮箱。
        返回值:
            错误信息列表。
        """
        errors = []
        if status_code != 200:
            errors.append(f"status_code must be 200, got {status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            return errors
        data = body["data"]
        if not isinstance(data, dict):
            return ["data must be dict"]
        if not data.get("token"):
            errors.append("data.token is required")
        user = data.get("user")
        if not isinstance(user, dict):
            errors.append("data.user must be dict")
            return errors
        if user.get("email") != expected_email:
            errors.append(f"user.email mismatch: expected={expected_email!r}, actual={user.get('email')!r}")
        return errors

    @staticmethod
    def me_success_errors(status_code: int, body: Any, expected_email: str) -> list[str]:
        """校验当前用户资料响应。

        请求参数:
            status_code: /me 接口 HTTP 状态码。
            body: /me 接口 JSON 响应体。
            expected_email: 期望邮箱。
        返回值:
            错误信息列表。
        """
        errors = []
        if status_code != 200:
            errors.append(f"status_code must be 200, got {status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            return errors
        data = body["data"]
        if not isinstance(data, dict):
            return ["data must be dict"]
        if data.get("email") != expected_email:
            errors.append(f"data.email mismatch: expected={expected_email!r}, actual={data.get('email')!r}")
        return errors
