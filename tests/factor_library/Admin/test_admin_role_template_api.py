from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.admin.admin_assertions import AdminAssertionService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Admin")
class TestAdminRoleTemplateAPI:
    """Admin 角色模板接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的角色模板参数发起 Admin 接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    def assert_admin_success(self, response, body) -> None:
        """断言 Admin 成功响应符合接口自身规则。

        请求参数:
            response: Admin 接口原始 HTTP 响应对象。
            body: Admin 接口返回的原始 JSON。
        返回值:
            无；响应错误时输出接口原始 JSON。
        """
        errors = AdminAssertionService.success_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("ADR-01 角色模板列表查询成功")
    def test_adr_01_list_role_templates_success(self, admin_api):
        """Case ID: ADR-01
        测试目的: 验证角色模板列表接口返回成功响应。

        请求参数:
            不传查询参数。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.list_role_templates()
        self.assert_admin_success(response, response.json())

    @allure.title("ADR-02 创建角色模板成功")
    def test_adr_02_create_role_template_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADR-02
        测试目的: 验证管理员可以创建自动化角色模板。

        请求参数:
            role_name/display_name 使用 auto 唯一值。
        返回值:
            创建角色模板接口应返回成功响应。
        """
        role_name = test_data_factory.role_name("adr_02")
        response = admin_api.create_role_template(
            {"role_name": role_name, "display_name": role_name, "description": "auto", "permissions": []}
        )
        body = response.json()

        self.assert_admin_success(response, body)
        resource_tracker.track("role_template", role_name, lambda value: admin_api.delete_role_template(value))

    @allure.title("ADR-03 重复角色模板创建失败")
    def test_adr_03_create_duplicate_role_template_fails(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADR-03
        测试目的: 验证重复 role_name 不能创建角色模板。

        请求参数:
            连续两次使用相同 role_name 创建角色模板。
        返回值:
            第二次应返回 400、409 或 422。
        """
        role_name = test_data_factory.role_name("adr_03")
        payload = {"role_name": role_name, "display_name": role_name, "description": "auto", "permissions": []}
        admin_api.create_role_template(payload)
        resource_tracker.track("role_template", role_name, lambda value: admin_api.delete_role_template(value))

        try:
            response = admin_api.create_role_template(payload)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 409, 422}

    @allure.title("ADR-04 查询角色模板详情成功")
    def test_adr_04_get_role_template_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADR-04
        测试目的: 验证可以按 role_name 查询角色模板详情。

        请求参数:
            先创建角色模板，再查询该 role_name。
        返回值:
            详情接口应返回成功响应。
        """
        role_name = test_data_factory.role_name("adr_04")
        admin_api.create_role_template({"role_name": role_name, "display_name": role_name, "permissions": []})
        resource_tracker.track("role_template", role_name, lambda value: admin_api.delete_role_template(value))

        response = admin_api.get_role_template(role_name)
        self.assert_admin_success(response, response.json())

    @allure.title("ADR-05 查询不存在角色模板失败")
    def test_adr_05_get_nonexistent_role_template_fails(self, admin_api):
        """Case ID: ADR-05
        测试目的: 验证查询不存在角色模板时返回明确错误。

        请求参数:
            role_name=auto_missing_role。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = admin_api.get_role_template("auto_missing_role")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("ADR-06 更新角色模板成功")
    def test_adr_06_update_role_template_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADR-06
        测试目的: 验证管理员可以更新角色模板。

        请求参数:
            先创建角色模板，再更新 description。
        返回值:
            更新接口应返回成功响应。
        """
        role_name = test_data_factory.role_name("adr_06")
        admin_api.create_role_template({"role_name": role_name, "display_name": role_name, "permissions": []})
        resource_tracker.track("role_template", role_name, lambda value: admin_api.delete_role_template(value))

        response = admin_api.update_role_template(role_name, {"description": "updated"})
        self.assert_admin_success(response, response.json())

    @allure.title("ADR-07 查询角色模板权限显示名成功")
    def test_adr_07_get_role_permission_names_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADR-07
        测试目的: 验证角色模板权限显示名接口返回成功响应。

        请求参数:
            先创建空权限角色模板，再查询 permission-names。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        role_name = test_data_factory.role_name("adr_07")
        admin_api.create_role_template({"role_name": role_name, "display_name": role_name, "permissions": []})
        resource_tracker.track("role_template", role_name, lambda value: admin_api.delete_role_template(value))

        response = admin_api.list_role_template_permission_names(role_name)
        self.assert_admin_success(response, response.json())

    @allure.title("ADR-08 删除角色模板成功")
    def test_adr_08_delete_role_template_success(self, admin_api, test_data_factory):
        """Case ID: ADR-08
        测试目的: 验证管理员可以删除自动化角色模板。

        请求参数:
            先创建角色模板，再删除该 role_name。
        返回值:
            删除接口应返回成功响应。
        """
        role_name = test_data_factory.role_name("adr_08")
        admin_api.create_role_template({"role_name": role_name, "display_name": role_name, "permissions": []})

        response = admin_api.delete_role_template(role_name)
        self.assert_admin_success(response, response.json())

    @allure.title("ADR-09 删除不存在角色模板失败")
    def test_adr_09_delete_nonexistent_role_template_fails(self, admin_api):
        """Case ID: ADR-09
        测试目的: 验证删除不存在角色模板时返回明确错误。

        请求参数:
            role_name=auto_missing_role。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = admin_api.delete_role_template("auto_missing_role")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("ADR-11 权限定义列表查询成功")
    def test_adr_11_list_permissions_success(self, admin_api):
        """Case ID: ADR-11
        测试目的: 验证权限定义列表接口返回成功响应。

        请求参数:
            不传查询参数。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.list_permissions()
        self.assert_admin_success(response, response.json())

    @allure.title("ADR-12 邀请码列表查询成功")
    def test_adr_12_list_invite_codes_success(self, admin_api):
        """Case ID: ADR-12
        测试目的: 验证邀请码列表接口返回成功响应。

        请求参数:
            不传查询参数。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.list_invite_codes()
        self.assert_admin_success(response, response.json())
