from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.factor_api import FactorAPI
from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.factors.factor_assertions import FactorAssertionService
from service.factor_library.factors.factor_test_data import FactorTestDataService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("factor")
class TestFactorAPI:
    """因子接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的因子参数发起因子接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    def create_auto_factor(self, factor_resource_api, test_data_factory, case_id: str) -> dict:
        """创建自动化因子并返回接口 data。

        请求参数:
            factor_resource_api: factor 模块 API fixture。
            test_data_factory: 自动化测试数据工厂 fixture。
            case_id: 当前用例编号。
        返回值:
            创建因子接口响应中的 data 字典。
        """
        response = factor_resource_api.create_factor(
            FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, case_id)
        )
        body = response.json()
        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        return body["data"]

    def first_factor_id(self, factor_resource_api) -> int:
        """从因子列表派生一个真实因子 ID。

        请求参数:
            factor_resource_api: factor 模块 API fixture。
        返回值:
            因子 ID；列表为空时跳过当前用例。
        """
        return FactorTestDataService.first_factor_id(factor_resource_api)

    @allure.title("FA-15 查询因子详情成功")
    def test_fa_15_get_factor_detail_by_valid_id(self, factor_resource_api):
        """Case ID: FA-15
        测试目的: 验证可以根据真实 factor_id 查询因子详情。

        请求参数:
            从因子列表派生 factor_id。
        返回值:
            因子详情接口应返回成功响应。
        """
        response = factor_resource_api.get_factor(self.first_factor_id(factor_resource_api))
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("FA-16 查询不存在因子失败")
    def test_fa_16_get_nonexistent_factor_fails(self, factor_resource_api):
        """Case ID: FA-16
        测试目的: 验证查询不存在因子时返回明确错误。

        请求参数:
            factor_id=999999999。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = factor_resource_api.get_factor(999999999)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("FA-17 创建因子成功")
    def test_fa_17_create_factor_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: FA-17
        测试目的: 验证管理员可以创建自动化因子。

        请求参数:
            serial_prefix=AUTO，factor_name/cn_name 使用 auto 唯一值。
        返回值:
            创建因子接口应返回成功响应。
        """
        data = self.create_auto_factor(factor_resource_api, test_data_factory, "fa_17")
        factor_id = data.get("id")
        if factor_id:
            resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

    @allure.title("FA-18 缺少 factor_name 创建因子失败")
    def test_fa_18_create_factor_missing_factor_name_fails(self, factor_resource_api, resource_tracker):
        """Case ID: FA-18
        测试目的: 验证创建因子缺少必填 factor_name 时失败。

        请求参数:
            serial_prefix=AUTO，不传 factor_name；如果接口错误创建出资源，则登记清理。
        返回值:
            接口应返回 400、401、403、409 或 422，不应返回 500。
        """
        try:
            response = factor_resource_api.create_factor({"serial_prefix": "AUTO"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json()
        factor_id = body.get("data", {}).get("id") if isinstance(body.get("data"), dict) else None
        if factor_id:
            resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        assert response.status_code in {400, 401, 403, 409, 422}

    @allure.title("FA-19 重复 factor_name 创建因子失败")
    def test_fa_19_create_duplicate_factor_fails(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: FA-19
        测试目的: 验证重复 factor_name 不能创建因子。

        请求参数:
            连续两次使用相同 payload 创建因子。
        返回值:
            第一次成功，第二次应返回 400、409 或 422。
        """
        payload = FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "fa_19")
        created_body = factor_resource_api.create_factor(payload).json()
        factor_id = created_body.get("data", {}).get("id")
        if factor_id:
            resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        try:
            response = factor_resource_api.create_factor(payload)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 409, 422}

    @allure.title("FA-20 更新因子成功")
    def test_fa_20_update_factor_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: FA-20
        测试目的: 验证管理员可以更新自动化因子。

        请求参数:
            先创建因子，再更新 cn_name。
        返回值:
            更新接口应返回成功响应。
        """
        data = self.create_auto_factor(factor_resource_api, test_data_factory, "fa_20")
        factor_id = data.get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        response = factor_resource_api.update_factor(factor_id, {"cn_name": f"{data.get('factor_name')}_updated"})
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("FA-21 更新不存在因子失败")
    def test_fa_21_update_nonexistent_factor_fails(self, factor_resource_api):
        """Case ID: FA-21
        测试目的: 验证更新不存在因子时返回明确错误。

        请求参数:
            factor_id=999999999，cn_name=missing。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = factor_resource_api.update_factor(999999999, {"cn_name": "missing"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("FA-22 更新因子状态成功")
    def test_fa_22_update_factor_status_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: FA-22
        测试目的: 验证管理员可以更新因子状态，且返回详情状态与请求状态一致。

        请求参数:
            先创建因子并登记清理，再将状态更新为 3。
        返回值:
            状态更新接口应返回成功响应，data.factor_detail.status 应等于 3。
        """
        data = self.create_auto_factor(factor_resource_api, test_data_factory, "fa_22")
        factor_id = data.get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        response = factor_resource_api.update_factor_status(factor_id, 3)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        response_data = body["data"]
        factor_detail = response_data.get("factor_detail") if isinstance(response_data, dict) else None
        assert isinstance(factor_detail, dict)
        assert factor_detail.get("status") == 3

    @allure.title("FA-23 批量更新因子状态成功")
    def test_fa_23_batch_update_factor_status_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: FA-23
        测试目的: 验证管理员可以批量更新因子状态，且响应状态与请求状态一致。

        请求参数:
            先创建因子并登记清理，再用 factor_ids 批量更新状态为 3。
        返回值:
            批量状态更新接口应返回成功响应，data.status 应等于 3，updated_factor_ids 应包含被更新因子。
        """
        data = self.create_auto_factor(factor_resource_api, test_data_factory, "fa_23")
        factor_id = data.get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        response = factor_resource_api.batch_update_factor_status([factor_id], 3)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        response_data = body["data"]
        assert isinstance(response_data, dict)
        assert response_data.get("status") == 3
        assert factor_id in response_data.get("updated_factor_ids", [])

    @allure.title("FA-24 复制因子成功")
    def test_fa_24_copy_factors_success_for_auto_factor(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: FA-24
        测试目的: 验证复制因子接口可以处理自动化因子，且复制副本进入新挖库。

        请求参数:
            先创建 auto_test 因子并登记源数据失效清理，再传 factor_ids=[factor_id]。
        返回值:
            copy 接口应返回成功响应；copy 副本详情状态应为 1，并保留后由人工或定时任务清理。
        """
        data = self.create_auto_factor(factor_resource_api, test_data_factory, "fa_24")
        factor_id = data.get("id")
        if not factor_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        response = factor_resource_api.copy_factors([factor_id])
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        response_data = body["data"]
        copied_factors = response_data.get("factors") if isinstance(response_data, dict) else None
        assert isinstance(copied_factors, list)
        assert copied_factors
        copied_factor_id = copied_factors[0].get("id")
        assert copied_factor_id
        resource_tracker.track("factor", copied_factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        copied_detail_response = factor_resource_api.get_factor(copied_factor_id)
        copied_detail_body = copied_detail_response.json()

        errors = FactorAssertionService.success_with_data_errors(copied_detail_response.status_code, copied_detail_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(copied_detail_body)
        copied_factor_detail = copied_detail_body["data"].get("factor_detail")
        assert isinstance(copied_factor_detail, dict)
        assert copied_factor_detail.get("status") == 1

    @allure.title("FA-25 因子图表汇总查询成功")
    def test_fa_25_factor_graph_returns_valid_structure(self, factor_resource_api):
        """Case ID: FA-25
        测试目的: 验证因子图表汇总接口返回成功响应。

        请求参数:
            type=new。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.get_factors_graph(type="new")
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("FA-26 因子筛选项查询成功")
    def test_fa_26_factor_filter_options_returns_valid_structure(self, factor_resource_api):
        """Case ID: FA-26
        测试目的: 验证因子筛选项接口返回成功响应。

        请求参数:
            不传筛选参数。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.list_factor_filter_options()
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("FA-27 未带 token 查询因子详情失败")
    def test_fa_27_get_factor_without_token_is_unauthorized(self):
        """Case ID: FA-27
        测试目的: 验证未带 token 不能访问因子详情。

        请求参数:
            factor_id=1，不传 Authorization。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = FactorAPI().get_factor(1)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {401, 403}
