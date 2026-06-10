from __future__ import annotations

import allure
import pytest

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.factor_library.admin.admin_assertions import AdminAssertionService
from service.factor_library.admin.admin_test_data import AdminTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Quantitative_Trading")
class TestQuantitativeTradingScenario:
    """Quantitative_Trading 连贯场景接口自动化用例集。

    请求参数:
        使用管理员 token、auto 测试数据和资源清理器串联量化账户接口。
    返回值:
        无返回值；pytest 根据链路断言判断场景是否通过。
    """

    def assert_quant_success(self, response, body) -> None:
        """断言 Quantitative_Trading 成功响应符合接口自身规则。

        请求参数:
            response: 量化交易接口原始 HTTP 响应对象。
            body: 量化交易接口返回的原始 JSON。
        返回值:
            无；响应错误时输出接口原始 JSON。
        """
        errors = AdminAssertionService.success_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("QT-01 量化账户创建-列表-详情-更新-资产-删除链路")
    def test_qt_01_quant_account_lifecycle(self, admin_api, test_data_factory):
        """Case ID: QT-01
        测试目的: 验证量化账户创建后可以查询、更新资产并删除。

        请求参数:
            exchange=binance，email/api_key/secret_key 使用 auto 唯一值。
        返回值:
            链路内每个接口都应返回成功响应。
        """
        payload = AdminTestDataService.build_quant_account_payload(test_data_factory, "qt_01")
        create_response = admin_api.create_quant_account(payload)
        create_body = create_response.json()
        self.assert_quant_success(create_response, create_body)
        account_id = create_body["data"]["id"]

        list_response = admin_api.list_quant_accounts(exchange="binance")
        self.assert_quant_success(list_response, list_response.json())

        detail_response = admin_api.get_quant_account(account_id)
        self.assert_quant_success(detail_response, detail_response.json())

        update_response = admin_api.update_quant_account(account_id, {"api_description": "updated"})
        self.assert_quant_success(update_response, update_response.json())

        asset_response = admin_api.update_quant_account_assets(account_id, 1.23)
        self.assert_quant_success(asset_response, asset_response.json())

        delete_response = admin_api.delete_quant_account(account_id)
        self.assert_quant_success(delete_response, delete_response.json())
