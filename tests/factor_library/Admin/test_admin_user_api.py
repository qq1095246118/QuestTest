from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.admin_api import AdminAPI
from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.admin.admin_test_data import AdminTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Admin")
class TestAdminUserAPI:
    """Admin 用户与权限接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的用户、权限参数发起 Admin 接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    def first_user_id(self, admin_api) -> int:
        """从用户列表派生一个真实用户 ID。

        请求参数:
            admin_api: Admin API fixture。
        返回值:
            用户 ID；列表为空时跳过当前用例。
        """
        body = admin_api.list_users().json()
        data = body.get("data")
        items = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        if not items:
            pytest.skip("用户列表为空，无法派生 user_id。")
        return items[0]["id"]

    @allure.title("ADU-01 用户列表查询成功")
    def test_adu_01_list_users_success(self, admin_api):
        """Case ID: ADU-01
        测试目的: 验证用户列表接口返回成功响应。

        请求参数:
            不传筛选参数。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.list_users()
        response_body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADU-02 按状态查询用户列表成功")
    def test_adu_02_list_users_filter_by_status_success(self, admin_api):
        """Case ID: ADU-02
        测试目的: 验证用户列表支持 status 筛选。

        请求参数:
            status=active。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.list_users(status="active")
        response_body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADU-03 未带 token 查询用户列表失败")
    def test_adu_03_list_users_without_token_unauthorized(self):
        """Case ID: ADU-03
        测试目的: 验证未带 token 不能访问 Admin 用户列表。

        请求参数:
            不传 Authorization。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AdminAPI().list_users()
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {401, 403}

    @allure.title("ADU-05 创建管理员成功")
    def test_adu_05_create_admin_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADU-05
        测试目的: 验证超级管理员可以创建自动化管理员账号。

        请求参数:
            email/display_name 使用 auto 唯一值，password 使用固定强密码。
        返回值:
            创建管理员接口应返回成功响应。
        """
        email = test_data_factory.email("adu_05")
        payload = {
            "email": email,
            "password": "Aa123456789!",
            "display_name": test_data_factory.name("admin", "adu_05"),
            "role": "admin",
            "notes": "auto",
        }
        response = admin_api.create_admin(payload)
        response_body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, response_body["data"], email)
        resource_tracker.track("admin_user", user_id, lambda value: admin_api.delete_user(value))

    @allure.title("ADU-06 重复管理员邮箱创建失败")
    def test_adu_06_create_duplicate_admin_fails(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADU-06
        测试目的: 验证重复邮箱不能创建管理员。

        请求参数:
            连续两次使用相同 email 创建管理员。
        返回值:
            第二次应返回 400、409 或 422。
        """
        email = test_data_factory.email("adu_06")
        payload = {"email": email, "password": "Aa123456789!", "display_name": email, "role": "admin"}
        created_body = admin_api.create_admin(payload).json()
        user_id = created_body.get("data", {}).get("id")
        if user_id:
            resource_tracker.track("admin_user", user_id, lambda value: admin_api.delete_user(value))

        try:
            response = admin_api.create_admin(payload)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 409, 422}

    @allure.title("ADU-07 更新自动化用户成功")
    def test_adu_07_update_auto_user_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADU-07
        测试目的: 验证管理员可以更新自动化用户资料。

        请求参数:
            先创建管理员，再更新 notes。
        返回值:
            更新用户接口应返回成功响应。
        """
        email = test_data_factory.email("adu_07")
        payload = {
            "email": email,
            "password": "Aa123456789!",
            "display_name": test_data_factory.name("admin", "adu_07"),
            "role": "admin",
            "notes": "auto",
        }
        create_response = admin_api.create_admin(payload)
        create_body = create_response.json()
        create_errors = []
        if create_response.status_code != 200:
            create_errors.append(f"status_code={create_response.status_code}")
        create_errors.extend(JSONResponseAssertionService.success_errors(create_body))
        if create_errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body["data"], email)
        resource_tracker.track("admin_user", user_id, lambda value: admin_api.delete_user(value))

        response = admin_api.update_user(user_id, {"notes": "updated"})
        response_body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADU-08 更新不存在用户失败")
    def test_adu_08_update_nonexistent_user_fails(self, admin_api):
        """Case ID: ADU-08
        测试目的: 验证更新不存在用户时返回明确错误。

        请求参数:
            user_id=999999999，notes=missing。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = admin_api.update_user(999999999, {"notes": "missing"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("ADU-09 删除自动化用户成功")
    def test_adu_09_delete_auto_user_success(self, admin_api, test_data_factory):
        """Case ID: ADU-09
        测试目的: 验证管理员可以删除自动化用户。

        请求参数:
            先创建管理员，再删除该 user_id。
        返回值:
            删除用户接口应返回成功响应。
        """
        email = test_data_factory.email("adu_09")
        payload = {
            "email": email,
            "password": "Aa123456789!",
            "display_name": test_data_factory.name("admin", "adu_09"),
            "role": "admin",
            "notes": "auto",
        }
        create_response = admin_api.create_admin(payload)
        create_body = create_response.json()
        create_errors = []
        if create_response.status_code != 200:
            create_errors.append(f"status_code={create_response.status_code}")
        create_errors.extend(JSONResponseAssertionService.success_errors(create_body))
        if create_errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body["data"], email)

        response = admin_api.delete_user(user_id)
        response_body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADU-10 查询用户权限成功")
    def test_adu_10_get_user_permissions_success(self, admin_api):
        """Case ID: ADU-10
        测试目的: 验证可以查询用户显式权限。

        请求参数:
            从用户列表派生 user_id。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.get_user_permissions(self.first_user_id(admin_api))
        response_body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADU-11 替换用户权限成功")
    def test_adu_11_set_user_permissions_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADU-11
        测试目的: 验证可以替换自动化用户显式权限。

        请求参数:
            先创建管理员，再设置 perm_codes=[]。
        返回值:
            替换权限接口应返回成功响应。
        """
        email = test_data_factory.email("adu_11")
        payload = {
            "email": email,
            "password": "Aa123456789!",
            "display_name": test_data_factory.name("admin", "adu_11"),
            "role": "admin",
            "notes": "auto",
        }
        create_response = admin_api.create_admin(payload)
        create_body = create_response.json()
        create_errors = []
        if create_response.status_code != 200:
            create_errors.append(f"status_code={create_response.status_code}")
        create_errors.extend(JSONResponseAssertionService.success_errors(create_body))
        if create_errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body["data"], email)
        resource_tracker.track("admin_user", user_id, lambda value: admin_api.delete_user(value))

        response = admin_api.replace_user_permissions(user_id, [])
        response_body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADU-12 授予用户权限返回明确结果")
    def test_adu_12_grant_user_permission_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADU-12
        测试目的: 验证授予用户单个权限接口返回明确结果。

        请求参数:
            从权限列表派生 permission code，授予自动化用户。
        返回值:
            接口应成功或返回明确参数错误，不应返回 500。
        """
        email = test_data_factory.email("adu_12")
        payload = {
            "email": email,
            "password": "Aa123456789!",
            "display_name": test_data_factory.name("admin", "adu_12"),
            "role": "admin",
            "notes": "auto",
        }
        create_response = admin_api.create_admin(payload)
        create_body = create_response.json()
        create_errors = []
        if create_response.status_code != 200:
            create_errors.append(f"status_code={create_response.status_code}")
        create_errors.extend(JSONResponseAssertionService.success_errors(create_body))
        if create_errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body["data"], email)
        resource_tracker.track("admin_user", user_id, lambda value: admin_api.delete_user(value))

        permissions_body = admin_api.list_permissions().json()
        permissions = permissions_body.get("data") or []
        if not permissions:
            pytest.skip("权限定义列表为空，无法派生 permission code。")
        code = permissions[0].get("code") or permissions[0].get("perm_code")
        if not code:
            pytest.skip("权限定义首条没有 code。")

        try:
            response = admin_api.grant_user_permission(user_id, code)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code < 500

    @allure.title("ADU-13 撤销用户权限返回明确结果")
    def test_adu_13_revoke_user_permission_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADU-13
        测试目的: 验证撤销用户单个权限接口返回明确结果。

        请求参数:
            从权限列表派生 permission code，撤销自动化用户该权限。
        返回值:
            接口应成功或返回明确参数错误，不应返回 500。
        """
        email = test_data_factory.email("adu_13")
        payload = {
            "email": email,
            "password": "Aa123456789!",
            "display_name": test_data_factory.name("admin", "adu_13"),
            "role": "admin",
            "notes": "auto",
        }
        create_response = admin_api.create_admin(payload)
        create_body = create_response.json()
        create_errors = []
        if create_response.status_code != 200:
            create_errors.append(f"status_code={create_response.status_code}")
        create_errors.extend(JSONResponseAssertionService.success_errors(create_body))
        if create_errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body["data"], email)
        resource_tracker.track("admin_user", user_id, lambda value: admin_api.delete_user(value))

        permissions_body = admin_api.list_permissions().json()
        permissions = permissions_body.get("data") or []
        if not permissions:
            pytest.skip("权限定义列表为空，无法派生 permission code。")
        code = permissions[0].get("code") or permissions[0].get("perm_code")
        if not code:
            pytest.skip("权限定义首条没有 code。")

        try:
            response = admin_api.revoke_user_permission(user_id, code)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code < 500

    @allure.title("ADU-14 解锁不存在用户返回明确结果")
    def test_adu_14_unlock_user_explicit_result_for_nonexistent_email(self, admin_api, test_data_factory):
        """Case ID: ADU-14
        测试目的: 验证解锁用户接口对不存在邮箱返回明确结果。

        请求参数:
            email 使用 auto 不存在邮箱。
        返回值:
            接口应成功或返回 400、404、422，不应返回 500。
        """
        try:
            response = admin_api.unlock_user(test_data_factory.email("adu_14"))
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {200, 400, 404, 422}

    @allure.title("ADU-15 重置自动化管理员密码成功")
    def test_adu_15_reset_auto_admin_password_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADU-15
        测试目的: 验证管理员可以重置自动化管理员密码。

        请求参数:
            先创建管理员，再设置 new_password。
        返回值:
            重置密码接口应返回成功响应。
        """
        email = test_data_factory.email("adu_15")
        payload = {
            "email": email,
            "password": "Aa123456789!",
            "display_name": test_data_factory.name("admin", "adu_15"),
            "role": "admin",
            "notes": "auto",
        }
        create_response = admin_api.create_admin(payload)
        create_body = create_response.json()
        create_errors = []
        if create_response.status_code != 200:
            create_errors.append(f"status_code={create_response.status_code}")
        create_errors.extend(JSONResponseAssertionService.success_errors(create_body))
        if create_errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body["data"], email)
        resource_tracker.track("admin_user", user_id, lambda value: admin_api.delete_user(value))

        response = admin_api.reset_admin_password(user_id, "Bb123456789!")
        response_body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)
