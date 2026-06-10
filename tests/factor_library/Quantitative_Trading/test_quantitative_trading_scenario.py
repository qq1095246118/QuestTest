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
        errors = AdminAssertionService.success_errors(create_response.status_code, create_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        account_id = create_body["data"]["id"]

        list_response = admin_api.list_quant_accounts(exchange="binance")
        list_response_body = list_response.json()
        errors = AdminAssertionService.success_errors(list_response.status_code, list_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(list_response_body)

        detail_response = admin_api.get_quant_account(account_id)
        detail_response_body = detail_response.json()
        errors = AdminAssertionService.success_errors(detail_response.status_code, detail_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(detail_response_body)

        update_response = admin_api.update_quant_account(account_id, {"api_description": "updated"})
        update_response_body = update_response.json()
        errors = AdminAssertionService.success_errors(update_response.status_code, update_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(update_response_body)

        asset_response = admin_api.update_quant_account_assets(account_id, 1.23)
        asset_response_body = asset_response.json()
        errors = AdminAssertionService.success_errors(asset_response.status_code, asset_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(asset_response_body)

        delete_response = admin_api.delete_quant_account(account_id)
        delete_response_body = delete_response.json()
        errors = AdminAssertionService.success_errors(delete_response.status_code, delete_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(delete_response_body)
