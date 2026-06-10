from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.auth_api import AuthAPI
from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.auth.auth_assertions import AuthAssertionService


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

    @allure.title("AU-01 有效账号登录成功")
    def test_au_01_login_success(self):
        """Case ID: AU-01
        测试目的: 验证有效账号可以登录因子库后端。

        请求参数:
            使用 config/env.<env> 中配置的 factor_email 和 factor_password。
        返回值:
            接口应返回 HTTP 200、success=True、data.token，且 data.user.email 与配置邮箱一致。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")
        if not settings.factor_email or not settings.factor_password:
            pytest.skip("没有配置登录邮箱或密码")

        response = AuthAPI().login()
        body = response.json()

        errors = AuthAssertionService.login_success_errors(response.status_code, body, settings.factor_email)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("AU-02 错误密码登录失败")
    def test_au_02_login_wrong_password_fails(self):
        """Case ID: AU-02
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

        assert response.status_code in {400, 401, 403}
        assert "token" not in str(body).lower()

    @allure.title("AU-03 缺少邮箱登录失败")
    def test_au_03_login_missing_email_fails(self):
        """Case ID: AU-03
        测试目的: 验证缺少邮箱时不能登录。

        请求参数:
            email 为空字符串，password 使用配置中的密码。
        返回值:
            接口应返回 400、401 或 422，不应返回 500。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AuthAPI().login(email="", password=settings.factor_password or "password")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 401, 422}

    @allure.title("AU-04 非法邮箱格式登录失败")
    def test_au_04_login_invalid_email_format_fails(self):
        """Case ID: AU-04
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

        assert response.status_code in {400, 401, 422}

    @allure.title("AU-05 缺少密码注册失败")
    def test_au_05_register_missing_password_fails(self, test_data_factory):
        """Case ID: AU-05
        测试目的: 验证注册接口缺少密码时返回明确参数错误。

        请求参数:
            email 使用 auto 邮箱，password 为空字符串，display_name 使用 auto 名称。
        返回值:
            接口应返回 400、401、409 或 422，不应返回 500。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = AuthAPI().register(
                email=test_data_factory.email("au_05"),
                password="",
                display_name=test_data_factory.name("user", "au_05"),
            )
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 401, 409, 422}

    @allure.title("AU-06 已存在邮箱注册失败")
    def test_au_06_register_duplicate_email_fails(self):
        """Case ID: AU-06
        测试目的: 验证已存在邮箱不能重复注册。

        请求参数:
            email 使用 config/env.<env> 中的管理员邮箱，password 和 display_name 使用固定值。
        返回值:
            接口应返回 400、401、409 或 422，不应返回 500。
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

        assert response.status_code in {400, 401, 409, 422}

    @allure.title("AU-07 未带 token 查询当前用户失败")
    def test_au_07_me_without_token_is_unauthorized(self):
        """Case ID: AU-07
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

        assert response.status_code in {401, 403}

    @allure.title("AU-08 有效 token 查询当前用户成功")
    def test_au_08_me_with_valid_token_success(self, token):
        """Case ID: AU-08
        测试目的: 验证有效 token 可以访问当前用户资料。

        请求参数:
            使用 token fixture 返回的管理员 JWT。
        返回值:
            /me 应返回 HTTP 200、success=True，且 data.email 与配置邮箱一致。
        """
        response = AuthAPI().me(token)
        body = response.json()

        errors = AuthAssertionService.me_success_errors(response.status_code, body, settings.factor_email)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
