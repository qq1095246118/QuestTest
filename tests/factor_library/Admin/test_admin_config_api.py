from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from service.common.http.json_response_assertion import JSONResponseAssertionService
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
        errors = []
        if create_response.status_code != 200:
            errors.append(f"status_code={create_response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(create_body))
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
        errors = []
        if update_response.status_code != 200:
            errors.append(f"status_code={update_response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(update_body))
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
            response = exc.response

        assert response is not None
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
            response = exc.response

        assert response is not None
        assert response.status_code in {400, 404, 422}
