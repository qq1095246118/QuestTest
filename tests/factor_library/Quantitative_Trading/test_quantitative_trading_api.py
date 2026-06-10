from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.admin.admin_assertions import AdminAssertionService
from service.factor_library.admin.admin_test_data import AdminTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Quantitative_Trading")
class TestQuantitativeTradingAPI:
    """Quantitative_Trading 量化账户和交易所接口自动化用例集。

    请求参数:
        使用管理员 token、自动化量化账户参数和可选交易所测试 key 发起接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    def create_quant_payload(self, test_data_factory, case_id: str) -> dict:
        """生成自动化量化账户创建参数。

        请求参数:
            test_data_factory: 自动化测试数据工厂 fixture。
            case_id: 当前用例编号。
        返回值:
            创建量化账户接口 JSON body。
        """
        return AdminTestDataService.build_quant_account_payload(test_data_factory, case_id)

    def create_auto_quant_account(self, admin_api, test_data_factory, case_id: str) -> dict:
        """创建自动化量化账户并返回接口 data。

        请求参数:
            admin_api: Admin API fixture。
            test_data_factory: 自动化测试数据工厂 fixture。
            case_id: 当前用例编号。
        返回值:
            创建量化账户接口响应中的 data 字典。
        """
        response = admin_api.create_quant_account(self.create_quant_payload(test_data_factory, case_id))
        body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        return body["data"]

    @allure.title("ADQ-01 量化账户列表查询成功")
    def test_adq_01_list_quant_accounts_success(self, admin_api):
        """Case ID: ADQ-01
        测试目的: 验证量化账户列表接口返回成功响应。

        请求参数:
            不传筛选参数。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.list_quant_accounts()
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADQ-02 创建量化账户成功")
    def test_adq_02_create_quant_account_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADQ-02
        测试目的: 验证管理员可以创建自动化量化账户。

        请求参数:
            exchange=binance，email/api_key/secret_key 使用 auto 唯一值。
        返回值:
            创建量化账户接口应返回成功响应。
        """
        data = self.create_auto_quant_account(admin_api, test_data_factory, "adq_02")
        account_id = data.get("id")
        if account_id:
            resource_tracker.track("quant_account", account_id, lambda value: admin_api.delete_quant_account(value))

    @allure.title("ADQ-03 缺少 api_key 创建量化账户失败")
    def test_adq_03_create_quant_account_missing_api_key_fails(self, admin_api, test_data_factory):
        """Case ID: ADQ-03
        测试目的: 验证创建量化账户缺少 api_key 时失败。

        请求参数:
            删除 payload 中的 api_key。
        返回值:
            接口应返回 400、401、403、409 或 422。
        """
        payload = self.create_quant_payload(test_data_factory, "adq_03")
        del payload["api_key"]
        try:
            response = admin_api.create_quant_account(payload)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 401, 403, 409, 422}

    @allure.title("ADQ-05 查询量化账户详情成功")
    def test_adq_05_get_quant_account_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADQ-05
        测试目的: 验证可以查询自动化量化账户详情。

        请求参数:
            先创建量化账户，再查询该 account_id。
        返回值:
            详情接口应返回成功响应。
        """
        data = self.create_auto_quant_account(admin_api, test_data_factory, "adq_05")
        account_id = data.get("id")
        if not account_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("quant_account", account_id, lambda value: admin_api.delete_quant_account(value))

        response = admin_api.get_quant_account(account_id)
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADQ-06 更新量化账户成功")
    def test_adq_06_update_quant_account_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADQ-06
        测试目的: 验证管理员可以更新自动化量化账户。

        请求参数:
            先创建量化账户，再更新 api_description。
        返回值:
            更新接口应返回成功响应。
        """
        data = self.create_auto_quant_account(admin_api, test_data_factory, "adq_06")
        account_id = data.get("id")
        if not account_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("quant_account", account_id, lambda value: admin_api.delete_quant_account(value))

        response = admin_api.update_quant_account(account_id, {"api_description": "updated"})
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADQ-07 更新量化账户资产成功")
    def test_adq_07_update_quant_account_assets_success(self, admin_api, test_data_factory, resource_tracker):
        """Case ID: ADQ-07
        测试目的: 验证管理员可以更新量化账户总资产。

        请求参数:
            先创建量化账户，再更新 total_assets_usdt=100.12。
        返回值:
            更新资产接口应返回成功响应。
        """
        data = self.create_auto_quant_account(admin_api, test_data_factory, "adq_07")
        account_id = data.get("id")
        if not account_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("quant_account", account_id, lambda value: admin_api.delete_quant_account(value))

        response = admin_api.update_quant_account_assets(account_id, 100.12)
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADQ-08 查询存储量化账户实时信息")
    def test_adq_08_get_stored_quant_account_info_success(self, admin_api, exchange_test_config, test_data_factory, resource_tracker):
        """Case ID: ADQ-08
        测试目的: 验证已保存交易所 key 的量化账户可以查询实时账户信息。

        请求参数:
            使用 EXCHANGE_TEST_* 创建量化账户，再查询 account-info。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        payload = self.create_quant_payload(test_data_factory, "adq_08")
        payload.update(
            {
                "exchange": exchange_test_config["exchange"],
                "api_key": exchange_test_config["api_key"],
                "secret_key": exchange_test_config["api_secret"],
                "api_password": exchange_test_config["api_passphrase"],
            }
        )
        body = admin_api.create_quant_account(payload).json()
        account_id = body.get("data", {}).get("id")
        if not account_id:
            JSONResponseAssertionService.fail_with_api_json(body)
        resource_tracker.track("quant_account", account_id, lambda value: admin_api.delete_quant_account(value))

        response = admin_api.get_quant_account_info(account_id, account_type=exchange_test_config["account_type"])
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADQ-09 直接交易所账户查询成功")
    def test_adq_09_direct_exchange_account_query_success(self, admin_api, exchange_test_config):
        """Case ID: ADQ-09
        测试目的: 验证使用直接交易所凭证可以查询账户信息。

        请求参数:
            使用 EXCHANGE_TEST_* 中的 exchange、api_key、api_secret、passphrase 和 account_type。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.query_exchange_account(
            {
                "exchange": exchange_test_config["exchange"],
                "api_key": exchange_test_config["api_key"],
                "secret_key": exchange_test_config["api_secret"],
                "passphrase": exchange_test_config["api_passphrase"],
                "account_type": exchange_test_config["account_type"],
            }
        )
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADQ-10 错误交易所 key 查询失败")
    def test_adq_10_direct_exchange_account_query_wrong_key_returns_error(self, admin_api):
        """Case ID: ADQ-10
        测试目的: 验证错误交易所 key 查询时返回明确错误。

        请求参数:
            exchange=binance，api_key=wrong，secret_key=wrong，account_type=spot。
        返回值:
            接口应返回 400、401、403 或 502。
        """
        try:
            response = admin_api.query_exchange_account(
                {"exchange": "binance", "api_key": "wrong", "secret_key": "wrong", "account_type": "spot"}
            )
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 401, 403, 502}

    @allure.title("ADQ-11 查询不存在量化账户失败")
    def test_adq_11_get_nonexistent_quant_account_fails(self, admin_api):
        """Case ID: ADQ-11
        测试目的: 验证查询不存在量化账户时返回明确错误。

        请求参数:
            account_id=999999999。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = admin_api.get_quant_account(999999999)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("ADQ-12 删除量化账户成功")
    def test_adq_12_delete_quant_account_success(self, admin_api, test_data_factory):
        """Case ID: ADQ-12
        测试目的: 验证管理员可以删除自动化量化账户。

        请求参数:
            先创建量化账户，再删除该 account_id。
        返回值:
            删除接口应返回成功响应。
        """
        data = self.create_auto_quant_account(admin_api, test_data_factory, "adq_12")
        account_id = data.get("id")
        if not account_id:
            JSONResponseAssertionService.fail_with_api_json(data)

        response = admin_api.delete_quant_account(account_id)
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)
