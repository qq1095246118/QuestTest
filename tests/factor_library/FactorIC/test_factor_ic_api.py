from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.factor_ic_api import FactorICAPI
from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.factor_ic.factor_ic_assertions import FactorICAssertionService
from service.factor_library.factor_ic.factor_ic_test_data import FactorICTestDataService
from service.factor_library.factors.factor_test_data import FactorTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("FactorIC")
class TestFactorICAPI:
    """FactorIC 接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的 IC 查询或写入参数发起 FactorIC 接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    def first_factor_id(self, factor_resource_api) -> int:
        """从因子列表派生一个真实因子 ID。

        请求参数:
            factor_resource_api: factor 模块 API fixture。
        返回值:
            因子 ID；列表为空时跳过当前用例。
        """
        return FactorTestDataService.first_factor_id(factor_resource_api)

    def first_sub_factor_id(self, factor_resource_api) -> int:
        """从子因子列表派生一个真实子因子 ID。

        请求参数:
            factor_resource_api: factor 模块 API fixture。
        返回值:
            子因子 ID；列表为空时跳过当前用例。
        """
        return FactorTestDataService.first_sub_factor_id(factor_resource_api)

    @allure.title("IC-01 因子 IC summary 查询成功")
    def test_ic_01_factor_summary_success_with_real_factor_id(self, factor_ic_api):
        """Case ID: IC-01
        测试目的: 验证真实因子 ID 可以查询 IC 汇总指标。

        请求参数:
            factor_id 从已有母因子 IC 汇总指标列表派生，ic_scope=time_series。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        factor_id = FactorICTestDataService.first_factor_id_with_summary_metric(factor_ic_api)
        response = factor_ic_api.get_factor_summary(factor_id, ic_scope="time_series")
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-02 不存在因子 IC summary 返回明确结果")
    def test_ic_02_factor_summary_nonexistent_factor_handles_404_or_empty_data(self, factor_ic_api):
        """Case ID: IC-02
        测试目的: 验证不存在因子 ID 查询 IC 汇总时不会返回服务端错误。

        请求参数:
            factor_id=999999999，ic_scope=time_series。
        返回值:
            接口应返回成功空数据或 400、404、422。
        """
        try:
            response = factor_ic_api.get_factor_summary(999999999, ic_scope="time_series")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {200, 400, 404, 422}

    @allure.title("IC-03 因子 by-symbol 查询成功")
    def test_ic_03_factor_by_symbol_success(self, factor_ic_api, factor_resource_api):
        """Case ID: IC-03
        测试目的: 验证真实因子 ID 可以查询 by-symbol 旧汇总接口。

        请求参数:
            factor_id 从因子列表派生，ic_scope=time_series。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.get_factor_by_symbol(self.first_factor_id(factor_resource_api), ic_scope="time_series")
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-04 因子 slice-metrics 查询成功")
    def test_ic_04_factor_slice_metrics_success(self, factor_ic_api, factor_resource_api):
        """Case ID: IC-04
        测试目的: 验证真实因子 ID 可以查询切片指标。

        请求参数:
            factor_id 从因子列表派生，ic_scope=time_series。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.get_factor_slice_metrics(self.first_factor_id(factor_resource_api), ic_scope="time_series")
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-05 因子 symbol-window-metrics 查询成功")
    def test_ic_05_factor_symbol_window_metrics_success(self, factor_ic_api, factor_resource_api):
        """Case ID: IC-05
        测试目的: 验证真实因子 ID 可以查询交易对窗口指标。

        请求参数:
            factor_id 从因子列表派生，universe_key=main，limit=5。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.get_factor_symbol_window_metrics(
            self.first_factor_id(factor_resource_api),
            universe_key="main",
            limit=5,
        )
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-06 子因子 summary 查询成功")
    def test_ic_06_sub_factor_summary_success_with_real_sub_factor_id(self, factor_ic_api):
        """Case ID: IC-06
        测试目的: 验证真实子因子 ID 可以查询 IC 汇总指标。

        请求参数:
            sub_factor_id 从已有子因子 IC 汇总指标列表派生，ic_scope=time_series。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        sub_factor_id = FactorICTestDataService.first_sub_factor_id_with_summary_metric(factor_ic_api)
        response = factor_ic_api.get_sub_factor_summary(
            sub_factor_id,
            ic_scope="time_series",
        )
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-07 子因子 by-symbol 查询成功")
    def test_ic_07_sub_factor_by_symbol_success(self, factor_ic_api, factor_resource_api):
        """Case ID: IC-07
        测试目的: 验证真实子因子 ID 可以查询 by-symbol 旧汇总接口。

        请求参数:
            sub_factor_id 从子因子列表派生，ic_scope=time_series。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.get_sub_factor_by_symbol(
            self.first_sub_factor_id(factor_resource_api),
            ic_scope="time_series",
        )
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-08 子因子 slice-metrics 查询成功")
    def test_ic_08_sub_factor_slice_metrics_success(self, factor_ic_api, factor_resource_api):
        """Case ID: IC-08
        测试目的: 验证真实子因子 ID 可以查询切片指标。

        请求参数:
            sub_factor_id 从子因子列表派生，ic_scope=time_series。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.get_sub_factor_slice_metrics(
            self.first_sub_factor_id(factor_resource_api),
            ic_scope="time_series",
        )
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-09 子因子 symbol-window-metrics 查询成功")
    def test_ic_09_sub_factor_symbol_window_metrics_success(self, factor_ic_api, factor_resource_api):
        """Case ID: IC-09
        测试目的: 验证真实子因子 ID 可以查询交易对窗口指标。

        请求参数:
            sub_factor_id 从子因子列表派生，universe_key=main，limit=5。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.get_sub_factor_symbol_window_metrics(
            self.first_sub_factor_id(factor_resource_api),
            universe_key="main",
            limit=5,
        )
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-10 IC 汇总指标列表查询成功")
    def test_ic_10_list_summary_metrics_success(self, factor_ic_api):
        """Case ID: IC-10
        测试目的: 验证 IC 汇总指标列表接口返回成功响应。

        请求参数:
            limit=5。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.list_summary_metrics(limit=5)
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-10A IC 汇总指标列表按 owner 类型筛选成功")
    @pytest.mark.parametrize("owner_filter, expected_is_sub_factor", [(0, False), (1, True)])
    def test_ic_10a_list_summary_metrics_filter_by_owner_type_success(
        self,
        factor_ic_api,
        owner_filter,
        expected_is_sub_factor,
    ):
        """Case ID: IC-10A
        测试目的: 验证 IC 汇总指标列表接口按母因子/子因子 owner 类型筛选时返回数据归属正确。

        请求参数:
            is_sub_factor_id=0 或 1，limit=5。
        返回值:
            接口应返回 HTTP 200、success=True；如果存在 items，每条记录的 is_sub_factor_id 应与筛选条件一致。
        """
        response = factor_ic_api.list_summary_metrics(is_sub_factor_id=owner_filter, limit=5)
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

        data = response_body.get("data")
        items = data.get("items") if isinstance(data, dict) else data if isinstance(data, list) else None
        if not isinstance(items, list):
            JSONResponseAssertionService.fail_with_api_json(response_body)
        for item in items:
            if "is_sub_factor_id" not in item or bool(item.get("is_sub_factor_id")) is not expected_is_sub_factor:
                JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-11 批量 upsert IC 汇总指标返回明确结果")
    def test_ic_11_batch_upsert_summary_metrics_success_with_auto_factor(
        self,
        factor_ic_api,
        factor_resource_api,
        test_data_factory,
        resource_tracker,
    ):
        """Case ID: IC-11
        测试目的: 验证可以为自动化因子写入 IC 汇总指标并按 factor_id 查询。

        请求参数:
            先创建 auto_test 因子，再用该 factor_id 写入一条 summary metrics。
        返回值:
            upsert 和列表查询接口都应返回成功响应；IC 指标保留后由人工或定时任务清理。
        """
        payload = FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "ic_11")
        create_body = factor_resource_api.create_factor(payload).json()
        factor_id = create_body.get("data", {}).get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        run_id = test_data_factory.name("ic_run", "ic_11")
        response = factor_ic_api.batch_upsert_summary_metrics([FactorICTestDataService.build_summary_metric_item(run_id, factor_id)])
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

        list_response = factor_ic_api.list_summary_metrics(factor_id=factor_id, is_sub_factor_id=0, limit=5)
        list_body = list_response.json()
        errors = FactorICAssertionService.success_errors(list_response.status_code, list_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(list_body)
        list_errors = FactorICAssertionService.metric_list_contains_errors(
            list_body,
            expected_factor_id=factor_id,
            expected_run_id=run_id,
            required_metric_keys=("mean_ic",),
        )
        if list_errors:
            JSONResponseAssertionService.fail_with_api_json(list_body)

    @allure.title("IC-12 IC 切片指标列表查询成功")
    def test_ic_12_list_slice_metrics_success(self, factor_ic_api):
        """Case ID: IC-12
        测试目的: 验证 IC 切片指标列表接口返回成功响应。

        请求参数:
            limit=5。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.list_slice_metrics(limit=5)
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-12A IC 切片指标列表按 owner 类型筛选成功")
    @pytest.mark.parametrize("owner_filter, expected_is_sub_factor", [(0, False), (1, True)])
    def test_ic_12a_list_slice_metrics_filter_by_owner_type_success(
        self,
        factor_ic_api,
        owner_filter,
        expected_is_sub_factor,
    ):
        """Case ID: IC-12A
        测试目的: 验证 IC 切片指标列表接口按母因子/子因子 owner 类型筛选时返回数据归属正确。

        请求参数:
            is_sub_factor_id=0 或 1，limit=5。
        返回值:
            接口应返回 HTTP 200、success=True；如果存在 items，每条记录的 is_sub_factor_id 应与筛选条件一致。
        """
        response = factor_ic_api.list_slice_metrics(is_sub_factor_id=owner_filter, limit=5)
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

        data = response_body.get("data")
        items = data.get("items") if isinstance(data, dict) else data if isinstance(data, list) else None
        if not isinstance(items, list):
            JSONResponseAssertionService.fail_with_api_json(response_body)
        for item in items:
            if "is_sub_factor_id" not in item or bool(item.get("is_sub_factor_id")) is not expected_is_sub_factor:
                JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-13 批量 upsert IC 切片指标返回明确结果")
    def test_ic_13_batch_upsert_slice_metrics_success_with_auto_factor(
        self,
        factor_ic_api,
        factor_resource_api,
        test_data_factory,
        resource_tracker,
    ):
        """Case ID: IC-13
        测试目的: 验证可以为自动化因子写入 IC 切片指标并按 factor_id 查询。

        请求参数:
            先创建 auto_test 因子，再用该 factor_id 写入一条 slice metrics。
        返回值:
            upsert 和列表查询接口都应返回成功响应；IC 指标保留后由人工或定时任务清理。
        """
        payload = FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "ic_13")
        create_body = factor_resource_api.create_factor(payload).json()
        factor_id = create_body.get("data", {}).get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(create_body)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        run_id = test_data_factory.name("ic_run", "ic_13")
        response = factor_ic_api.batch_upsert_slice_metrics([FactorICTestDataService.build_slice_metric_item(run_id, factor_id)])
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

        list_response = factor_ic_api.list_slice_metrics(factor_id=factor_id, is_sub_factor_id=0, symbol="BTCUSDT", limit=5)
        list_body = list_response.json()
        errors = FactorICAssertionService.success_errors(list_response.status_code, list_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(list_body)
        list_errors = FactorICAssertionService.metric_list_contains_errors(
            list_body,
            expected_factor_id=factor_id,
            expected_run_id=run_id,
            expected_symbol="BTCUSDT",
            required_metric_keys=("ic",),
        )
        if list_errors:
            JSONResponseAssertionService.fail_with_api_json(list_body)

    @allure.title("IC-14 IC 运行记录列表查询成功")
    def test_ic_14_list_ic_runs_success(self, factor_ic_api):
        """Case ID: IC-14
        测试目的: 验证 IC 运行记录列表接口返回成功响应。

        请求参数:
            limit=5。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.list_runs(limit=5)
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-15 创建 IC 运行记录成功")
    def test_ic_15_create_ic_run_success(self, factor_ic_api, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: IC-15
        测试目的: 验证可以为自动化因子创建 IC 运行记录并查询详情。

        请求参数:
            先创建 auto_test 因子，再用该 factor_id 创建 IC run。
        返回值:
            创建和详情查询接口都应返回成功响应；IC run 保留后由人工或定时任务清理。
        """
        payload = FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "ic_15")
        create_factor_body = factor_resource_api.create_factor(payload).json()
        factor_id = create_factor_body.get("data", {}).get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(create_factor_body)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        response = factor_ic_api.create_run(FactorICTestDataService.build_run_payload(factor_id, "ic_15"))
        body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

        run_id = body.get("data", {}).get("run_id")
        if not run_id:
            JSONResponseAssertionService.fail_with_api_json(body)
        detail_response = factor_ic_api.get_run(run_id)
        detail_response_body = detail_response.json()
        errors = FactorICAssertionService.success_errors(detail_response.status_code, detail_response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(detail_response_body)

    @allure.title("IC-16 查询 IC 运行记录详情成功")
    def test_ic_16_get_ic_run_success(self, factor_ic_api):
        """Case ID: IC-16
        测试目的: 验证可以根据真实 run_id 查询 IC 运行记录详情。

        请求参数:
            先查询 IC runs 列表首条 run_id。
        返回值:
            详情接口应返回成功响应。
        """
        response = factor_ic_api.get_run(FactorICTestDataService.first_run_id(factor_ic_api))
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-17 查询不存在 IC 运行记录失败")
    def test_ic_17_get_nonexistent_ic_run_fails(self, factor_ic_api):
        """Case ID: IC-17
        测试目的: 验证查询不存在 run_id 时返回明确错误。

        请求参数:
            run_id=999999999。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = factor_ic_api.get_run(999999999)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("IC-18 IC 评分标准查询成功")
    def test_ic_18_list_scoring_standards_success(self, factor_ic_api):
        """Case ID: IC-18
        测试目的: 验证 IC 评分标准列表接口返回成功响应。

        请求参数:
            coin_category=main。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_ic_api.list_scoring_standards(coin_category="main")
        response_body = response.json()
        errors = FactorICAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("IC-19 未带 token 查询 IC summary 失败")
    def test_ic_19_no_token_summary_unauthorized(self):
        """Case ID: IC-19
        测试目的: 验证未带 token 不能访问 IC summary。

        请求参数:
            factor_id=1，不传 Authorization。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = FactorICAPI().get_factor_summary(1)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {401, 403}

    @allure.title("IC-20 无效 token 查询 IC summary 失败")
    def test_ic_20_invalid_token_summary_unauthorized(self):
        """Case ID: IC-20
        测试目的: 验证无效 token 不能访问 IC summary。

        请求参数:
            factor_id=1，token=invalid-token。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = FactorICAPI(token="invalid-token").get_factor_summary(1)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {401, 403}
