from service.common.http.json_response_assertion import JSONResponseAssertionService


class TestJSONResponseAssertionService:
    """JSON 响应断言服务的轻量测试。

    请求参数:
        使用内存字典构造接口响应体。
    返回值:
        无返回值；pytest 根据断言判断服务行为是否符合预期。
    """

    def test_success_errors_empty_for_success_body(self):
        """验证成功响应信封不会返回错误。

        请求参数:
            success=True 且包含 data 的响应体。
        返回值:
            错误列表应为空。
        """
        body = {"success": True, "data": {"id": 1}}

        errors = JSONResponseAssertionService.success_errors(body)

        assert errors == []

    def test_success_errors_detects_missing_data(self):
        """验证缺失 data 的成功信封会返回错误。

        请求参数:
            success=True 但不包含 data 的响应体。
        返回值:
            错误列表应包含缺失 data 的说明。
        """
        body = {"success": True}

        errors = JSONResponseAssertionService.success_errors(body)

        assert errors == ["body must contain data"]
