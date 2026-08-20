"""组合因子新增协议封装的离线测试。"""

from __future__ import annotations

from typing import Any

from api.agent_api import AgentAPI
from api.auth_api import AuthResponsePayload
from api.client import HTTPClient
from api.performance_api import PerformanceAPI
from api.sub_factor_api import SubFactorAPI
from config.settings import ApiSettings


class RecordingResponse:
    """提供固定状态码和 JSON 正文的响应替身。"""

    status_code = 200
    text = "{}"

    def json(self) -> dict[str, Any]:
        """返回空 JSON 对象。"""

        return {}


class RecordingSession:
    """记录 HTTP 请求参数并返回固定响应。"""

    def __init__(self) -> None:
        """初始化请求记录。"""

        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> RecordingResponse:
        """记录方法、URL 和关键字参数，返回响应替身。"""

        self.calls.append({"method": method, "url": url, **kwargs})
        return RecordingResponse()


def _settings(base_url: str) -> ApiSettings:
    """构造带鉴权的协议测试配置。"""

    return ApiSettings(
        base_url=base_url,
        timeout_seconds=60,
        retry_attempts=0,
        retry_backoff_seconds=0,
        auth_token="jwt-for-test",
    )


class TestFactorComboAPIExtensions:
    """验证新端点的路径、查询参数和用户归属头。"""

    def test_agent_list_sends_same_user_id_header_as_jwt_context(self) -> None:
        """查询 Agent 时必须同时发送 Authorization 和 X-User-Id。"""

        session = RecordingSession()
        AgentAPI(HTTPClient(_settings("https://agent.example.test/api/v2"), session=session)).list_agents(42)

        request = session.calls[0]
        assert request["method"] == "GET"
        assert request["url"] == "https://agent.example.test/api/v2/agents"
        assert request["headers"]["Authorization"] == "Bearer jwt-for-test"
        assert request["headers"]["X-User-Id"] == "42"

    def test_refresh_query_and_sub_factor_query_use_expected_contract(self) -> None:
        """刷新查询使用任务路径，子因子回查固定携带 timeseries 模式。"""

        session = RecordingSession()
        client = HTTPClient(_settings("https://factor.example.test/api/v1"), session=session)
        PerformanceAPI(client).get_refresh_run("refresh/701")
        SubFactorAPI(client).get_sub_factor(801, ic_mode="timeseries")

        assert session.calls[0]["url"] == "https://factor.example.test/api/v1/factor/performance/runs/refresh%2F701"
        assert session.calls[1]["url"] == "https://factor.example.test/api/v1/sub-factors/801"
        assert session.calls[1]["params"] == {"ic_mode": "timeseries"}

    def test_authentication_payload_merges_nested_user_and_top_level_permissions(self) -> None:
        """认证解析同时兼容 data.user 和 data.permissions 的响应布局。"""

        user = AuthResponsePayload.user(
            {
                "data": {
                    "token": "not-used-in-this-test",
                    "permissions": ["use_research_agent"],
                    "user": {"id": 42, "email": "qa@example.test", "status": "approved"},
                }
            }
        )

        assert AuthResponsePayload.user_id(user) == 42
        assert AuthResponsePayload.permissions(user) == frozenset({"use_research_agent"})
