from service.factor_library.admin.admin_test_data import AdminTestDataService
from service.factor_library.common.test_data_factory import TestDataFactory


class FakeJSONResponse:
    """单元测试用 JSON 响应对象。

    请求参数:
        body: 需要由 json 方法返回的响应体。
    返回值:
        提供 json 方法的轻量对象。
    """

    def __init__(self, body):
        """保存单元测试响应体。

        请求参数:
            body: 模拟接口返回的 JSON 数据。
        返回值:
            无，实例化后保存 body。
        """
        self.body = body

    def json(self):
        """返回模拟 JSON 响应体。

        请求参数:
            无。
        返回值:
            初始化时传入的 body。
        """
        return self.body


class FakeAdminAPI:
    """单元测试用 Admin API 对象。

    请求参数:
        users_body: 用户列表接口模拟响应体。
    返回值:
        提供 list_users 方法的轻量对象。
    """

    def __init__(self, users_body):
        """保存用户列表模拟响应体。

        请求参数:
            users_body: 用户列表接口模拟 JSON。
        返回值:
            无，实例化后保存 users_body 和调用次数。
        """
        self.users_body = users_body
        self.list_users_call_count = 0

    def list_users(self):
        """模拟用户列表接口。

        请求参数:
            无。
        返回值:
            携带 users_body 的 FakeJSONResponse。
        """
        self.list_users_call_count += 1
        return FakeJSONResponse(self.users_body)


class TestAdminTestDataService:
    """Admin 测试数据服务单元测试。

    请求参数:
        使用固定 run_id 的 TestDataFactory。
    返回值:
        无返回值；pytest 根据生成 payload 判断 Admin 写入接口参数是否符合后端 schema。
    """

    def test_build_quant_account_payload_uses_schema_types(self):
        """验证量化账号创建 payload 使用后端 schema 支持的字段类型。

        请求参数:
            使用 TestDataFactory 生成 auto 量化账号字段。
        返回值:
            payload 中 status 应为字符串，total_assets_usdt 应为数字。
        """
        factory = TestDataFactory(run_id="20260610150000")

        payload = AdminTestDataService.build_quant_account_payload(factory, "adq_02")

        assert payload["exchange"] == "binance"
        assert payload["status"] == "active"
        assert payload["total_assets_usdt"] == 0
        assert payload["email"].endswith("@example.com")
        assert payload["api_key"].startswith("auto_test_20260610150000_api_key_adq_02_")

    def test_build_factor_evaluation_standard_payload_uses_auto_test_coin_category(self):
        """验证因子评价标准 payload 使用 auto_test 标记和稳定阈值。

        请求参数:
            使用 TestDataFactory 生成 auto_test coin_category。
        返回值:
            payload 应包含 time_window、coin_category 和各类阈值字段。
        """
        factory = TestDataFactory(run_id="20260610150000")

        payload = AdminTestDataService.build_factor_evaluation_standard_payload(factory, "adc_03")

        assert payload["time_window"] == "1d"
        assert payload["coin_category"].startswith("auto_test_20260610150000_standard_adc_03_")
        assert payload["ic_good_min"] == 0.01
        assert payload["icir_better_max"] == 9.99

    def test_resolve_created_user_id_prefers_create_response_id(self):
        """验证创建响应已返回 id 时不再查询用户列表。

        请求参数:
            创建响应 data 带 id，Admin API 使用空用户列表模拟对象。
        返回值:
            应直接返回创建响应中的 id，且不调用 list_users。
        """
        admin_api = FakeAdminAPI({"success": True, "data": []})

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, {"id": 101}, "auto@example.com")

        assert user_id == 101
        assert admin_api.list_users_call_count == 0

    def test_resolve_created_user_id_falls_back_to_user_list_email(self):
        """验证创建响应缺少 id 时可以按邮箱从用户列表反查。

        请求参数:
            创建响应 data 不带 id，用户列表包含同邮箱用户。
        返回值:
            应返回用户列表中精确匹配邮箱的用户 id。
        """
        admin_api = FakeAdminAPI(
            {
                "success": True,
                "data": [
                    {"id": 201, "email": "other@example.com"},
                    {"id": 202, "email": "auto@example.com"},
                ],
            }
        )

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, {"email": "auto@example.com"}, "auto@example.com")

        assert user_id == 202
        assert admin_api.list_users_call_count == 1
