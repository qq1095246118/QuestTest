from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.factor_ic.factor_ic_test_data import FactorICTestDataService
from service.factor_library.factors.factor_test_data import FactorTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Scenario")
class TestFactorICScenario:
    """FactorIC 连贯场景接口自动化用例集。

    请求参数:
        使用管理员 token 和真实因子 ID 串联 IC run、summary metrics、slice metrics 接口。
    返回值:
        无返回值；pytest 根据链路断言判断场景是否通过。
    """

    def first_factor_id(self, factor_resource_api) -> int:
        """从因子列表派生一个真实因子 ID。

        请求参数:
            factor_resource_api: factor 模块 API fixture。
        返回值:
            因子 ID；列表为空时跳过当前用例。
        """
        body = factor_resource_api.list_factors(page=1, limit=1).json()
        items = body.get("data", {}).get("items", [])
        if not items:
            pytest.skip("因子列表为空，无法派生 factor_id。")
        return items[0]["id"]

    @allure.title("ICS-01 创建 IC run 后查询详情")
    def test_ics_01_create_factor_and_ic_run_then_query_run_detail(
        self,
        factor_ic_api,
        factor_resource_api,
        test_data_factory,
        resource_tracker,
    ):
        """Case ID: ICS-01
        测试目的: 验证自动化因子创建 IC run 后可以查询运行详情。

        请求参数:
            创建 auto_test 因子，使用其 factor_id 创建 IC run。
        返回值:
            创建和详情查询接口都应返回成功响应；IC run 保留后由人工或定时任务清理。
        """
        payload = FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "ics_01")
        create_factor_body = factor_resource_api.create_factor(payload).json()
        factor_id = create_factor_body.get("data", {}).get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(create_factor_body)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        create_run_response = factor_ic_api.create_run(FactorICTestDataService.build_run_payload(factor_id, "ics_01"))
        create_run_body = create_run_response.json()
        errors = []
        if create_run_response.status_code != 200:
            errors.append(f"status_code={create_run_response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(create_run_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(create_run_body)

        run_id = create_run_body.get("data", {}).get("run_id")
        if not run_id:
            JSONResponseAssertionService.fail_with_api_json(create_run_body)
        detail_response = factor_ic_api.get_run(run_id)
        detail_response_body = detail_response.json()
        errors = []
        if detail_response.status_code != 200:
            errors.append(f"status_code={detail_response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(detail_response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(detail_response_body)

    @allure.title("ICS-02 upsert summary metrics 后查询 summary metrics")
    def test_ics_02_batch_upsert_summary_metrics_then_query_summary_metrics(
        self,
        factor_ic_api,
        factor_resource_api,
        test_data_factory,
        resource_tracker,
    ):
        """Case ID: ICS-02
        测试目的: 验证写入自动化因子 summary metrics 后可以按 factor_id 查询。

        请求参数:
            创建 auto_test 因子，写入一条 summary metrics。
        返回值:
            upsert 和查询接口都应返回成功响应；IC 指标保留后由人工或定时任务清理。
        """
        payload = FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "ics_02")
        create_factor_body = factor_resource_api.create_factor(payload).json()
        factor_id = create_factor_body.get("data", {}).get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(create_factor_body)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        run_id = test_data_factory.name("ic_run", "ics_02")
        try:
            upsert_response = factor_ic_api.batch_upsert_summary_metrics([FactorICTestDataService.build_summary_metric_item(run_id, factor_id)])
        except HTTPError as exc:
            upsert_response = HTTPResponseService.from_http_error(exc)
        upsert_response_body = upsert_response.json()
        errors = []
        if upsert_response.status_code != 200:
            errors.append(f"status_code={upsert_response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(upsert_response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(upsert_response_body)

        list_response = factor_ic_api.list_summary_metrics(factor_id=factor_id, is_sub_factor_id=0, limit=5)
        list_body = list_response.json()
        errors = []
        if list_response.status_code != 200:
            errors.append(f"status_code={list_response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(list_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(list_body)
        matched_item = FactorICTestDataService.find_summary_metric_item(list_body, factor_id, run_id)
        if matched_item is None:
            JSONResponseAssertionService.fail_with_api_json(list_body)

    @allure.title("ICS-03 upsert slice metrics 后查询 slice metrics")
    def test_ics_03_batch_upsert_slice_metrics_then_query_slice_metrics(
        self,
        factor_ic_api,
        factor_resource_api,
        test_data_factory,
        resource_tracker,
    ):
        """Case ID: ICS-03
        测试目的: 验证写入自动化因子 slice metrics 后可以按 factor_id 查询。

        请求参数:
            创建 auto_test 因子，写入一条 slice metrics。
        返回值:
            upsert 和查询接口都应返回成功响应；IC 指标保留后由人工或定时任务清理。
        """
        payload = FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "ics_03")
        create_factor_body = factor_resource_api.create_factor(payload).json()
        factor_id = create_factor_body.get("data", {}).get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(create_factor_body)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        run_id = test_data_factory.name("ic_run", "ics_03")
        try:
            upsert_response = factor_ic_api.batch_upsert_slice_metrics([FactorICTestDataService.build_slice_metric_item(run_id, factor_id)])
        except HTTPError as exc:
            upsert_response = HTTPResponseService.from_http_error(exc)
        upsert_response_body = upsert_response.json()
        errors = []
        if upsert_response.status_code != 200:
            errors.append(f"status_code={upsert_response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(upsert_response_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(upsert_response_body)

        list_response = factor_ic_api.list_slice_metrics(factor_id=factor_id, is_sub_factor_id=0, symbol="BTCUSDT", limit=5)
        list_body = list_response.json()
        errors = []
        if list_response.status_code != 200:
            errors.append(f"status_code={list_response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(list_body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(list_body)
        matched_item = FactorICTestDataService.find_slice_metric_item(list_body, factor_id, run_id, "BTCUSDT")
        if matched_item is None:
            JSONResponseAssertionService.fail_with_api_json(list_body)
