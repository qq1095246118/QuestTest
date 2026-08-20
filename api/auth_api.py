"""认证接口的协议层封装。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from api.client import HTTPClient
from config.settings import ApiSettings


@dataclass(frozen=True)
class AuthenticatedAccount:
    """保存登录后可供多个 API 使用的账号上下文。

    参数 ``api_settings`` 包含当前账号的 JWT 及网络配置，``user_id``、``email``、``status`` 和 ``permissions``
    来自登录后的 ``/me`` 响应。返回值由测试 Fixture 创建；该对象不保存密码，也不把 Token 暴露到日志中。
    """

    api_settings: ApiSettings
    user_id: int
    email: str
    status: str
    permissions: frozenset[str]

    def has_permission(self, permission: str) -> bool:
        """判断当前账号是否具备指定权限。

        参数 ``permission`` 是权限编码。
        返回 ``True`` 表示权限集合中存在完全匹配的编码；空编码或大小写不同的编码不会被视为匹配。
        """

        return permission in self.permissions


class AuthAPI:
    """封装邮箱密码登录和当前用户查询端点。"""

    def __init__(self, client: HTTPClient) -> None:
        """初始化认证 API。

        参数 ``client`` 是配置了基础地址、超时和可选 JWT 的 ``HTTPClient``。
        不返回值；登录和当前用户查询均通过该客户端发送。
        """

        self._client = client

    def login(self, email: str, password: str) -> requests.Response:
        """使用邮箱和密码登录。

        参数 ``email`` 和 ``password`` 是测试账号凭据。
        返回登录接口的原始 HTTP 响应；网络错误直接抛出，登录 POST 不自动重放，避免异常网络下重复累计失败次数。
        """

        return self._client.request(
            "POST",
            "/auth/login",
            json_body={"email": email, "password": password},
            headers={"Content-Type": "application/json"},
            retryable=False,
        )

    def get_current_user(self) -> requests.Response:
        """查询当前 JWT 对应的用户。

        不接收请求参数，鉴权信息由 ``HTTPClient`` 注入。
        返回当前用户接口的原始 HTTP 响应；缺失、无效或过期 JWT 时由服务端返回 401。
        """

        return self._client.request("GET", "/me")


class AuthResponsePayload:
    """解析认证成功响应中的稳定字段。"""

    @staticmethod
    def token(response_json: dict[str, Any]) -> str:
        """读取登录响应中的 JWT。

        参数 ``response_json`` 是登录接口解析后的 JSON 对象。
        返回非空 JWT 字符串；响应结构或字段类型不正确时抛出 ``ValueError``，且异常信息不包含 Token。
        """

        data = response_json.get("data")
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Login response does not contain a non-empty data.token")
        return token.strip()

    @staticmethod
    def user(response_json: dict[str, Any]) -> dict[str, Any]:
        """读取认证响应中的用户对象。

        参数 ``response_json`` 是登录或当前用户接口解析后的 JSON 对象。
        返回用户对象副本；响应缺少 ``data`` 对象时抛出 ``ValueError``。
        """

        data = response_json.get("data")
        if not isinstance(data, dict):
            raise ValueError("Authentication response does not contain a data object")
        nested_user = data.get("user")
        if isinstance(nested_user, dict):
            user = dict(data)
            user.update(nested_user)
            return user
        return dict(data)

    @staticmethod
    def user_id(user: dict[str, Any]) -> int:
        """从用户对象中读取正整数用户 ID。

        参数 ``user`` 是登录或 ``/me`` 响应中的用户对象，兼容 ``id``、``user_id`` 和 ``uid`` 三种常见字段名。
        返回正整数用户 ID；字段缺失、类型错误或数值非正时抛出 ``ValueError``。
        """

        for field_name in ("id", "user_id", "uid"):
            value = user.get(field_name)
            if value is None or isinstance(value, bool):
                continue
            try:
                normalized = int(value)
            except (TypeError, ValueError):
                continue
            if normalized > 0:
                return normalized
        raise ValueError("Authentication response does not contain a positive user id")

    @staticmethod
    def permissions(user: dict[str, Any]) -> frozenset[str]:
        """从用户对象中读取权限编码集合。

        参数 ``user`` 是登录或 ``/me`` 响应中的用户对象。
        返回去除空白后的不可变权限集合；权限字段缺失或不是字符串数组时抛出 ``ValueError``。
        """

        raw_permissions = user.get("permissions")
        if not isinstance(raw_permissions, (list, tuple, set, frozenset)):
            raise ValueError("Authentication response does not contain a permissions list")
        permissions = {str(item).strip() for item in raw_permissions if isinstance(item, str) and item.strip()}
        return frozenset(permissions)
