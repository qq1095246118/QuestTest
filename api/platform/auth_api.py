"""因子库鉴权 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务断言。
"""

from __future__ import annotations

from config.settings import settings
from service.common.http.http_client import HTTPClient


class AuthAPI:
    """因子库鉴权接口原始请求封装。

    请求参数:
        实例化时不接收参数，默认读取当前环境配置。
    返回值:
        提供登录相关 HTTP 请求方法的 API 客户端实例。
    """

    def __init__(self):
        """初始化因子库鉴权 API 客户端。

        请求参数:
            无，默认读取 config/env.<env> 中的 base_url。
        返回值:
            无，实例化后保存 base_url 和默认 JSON 请求头。
        """
        self.base_url = settings.base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}

    def post(self, endpoint: str, json: dict | None = None):
        """向因子库后端发送 POST 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            json: 请求 JSON body。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("POST", url, headers=self.headers, json=json)

    def get(self, endpoint: str, token: str | None = None):
        """向因子库后端发送 GET 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            token: 可选 JWT token；传入后用于 Authorization header。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        headers = dict(self.headers)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=headers, params={})

    def register(self, email: str, password: str, display_name: str):
        """调用因子库注册接口。

        请求参数:
            email: 注册邮箱。
            password: 注册密码。
            display_name: 用户展示名。
        返回值:
            注册接口 requests.Response 对象。
        """
        return self.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "display_name": display_name},
        )

    def login(self, email: str | None = None, password: str | None = None):
        """调用因子库登录接口。

        请求参数:
            email: 登录邮箱；不传时使用配置中的 factor_email。
            password: 登录密码；不传时使用配置中的 factor_password。
        返回值:
            登录接口 requests.Response 对象。
        """
        return self.post(
            "/api/v1/auth/login",
            json={
                "email": settings.factor_email if email is None else email,
                "password": settings.factor_password if password is None else password,
            },
        )

    def me(self, token: str):
        """调用当前登录用户资料接口。

        请求参数:
            token: 登录接口返回的 JWT token。
        返回值:
            当前用户资料接口 requests.Response 对象。
        """
        return self.get("/api/v1/me", token=token)
