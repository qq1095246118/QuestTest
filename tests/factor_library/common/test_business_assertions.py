from service.factor_library.admin.admin_assertions import AdminAssertionService
from service.factor_library.auth.auth_assertions import AuthAssertionService
from service.factor_library.factor_ic.factor_ic_assertions import FactorICAssertionService
from service.factor_library.factors.factor_assertions import FactorAssertionService


class TestBusinessAssertions:
    """因子库业务断言服务测试。

    请求参数:
        使用内存响应体模拟 Auth、factor、FactorIC 和 Admin 接口返回。
    返回值:
        无返回值；pytest 根据断言判断各模块基础断言服务是否符合用例要求。
    """

    def test_auth_login_success_errors_empty_for_valid_body(self):
        """验证 Auth 登录成功响应断言通过。

        请求参数:
            status_code=200，body 包含 success、data.token 和 data.user.email。
        返回值:
            错误列表应为空。
        """
        body = {"success": True, "data": {"token": "jwt", "user": {"email": "auto@example.com"}}}

        errors = AuthAssertionService.login_success_errors(200, body, "auto@example.com")

        assert errors == []

    def test_factor_list_pagination_errors_detects_limit_mismatch(self):
        """验证 factor 列表分页断言能识别 limit 不一致。

        请求参数:
            body 中 pagination.limit=10，期望 limit=5。
        返回值:
            错误列表应包含 limit mismatch。
        """
        body = {"success": True, "data": {"items": [], "pagination": {"page": 1, "limit": 10, "total": 0}}}

        errors = FactorAssertionService.list_pagination_errors(body, expected_page=1, expected_limit=5)

        assert "pagination.limit mismatch: expected=5, actual=10" in errors

    def test_factor_ic_success_errors_empty_for_success_body(self):
        """验证 FactorIC 成功响应断言通过。

        请求参数:
            status_code=200，body 包含 success=True 和 data。
        返回值:
            错误列表应为空。
        """
        body = {"success": True, "data": []}

        errors = FactorICAssertionService.success_errors(200, body)

        assert errors == []

    def test_factor_ic_metric_list_contains_errors_empty_for_matching_summary_metric(self):
        """验证 FactorIC 指标列表断言能定位本次写入的 summary metric。

        请求参数:
            body 中 data.items 包含 factor_id=101、run_id=run_1 和 mean_ic。
        返回值:
            错误列表应为空。
        """
        body = {"success": True, "data": {"items": [{"factor_id": 101, "run_id": "run_1", "mean_ic": 0.12}]}}

        errors = FactorICAssertionService.metric_list_contains_errors(
            body,
            expected_factor_id=101,
            expected_run_id="run_1",
            required_metric_keys=("mean_ic",),
        )

        assert errors == []

    def test_factor_ic_metric_list_contains_errors_reports_missing_metric(self):
        """验证 FactorIC 指标列表断言能识别本次写入记录缺失。

        请求参数:
            body 中 data.items 只有其他 run_id 的记录。
        返回值:
            错误列表应说明未找到目标指标记录。
        """
        body = {"success": True, "data": {"items": [{"factor_id": 101, "run_id": "other_run", "mean_ic": 0.12}]}}

        errors = FactorICAssertionService.metric_list_contains_errors(
            body,
            expected_factor_id=101,
            expected_run_id="run_1",
            expected_symbol="BTCUSDT",
            required_metric_keys=("ic",),
        )

        assert errors == ["metric item not found for factor_id=101, run_id=run_1, symbol=BTCUSDT"]

    def test_admin_forbidden_errors_empty_only_for_403(self):
        """验证 Admin 403 断言只接受 HTTP 403。

        请求参数:
            status_code=403 和 401。
        返回值:
            403 返回空错误列表，401 返回错误说明。
        """
        assert AdminAssertionService.forbidden_errors(403) == []
        assert AdminAssertionService.forbidden_errors(401) == ["status_code must be 403, got 401"]
