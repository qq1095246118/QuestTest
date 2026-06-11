from __future__ import annotations

import allure
import pytest

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.factor_library.factors.factor_assertions import FactorAssertionService
from service.factor_library.factors.factor_test_data import FactorTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Scenario")
class TestFactorScenario:
    """factor 模块连贯场景接口自动化用例集。

    请求参数:
        使用管理员 token、auto 测试数据和资源清理器串联主题、因子、子因子接口。
    返回值:
        无返回值；pytest 根据链路断言判断场景是否通过。
    """

    @allure.title("FS-01 主题创建-列表-详情-更新-状态链路")
    def test_fs_01_theme_lifecycle_create_list_detail_update_status(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: FS-01
        测试目的: 验证主题创建后可以在列表和详情中查询，并支持更新与状态变更。

        请求参数:
            使用 auto theme_key 创建主题并登记清理，随后查询列表、详情、更新 cn_name，并置为状态 3。
        返回值:
            链路内每个接口都应返回成功响应，列表中应能找到创建的 theme_key，最终主题状态应为 3。
        """
        name = test_data_factory.name("theme", "fs_01")
        create_response = factor_resource_api.create_theme({"theme_key": name, "theme_name": name, "cn_name": name})
        create_body = create_response.json()
        errors = FactorAssertionService.success_with_data_errors(create_response.status_code, create_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        theme_id = create_body["data"]["id"]
        resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

        list_body = factor_resource_api.list_themes(theme_key=name).json()
        list_data = list_body.get("data")
        items = list_data if isinstance(list_data, list) else list_data.get("items", []) if isinstance(list_data, dict) else []
        assert any(item.get("theme_key") == name for item in items)

        detail_response = factor_resource_api.get_theme(theme_id)
        detail_response_body = detail_response.json()
        errors = FactorAssertionService.success_with_data_errors(detail_response.status_code, detail_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(detail_response_body)

        update_response = factor_resource_api.update_theme(theme_id, {"cn_name": f"{name}_updated"})
        update_response_body = update_response.json()
        errors = FactorAssertionService.success_with_data_errors(update_response.status_code, update_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(update_response_body)

        status_response = factor_resource_api.update_theme_status(theme_id, 3)
        status_response_body = status_response.json()
        errors = FactorAssertionService.success_with_data_errors(status_response.status_code, status_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(status_response_body)
        assert status_response_body["data"].get("status") == 3

    @allure.title("FS-02 因子创建-列表-详情-更新-状态链路")
    def test_fs_02_factor_lifecycle_create_list_detail_update_status(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: FS-02
        测试目的: 验证因子创建后可以查询、更新、变更状态。

        请求参数:
            使用 auto factor_name 创建因子并登记清理，随后查询列表、详情、更新 cn_name、置状态 3。
        返回值:
            链路内每个接口都应返回成功响应，最终因子详情状态应为 3。
        """
        payload = FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "fs_02")
        name = payload["factor_name"]
        create_response = factor_resource_api.create_factor(payload)
        create_body = create_response.json()
        errors = FactorAssertionService.success_with_data_errors(create_response.status_code, create_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        factor_id = create_body["data"]["id"]
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        list_body = factor_resource_api.list_factors(page=1, limit=5, created_by=None).json()
        assert list_body.get("success") is True

        detail_response = factor_resource_api.get_factor(factor_id)
        detail_response_body = detail_response.json()
        errors = FactorAssertionService.success_with_data_errors(detail_response.status_code, detail_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(detail_response_body)

        update_response = factor_resource_api.update_factor(factor_id, {"cn_name": f"{name}_updated"})
        update_response_body = update_response.json()
        errors = FactorAssertionService.success_with_data_errors(update_response.status_code, update_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(update_response_body)

        status_response = factor_resource_api.update_factor_status(factor_id, 3)
        status_response_body = status_response.json()
        errors = FactorAssertionService.success_with_data_errors(status_response.status_code, status_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(status_response_body)
        factor_detail = status_response_body["data"].get("factor_detail")
        assert isinstance(factor_detail, dict)
        assert factor_detail.get("status") == 3

    @allure.title("FS-03 子因子创建-列表-详情-更新-状态-refresh 链路")
    def test_fs_03_sub_factor_lifecycle_create_list_detail_update_status_refresh(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: FS-03
        测试目的: 验证子因子创建后可以查询、更新、变更状态和刷新。

        请求参数:
            使用 auto sub_factor_name 创建子因子并登记清理，随后查询列表、详情、更新 cn_name、置状态 3 和 refresh。
        返回值:
            链路内核心接口应返回成功响应，最终子因子详情状态应为 3，refresh 应返回非 500 的明确结果。
        """
        payload = FactorTestDataService.build_sub_factor_payload(factor_resource_api, test_data_factory, "fs_03")
        name = payload["sub_factor_name"]
        create_response = factor_resource_api.create_sub_factor(payload)
        create_body = create_response.json()
        errors = FactorAssertionService.success_with_data_errors(create_response.status_code, create_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        sub_factor_id = create_body["data"]["id"]
        resource_tracker.track("sub_factor", sub_factor_id, lambda value: factor_resource_api.update_sub_factor_status(value, 3))

        list_body = factor_resource_api.list_sub_factors(page=1, limit=5).json()
        assert list_body.get("success") is True

        detail_response = factor_resource_api.get_sub_factor(sub_factor_id)
        detail_response_body = detail_response.json()
        errors = FactorAssertionService.success_with_data_errors(detail_response.status_code, detail_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(detail_response_body)

        update_response = factor_resource_api.update_sub_factor(sub_factor_id, {"cn_name": f"{name}_updated"})
        update_response_body = update_response.json()
        errors = FactorAssertionService.success_with_data_errors(update_response.status_code, update_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(update_response_body)

        status_response = factor_resource_api.update_sub_factor_status(sub_factor_id, 3)
        status_response_body = status_response.json()
        errors = FactorAssertionService.success_with_data_errors(status_response.status_code, status_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(status_response_body)
        response_data = status_response_body["data"]
        sub_factor_detail = response_data.get("sub_factor_detail")
        actual_status = sub_factor_detail.get("status") if isinstance(sub_factor_detail, dict) else response_data.get("status")
        assert actual_status == 3

        try:
            refresh_response = factor_resource_api.refresh_sub_factor(sub_factor_id)
            assert refresh_response.status_code < 500
        except Exception as exc:
            pytest.fail(f"refresh 子因子接口异常: {exc}")
