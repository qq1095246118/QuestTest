from __future__ import annotations

from typing import Any

import allure
import pytest
from requests import Response
from requests.exceptions import HTTPError

from api.platform.factor_library_api import FactorLibraryAPI
from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.factors.factor_library_compare import FactorListCompareService
from service.factor_library.factors.factor_library_queries import FactorListDBService, FactorListQuery


@pytest.mark.factor_library_api
class TestFactorListAPI:
    """因子列表接口自动化用例集。

    请求参数:
        通过每个测试方法内声明的分页、筛选、排序和鉴权参数发起因子列表请求。
    返回值:
        无返回值；pytest 根据方法内断言判断用例是否通过。
    """

    def assert_factor_list_api_success(
        self,
        response: Response,
        body: Any,
        expected_page: int | None = None,
        expected_limit: int | None = None,
    ) -> None:
        """断言因子列表接口自身基础响应正确。

        请求参数:
            response: 因子列表接口原始 HTTP 响应对象。
            body: 因子列表接口返回的原始 JSON。
            expected_page: 当前用例期望的分页页码。
            expected_limit: 当前用例期望的分页条数。
        返回值:
            无；接口自身基础响应错误时在控制台和 Allure 中输出接口原始 JSON。
        """
        errors = FactorListCompareService.factor_list_api_errors(
            response.status_code,
            body,
            expected_page=expected_page,
            expected_limit=expected_limit,
        )
        if not errors:
            return

        JSONResponseAssertionService.fail_with_api_json(body)

    def assert_factor_list_business_rules(self, body: Any, query_params: dict[str, Any]) -> None:
        """断言因子列表接口响应符合请求参数对应的接口自身业务规则。

        请求参数:
            body: 因子列表接口返回的原始 JSON。
            query_params: 当前请求使用的查询参数。
        返回值:
            无；接口自身业务规则错误时在控制台和 Allure 中输出接口原始 JSON。
        """
        errors = FactorListCompareService.factor_list_business_rule_errors(body, query_params)
        if not errors:
            return

        JSONResponseAssertionService.fail_with_api_json(body)

    def assert_no_factor_list_db_mismatches(self, api_body: Any, db_page: Any) -> None:
        """断言因子列表接口响应与 DB 查询结果一致。

        请求参数:
            api_body: 因子列表接口返回的原始 JSON。
            db_page: DB 查询 service 返回的原始分页 JSON。
        返回值:
            无；不一致时在控制台和 Allure 中输出接口原始 JSON 与 DB 原始 JSON。
        """
        mismatches = FactorListCompareService.factor_list_db_mismatches(api_body, db_page)
        if not mismatches:
            return

        JSONResponseAssertionService.fail_with_two_json("接口返回 JSON", api_body, "DB 查询 JSON", db_page)

    def assert_no_theme_relation_mismatches(self, factor_body: Any, themes_body: Any) -> None:
        """断言因子列表主题与主题列表接口数据一致。

        请求参数:
            factor_body: 因子列表接口返回的原始 JSON。
            themes_body: 主题列表接口返回的原始 JSON。
        返回值:
            无；不一致时在控制台和 Allure 中输出两个接口的原始 JSON。
        """
        mismatches = FactorListCompareService.theme_relation_mismatches(factor_body, themes_body)
        if not mismatches:
            return

        JSONResponseAssertionService.fail_with_two_json(
            "因子列表接口返回 JSON",
            factor_body,
            "主题列表接口返回 JSON",
            themes_body,
        )

    def assert_no_page_overlap(self, first_body: Any, second_body: Any) -> None:
        """断言两个分页接口响应中的因子 id 不重复。

        请求参数:
            first_body: 第一页因子列表接口返回的原始 JSON。
            second_body: 第二页因子列表接口返回的原始 JSON。
        返回值:
            无；存在重复数据时在控制台和 Allure 中输出两个分页接口的原始 JSON。
        """
        first_ids = {item["id"] for item in first_body["data"]["items"]}
        second_ids = {item["id"] for item in second_body["data"]["items"]}
        if first_ids.isdisjoint(second_ids):
            return

        JSONResponseAssertionService.fail_with_two_json(
            "第一页接口返回 JSON",
            first_body,
            "第二页接口返回 JSON",
            second_body,
        )

    @allure.title("FA-01 默认第一页因子列表与 DB 一致")
    @pytest.mark.live_db
    def test_fa_01_default_first_page_matches_db(self, factor_api, db_client):
        """Case ID: FA-01
        测试目的: 验证默认第一页因子列表与 DB 当前页数据一致。

        请求参数:
            page=1，limit=5。
        返回值:
            接口响应结构应正确，分页总数、当前页 id 顺序、基础字段、详情字段和主题字段应与 DB 一致。
        """
        params = {"page": 1, "limit": 5}
        response = factor_api.list_factors(**params)
        body = response.json()

        self.assert_factor_list_api_success(response, body, expected_page=params["page"], expected_limit=params["limit"])
        self.assert_factor_list_business_rules(body, params)
        db_page = FactorListDBService.fetch_factor_list_page(db_client, FactorListQuery(**params))
        self.assert_no_factor_list_db_mismatches(body, db_page)

    @allure.title("FA-03 第二页因子列表与第一页不重复且与 DB 一致")
    @pytest.mark.live_db
    def test_fa_03_page_two_does_not_overlap_first_page_and_matches_db(self, factor_api, db_client):
        """Case ID: FA-03
        测试目的: 验证第二页因子列表与第一页不重复，并与 DB 当前页一致。

        请求参数:
            第一次请求 page=1，limit=5；第二次请求 page=2，limit=5。
        返回值:
            第一页和第二页 id 不应重复，第二页分页和字段数据应与 DB 一致。
        """
        first_params = {"page": 1, "limit": 5}
        second_params = {"page": 2, "limit": 5}

        first_response = factor_api.list_factors(**first_params)
        second_response = factor_api.list_factors(**second_params)
        first_body = first_response.json()
        second_body = second_response.json()

        self.assert_factor_list_api_success(
            first_response,
            first_body,
            expected_page=first_params["page"],
            expected_limit=first_params["limit"],
        )
        self.assert_factor_list_api_success(
            second_response,
            second_body,
            expected_page=second_params["page"],
            expected_limit=second_params["limit"],
        )
        self.assert_factor_list_business_rules(first_body, first_params)
        self.assert_factor_list_business_rules(second_body, second_params)
        self.assert_no_page_overlap(first_body, second_body)

        db_page = FactorListDBService.fetch_factor_list_page(db_client, FactorListQuery(**second_params))
        self.assert_no_factor_list_db_mismatches(second_body, db_page)

    @allure.title("FA-05 按 updated_at 升序查询因子列表与 DB 一致")
    @pytest.mark.live_db
    def test_fa_05_sort_updated_at_asc_matches_db(self, factor_api, db_client):
        """Case ID: FA-05
        测试目的: 验证因子列表按 updated_at 升序排序时与 DB 顺序一致。

        请求参数:
            page=1，limit=5，sort_by=updated_at，sort_order=asc。
        返回值:
            接口当前页 id 顺序和字段数据应与 DB 按相同排序查询的结果一致。
        """
        params = {"page": 1, "limit": 5, "sort_by": "updated_at", "sort_order": "asc"}
        response = factor_api.list_factors(**params)
        body = response.json()

        self.assert_factor_list_api_success(response, body, expected_page=params["page"], expected_limit=params["limit"])
        self.assert_factor_list_business_rules(body, params)
        db_page = FactorListDBService.fetch_factor_list_page(db_client, FactorListQuery(**params))
        self.assert_no_factor_list_db_mismatches(body, db_page)

    @allure.title("FA-06 按 updated_at 降序查询因子列表与 DB 一致")
    @pytest.mark.live_db
    def test_fa_06_sort_updated_at_desc_matches_db(self, factor_api, db_client):
        """Case ID: FA-06
        测试目的: 验证因子列表按 updated_at 降序排序时与 DB 顺序一致。

        请求参数:
            page=1，limit=5，sort_by=updated_at，sort_order=desc。
        返回值:
            接口当前页 id 顺序和字段数据应与 DB 按相同排序查询的结果一致。
        """
        params = {"page": 1, "limit": 5, "sort_by": "updated_at", "sort_order": "desc"}
        response = factor_api.list_factors(**params)
        body = response.json()

        self.assert_factor_list_api_success(response, body, expected_page=params["page"], expected_limit=params["limit"])
        self.assert_factor_list_business_rules(body, params)
        db_page = FactorListDBService.fetch_factor_list_page(db_client, FactorListQuery(**params))
        self.assert_no_factor_list_db_mismatches(body, db_page)

    @allure.title("FA-07 按主题筛选因子列表与 DB 一致")
    @pytest.mark.live_db
    def test_fa_07_filter_by_theme_matches_db(self, factor_api, db_client):
        """Case ID: FA-07
        测试目的: 验证使用真实 theme_key 筛选因子列表时与 DB 一致。

        请求参数:
            先请求 page=1，limit=5 获取第一个可用 theme_key，再请求 page=1，limit=5，factor_theme=<theme_key>。
        返回值:
            筛选后的接口分页和字段数据应与 DB 按相同 theme_key 查询的结果一致。
        """
        seed_params = {"page": 1, "limit": 5}
        seed_response = factor_api.list_factors(**seed_params)
        seed_body = seed_response.json()

        self.assert_factor_list_api_success(
            seed_response,
            seed_body,
            expected_page=seed_params["page"],
            expected_limit=seed_params["limit"],
        )
        self.assert_factor_list_business_rules(seed_body, seed_params)

        if not seed_body["data"]["items"]:
            pytest.skip("Factor list first page is empty; cannot derive theme filter.")

        theme_key = FactorListCompareService.first_theme_key_from_factor_list(seed_body)
        if not theme_key:
            pytest.skip("Factor list first page has no theme_key; cannot derive theme filter.")

        params = {"page": 1, "limit": 5, "factor_theme": theme_key}
        response = factor_api.list_factors(**params)
        body = response.json()

        self.assert_factor_list_api_success(response, body, expected_page=params["page"], expected_limit=params["limit"])
        self.assert_factor_list_business_rules(body, params)
        db_page = FactorListDBService.fetch_factor_list_page(db_client, FactorListQuery(**params))
        self.assert_no_factor_list_db_mismatches(body, db_page)

    @allure.title("FA-08 按 factor_detail_status=1 筛选因子列表与 DB 一致")
    @pytest.mark.live_db
    def test_fa_08_filter_by_factor_detail_status_matches_db(self, factor_api, db_client):
        """Case ID: FA-08
        测试目的: 验证使用详情状态筛选因子列表时与 DB 一致。

        请求参数:
            page=1，limit=5，factor_detail_status=1。
        返回值:
            筛选后的接口分页和字段数据应与 DB 按相同详情状态查询的结果一致。
        """
        params = {"page": 1, "limit": 5, "factor_detail_status": 1}
        response = factor_api.list_factors(**params)
        body = response.json()

        self.assert_factor_list_api_success(response, body, expected_page=params["page"], expected_limit=params["limit"])
        self.assert_factor_list_business_rules(body, params)
        db_page = FactorListDBService.fetch_factor_list_page(db_client, FactorListQuery(**params))
        self.assert_no_factor_list_db_mismatches(body, db_page)

    @allure.title("FA-09 未带 token 查询因子列表")
    def test_fa_09_list_factors_without_token_is_unauthorized(self):
        """Case ID: FA-09
        测试目的: 验证未带 Authorization 时不能访问因子列表。

        请求参数:
            page=1，limit=5，不传 token。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("Factor Library API BASE_URL is not configured.")

        try:
            response = FactorLibraryAPI().list_factors(page=1, limit=5)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {401, 403}

    @allure.title("FA-10 使用无效 token 查询因子列表")
    def test_fa_10_list_factors_invalid_token_is_unauthorized(self):
        """Case ID: FA-10
        测试目的: 验证伪造 token 不能访问因子列表。

        请求参数:
            page=1，limit=5，token=invalid-token。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("Factor Library API BASE_URL is not configured.")

        try:
            response = FactorLibraryAPI(token="invalid-token").list_factors(page=1, limit=5)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {401, 403}

    @allure.title("FA-11 page=0 查询因子列表不返回 500")
    def test_fa_11_page_zero_does_not_return_500(self, factor_api):
        """Case ID: FA-11
        测试目的: 验证 page=0 时接口不会返回服务端错误。

        请求参数:
            page=0，limit=20。
        返回值:
            接口应返回明确参数错误或合法修正结果，HTTP 状态码应小于 500。
        """
        try:
            response = factor_api.list_factors(page=0, limit=20)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code < 500

    @allure.title("FA-12 limit=501 查询因子列表不返回 500")
    def test_fa_12_limit_too_large_does_not_return_500(self, factor_api):
        """Case ID: FA-12
        测试目的: 验证 limit 超出常规范围时接口不会返回服务端错误。

        请求参数:
            page=1，limit=501。
        返回值:
            接口应返回明确参数错误或合法限制结果，HTTP 状态码应小于 500。
        """
        try:
            response = factor_api.list_factors(page=1, limit=501)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code < 500

    @allure.title("FA-13 sort_order=bad 查询因子列表不返回 500")
    def test_fa_13_invalid_sort_order_does_not_return_500(self, factor_api):
        """Case ID: FA-13
        测试目的: 验证非法 sort_order 时接口不会返回服务端错误。

        请求参数:
            page=1，limit=20，sort_by=updated_at，sort_order=bad。
        返回值:
            接口应返回明确参数错误或合法默认排序结果，HTTP 状态码应小于 500。
        """
        try:
            response = factor_api.list_factors(page=1, limit=20, sort_by="updated_at", sort_order="bad")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code < 500

    @allure.title("FA-14 因子列表主题存在于主题列表")
    def test_fa_14_factor_themes_exist_in_theme_list(self, factor_api):
        """Case ID: FA-14
        测试目的: 验证因子列表返回的主题能在主题列表接口中找到。

        请求参数:
            因子列表 page=1，limit=5；主题列表不传筛选参数。
        返回值:
            因子列表中每个 theme_id 都应存在于主题列表接口返回结果中。
        """
        factor_params = {"page": 1, "limit": 5}
        factor_response = factor_api.list_factors(**factor_params)
        factor_body = factor_response.json()
        themes_body = factor_api.list_themes().json()

        self.assert_factor_list_api_success(
            factor_response,
            factor_body,
            expected_page=factor_params["page"],
            expected_limit=factor_params["limit"],
        )
        self.assert_factor_list_business_rules(factor_body, factor_params)
        self.assert_no_theme_relation_mismatches(factor_body, themes_body)
