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
class TestSubFactorAPI:
    """子因子接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的子因子参数发起子因子接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    def create_auto_sub_factor(self, factor_resource_api, test_data_factory, case_id: str) -> dict:
        """创建自动化子因子并返回接口 data。

        请求参数:
            factor_resource_api: factor 模块 API fixture。
            test_data_factory: 自动化测试数据工厂 fixture。
            case_id: 当前用例编号。
        返回值:
            创建子因子接口响应中的 data 字典。
        """
        response = factor_resource_api.create_sub_factor(
            FactorTestDataService.build_sub_factor_payload(factor_resource_api, test_data_factory, case_id)
        )
        body = response.json()
        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        return body["data"]

    def first_sub_factor_id(self, factor_resource_api) -> int:
        """从子因子列表派生一个真实子因子 ID。

        请求参数:
            factor_resource_api: factor 模块 API fixture。
        返回值:
            子因子 ID；列表为空时跳过当前用例。
        """
        return FactorTestDataService.first_sub_factor_id(factor_resource_api)

    @allure.title("SF-01 子因子列表查询成功")
    def test_sf_01_list_sub_factors_success(self, factor_resource_api):
        """Case ID: SF-01
        测试目的: 验证子因子列表接口返回成功响应和分页结构。

        请求参数:
            page=1，limit=5。
        返回值:
            接口应返回 HTTP 200、success=True、items 和 pagination。
        """
        params = {"page": 1, "limit": 5}
        response = factor_resource_api.list_sub_factors(**params)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        errors.extend(FactorAssertionService.list_pagination_errors(body, params["page"], params["limit"]))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("SF-02 按 factor_id 查询子因子列表成功")
    def test_sf_02_list_sub_factors_filter_by_factor_id_success(self, factor_resource_api):
        """Case ID: SF-02
        测试目的: 验证子因子列表支持 factor_id 筛选。

        请求参数:
            从因子列表派生 factor_id，再查询子因子列表。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        factor_body = factor_resource_api.list_factors(page=1, limit=1).json()
        factor_items = factor_body.get("data", {}).get("items", [])
        if not factor_items:
            pytest.skip("因子列表为空，无法派生 factor_id。")

        response = factor_resource_api.list_sub_factors(page=1, limit=5, factor_id=factor_items[0]["id"])
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("SF-02B 按 status=1/2/3 筛选子因子列表成功")
    @pytest.mark.parametrize("status", [1, 2, 3])
    def test_sf_02b_list_sub_factors_filter_by_status_success(self, factor_resource_api, status):
        """Case ID: SF-02B
        测试目的: 验证子因子列表支持按库状态筛选，且返回子因子详情状态与请求状态一致。

        请求参数:
            page=1，limit=5，status 分别为 1、2、3。
        返回值:
            接口应返回 HTTP 200、success=True、分页结构正确，items 中每条 sub_factor_detail.status 应等于请求 status。
        """
        params = {"page": 1, "limit": 5, "status": status}
        response = factor_resource_api.list_sub_factors(**params)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        errors.extend(FactorAssertionService.list_pagination_errors(body, params["page"], params["limit"]))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

        for item in body["data"]["items"]:
            sub_factor_detail = item.get("sub_factor_detail")
            assert isinstance(sub_factor_detail, dict)
            assert sub_factor_detail.get("status") == status

    @allure.title("SF-03 无效 token 查询子因子列表失败")
    def test_sf_03_list_sub_factors_invalid_token_unauthorized(self):
        """Case ID: SF-03
        测试目的: 验证无效 token 不能访问子因子列表。

        请求参数:
            token=invalid-token，page=1，limit=5。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = FactorAPI(token="invalid-token").list_sub_factors(page=1, limit=5)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {401, 403}

    @allure.title("SF-04 创建子因子成功")
    def test_sf_04_create_sub_factor_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: SF-04
        测试目的: 验证管理员可以创建自动化子因子。

        请求参数:
            serial_prefix=AUTO，sub_factor_name/cn_name 使用 auto 唯一值。
        返回值:
            创建子因子接口应返回成功响应。
        """
        data = self.create_auto_sub_factor(factor_resource_api, test_data_factory, "sf_04")
        sub_factor_id = data.get("id")
        if sub_factor_id:
            resource_tracker.track("sub_factor", sub_factor_id, lambda value: factor_resource_api.update_sub_factor_status(value, 3))

    @allure.title("SF-05 缺少 sub_factor_name 创建子因子失败")
    def test_sf_05_create_sub_factor_missing_name_fails(self, factor_resource_api, resource_tracker):
        """Case ID: SF-05
        测试目的: 验证创建子因子缺少必填 sub_factor_name 时失败。

        请求参数:
            serial_prefix=AUTO，不传 sub_factor_name；如果接口错误创建出资源，则登记清理。
        返回值:
            接口应返回 400、401、403、409 或 422，不应返回 500。
        """
        try:
            response = factor_resource_api.create_sub_factor({"serial_prefix": "AUTO"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json()
        sub_factor_id = body.get("data", {}).get("id") if isinstance(body.get("data"), dict) else None
        if sub_factor_id:
            resource_tracker.track("sub_factor", sub_factor_id, lambda value: factor_resource_api.update_sub_factor_status(value, 3))

        assert response.status_code in {400, 401, 403, 409, 422}

    @allure.title("SF-06 重复 sub_factor_name 创建子因子失败")
    def test_sf_06_create_duplicate_sub_factor_fails(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: SF-06
        测试目的: 验证重复 sub_factor_name 不能创建子因子。

        请求参数:
            连续两次使用相同 payload 创建子因子。
        返回值:
            第一次成功，第二次应返回 400、409 或 422。
        """
        payload = FactorTestDataService.build_sub_factor_payload(factor_resource_api, test_data_factory, "sf_06")
        created_body = factor_resource_api.create_sub_factor(payload).json()
        sub_factor_id = created_body.get("data", {}).get("id")
        if sub_factor_id:
            resource_tracker.track("sub_factor", sub_factor_id, lambda value: factor_resource_api.update_sub_factor_status(value, 3))

        try:
            response = factor_resource_api.create_sub_factor(payload)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 409, 422}

    @allure.title("SF-07 查询子因子详情成功")
    def test_sf_07_get_sub_factor_success(self, factor_resource_api):
        """Case ID: SF-07
        测试目的: 验证可以根据真实 sub_factor_id 查询子因子详情。

        请求参数:
            从子因子列表派生 sub_factor_id。
        返回值:
            子因子详情接口应返回成功响应。
        """
        response = factor_resource_api.get_sub_factor(self.first_sub_factor_id(factor_resource_api))
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("SF-08 查询不存在子因子失败")
    def test_sf_08_get_nonexistent_sub_factor_fails(self, factor_resource_api):
        """Case ID: SF-08
        测试目的: 验证查询不存在子因子时返回明确错误。

        请求参数:
            sub_factor_id=999999999。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = factor_resource_api.get_sub_factor(999999999)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("SF-09 更新子因子成功")
    def test_sf_09_update_sub_factor_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: SF-09
        测试目的: 验证管理员可以更新自动化子因子。

        请求参数:
            先创建子因子，再更新 cn_name。
        返回值:
            更新接口应返回成功响应。
        """
        data = self.create_auto_sub_factor(factor_resource_api, test_data_factory, "sf_09")
        sub_factor_id = data.get("id")
        if not sub_factor_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("sub_factor", sub_factor_id, lambda value: factor_resource_api.update_sub_factor_status(value, 3))

        response = factor_resource_api.update_sub_factor(sub_factor_id, {"cn_name": f"{data.get('sub_factor_name')}_updated"})
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("SF-10 更新不存在子因子失败")
    def test_sf_10_update_nonexistent_sub_factor_fails(self, factor_resource_api):
        """Case ID: SF-10
        测试目的: 验证更新不存在子因子时返回明确错误。

        请求参数:
            sub_factor_id=999999999，cn_name=missing。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = factor_resource_api.update_sub_factor(999999999, {"cn_name": "missing"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("SF-11 更新子因子状态成功")
    def test_sf_11_update_sub_factor_status_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: SF-11
        测试目的: 验证管理员可以更新子因子状态，且返回详情状态与请求状态一致。

        请求参数:
            先创建子因子并登记清理，再将状态更新为 3。
        返回值:
            状态更新接口应返回成功响应，data.status 或 data.sub_factor_detail.status 应等于 3。
        """
        data = self.create_auto_sub_factor(factor_resource_api, test_data_factory, "sf_11")
        sub_factor_id = data.get("id")
        if not sub_factor_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("sub_factor", sub_factor_id, lambda value: factor_resource_api.update_sub_factor_status(value, 3))

        response = factor_resource_api.update_sub_factor_status(sub_factor_id, 3)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        response_data = body["data"]
        assert isinstance(response_data, dict)
        sub_factor_detail = response_data.get("sub_factor_detail")
        actual_status = sub_factor_detail.get("status") if isinstance(sub_factor_detail, dict) else response_data.get("status")
        assert actual_status == 3

    @allure.title("SF-12 批量更新子因子状态成功")
    def test_sf_12_batch_update_sub_factor_status_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: SF-12
        测试目的: 验证管理员可以批量更新子因子状态，且响应状态与请求状态一致。

        请求参数:
            先创建子因子并登记清理，再用 sub_factor_ids 批量更新状态为 3。
        返回值:
            批量状态更新接口应返回成功响应，data.status 应等于 3，updated_sub_factor_ids 应包含被更新子因子。
        """
        data = self.create_auto_sub_factor(factor_resource_api, test_data_factory, "sf_12")
        sub_factor_id = data.get("id")
        if not sub_factor_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("sub_factor", sub_factor_id, lambda value: factor_resource_api.update_sub_factor_status(value, 3))

        response = factor_resource_api.batch_update_sub_factor_status([sub_factor_id], 3)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        response_data = body["data"]
        assert isinstance(response_data, dict)
        assert response_data.get("status") == 3
        assert sub_factor_id in response_data.get("updated_sub_factor_ids", [])

    @allure.title("SF-13 创建子因子刷新任务返回明确结果")
    def test_sf_13_refresh_sub_factor_accepted_or_validation_error(self, factor_resource_api):
        """Case ID: SF-13
        测试目的: 验证刷新子因子接口不会返回服务端错误。

        请求参数:
            从子因子列表派生 sub_factor_id。
        返回值:
            接口应返回成功、Accepted 或明确参数/业务错误，HTTP 状态码小于 500。
        """
        try:
            response = factor_resource_api.refresh_sub_factor(self.first_sub_factor_id(factor_resource_api))
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code < 500

    @allure.title("SF-14 查询子因子刷新任务状态成功")
    def test_sf_14_get_refresh_status_success_when_refresh_accepted(self, factor_resource_api):
        """Case ID: SF-14
        测试目的: 验证刷新接口返回 refresh_id 后可以查询刷新任务状态。

        请求参数:
            从子因子列表派生 sub_factor_id，先调用 refresh，再使用返回的 refresh_id 查询状态。
        返回值:
            如果 refresh 响应返回 refresh_id/id，则状态查询接口应返回成功响应；未返回查询凭证时跳过。
        """
        sub_factor_id = self.first_sub_factor_id(factor_resource_api)
        try:
            refresh_response = factor_resource_api.refresh_sub_factor(sub_factor_id)
        except HTTPError as exc:
            refresh_response = HTTPResponseService.from_http_error(exc)

        refresh_body = refresh_response.json() if refresh_response.content else {}
        assert refresh_response.status_code < 500
        data = refresh_body.get("data") if isinstance(refresh_body, dict) else {}
        refresh_id = None
        if isinstance(data, dict):
            refresh_id = data.get("refresh_id") or data.get("id")
        if not refresh_id:
            pytest.skip("refresh 响应未返回 refresh_id/id，无法继续查询刷新任务状态。")

        response = factor_resource_api.get_sub_factor_refresh(sub_factor_id, refresh_id)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("SF-15 子因子汇总查询成功")
    def test_sf_15_sub_factor_summary_returns_valid_structure(self, factor_resource_api):
        """Case ID: SF-15
        测试目的: 验证子因子汇总接口返回成功响应。

        请求参数:
            type=new，page=1，limit=5。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.list_sub_factor_summary(type="new", page=1, limit=5)
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("SF-16 子因子图表汇总查询成功")
    def test_sf_16_sub_factor_graph_returns_valid_structure(self, factor_resource_api):
        """Case ID: SF-16
        测试目的: 验证子因子图表汇总接口返回成功响应。

        请求参数:
            type=new。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.get_sub_factors_graph(type="new")
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("SF-17 子因子筛选项查询成功")
    def test_sf_17_sub_factor_filter_options_returns_valid_structure(self, factor_resource_api):
        """Case ID: SF-17
        测试目的: 验证子因子筛选项接口返回成功响应。

        请求参数:
            不传筛选参数。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.list_sub_factor_filter_options()
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("SF-18 复制子因子成功")
    def test_sf_18_copy_sub_factors_success_for_auto_sub_factor(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: SF-18
        测试目的: 验证复制子因子接口可以处理自动化子因子，且复制副本进入新挖库。

        请求参数:
            先创建 auto_test 子因子并登记源数据失效清理，再传 sub_factor_ids=[sub_factor_id]。
        返回值:
            copy 接口应返回成功响应；copy 副本详情状态应为 1，并保留后由人工或定时任务清理。
        """
        data = self.create_auto_sub_factor(factor_resource_api, test_data_factory, "sf_18")
        sub_factor_id = data.get("id")
        if not sub_factor_id:
            JSONResponseAssertionService.fail_with_api_json(data)
        resource_tracker.track("sub_factor", sub_factor_id, lambda value: factor_resource_api.update_sub_factor_status(value, 3))

        response = factor_resource_api.copy_sub_factors([sub_factor_id])
        body = response.json()

        errors = FactorAssertionService.success_with_data_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        response_data = body["data"]
        copied_sub_factors = response_data.get("sub_factors") if isinstance(response_data, dict) else None
        assert isinstance(copied_sub_factors, list)
        assert copied_sub_factors
        copied_sub_factor_id = copied_sub_factors[0].get("id")
        assert copied_sub_factor_id
        resource_tracker.track("sub_factor", copied_sub_factor_id, lambda value: factor_resource_api.update_sub_factor_status(value, 3))

        copied_detail_response = factor_resource_api.get_sub_factor(copied_sub_factor_id)
        copied_detail_body = copied_detail_response.json()

        errors = FactorAssertionService.success_with_data_errors(copied_detail_response.status_code, copied_detail_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(copied_detail_body)
        copied_sub_factor_detail = copied_detail_body["data"].get("sub_factor_detail")
        assert isinstance(copied_sub_factor_detail, dict)
        assert copied_sub_factor_detail.get("status") == 1
