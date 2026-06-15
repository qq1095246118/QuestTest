from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.auth_api import AuthAPI
from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.admin.admin_test_data import AdminTestDataService


class KnownRegisterInviteCodeRequired(Exception):
    """测试环境注册接口强制要求 invite_code 时抛出的已知差异异常。"""


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Scenario")
class TestAuthScenario:
    """Auth 场景接口自动化用例集。

    请求参数:
        使用 config/env.<env> 中的接口地址和账号配置串联注册、登录、当前用户资料和解锁接口。
    返回值:
        无返回值；pytest 根据类内用例断言判断链路是否通过。
    """

    @allure.title("AS-01 登录后使用 token 查询当前用户资料")
    def test_as_01_login_then_me_profile_consistent(self):
        """Case ID: AS-01
        测试目的: 验证登录 token 可以获取当前用户资料，且用户邮箱一致。

        请求参数:
            使用 config/env.<env> 中配置的管理员邮箱和密码登录，再用 token 请求 /me。
        返回值:
            登录响应 user.email 和 /me 响应 email 应一致。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")
        if not settings.factor_email or not settings.factor_password:
            pytest.skip("没有配置登录邮箱或密码")

        login_response = AuthAPI().login()
        login_body = login_response.json()
        login_data = login_body.get("data")
        if login_response.status_code != 200 or login_body.get("success") is not True or not isinstance(login_data, dict):
            JSONResponseAssertionService.fail_with_api_json(login_body)
        if not login_data.get("token") or not isinstance(login_data.get("user"), dict):
            JSONResponseAssertionService.fail_with_api_json(login_body)

        me_response = AuthAPI().me(login_data["token"])
        me_body = me_response.json()
        me_data = me_body.get("data")
        if me_response.status_code != 200 or me_body.get("success") is not True or not isinstance(me_data, dict):
            JSONResponseAssertionService.fail_with_api_json(me_body)

        assert login_data["user"]["email"] == me_data["email"]

    @allure.title("AS-02 注册-登录-me 成功链路")
    @pytest.mark.xfail(
        raises=KnownRegisterInviteCodeRequired,
        strict=True,
        reason="测试环境注册接口当前强制要求 invite_code，导致注册登录链路无法按新版文档执行。",
    )
    def test_as_02_register_login_me_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: AS-02
        测试目的: 验证新用户注册后可以登录并查询当前用户资料。

        请求参数:
            email/display_name 使用 auto 唯一值，password 使用固定强密码。
        返回值:
            注册、登录、/me 均成功，/me 邮箱与注册邮箱一致；用例结束删除 auto 用户。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        email = test_data_factory.email("as_02")
        password = "Aa123456789!"
        try:
            register_response = AuthAPI().register(email=email, password=password, display_name=email)
        except HTTPError as exc:
            register_response = HTTPResponseService.from_http_error(exc)
        register_body = register_response.json()
        if (
            register_response.status_code in {400, 422}
            and register_body.get("success") is False
            and register_body.get("error") == "invite_code is required"
        ):
            raise KnownRegisterInviteCodeRequired(register_body["error"])
        if register_response.status_code != 200 or register_body.get("success") is not True:
            JSONResponseAssertionService.fail_with_api_json(register_body)
        user_id = AdminTestDataService.resolve_created_user_id(admin_api, register_body.get("data", {}), email)
        resource_tracker.track("registered_user", user_id, lambda value: admin_api.delete_user(value))

        login_response = AuthAPI().login(email=email, password=password)
        login_body = login_response.json()
        token = login_body.get("data", {}).get("token") if isinstance(login_body.get("data"), dict) else None
        if login_response.status_code != 200 or login_body.get("success") is not True or not token:
            JSONResponseAssertionService.fail_with_api_json(login_body)

        me_response = AuthAPI().me(token)
        me_body = me_response.json()
        if me_response.status_code != 200 or me_body.get("success") is not True:
            JSONResponseAssertionService.fail_with_api_json(me_body)
        assert me_body.get("data", {}).get("email") == email

    @allure.title("AS-03 登录失败 6 次锁定-管理员解锁-登录成功链路")
    def test_as_03_lock_unlock_login_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: AS-03
        测试目的: 验证 6 次错误密码触发锁定，管理员解锁后可以登录成功。

        请求参数:
            先注册 auto 用户，连续 6 次错误密码登录，再调用 Admin unlock，最后正确密码登录。
        返回值:
            错误密码均失败，锁定后正确密码失败，解锁后正确密码登录成功。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        email = test_data_factory.email("as_03")
        password = "Aa123456789!"
        create_body = admin_api.create_admin(
            {"email": email, "password": password, "display_name": email, "role": "admin"}
        ).json()
        user_id = AdminTestDataService.resolve_created_user_id(admin_api, create_body.get("data", {}), email)
        resource_tracker.track("registered_user", user_id, lambda value: admin_api.delete_user(value))
        resource_tracker.track("locked_user", email, lambda value: admin_api.unlock_user(value))

        for _ in range(6):
            try:
                response = AuthAPI().login(email=email, password="WrongPass123!")
            except HTTPError as exc:
                response = HTTPResponseService.from_http_error(exc)
            body = response.json() if response.content else {}
            assert response.status_code in {400, 401, 403}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

        try:
            locked_login_response = AuthAPI().login(email=email, password=password)
        except HTTPError as exc:
            locked_login_response = HTTPResponseService.from_http_error(exc)
        locked_login_body = locked_login_response.json() if locked_login_response.content else {}
        if locked_login_response.status_code not in {400, 401, 403}:
            JSONResponseAssertionService.fail_with_api_json(locked_login_body)

        unlock_response = admin_api.unlock_user(email)
        unlock_body = unlock_response.json()
        if unlock_response.status_code != 200 or unlock_body.get("success") is not True:
            JSONResponseAssertionService.fail_with_api_json(unlock_body)

        login_response = AuthAPI().login(email=email, password=password)
        login_body = login_response.json()
        if login_response.status_code != 200 or login_body.get("success") is not True or not login_body.get("data", {}).get("token"):
            JSONResponseAssertionService.fail_with_api_json(login_body)
