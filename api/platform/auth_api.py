"""因子库鉴权 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务断言。
"""

from __future__ import annotations

from config.settings import settings
from infrastructure.http.http_client import HTTPClient


class AuthAPI:
    def __init__(self):
        self.base_url = settings.base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}

    def post(self, endpoint: str, json: dict | None = None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("POST", url, headers=self.headers, json=json)

    def login(self, email: str | None = None, password: str | None = None):
        return self.post(
            "/api/v1/auth/login",
            json={
                "email": email or settings.factor_email,
                "password": password or settings.factor_password,
            },
        )
