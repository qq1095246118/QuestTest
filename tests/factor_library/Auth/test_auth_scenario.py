from __future__ import annotations

import allure
import pytest

from api.platform.auth_api import AuthAPI
from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.factor_library.auth.auth_assertions import AuthAssertionService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Scenario")
class TestAuthScenario:
    """Auth 场景接口自动化用例集。

    请求参数:
        使用 config/env.<env> 中的接口地址和账号配置串联登录与当前用户资料接口。
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
        login_errors = AuthAssertionService.login_success_errors(
            login_response.status_code,
            login_body,
            settings.factor_email,
        )
        if login_errors:
            JSONResponseAssertionService.fail_with_api_json(login_body)

        token = login_body["data"]["token"]
        me_response = AuthAPI().me(token)
        me_body = me_response.json()
        me_errors = AuthAssertionService.me_success_errors(me_response.status_code, me_body, settings.factor_email)
        if me_errors:
            JSONResponseAssertionService.fail_with_api_json(me_body)

        assert login_body["data"]["user"]["email"] == me_body["data"]["email"]
