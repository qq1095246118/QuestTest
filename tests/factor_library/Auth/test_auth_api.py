from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.auth_api import AuthAPI
from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.admin.admin_test_data import AdminTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Auth")
class TestAuthAPI:
    """Auth 接口自动化用例集。

    请求参数:
        使用 config/env.<env> 中的接口地址和账号配置发起 Auth 请求。
    返回值:
        无返回值；pytest 根据类内用例断言判断接口是否通过。
    """

    @allure.title("AU-01 新邮箱注册成功")
    @pytest.mark.xfail(strict=True, reason="测试环境注册接口当前强制要求 invite_code，但新版接口文档未声明该入参。")
    def test_au_01_register_new_email_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: AU-01
        测试目的: 验证新邮箱可以注册成功。

        请求参数:
            email/display_name 使用 auto 唯一值，password 使用固定强密码。
        返回值:
            注册接口应返回 HTTP 200、success=True 和用户 data；注册用户由清理器删除。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        email = test_data_factory.email("au_01")
        try:
            response = AuthAPI().register(email=email, password="Aa123456789!", display_name=email)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        if body.get("success") is not True:
            errors.append("success is not True")
        if not isinstance(body.get("data"), dict):
            errors.append("data is not dict")
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

        user_id = AdminTestDataService.resolve_created_user_id(admin_api, body["data"], email)
        resource_tracker.track("registered_user", user_id, lambda value: admin_api.delete_user(value))

    @allure.title("AU-02 已存在邮箱注册失败")
    def test_au_02_register_duplicate_email_fails(self):
        """Case ID: AU-02
        测试目的: 验证已存在邮箱不能重复注册。

        请求参数:
            email 使用 config/env.<env> 中的管理员邮箱，password 和 display_name 使用固定值。
        返回值:
            接口应返回 400、409 或 422，不应返回 500。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")
        if not settings.factor_email:
            pytest.skip("没有配置登录邮箱")

        try:
            response = AuthAPI().register(
                email=settings.factor_email,
                password="Aa123456789!",
                display_name="duplicate_email_user",
            )
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 409, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("AU-03 密码长度不足注册失败")
    def test_au_03_register_short_password_fails(self, test_data_factory):
        """Case ID: AU-03
        测试目的: 验证注册密码长度不足时返回明确错误。

        请求参数:
            email/display_name 使用 auto 唯一值，password=1234567。
        返回值:
            接口应返回 400 或 422，不应返回 500。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AuthAPI().register(
                email=test_data_factory.email("au_03"),
                password="1234567",
                display_name=test_data_factory.name("user", "au_03"),
            )
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("AU-04 缺少邮箱注册失败")
    def test_au_04_register_missing_email_fails(self):
        """Case ID: AU-04
        测试目的: 验证注册缺少邮箱时返回明确错误。

        请求参数:
            email 为空字符串，password 使用固定强密码。
        返回值:
            接口应返回 400 或 422，不应返回 500。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AuthAPI().register(email="", password="Aa123456789!", display_name="missing_email")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("AU-05 非法邮箱格式注册失败")
    def test_au_05_register_invalid_email_format_fails(self):
        """Case ID: AU-05
        测试目的: 验证注册邮箱格式非法时返回明确错误。

        请求参数:
            email=not-an-email，password 使用固定强密码。
        返回值:
            接口应返回 400 或 422，不应返回 500。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AuthAPI().register(email="not-an-email", password="Aa123456789!", display_name="invalid_email")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("AU-06 有效账号登录成功")
    def test_au_06_login_success(self):
        """Case ID: AU-06
        测试目的: 验证有效账号可以登录因子库后端。

        请求参数:
            使用 config/env.<env> 中配置的 factor_email 和 factor_password。
        返回值:
            登录接口应返回 HTTP 200、success=True、data.token，且 data.user.email 与配置邮箱一致。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")
        if not settings.factor_email or not settings.factor_password:
            pytest.skip("没有配置登录邮箱或密码")

        response = AuthAPI().login()
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        if body.get("success") is not True:
            errors.append("success is not True")
        data = body.get("data")
        if not isinstance(data, dict):
            errors.append("data is not dict")
        elif not data.get("token"):
            errors.append("data.token is missing")
        elif not isinstance(data.get("user"), dict) or data["user"].get("email") != settings.factor_email:
            errors.append("data.user.email mismatch")
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("AU-07 错误密码登录失败")
    def test_au_07_login_wrong_password_fails(self):
        """Case ID: AU-07
        测试目的: 验证错误密码不能登录因子库后端。

        请求参数:
            email 使用 config/env.<env> 中配置的 factor_email，password 使用固定错误值。
        返回值:
            接口应返回 400、401 或 403，且响应内容不应包含 token。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")
        if not settings.factor_email:
            pytest.skip("没有配置登录邮箱")

        try:
            response = AuthAPI().login(email=settings.factor_email, password="wrong-password-for-api-test")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 401, 403}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)
        assert "token" not in str(body).lower()

    @allure.title("AU-08 缺少邮箱登录失败")
    def test_au_08_login_missing_email_fails(self):
        """Case ID: AU-08
        测试目的: 验证缺少邮箱时不能登录。

        请求参数:
            email 为空字符串，password 使用配置中的密码或固定值。
        返回值:
            接口应返回 400、401 或 422，不应返回 500。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AuthAPI().login(email="", password=settings.factor_password or "password")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 401, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("AU-09 缺少密码登录失败")
    def test_au_09_login_missing_password_fails(self):
        """Case ID: AU-09
        测试目的: 验证缺少密码时不能登录。

        请求参数:
            email 使用配置中的管理员邮箱，password 为空字符串。
        返回值:
            接口应返回 400、401 或 422，不应返回 500。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")
        if not settings.factor_email:
            pytest.skip("没有配置登录邮箱")

        try:
            response = AuthAPI().login(email=settings.factor_email, password="")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 401, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("AU-10 非法邮箱格式登录失败")
    def test_au_10_login_invalid_email_format_fails(self):
        """Case ID: AU-10
        测试目的: 验证非法邮箱格式不能登录。

        请求参数:
            email=not-an-email，password 使用任意固定值。
        返回值:
            接口应返回 400、401 或 422，不应返回 500。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AuthAPI().login(email="not-an-email", password="invalid-password")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 401, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("AU-11 连续 5 次错误密码未锁定")
    def test_au_11_five_wrong_password_attempts_do_not_lock_user(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: AU-11
        测试目的: 验证同一用户连续 5 次错误密码后仍未被锁定。

        请求参数:
            先注册 auto 用户，连续 5 次使用错误密码登录，再使用正确密码登录。
        返回值:
            前 5 次错误登录均失败，第 5 次后正确密码登录应成功；用例结束删除 auto 用户。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        email = test_data_factory.email("au_11")
        password = "Aa123456789!"
        create_body = admin_api.create_admin(
            {"email": email, "password": password, "display_name": email, "role": "admin"}
        ).json()
        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body.get("data", {}), email)
        resource_tracker.track("registered_user", user_id, lambda value: admin_api.delete_user(value))

        for _ in range(5):
            try:
                response = AuthAPI().login(email=email, password="WrongPass123!")
            except HTTPError as exc:
                response = HTTPResponseService.from_http_error(exc)
            body = response.json() if response.content else {}
            assert response.status_code in {400, 401, 403}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

        response = AuthAPI().login(email=email, password=password)
        body = response.json()
        if response.status_code != 200 or body.get("success") is not True or not body.get("data", {}).get("token"):
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("AU-12 第 6 次错误密码触发锁定")
    def test_au_12_sixth_wrong_password_locks_user(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: AU-12
        测试目的: 验证同一用户第 6 次错误密码登录后被锁定，锁定后正确密码也不能登录。

        请求参数:
            先注册 auto 用户，连续 6 次使用错误密码登录，再使用正确密码登录。
        返回值:
            第 6 次错误登录返回明确失败，锁定后正确密码登录也返回失败；用例结束解锁并删除 auto 用户。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        email = test_data_factory.email("au_12")
        password = "Aa123456789!"
        create_body = admin_api.create_admin(
            {"email": email, "password": password, "display_name": email, "role": "admin"}
        ).json()
        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body.get("data", {}), email)
        resource_tracker.track("registered_user", user_id, lambda value: admin_api.delete_user(value))
        resource_tracker.track("locked_user", email, lambda value: admin_api.unlock_user(value))

        locked_body = {}
        for _ in range(6):
            try:
                response = AuthAPI().login(email=email, password="WrongPass123!")
            except HTTPError as exc:
                response = HTTPResponseService.from_http_error(exc)
            locked_body = response.json() if response.content else {}
            assert response.status_code in {400, 401, 403}, JSONResponseAssertionService.attach_json("接口返回 JSON", locked_body)

        try:
            correct_response = AuthAPI().login(email=email, password=password)
        except HTTPError as exc:
            correct_response = HTTPResponseService.from_http_error(exc)

        correct_body = correct_response.json() if correct_response.content else {}
        if correct_response.status_code not in {400, 401, 403}:
            JSONResponseAssertionService.fail_with_api_json({"sixth_wrong_password": locked_body, "correct_password": correct_body})

    @allure.title("AU-13 管理解锁后登录成功")
    def test_au_13_unlock_user_then_login_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: AU-13
        测试目的: 验证用户被 6 次错误密码锁定后，管理员解锁可恢复登录。

        请求参数:
            先注册 auto 用户并触发锁定，再调用 Admin unlock，最后使用正确密码登录。
        返回值:
            解锁接口和解锁后的正确密码登录均应成功。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        email = test_data_factory.email("au_13")
        password = "Aa123456789!"
        create_body = admin_api.create_admin(
            {"email": email, "password": password, "display_name": email, "role": "admin"}
        ).json()
        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body.get("data", {}), email)
        resource_tracker.track("registered_user", user_id, lambda value: admin_api.delete_user(value))

        for _ in range(6):
            try:
                AuthAPI().login(email=email, password="WrongPass123!")
            except HTTPError:
                pass

        unlock_response = admin_api.unlock_user(email)
        unlock_body = unlock_response.json()
        if unlock_response.status_code != 200 or unlock_body.get("success") is not True:
            JSONResponseAssertionService.fail_with_api_json(unlock_body)

        login_response = AuthAPI().login(email=email, password=password)
        login_body = login_response.json()
        if login_response.status_code != 200 or login_body.get("success") is not True or not login_body.get("data", {}).get("token"):
            JSONResponseAssertionService.fail_with_api_json(login_body)

    @allure.title("AU-14 有效 token 查询当前用户成功")
    def test_au_14_me_with_valid_token_success(self, token):
        """Case ID: AU-14
        测试目的: 验证有效 token 可以访问当前用户资料。

        请求参数:
            使用 token fixture 返回的管理员 JWT。
        返回值:
            /me 应返回 HTTP 200、success=True，且 data.email 与配置邮箱一致。
        """
        response = AuthAPI().me(token)
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        if body.get("success") is not True:
            errors.append("success is not True")
        if not isinstance(body.get("data"), dict):
            errors.append("data is not dict")
        elif body["data"].get("email") != settings.factor_email:
            errors.append("data.email mismatch")
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("AU-15 未带 token 查询当前用户失败")
    def test_au_15_me_without_token_is_unauthorized(self):
        """Case ID: AU-15
        测试目的: 验证未带 token 不能访问当前用户资料。

        请求参数:
            token 为空字符串。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AuthAPI().me("")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {401, 403}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("AU-16 无效 token 查询当前用户失败")
    def test_au_16_me_invalid_token_is_unauthorized(self):
        """Case ID: AU-16
        测试目的: 验证无效 token 不能访问当前用户资料。

        请求参数:
            token=invalid-token。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AuthAPI().me("invalid-token")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {401, 403}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("AU-17 token 对应用户不存在查询当前用户失败")
    def test_au_17_deleted_user_token_me_fails(self, admin_api, test_data_factory):
        """Case ID: AU-17
        测试目的: 验证 token 对应用户删除后不能继续查询当前用户资料。

        请求参数:
            注册 auto 用户并登录拿 token，随后管理员删除该用户，再调用 /me。
        返回值:
            /me 应返回 401、403 或 404。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        email = test_data_factory.email("au_17")
        password = "Aa123456789!"
        create_body = admin_api.create_admin(
            {"email": email, "password": password, "display_name": email, "role": "admin"}
        ).json()
        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body.get("data", {}), email)
        token = AuthAPI().login(email=email, password=password).json()["data"]["token"]
        admin_api.delete_user(user_id)

        try:
            response = AuthAPI().me(token)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {401, 403, 404}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)
