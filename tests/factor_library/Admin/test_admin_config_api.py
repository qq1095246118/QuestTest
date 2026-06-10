from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.admin.admin_assertions import AdminAssertionService
from service.factor_library.admin.admin_test_data import AdminTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Admin")
class TestAdminConfigAPI:
    """Admin 配置和评价标准接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的配置、评价标准参数发起 Admin 接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    @allure.title("ADC-01 更新 Agent Factory 配置成功")
    def test_adc_01_update_agent_factory_config_success(self, admin_api, factor_resource_api, resource_tracker):
        """Case ID: ADC-01
        测试目的: 验证管理员可以更新 Agent Factory 配置。

        请求参数:
            先读取 coin_category=main 的当前配置，再用相同配置调用更新接口。
        返回值:
            更新接口应返回成功响应；用例结束时按原配置恢复，避免污染全局配置。
        """
        current_response = factor_resource_api.get_agent_factory_config(coin_category="main")
        current_body = current_response.json()
        current_data = current_body.get("data")
        if not isinstance(current_data, dict):
            JSONResponseAssertionService.fail_with_api_json(current_body)

        restore_payload = dict(current_data)
        resource_tracker.track(
            "agent_factory_config",
            restore_payload,
            lambda value: admin_api.update_agent_factory_config(value, coin_category=value.get("coin_category", "main")),
        )

        response = admin_api.update_agent_factory_config(restore_payload, coin_category=restore_payload.get("coin_category", "main"))
        body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("ADC-02 非法 Agent Factory 配置更新失败")
    def test_adc_02_update_agent_factory_config_invalid_body_fails(self, admin_api):
        """Case ID: ADC-02
        测试目的: 验证 Agent Factory 配置非法 body 返回明确错误。

        请求参数:
            body 中 agent_enabled 使用非法字符串。
        返回值:
            接口应返回 400、401、403 或 422。
        """
        try:
            response = admin_api.update_agent_factory_config({"agent_enabled": "bad"}, coin_category="main")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 401, 403, 422}

    @allure.title("ADC-03 创建因子评价标准暂不执行")
    def test_adc_03_create_factor_evaluation_standard_explicit_result(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADC-03
        测试目的: 验证管理员可以创建并更新自动化因子评价标准。

        请求参数:
            time_window=1d，coin_category 使用 auto_test 唯一值。
        返回值:
            创建和更新接口都应返回成功响应；用例结束后通过删除接口清理。
        """
        payload = AdminTestDataService.build_factor_evaluation_standard_payload(test_data_factory, "adc_03")
        create_response = admin_api.create_factor_evaluation_standard(payload)
        create_body = create_response.json()
        errors = AdminAssertionService.success_errors(create_response.status_code, create_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        standard_id = create_body.get("data", {}).get("id")
        if not standard_id:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        resource_tracker.track("factor_evaluation_standard", standard_id, lambda value: admin_api.delete_factor_evaluation_standard(value))

        update_payload = dict(payload)
        update_payload["ic_good_min"] = 0.02
        update_response = admin_api.update_factor_evaluation_standard(standard_id, update_payload)
        update_body = update_response.json()
        errors = AdminAssertionService.success_errors(update_response.status_code, update_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(update_body)


    @allure.title("ADC-05 更新不存在因子评价标准失败")
    def test_adc_05_update_factor_evaluation_standard_fails_for_missing_id(self, admin_api):
        """Case ID: ADC-05
        测试目的: 验证更新不存在因子评价标准时返回明确错误。

        请求参数:
            id=999999999，time_window=1d。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = admin_api.update_factor_evaluation_standard(999999999, {"time_window": "1d"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("ADC-07 删除不存在因子评价标准失败")
    def test_adc_07_delete_nonexistent_factor_evaluation_standard_fails(self, admin_api):
        """Case ID: ADC-07
        测试目的: 验证删除不存在因子评价标准时返回明确错误。

        请求参数:
            id=999999999。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = admin_api.delete_factor_evaluation_standard(999999999)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("ADC-09 Agent Factory 公共配置查询成功")
    def test_adc_09_public_agent_factory_config_success(self, factor_resource_api):
        """Case ID: ADC-09
        测试目的: 验证公共 Agent Factory 配置查询接口返回成功响应。

        请求参数:
            coin_category=main。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.get_agent_factory_config(coin_category="main")
        body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, body)
        assert errors == []
