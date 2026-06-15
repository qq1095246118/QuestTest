from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.factor_api import FactorAPI
from config.settings import settings
from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.factors.factor_test_data import FactorTestDataService


class KnownDuplicateThemeAllowed(Exception):
    """后端允许重复 theme_name 或 cn_name 创建主题时抛出的已知差异异常。"""


class KnownThemeNoChangeUpdateAllowed(Exception):
    """后端允许主题无变更更新时抛出的已知差异异常。"""


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("factor")
class TestThemeAPI:
    """主题接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的主题参数发起主题接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    @allure.title("TH-01 主题列表查询成功")
    def test_th_01_list_themes_success(self, factor_resource_api):
        """Case ID: TH-01
        测试目的: 验证主题列表接口返回成功响应。

        请求参数:
            不传筛选参数。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = factor_resource_api.list_themes()
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("TH-02 按 theme_key 查询主题列表成功")
    def test_th_02_list_themes_by_theme_key_success(self, factor_resource_api):
        """Case ID: TH-02
        测试目的: 验证主题列表支持 theme_key 筛选。

        请求参数:
            先读取主题列表第一个 theme_key，再按该 theme_key 查询。
        返回值:
            筛选接口应返回 HTTP 200、success=True 和 data。
        """
        seed_body = factor_resource_api.list_themes().json()
        data = seed_body.get("data")
        items = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        if not items:
            pytest.skip("主题列表为空，无法派生 theme_key。")

        theme_key = items[0].get("theme_key")
        if not theme_key:
            pytest.skip("主题列表首条数据没有 theme_key。")

        response = factor_resource_api.list_themes(theme_key=theme_key)
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("TH-03 未带 token 查询主题列表失败")
    def test_th_03_list_themes_without_token_is_unauthorized(self):
        """Case ID: TH-03
        测试目的: 验证未带 token 不能访问主题列表。

        请求参数:
            不传 Authorization。
        返回值:
            接口应返回 401 或 403。
        """
        if not settings.base_url:
            pytest.skip("没有配置接口地址")

        try:
            response = FactorAPI().list_themes()
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {401, 403}

    @allure.title("TH-04 创建主题成功")
    def test_th_04_create_theme_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: TH-04
        测试目的: 验证管理员可以创建自动化主题。

        请求参数:
            theme_key、theme_name、cn_name 使用 auto 前缀唯一值。
        返回值:
            接口应返回成功响应，并返回创建出的主题数据。
        """
        name = test_data_factory.name("theme", "th_04")
        response = factor_resource_api.create_theme(
            {"theme_key": name, "theme_name": name, "cn_name": name, "theme_tags": "auto"}
        )
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

        theme_id = body["data"].get("id")
        if theme_id:
            resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

    @allure.title("TH-04B 创建主题默认有效")
    def test_th_04b_create_theme_default_status_is_valid(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: TH-04B
        测试目的: 验证新创建主题默认 status=2。

        请求参数:
            theme_key、theme_name、cn_name 使用 auto 前缀唯一值，不传 status。
        返回值:
            创建响应或详情中的 status 应为 2。
        """
        payload = FactorTestDataService.build_theme_payload(test_data_factory, "th_04b")
        response = factor_resource_api.create_theme(payload)
        body = response.json()
        theme_id = body.get("data", {}).get("id") if isinstance(body.get("data"), dict) else None
        if not theme_id:
            JSONResponseAssertionService.fail_with_api_json(body)
        resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

        detail_body = factor_resource_api.get_theme(theme_id).json()
        status = detail_body.get("data", {}).get("status")
        if status != 2:
            JSONResponseAssertionService.fail_with_api_json(detail_body)

    @allure.title("TH-05 缺少 theme_key 创建主题失败")
    def test_th_05_create_theme_missing_theme_key_fails(self, factor_resource_api, resource_tracker):
        """Case ID: TH-05
        测试目的: 验证创建主题缺少必填 theme_key 时失败。

        请求参数:
            theme_name=auto_missing_key，不传 theme_key；如果接口错误创建出资源，则登记清理。
        返回值:
            接口应返回 400、401、403、409 或 422，不应返回 500。
        """
        try:
            response = factor_resource_api.create_theme({"theme_name": "auto_missing_key"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json()
        theme_id = body.get("data", {}).get("id") if isinstance(body.get("data"), dict) else None
        if theme_id:
            resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

        assert response.status_code in {400, 401, 403, 409, 422}

    @allure.title("TH-06 重复 theme_key 创建主题失败")
    def test_th_06_create_duplicate_theme_key_fails(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: TH-06
        测试目的: 验证重复 theme_key 不能创建主题。

        请求参数:
            连续两次使用相同 theme_key 创建主题。
        返回值:
            第一次成功，第二次应返回 400、409 或 422。
        """
        name = test_data_factory.name("theme", "th_06")
        payload = {"theme_key": name, "theme_name": name, "cn_name": name}
        created_body = factor_resource_api.create_theme(payload).json()
        theme_id = created_body.get("data", {}).get("id")
        if theme_id:
            resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

        try:
            response = factor_resource_api.create_theme(payload)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 409, 422}

    @allure.title("TH-06B 重复 theme_name 创建主题失败")
    @pytest.mark.xfail(
        raises=KnownDuplicateThemeAllowed,
        strict=True,
        reason="后端当前允许重复 theme_name 创建主题，和已确认规则不一致。",
    )
    def test_th_06b_create_duplicate_theme_name_fails(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: TH-06B
        测试目的: 验证重复 theme_name 不能创建主题。

        请求参数:
            第一次创建 auto 主题，第二次使用不同 theme_key 但相同 theme_name。
        返回值:
            第二次创建应返回 400、409 或 422。
        """
        payload = FactorTestDataService.build_theme_payload(test_data_factory, "th_06b")
        created_body = factor_resource_api.create_theme(payload).json()
        theme_id = created_body.get("data", {}).get("id")
        if theme_id:
            resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

        duplicate_payload = dict(payload)
        duplicate_payload["theme_key"] = test_data_factory.name("theme", "th_06b_dup")
        try:
            response = factor_resource_api.create_theme(duplicate_payload)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        duplicate_theme_id = body.get("data", {}).get("id") if isinstance(body.get("data"), dict) else None
        if duplicate_theme_id:
            resource_tracker.track("theme", duplicate_theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))
        if response.status_code == 200 and body.get("success") is True and duplicate_theme_id:
            raise KnownDuplicateThemeAllowed("duplicate theme_name was created")
        assert response.status_code in {400, 409, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("TH-06C 重复 cn_name 创建主题失败")
    @pytest.mark.xfail(
        raises=KnownDuplicateThemeAllowed,
        strict=True,
        reason="后端当前允许重复 cn_name 创建主题，和已确认规则不一致。",
    )
    def test_th_06c_create_duplicate_theme_cn_name_fails(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: TH-06C
        测试目的: 验证重复 cn_name 不能创建主题。

        请求参数:
            第一次创建 auto 主题，第二次使用不同 theme_key/theme_name 但相同 cn_name。
        返回值:
            第二次创建应返回 400、409 或 422。
        """
        payload = FactorTestDataService.build_theme_payload(test_data_factory, "th_06c")
        created_body = factor_resource_api.create_theme(payload).json()
        theme_id = created_body.get("data", {}).get("id")
        if theme_id:
            resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

        duplicate_payload = dict(payload)
        duplicate_payload["theme_key"] = test_data_factory.name("theme", "th_06c_key")
        duplicate_payload["theme_name"] = test_data_factory.name("theme", "th_06c_name")
        try:
            response = factor_resource_api.create_theme(duplicate_payload)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        duplicate_theme_id = body.get("data", {}).get("id") if isinstance(body.get("data"), dict) else None
        if duplicate_theme_id:
            resource_tracker.track("theme", duplicate_theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))
        if response.status_code == 200 and body.get("success") is True and duplicate_theme_id:
            raise KnownDuplicateThemeAllowed("duplicate theme cn_name was created")
        assert response.status_code in {400, 409, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("TH-07 查询主题详情成功")
    def test_th_07_get_theme_success(self, factor_resource_api):
        """Case ID: TH-07
        测试目的: 验证可以根据真实 theme_id 查询主题详情。

        请求参数:
            从主题列表首条数据派生 theme_id。
        返回值:
            主题详情接口应返回成功响应。
        """
        seed_body = factor_resource_api.list_themes().json()
        data = seed_body.get("data")
        items = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        if not items:
            pytest.skip("主题列表为空，无法派生 theme_id。")

        theme_id = items[0].get("id")
        response = factor_resource_api.get_theme(theme_id)
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("TH-08 查询不存在主题失败")
    def test_th_08_get_nonexistent_theme_fails(self, factor_resource_api):
        """Case ID: TH-08
        测试目的: 验证查询不存在主题时返回明确错误。

        请求参数:
            theme_id=999999999。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = factor_resource_api.get_theme(999999999)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("TH-09 更新主题成功")
    def test_th_09_update_theme_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: TH-09
        测试目的: 验证管理员可以更新自动化主题。

        请求参数:
            先创建主题，再更新 cn_name。
        返回值:
            更新接口应返回成功响应。
        """
        name = test_data_factory.name("theme", "th_09")
        created_body = factor_resource_api.create_theme({"theme_key": name, "theme_name": name, "cn_name": name}).json()
        theme_id = created_body.get("data", {}).get("id")
        if not theme_id:
            JSONResponseAssertionService.fail_with_api_json(created_body)
        resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

        response = factor_resource_api.update_theme(theme_id, {"cn_name": f"{name}_updated"})
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("TH-09B 无变更更新主题失败")
    @pytest.mark.xfail(
        raises=KnownThemeNoChangeUpdateAllowed,
        strict=True,
        reason="后端当前允许主题无变更更新，和已确认规则不一致。",
    )
    def test_th_09b_update_theme_without_changes_fails(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: TH-09B
        测试目的: 验证主题无实际字段变更时不允许提交更新。

        请求参数:
            先创建 auto 主题，再使用当前 cn_name 更新。
        返回值:
            更新接口应返回 400、409 或 422。
        """
        payload = FactorTestDataService.build_theme_payload(test_data_factory, "th_09b")
        created_body = factor_resource_api.create_theme(payload).json()
        theme_id = created_body.get("data", {}).get("id")
        if not theme_id:
            JSONResponseAssertionService.fail_with_api_json(created_body)
        resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

        try:
            response = factor_resource_api.update_theme(theme_id, {"cn_name": payload["cn_name"]})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        if response.status_code == 200 and body.get("success") is True:
            response_data = body.get("data")
            if isinstance(response_data, dict) and response_data.get("id") == theme_id:
                raise KnownThemeNoChangeUpdateAllowed("theme no-change update was accepted")
        assert response.status_code in {400, 409, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)

    @allure.title("TH-10 更新不存在主题失败")
    def test_th_10_update_nonexistent_theme_fails(self, factor_resource_api):
        """Case ID: TH-10
        测试目的: 验证更新不存在主题时返回明确错误。

        请求参数:
            theme_id=999999999，cn_name=missing。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = factor_resource_api.update_theme(999999999, {"cn_name": "missing"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}

    @allure.title("TH-11 更新主题状态成功")
    def test_th_11_update_theme_status_success(self, factor_resource_api, test_data_factory, resource_tracker):
        """Case ID: TH-11
        测试目的: 验证管理员可以更新主题状态，且返回主题状态与请求状态一致。

        请求参数:
            先创建主题并登记清理，再将状态更新为 3。
        返回值:
            状态更新接口应返回成功响应，data.status 应等于 3。
        """
        name = test_data_factory.name("theme", "th_11")
        created_body = factor_resource_api.create_theme({"theme_key": name, "theme_name": name, "cn_name": name}).json()
        theme_id = created_body.get("data", {}).get("id")
        if not theme_id:
            JSONResponseAssertionService.fail_with_api_json(created_body)
        resource_tracker.track("theme", theme_id, lambda value: factor_resource_api.update_theme_status(value, 3))

        response = factor_resource_api.update_theme_status(theme_id, 3)
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        response_data = body["data"]
        assert isinstance(response_data, dict)
        assert response_data.get("status") == 3

    @allure.title("TH-12 非法主题状态更新失败")
    def test_th_12_update_theme_status_invalid_value_fails(self, factor_resource_api):
        """Case ID: TH-12
        测试目的: 验证主题状态不接受非法枚举值。

        请求参数:
            theme_id=999999999，status=999。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = factor_resource_api.update_theme_status(999999999, 999)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}
