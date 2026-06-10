from __future__ import annotations

import allure
import pytest

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.factor_library.admin.admin_assertions import AdminAssertionService
from service.factor_library.admin.admin_test_data import AdminTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Scenario")
class TestAdminScenario:
    """Admin 连贯场景接口自动化用例集。

    请求参数:
        使用管理员 token、auto 测试数据和资源清理器串联 Admin 业务接口。
    返回值:
        无返回值；pytest 根据链路断言判断场景是否通过。
    """

    @allure.title("ADS-01 角色模板创建-列表-详情-更新-权限名-删除链路")
    def test_ads_01_role_template_lifecycle(self, admin_api, test_data_factory):
        """Case ID: ADS-01
        测试目的: 验证角色模板创建后可以查询、更新、获取权限名并删除。

        请求参数:
            role_name/display_name 使用 auto 唯一值。
        返回值:
            链路内每个接口都应返回成功响应。
        """
        role_name = test_data_factory.role_name("ads_01")
        create_response = admin_api.create_role_template(
            {"role_name": role_name, "display_name": role_name, "description": "auto", "permissions": []}
        )
        create_response_body = create_response.json()
        errors = AdminAssertionService.success_errors(create_response.status_code, create_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(create_response_body)

        list_response = admin_api.list_role_templates()
        list_response_body = list_response.json()
        errors = AdminAssertionService.success_errors(list_response.status_code, list_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(list_response_body)

        detail_response = admin_api.get_role_template(role_name)
        detail_response_body = detail_response.json()
        errors = AdminAssertionService.success_errors(detail_response.status_code, detail_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(detail_response_body)

        update_response = admin_api.update_role_template(role_name, {"description": "updated"})
        update_response_body = update_response.json()
        errors = AdminAssertionService.success_errors(update_response.status_code, update_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(update_response_body)

        permission_response = admin_api.list_role_template_permission_names(role_name)
        permission_response_body = permission_response.json()
        errors = AdminAssertionService.success_errors(permission_response.status_code, permission_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(permission_response_body)

        delete_response = admin_api.delete_role_template(role_name)
        delete_response_body = delete_response.json()
        errors = AdminAssertionService.success_errors(delete_response.status_code, delete_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(delete_response_body)

    @allure.title("ADS-02 用户权限创建-替换-查询-删除链路")
    def test_ads_02_user_permission_lifecycle(self, admin_api, test_data_factory):
        """Case ID: ADS-02
        测试目的: 验证自动化管理员创建后可以替换权限、查询权限并删除。

        请求参数:
            email/display_name 使用 auto 唯一值，perm_codes=[]。
        返回值:
            链路内每个接口都应返回成功响应。
        """
        email = test_data_factory.email("ads_02")
        create_response = admin_api.create_admin(
            {"email": email, "password": "Aa123456789!", "display_name": email, "role": "admin"}
        )
        create_body = create_response.json()
        errors = AdminAssertionService.success_errors(create_response.status_code, create_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body["data"], email)

        set_response = admin_api.replace_user_permissions(user_id, [])
        set_response_body = set_response.json()
        errors = AdminAssertionService.success_errors(set_response.status_code, set_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(set_response_body)

        get_response = admin_api.get_user_permissions(user_id)
        get_response_body = get_response.json()
        errors = AdminAssertionService.success_errors(get_response.status_code, get_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(get_response_body)

        delete_response = admin_api.delete_user(user_id)
        delete_response_body = delete_response.json()
        errors = AdminAssertionService.success_errors(delete_response.status_code, delete_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(delete_response_body)

    @allure.title("ADS-04 提示词创建-列表-更新链路")
    def test_ads_04_prompt_lifecycle(self, admin_api, test_data_factory):
        """Case ID: ADS-04
        测试目的: 验证提示词创建后可以查询列表并更新。

        请求参数:
            name 使用 auto 唯一值，type=system，used_by=api_test。
        返回值:
            链路内每个接口都应返回成功响应；提示词无删除接口，auto_test 数据保留后由人工或定时任务清理。
        """
        name = test_data_factory.prompt_name("ads_04")
        create_response = admin_api.create_prompt(
            {
                "name": name,
                "type": "system",
                "used_by": "api_test",
                "user_prompt": "auto user prompt",
                "system_prompt": "auto system prompt",
            }
        )
        create_body = create_response.json()
        errors = AdminAssertionService.success_errors(create_response.status_code, create_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        prompt_id = create_body["data"]["id"]

        list_response = admin_api.list_prompts(name=name, limit=5)
        list_response_body = list_response.json()
        errors = AdminAssertionService.success_errors(list_response.status_code, list_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(list_response_body)

        update_response = admin_api.update_prompt(prompt_id, {"user_prompt": "updated"})
        update_response_body = update_response.json()
        errors = AdminAssertionService.success_errors(update_response.status_code, update_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(update_response_body)
