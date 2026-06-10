from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.admin.admin_assertions import AdminAssertionService


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Admin")
class TestAdminPromptAPI:
    """Admin 提示词接口自动化用例集。

    请求参数:
        使用管理员 token 和每个用例内声明的提示词参数发起 Admin Prompt 接口请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    def create_prompt_payload(self, test_data_factory, case_id: str) -> dict:
        """生成自动化提示词创建参数。

        请求参数:
            test_data_factory: 自动化测试数据工厂 fixture。
            case_id: 当前用例编号。
        返回值:
            创建提示词接口 JSON body。
        """
        name = test_data_factory.prompt_name(case_id)
        return {
            "name": name,
            "type": "system",
            "used_by": "api_test",
            "user_prompt": "auto user prompt",
            "system_prompt": "auto system prompt",
        }

    def create_auto_prompt(self, admin_api, test_data_factory, case_id: str) -> dict:
        """创建自动化提示词并返回接口 data。

        请求参数:
            admin_api: Admin API fixture。
            test_data_factory: 自动化测试数据工厂 fixture。
            case_id: 当前用例编号。
        返回值:
            创建提示词接口响应中的 data 字典。
        """
        response = admin_api.create_prompt(self.create_prompt_payload(test_data_factory, case_id))
        body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)
        return body["data"]

    @allure.title("ADP-01 提示词列表查询成功")
    def test_adp_01_list_prompts_success(self, admin_api):
        """Case ID: ADP-01
        测试目的: 验证提示词列表接口返回成功响应。

        请求参数:
            limit=5。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.list_prompts(limit=5)
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADP-02 按 used_by 查询提示词成功")
    def test_adp_02_list_prompts_filter_by_used_by_success(self, admin_api):
        """Case ID: ADP-02
        测试目的: 验证提示词列表支持 used_by 筛选。

        请求参数:
            used_by=api_test，limit=5。
        返回值:
            接口应返回 HTTP 200、success=True 和 data。
        """
        response = admin_api.list_prompts(used_by="api_test", limit=5)
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADP-03 创建提示词成功")
    def test_adp_03_create_prompt_success(self, admin_api, test_data_factory):
        """Case ID: ADP-03
        测试目的: 验证管理员可以创建自动化提示词。

        请求参数:
            name 使用 auto_test 唯一值，type=system，used_by=api_test。
        返回值:
            创建提示词接口应返回成功响应；提示词无删除接口，auto_test 数据保留后由人工或定时任务清理。
        """
        data = self.create_auto_prompt(admin_api, test_data_factory, "adp_03")

        assert data.get("id")

    @allure.title("ADP-04 缺少 name 创建提示词失败")
    def test_adp_04_create_prompt_missing_name_fails(self, admin_api):
        """Case ID: ADP-04
        测试目的: 验证创建提示词缺少 name 时失败。

        请求参数:
            type=system，used_by=api_test，user_prompt=test，不传 name。
        返回值:
            接口应返回 400、401、403、409 或 422。
        """
        try:
            response = admin_api.create_prompt({"type": "system", "used_by": "api_test", "user_prompt": "test"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 401, 403, 409, 422}

    @allure.title("ADP-05 更新提示词成功")
    def test_adp_05_update_prompt_success(self, admin_api, test_data_factory):
        """Case ID: ADP-05
        测试目的: 验证管理员可以更新自动化提示词。

        请求参数:
            先创建 auto_test 提示词，再更新 user_prompt。
        返回值:
            更新提示词接口应返回成功响应；提示词无删除接口，auto_test 数据保留后由人工或定时任务清理。
        """
        data = self.create_auto_prompt(admin_api, test_data_factory, "adp_05")
        prompt_id = data.get("id")
        if not prompt_id:
            JSONResponseAssertionService.fail_with_api_json(data)

        response = admin_api.update_prompt(prompt_id, {"user_prompt": "updated"})
        response_body = response.json()
        errors = AdminAssertionService.success_errors(response.status_code, response_body)
        if errors:
            JSONResponseAssertionService.fail_with_api_json(response_body)

    @allure.title("ADP-06 更新不存在提示词失败")
    def test_adp_06_update_nonexistent_prompt_fails(self, admin_api):
        """Case ID: ADP-06
        测试目的: 验证更新不存在提示词时返回明确错误。

        请求参数:
            prompt_id=999999999，name=missing。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = admin_api.update_prompt(999999999, {"name": "missing"})
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        assert response.status_code in {400, 404, 422}
