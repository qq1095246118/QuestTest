import requests

from service.common.http.http_client import HTTPClient
from service.factor_library.common.auth_session import AuthSessionService
from service.factor_library.common.resource_tracker import ResourceTracker
from service.factor_library.common.test_data_factory import TestDataFactory


class TestCommonServices:
    """因子库通用 service 单元测试。

    请求参数:
        使用内存对象和假 API 客户端模拟自动化测试数据、资源清理和登录响应。
    返回值:
        无返回值；pytest 根据断言判断 service 行为是否符合自动化框架约定。
    """

    def test_test_data_factory_generates_unique_auto_test_values(self):
        """验证测试数据工厂生成带 auto_test 前缀的唯一值。

        请求参数:
            使用固定 run_id 创建 TestDataFactory。
        返回值:
            生成的名称和邮箱应包含 auto_test 前缀、run_id 和业务标识，并且多次调用不重复。
        """
        factory = TestDataFactory(run_id="20260610123000")

        first_name = factory.name("factor", "create")
        second_name = factory.name("factor", "create")
        email = factory.email("admin")

        assert first_name.startswith("auto_test_20260610123000_factor_create_")
        assert second_name.startswith("auto_test_20260610123000_factor_create_")
        assert first_name != second_name
        assert email.endswith("@example.com")
        assert "auto_test_20260610123000_admin_" in email

    def test_resource_tracker_cleans_resources_in_reverse_order(self):
        """验证资源清理器按登记逆序调用清理函数。

        请求参数:
            使用两个内存 cleanup 函数登记资源。
        返回值:
            cleanup_all 后调用顺序应为后登记的资源先清理，且 tracked 记录被清空。
        """
        cleaned = []
        tracker = ResourceTracker()

        tracker.track("role", "role_1", lambda value: cleaned.append(("role", value)))
        tracker.track("prompt", 8, lambda value: cleaned.append(("prompt", value)))
        tracker.cleanup_all()

        assert cleaned == [("prompt", 8), ("role", "role_1")]
        assert tracker.resources == []

    def test_resource_tracker_records_cleanup_errors_without_raising(self):
        """验证资源清理失败会被记录但不会抛出异常。

        请求参数:
            使用一个会抛出 RuntimeError 的 cleanup 函数登记资源。
        返回值:
            cleanup_all 应返回清理失败信息，并清空 tracked 记录。
        """
        tracker = ResourceTracker()

        def failing_cleanup(value):
            """模拟清理接口失败。

            请求参数:
                value: 待清理资源标识。
            返回值:
                无；调用时抛出 RuntimeError。
            """
            raise RuntimeError(f"cleanup failed for {value}")

        tracker.track("role", "role_1", failing_cleanup)

        errors = tracker.cleanup_all()

        assert errors == [{"resource_type": "role", "value": "role_1", "error": "cleanup failed for role_1"}]
        assert tracker.resources == []

    def test_auth_session_extracts_token_from_success_response(self):
        """验证 Auth session 能从登录成功响应中提取 token。

        请求参数:
            使用内存响应体模拟登录成功 JSON。
        返回值:
            返回值应为 data.token 字符串。
        """
        body = {"success": True, "data": {"token": "jwt-token", "user": {"email": "auto@example.com"}}}

        token = AuthSessionService.extract_token(body)

        assert token == "jwt-token"

    def test_http_client_retries_ssl_error_and_uses_60_second_timeout(self, monkeypatch):
        """验证 HTTPClient 会重试临时 SSL 断连并默认使用 60 秒连接和读取超时。

        请求参数:
            使用 monkeypatch 模拟第一次 requests.request 抛出 SSLError，第二次返回成功响应。
        返回值:
            HTTPClient.request 应返回第二次成功响应，且两次请求都使用 timeout=(60, 60)。
        """
        calls = []

        class FakeResponse:
            """模拟 requests.Response 成功响应。

            请求参数:
                无。
            返回值:
                提供 status_code、url 和 raise_for_status 供 HTTPClient 使用。
            """

            status_code = 200
            url = "https://test-factor-backend.questvector.ai/api/v1/admin/role-templates"

            def raise_for_status(self):
                """模拟成功响应不抛出 HTTPError。

                请求参数:
                    无。
                返回值:
                    None。
                """
                return None

        def fake_request(method, url, **kwargs):
            """模拟 requests.request 的临时 SSL 失败和后续成功。

            请求参数:
                method: HTTP 方法。
                url: 请求地址。
                **kwargs: HTTPClient 透传的请求参数。
            返回值:
                第二次调用返回 FakeResponse；第一次调用抛出 SSLError。
            """
            calls.append({"method": method, "url": url, "kwargs": kwargs})
            if len(calls) == 1:
                raise requests.exceptions.SSLError("unexpected eof")
            return FakeResponse()

        monkeypatch.setattr(requests, "request", fake_request)
        monkeypatch.setattr(HTTPClient.request.retry, "sleep", lambda retry_state: None)

        response = HTTPClient.request(
            "GET",
            "https://test-factor-backend.questvector.ai/api/v1/admin/role-templates",
        )

        assert response.status_code == 200
        assert len(calls) == 2
        assert calls[0]["kwargs"]["timeout"] == (60, 60)
        assert calls[1]["kwargs"]["timeout"] == (60, 60)
