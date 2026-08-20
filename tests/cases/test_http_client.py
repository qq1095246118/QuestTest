"""HTTP 客户端超时和重试策略测试。"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from api.client import HTTPClient
from api.factor_combo_api import FactorComboAPI
from config.settings import ApiSettings


class StubResponse:
    """提供状态码和 JSON 数据的可控 HTTP 响应替身。"""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        """保存响应状态和数据；参数分别对应 HTTP 状态码与 JSON 对象，不执行网络请求。"""

        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        """返回初始化时传入的 JSON 对象；不接收参数，也不会抛出 JSON 解析异常。"""

        return self._payload


class SequenceSession:
    """按顺序返回响应或抛出异常，并记录每次请求参数。"""

    def __init__(self, outcomes: list[Any]) -> None:
        """接收响应或异常序列并初始化请求记录；序列耗尽时由 ``request`` 抛出 ``AssertionError``。"""

        self._outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """记录请求并消费一个预设结果；返回响应对象，预设结果为异常时原样抛出。"""

        self.requests.append({"method": method, "url": url, **kwargs})
        if not self._outcomes:
            raise AssertionError("SequenceSession has no remaining outcome")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class TestHTTPClientRetry:
    """验证网络异常、服务端临时错误和非幂等请求的重试边界。"""

    @staticmethod
    def _settings() -> ApiSettings:
        """构造 60 秒超时和两次重试的测试配置；不接收参数，返回独立 ``ApiSettings``。"""

        return ApiSettings(
            base_url="https://example.test/api/v1",
            timeout_seconds=60,
            retry_attempts=2,
            retry_backoff_seconds=0,
            auth_token="test-token",
        )

    def test_retryable_factor_combo_post_retries_ssl_eof_with_sixty_second_timeout(self) -> None:
        """组合因子幂等 POST 首次发生 SSL 错误时重试，并在每次请求中使用 60 秒超时。"""

        session = SequenceSession(
            [
                requests.exceptions.SSLError("unexpected EOF while reading"),
                StubResponse(202, {"success": True, "data": {"form_id": 1}}),
            ]
        )
        api = FactorComboAPI(HTTPClient(self._settings(), session=session))

        response = api.submit_form({"session_id": 1})

        assert response.status_code == 202, response.json()
        assert len(session.requests) == 2, session.requests
        assert [request["timeout"] for request in session.requests] == [60, 60], session.requests

    def test_retryable_factor_combo_post_retries_temporary_server_error(self) -> None:
        """组合因子幂等 POST 收到 503 时按配置重试，并返回下一次成功响应。"""

        session = SequenceSession(
            [
                StubResponse(503, {"success": False, "error": "temporary unavailable"}),
                StubResponse(202, {"success": True, "data": {"form_id": 1}}),
            ]
        )
        api = FactorComboAPI(HTTPClient(self._settings(), session=session))

        response = api.submit_form({"session_id": 1})

        assert response.status_code == 202, response.json()
        assert len(session.requests) == 2, session.requests

    def test_force_fresh_run_post_does_not_retry_after_transport_error(self) -> None:
        """强制创建新 Run 的 POST 遇到网络错误时不得自动重放，避免服务端已创建后产生重复 Run。"""

        session = SequenceSession(
            [
                requests.exceptions.SSLError("response lost after server accepted request"),
                StubResponse(202, {"success": True, "data": {"pipeline_run_id": "unused"}}),
            ]
        )
        api = FactorComboAPI(HTTPClient(self._settings(), session=session))

        with pytest.raises(requests.exceptions.SSLError):
            api.start_run(
                22,
                {
                    "agent_uid": "agent-1",
                    "force_fresh_pipeline_run": True,
                },
            )

        assert len(session.requests) == 1, session.requests

    def test_non_idempotent_post_does_not_retry_network_error_by_default(self) -> None:
        """未显式声明可重放的普通 POST 遇到网络错误时立即抛出，避免重复创建业务数据。"""

        session = SequenceSession(
            [
                requests.exceptions.SSLError("unexpected EOF while reading"),
                StubResponse(201, {"success": True, "data": {"id": 1}}),
            ]
        )
        client = HTTPClient(self._settings(), session=session)

        with pytest.raises(requests.exceptions.SSLError):
            client.request("POST", "/chat/sessions", json_body={"title": "test"})

        assert len(session.requests) == 1, session.requests
