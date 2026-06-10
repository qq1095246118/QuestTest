"""因子库鉴权会话服务。"""

from __future__ import annotations

from typing import Any

from api.platform.auth_api import AuthAPI


class AuthSessionService:
    """因子库鉴权会话辅助服务。

    请求参数:
        可选 AuthAPI 实例；不传时使用默认 AuthAPI。
    返回值:
        提供登录、注册和 token 提取能力的 service 实例。
    """

    def __init__(self, auth_api: AuthAPI | None = None):
        """初始化鉴权会话服务。

        请求参数:
            auth_api: 可选 AuthAPI 实例。
        返回值:
            无，实例化后保存 AuthAPI 客户端。
        """
        self.auth_api = auth_api or AuthAPI()

    def login_and_get_token(self, email: str | None = None, password: str | None = None) -> str:
        """登录并返回 token。

        请求参数:
            email: 登录邮箱；不传时由 AuthAPI 使用配置账号。
            password: 登录密码；不传时由 AuthAPI 使用配置密码。
        返回值:
            登录响应中的 data.token 字符串。
        """
        response = self.auth_api.login(email=email, password=password)
        return self.extract_token(response.json())

    def register_user(self, email: str, password: str, display_name: str) -> dict[str, Any]:
        """注册用户并返回响应 data。

        请求参数:
            email: 注册邮箱。
            password: 注册密码。
            display_name: 用户展示名。
        返回值:
            注册响应中的 data 字典。
        """
        response = self.auth_api.register(email=email, password=password, display_name=display_name)
        body = response.json()
        data = body.get("data")
        if not isinstance(data, dict):
            raise AssertionError(f"register response missing data: {body}")
        return data

    @staticmethod
    def extract_token(body: Any) -> str:
        """从登录成功响应中提取 token。

        请求参数:
            body: 登录接口响应 JSON 解析结果。
        返回值:
            data.token 字符串；响应结构不符合预期时抛出 AssertionError。
        """
        if not isinstance(body, dict):
            raise AssertionError(f"login response body must be dict: {body}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise AssertionError(f"login response missing data: {body}")
        token = data.get("token")
        if not token:
            raise AssertionError(f"login response missing token: {body}")
        return str(token)
