from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.approval.approval_test_data import ApprovalTestDataService
from service.factor_library.factors.factor_test_data import FactorTestDataService


class KnownDuplicatePendingApprovalAllowed(Exception):
    """后端允许同一对象重复创建 pending 审批时抛出的已知差异异常。"""


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Approval")
class TestApprovalAPI:
    """审批接口自动化用例集。

    请求参数:
        使用管理员 token、自动化创建的业务对象和每个用例内声明的审批参数发起请求。
    返回值:
        无返回值；pytest 根据接口自身断言判断用例是否通过。
    """

    @allure.title("AP-01 查询审批列表成功")
    def test_ap_01_list_approvals_success(self, approval_api):
        """Case ID: AP-01
        测试目的: 验证审批列表接口可按默认分页成功返回。

        请求参数:
            page=1，limit=20。
        返回值:
            审批列表接口应返回 HTTP 200、success=True 和列表数据结构。
        """
        response = approval_api.list_approvals(page=1, limit=20)
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        if body.get("success") is not True:
            errors.append("success is not True")
        data = body.get("data")
        if not isinstance(data, dict):
            errors.append("data is not dict")
        elif "items" in data and not isinstance(data.get("items"), list):
            errors.append("data.items is not list")
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("AP-02 按审批状态筛选审批列表")
    @pytest.mark.parametrize("status", ["pending", "approved", "rejected", "cancelled"])
    def test_ap_02_list_approvals_filter_by_status(self, approval_api, status):
        """Case ID: AP-02
        测试目的: 验证审批列表 status 筛选条件生效。

        请求参数:
            status 为 pending、approved、rejected、cancelled，page=1，limit=20。
        返回值:
            接口应返回成功响应；如果返回 items，每条已有审批的 status 均等于查询条件。
        """
        response = approval_api.list_approvals(status=status, page=1, limit=20)
        body = response.json()

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        if body.get("success") is not True:
            errors.append("success is not True")
        data = body.get("data")
        items = data.get("items", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            errors.append("data.items is not list")
        for item in items:
            if item.get("status") != status:
                errors.append(f"unexpected item status: {item}")
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("AP-03 创建审批请求成功")
    def test_ap_03_create_approval_success(self, approval_api, factor_resource_api):
        """Case ID: AP-03
        测试目的: 验证可以为真实母因子创建 pending 审批。

        请求参数:
            target_type=factor，target_id 从母因子列表派生，request_type=update。
        返回值:
            创建审批接口应返回成功响应和审批 ID；用例结束时取消审批释放占用。
        """
        factor_id = FactorTestDataService.first_factor_id(factor_resource_api)
        payload = ApprovalTestDataService.build_update_approval_payload(
            target_type="factor",
            target_id=factor_id,
            target_name=f"factor_{factor_id}",
            before_data={"cn_name": "before"},
            after_data={"cn_name": "after"},
        )
        response = approval_api.create_approval(payload)
        body = response.json()
        approval_id = ApprovalTestDataService.extract_approval_id(body)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        approval_data = data.get("approval") if isinstance(data.get("approval"), dict) else data

        try:
            errors = []
            if response.status_code not in {200, 201}:
                errors.append(f"status_code={response.status_code}")
            if body.get("success") is not True:
                errors.append("success is not True")
            if not approval_id:
                errors.append("approval id missing")
            if approval_data.get("status") not in {None, "pending"}:
                errors.append("approval status is not pending")
            if errors:
                JSONResponseAssertionService.fail_with_api_json(body)
        finally:
            if approval_id:
                approval_api.cancel_approval(approval_id)

    @allure.title("AP-04 查询审批详情成功")
    def test_ap_04_get_approval_detail_success(self, approval_api, factor_resource_api):
        """Case ID: AP-04
        测试目的: 验证新建审批可以通过审批 ID 查询详情。

        请求参数:
            先创建 factor 更新审批，再调用详情接口查询该审批。
        返回值:
            详情接口应返回成功响应，data.id 应等于创建审批返回的 ID。
        """
        factor_id = FactorTestDataService.first_factor_id(factor_resource_api)
        payload = ApprovalTestDataService.build_update_approval_payload(
            target_type="factor",
            target_id=factor_id,
            target_name=f"factor_{factor_id}",
            before_data={"cn_name": "before"},
            after_data={"cn_name": "after"},
        )
        create_body = approval_api.create_approval(payload).json()
        approval_id = ApprovalTestDataService.extract_approval_id(create_body)
        if not approval_id:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        try:
            response = approval_api.get_approval(approval_id)
            body = response.json()
            data = body.get("data")
            approval_data = data.get("approval") if isinstance(data, dict) and isinstance(data.get("approval"), dict) else data

            errors = []
            if response.status_code != 200:
                errors.append(f"status_code={response.status_code}")
            if body.get("success") is not True:
                errors.append("success is not True")
            if not isinstance(approval_data, dict) or approval_data.get("id") != approval_id:
                errors.append("approval detail id mismatch")
            if errors:
                JSONResponseAssertionService.fail_with_api_json(body)
        finally:
            approval_api.cancel_approval(approval_id)

    @allure.title("AP-05 取消审批请求成功")
    def test_ap_05_cancel_approval_success(self, approval_api, factor_resource_api):
        """Case ID: AP-05
        测试目的: 验证 pending 审批可以被取消。

        请求参数:
            先创建 factor 更新审批，再调用取消审批接口。
        返回值:
            取消接口应返回成功响应，取消后的审批状态应为 cancelled。
        """
        factor_id = FactorTestDataService.first_factor_id(factor_resource_api)
        payload = ApprovalTestDataService.build_update_approval_payload(
            target_type="factor",
            target_id=factor_id,
            target_name=f"factor_{factor_id}",
            before_data={"cn_name": "before"},
            after_data={"cn_name": "after"},
        )
        create_body = approval_api.create_approval(payload).json()
        approval_id = ApprovalTestDataService.extract_approval_id(create_body)
        if not approval_id:
            JSONResponseAssertionService.fail_with_api_json(create_body)

        response = approval_api.cancel_approval(approval_id)
        body = response.json()
        data = body.get("data")

        errors = []
        if response.status_code != 200:
            errors.append(f"status_code={response.status_code}")
        if body.get("success") is not True:
            errors.append("success is not True")
        if isinstance(data, dict) and data.get("status") not in {None, "cancelled"}:
            errors.append("approval status is not cancelled")
        if errors:
            JSONResponseAssertionService.fail_with_api_json(body)

    @allure.title("AP-06 重复提交同一对象 pending 审批失败")
    @pytest.mark.xfail(
        raises=KnownDuplicatePendingApprovalAllowed,
        strict=True,
        reason="后端当前允许同一对象重复创建 pending 审批，和已确认规则不一致。",
    )
    def test_ap_06_duplicate_pending_approval_fails(self, approval_api, factor_resource_api):
        """Case ID: AP-06
        测试目的: 验证同一业务对象已存在 pending 审批时不能重复提交。

        请求参数:
            同一个 factor_id 连续提交两次 request_type=update 审批。
        返回值:
            第二次提交应返回 400、409 或 422；用例结束时取消第一次审批释放占用。
        """
        factor_id = FactorTestDataService.first_factor_id(factor_resource_api)
        payload = ApprovalTestDataService.build_update_approval_payload(
            target_type="factor",
            target_id=factor_id,
            target_name=f"factor_{factor_id}",
            before_data={"cn_name": "before"},
            after_data={"cn_name": "after"},
        )
        first_body = approval_api.create_approval(payload).json()
        approval_id = ApprovalTestDataService.extract_approval_id(first_body)
        if not approval_id:
            JSONResponseAssertionService.fail_with_api_json(first_body)

        try:
            try:
                duplicate_response = approval_api.create_approval(payload)
            except HTTPError as exc:
                duplicate_response = HTTPResponseService.from_http_error(exc)

            if duplicate_response.status_code not in {400, 409, 422}:
                duplicate_body = duplicate_response.json()
                duplicate_approval_id = ApprovalTestDataService.extract_approval_id(duplicate_body)
                if duplicate_approval_id:
                    approval_api.cancel_approval(duplicate_approval_id)
                if duplicate_response.status_code in {200, 201} and duplicate_body.get("success") is True:
                    raise KnownDuplicatePendingApprovalAllowed("duplicate pending approval was created")
                JSONResponseAssertionService.fail_with_api_json({"first": first_body, "second": duplicate_body})
        finally:
            approval_api.cancel_approval(approval_id)

    @allure.title("AP-07 处理不存在审批失败")
    def test_ap_07_process_missing_approval_fails(self, approval_api):
        """Case ID: AP-07
        测试目的: 验证处理不存在审批 ID 时返回明确错误。

        请求参数:
            approval_id=999999999，action=approve。
        返回值:
            接口应返回 400、404 或 422。
        """
        try:
            response = approval_api.process_approval(999999999, "approve", comment="auto")
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        body = response.json() if response.content else {}
        assert response.status_code in {400, 404, 422}, JSONResponseAssertionService.attach_json("接口返回 JSON", body)
