from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.admin.admin_test_data import AdminTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Admin")
class TestAdminQuantAccountAPI:
    """Admin 量化账户接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的量化账户参数发起 Admin 量化账户接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    @allure.title("ADQ-01 量化账户列表查询成功")
    def test_adq_01_list_quant_accounts_success(self, admin_api):
        """Case ID: ADQ-01
        测试目的: 验证量化账户列表接口返回成功响应。

        请求参数:
            page=1，limit=20。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.list_quant_accounts(page=1, limit=20)
        body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("ADQ-02 创建量化账户成功")
    def test_adq_02_create_quant_account_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADQ-02
        测试目的: 验证管理员可以创建自动化量化账户。

        请求参数:
            exchange=binance，email/api_key/secret_key 使用 auto 唯一值。
        返回值:
            创建接口应返回成功响应；用例结束删除该账户。
        """
        payload = AdminTestDataService.build_quant_account_payload(test_data_factory, "adq_02")
        response = admin_api.create_quant_account(payload)
        body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

        account_id = body.get("data", {}).get("id") if isinstance(body.get("data"), dict) else None
        if not account_id:
            JSONResponseAssertionService.fail_with_api_json(body)
        resource_tracker.track("quant_account", account_id, lambda value: admin_api.delete_quant_account(value))

    @allure.title("ADQ-03 查询量化账户详情成功")
    def test_adq_03_get_quant_account_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADQ-03
        测试目的: 验证自动化量化账户创建后可以查询详情。

        请求参数:
            先创建 auto 量化账户，再按 id 查询详情。
        返回值:
            详情接口应返回成功响应，且 data.id 等于创建账户 ID。
        """
        payload = AdminTestDataService.build_quant_account_payload(test_data_factory, "adq_03")
        create_body = admin_api.create_quant_account(payload).json()
        account_id = create_body.get("data", {}).get("id")
        if not account_id:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        resource_tracker.track("quant_account", account_id, lambda value: admin_api.delete_quant_account(value))

        response = admin_api.get_quant_account(account_id)
        body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        if body.get("data", {}).get("id") != account_id:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("ADQ-04 删除量化账户成功")
    def test_adq_04_delete_quant_account_success(self, admin_api, test_data_factory):
        """Case ID: ADQ-04
        测试目的: 验证管理员可以删除自动化量化账户。

        请求参数:
            先创建 auto 量化账户，再按 id 删除。
        返回值:
            删除接口应返回成功响应。
        """
        payload = AdminTestDataService.build_quant_account_payload(test_data_factory, "adq_04")
        create_body = admin_api.create_quant_account(payload).json()
        account_id = create_body.get("data", {}).get("id")
        if not account_id:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        response = admin_api.delete_quant_account(account_id)
        body = response.json()
        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("ADQ-05 量化账户实时资产非法 account_type 失败")
    def test_adq_05_get_quant_account_info_invalid_account_type_fails(self, admin_api):
        """Case ID: ADQ-05
        测试目的: 验证实时资产查询不接受非法 account_type。

        请求参数:
            account_id=999999999，account_type=bad。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = admin_api.get_quant_account_info(999999999, account_type="bad")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 404, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)
