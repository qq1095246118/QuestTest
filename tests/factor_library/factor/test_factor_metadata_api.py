from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.factors.factor_assertions import FactorAssertionService
from service.factor_library.factors.factor_mining_queries import FactorMiningDBService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("factor")
class TestFactorMetadataAPI:
    """因子元数据接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的查询参数发起配置、评价标准、币种池和通知接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    @allure.title("FM-01 Agent Factory 配置查询成功")
    def test_fm_01_agent_factory_config_success(self, factor_resource_api):
        """Case ID: FM-01
        测试目的: 验证 Agent Factory 配置查询接口返回成功响应。

        请求参数:
            coin_category=main。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.get_agent_factory_config(coin_category="main")
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("FM-02 因子评价标准查询成功")
    def test_fm_02_factor_evaluation_standards_success(self, factor_resource_api):
        """Case ID: FM-02
        测试目的: 验证因子评价标准列表接口返回成功响应。

        请求参数:
            coin_category=main。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.list_factor_evaluation_standards(coin_category="main")
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("FM-03 币种池交易对查询成功")
    def test_fm_03_coin_universe_symbols_success(self, factor_resource_api):
        """Case ID: FM-03
        测试目的: 验证币种池交易对列表接口返回成功响应。

        请求参数:
            universe_key=main，is_active=1。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.list_coin_universe_symbols(universe_key="main", is_active=1)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("FM-04 因子通知缺少 run_id 失败")
    def test_fm_04_factor_mining_notification_missing_run_id_fails(self, factor_resource_api):
        """Case ID: FM-04
        测试目的: 验证因子挖掘通知接口缺少 run_id 时返回明确错误。

        请求参数:
            run_id 为空字符串。
        返回值:
            接口应返回 400、401、403 或 422，不应返回 500。
        """
        if not settings.factor_webhook_secret:
            pytest.skip("因子挖掘通知接口需要 FACTOR_WEBHOOK_SECRET，当前环境未配置。")

        try:
            response = factor_resource_api.notify_factor_result("")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json()
        if body.get("error") == "invalid webhook signature":
            JSONResponseAssertionService.fail_with_api_json(body)
        assert response.status_code in {400, 422}

    @allure.title("FM-05 因子通知有效载荷正向场景暂不执行")
    def test_fm_05_factor_mining_notification_valid_payload_success_with_selected_run_id(self, factor_resource_api, request):
        """Case ID: FM-05
        测试目的: 验证可以用已有 selected 挖掘结果 run_id 通知后端同步因子详情。

        请求参数:
            从 DB 只读查询 factor_mining_details.is_selected=true 的 run_id，再调用通知接口。
        返回值:
            通知接口应返回成功响应，data.run_id 应与请求 run_id 一致；缺少可用 run_id 时跳过。
        """
        if not settings.factor_webhook_secret:
            pytest.skip("因子挖掘通知接口需要 FACTOR_WEBHOOK_SECRET，当前环境未配置。")

        db_client = request.getfixturevalue("db_client")
        run_id = FactorMiningDBService.first_selected_run_id(db_client)
        response = factor_resource_api.notify_factor_result(run_id)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        if body.get("data", {}).get("run_id") != run_id:
            JSONResponseAssertionService.fail_with_api_json(body)
