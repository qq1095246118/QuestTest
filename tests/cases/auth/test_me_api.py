"""当前登录用户接口的协议层测试。"""

from __future__ import annotations

from unittest.mock import Mock

import requests

from api.auth_api import AuthAPI, AuthResponsePayload
from api.client import HTTPClient
from config.settings import ApiSettings
from tools.http_response import read_json


class TestCurrentUserAPI:
    """验证当前用户端点使用动态 JWT 并解析用户身份。"""

    def test_get_current_user_sends_dynamic_bearer_token(self) -> None:
        """携带登录取得的 JWT 查询当前用户，并验证端点、超时和响应邮箱。"""

        response = Mock(spec=requests.Response)
        response.status_code = 200
        response.json.return_value = {
            "success": True,
            "data": {"id": 7, "email": "privileged@example.test", "role": "super_admin"},
        }
        session = Mock()
        session.request.return_value = response
        settings = ApiSettings(
            base_url="https://example.test/api/v1",
            timeout_seconds=60,
            retry_attempts=2,
            retry_backoff_seconds=0,
            auth_token="dynamic-jwt",
        )

        actual_response = AuthAPI(HTTPClient(settings, session=session)).get_current_user()
        body = read_json(actual_response)

        assert actual_response.status_code == 200, body
        assert AuthResponsePayload.user(body)["email"] == "privileged@example.test", body
        session.request.assert_called_once_with(
            "GET",
            "https://example.test/api/v1/me",
            params=None,
            json=None,
            headers={"Accept": "application/json", "Authorization": "Bearer dynamic-jwt"},
            timeout=60,
        )
