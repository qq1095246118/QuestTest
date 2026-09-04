"""邮箱密码登录接口的协议层测试。"""

from __future__ import annotations

from unittest.mock import Mock

import requests

from api.auth_api import AuthAPI, AuthResponsePayload
from api.client import HTTPClient
from config.settings import ApiSettings
from tools.http_response import read_json


class TestLoginAPI:
    """验证登录端点、凭据请求体和 JWT 响应解析。"""

    def test_login_sends_credentials_without_bearer_and_does_not_retry_post(self) -> None:
        """发送邮箱密码登录请求，并验证请求不携带旧 JWT 且显式关闭 POST 重试。"""

        response = Mock(spec=requests.Response)
        response.status_code = 200
        response.json.return_value = {
            "success": True,
            "data": {
                "token": "new-jwt",
                "user": {"email": "privileged@example.test"},
            },
        }
        session = Mock()
        session.request.return_value = response
        settings = ApiSettings(
            base_url="https://example.test/api/v1",
            timeout_seconds=60,
            retry_attempts=2,
            retry_backoff_seconds=0,
            auth_token=None,
        )

        actual_response = AuthAPI(HTTPClient(settings, session=session)).login(
            "privileged@example.test",
            "test-password",
        )
        body = read_json(actual_response)

        assert actual_response.status_code == 200, body
        assert AuthResponsePayload.token(body) == "new-jwt", body
        assert AuthResponsePayload.user(body)["email"] == "privileged@example.test", body
        session.request.assert_called_once_with(
            "POST",
            "https://example.test/api/v1/auth/login",
            params=None,
            json={"email": "privileged@example.test", "password": "test-password"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=60,
        )

    def test_login_payload_without_token_is_rejected_by_parser(self) -> None:
        """解析缺少 JWT 的成功响应，并验证框架立即报告响应契约错误。"""

        body = {"success": True, "data": {"user": {"email": "privileged@example.test"}}}

        try:
            AuthResponsePayload.token(body)
        except ValueError as error:
            assert str(error) == "Login response does not contain a non-empty data.token"
        else:
            raise AssertionError("缺少 data.token 的登录响应不应被接受")
