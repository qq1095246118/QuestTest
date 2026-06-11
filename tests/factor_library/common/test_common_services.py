import requests

from service.common.http.http_client import HTTPClient
from service.factor_library.common.auth_session import AuthSessionService
from service.factor_library.common.resource_tracker import ResourceTracker
from service.factor_library.common.test_data_factory import TestDataFactory
from service.factor_library.factors.factor_library_compare import FactorListCompareService
from service.factor_library.factors.factor_library_queries import FactorListDBService, FactorListQuery


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

    def test_factor_list_status_alias_checks_factor_detail_status(self):
        """验证因子列表 status 查询参数会按详情状态校验接口返回。

        请求参数:
            构造 status=2 的因子列表查询参数和 factor_detail.status=3 的内存响应体。
        返回值:
            业务规则校验应返回 status 不一致错误。
        """
        body = {
            "success": True,
            "data": {
                "items": [
                    {
                        "id": 1,
                        "serial_number": "F001",
                        "factor_name": "factor_1",
                        "cn_name": "因子1",
                        "factor_detail": {"factor_id": 1, "is_sub_factor_id": False, "status": 3},
                        "themes": [],
                    }
                ],
                "pagination": {"page": 1, "limit": 5, "total": 1},
            },
        }

        errors = FactorListCompareService.factor_list_business_rule_errors(body, {"status": 2})

        assert errors
        assert "factor_detail.status mismatch" in errors[0]

    def test_factor_list_db_query_status_alias_filters_factor_detail_status(self):
        """验证因子列表 DB 查询会把 status 参数映射为详情状态筛选。

        请求参数:
            使用 status=2 构造 FactorListQuery，并生成 DB 查询过滤条件。
        返回值:
            SQL 条件应 JOIN factors_details，并使用 fd.status 按 status 参数过滤。
        """
        where_sql, params = FactorListDBService.build_filters(FactorListQuery(status=2))

        assert "JOIN factors_details fd" in where_sql
        assert "fd.status = %(factor_detail_status)s" in where_sql
        assert params == {"factor_detail_status": 2}

    def test_factor_list_db_query_uses_updated_at_desc_as_default_sort(self):
        """验证因子列表 DB 查询默认排序与接口默认排序一致。

        请求参数:
            使用未传 sort_by 的 FactorListQuery 和记录 SQL 的假 DB client 查询分页。
        返回值:
            生成的分页 SQL 应按 f.updated_at DESC、f.id DESC 排序。
        """
        executed_sql = []

        class FakeDBClient:
            """记录 DB 查询 SQL 的假只读 client。

            请求参数:
                无。
            返回值:
                提供 fetch_one 和 fetch_all 方法供 FactorListDBService 调用。
            """

            def fetch_one(self, sql, params):
                """记录 count SQL 并返回空总数。

                请求参数:
                    sql: 待执行 SQL。
                    params: SQL 参数。
                返回值:
                    total=0 的查询结果。
                """
                executed_sql.append(sql)
                return {"total": 0}

            def fetch_all(self, sql, params):
                """记录分页 SQL 并返回空列表。

                请求参数:
                    sql: 待执行 SQL。
                    params: SQL 参数。
                返回值:
                    空列表，表示当前页无数据。
                """
                executed_sql.append(sql)
                return []

        FactorListDBService.fetch_factor_list_page(FakeDBClient(), FactorListQuery(page=1, limit=5))

        assert "ORDER BY sort_value DESC, f.id DESC" in executed_sql[1]
        assert "f.updated_at AS sort_value" in executed_sql[1]

    def test_factor_list_db_query_maps_user_display_names_like_api_response(self):
        """验证因子列表 DB 查询按接口展示口径映射创建人和操作人。

        请求参数:
            使用记录 SQL 的假 DB client 调用 fetch_factors。
        返回值:
            查询 SQL 应关联 app_users，并优先使用非空 display_name 作为 created_by/operator_by。
        """
        executed_sql = []

        class FakeDBClient:
            """记录 fetch_factors SQL 的假只读 client。

            请求参数:
                无。
            返回值:
                提供 fetch_all 方法供 FactorListDBService.fetch_factors 调用。
            """

            def fetch_all(self, sql, params):
                """记录 SQL 并返回空列表。

                请求参数:
                    sql: 待执行 SQL。
                    params: SQL 参数。
                返回值:
                    空列表。
                """
                executed_sql.append(sql)
                return []

        FactorListDBService.fetch_factors(FakeDBClient(), [1])

        sql = executed_sql[0]
        assert "LEFT JOIN app_users created_user ON created_user.id = f.created_by_uid" in sql
        assert "LEFT JOIN app_users operator_user ON operator_user.id = f.operator_by_uid" in sql
        assert "COALESCE(NULLIF(TRIM(created_user.display_name), ''), f.created_by) AS created_by" in sql
        assert "COALESCE(NULLIF(TRIM(operator_user.display_name), ''), f.operator_by) AS operator_by" in sql
