from __future__ import annotations

import allure
import pytest
from requests.exceptions import HTTPError

from service.common.http.json_response_assertion import JSONResponseAssertionService
from service.common.http.response_utils import HTTPResponseService
from service.factor_library.approval.approval_test_data import ApprovalTestDataService
from service.factor_library.factors.factor_test_data import FactorTestDataService


class KnownCrossTypePendingApprovalAllowed(Exception):
    """后端允许同一对象同时存在不同类型 pending 审批时抛出的已知差异异常。"""


@pytest.mark.factor_library_api
@allure.feature("Factor Library API")
@allure.story("Approval Scenario")
class TestApprovalScenario:
    """审批流程连贯场景自动化用例集。

    请求参数:
        使用自动化创建的母因子作为审批对象发起 with-approval 和审批处理请求。
    返回值:
        无返回值；pytest 根据审批状态和业务对象变更判断用例是否通过。
    """

    @allure.title("APS-01 with-approval 更新审批通过后母因子数据生效")
    def test_aps_01_factor_update_approval_approve_applies_change(
        self,
        factor_resource_api,
        approval_api,
        test_data_factory,
        resource_tracker,
    ):
        """Case ID: APS-01
        测试目的: 验证母因子 with-approval 更新提交后不立即生效，通过审批后才生效。

        请求参数:
            自动化母因子 ID，cn_name 更新为唯一名称。
        返回值:
            审批 pending 时详情仍为旧值，审批 approved 后详情变为新值。
        """
        create_response = factor_resource_api.create_factor(
            FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "aps_01")
        )
        create_body = create_response.json()
        factor = create_body.get("data") if isinstance(create_body.get("data"), dict) else {}
        if create_response.status_code not in {200, 201} or create_body.get("success") is not True or not factor.get("id"):
            JSONResponseAssertionService.fail_with_api_json(create_body)
        factor_id = factor["id"]
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))
        original_cn_name = factor.get("cn_name")
        new_cn_name = test_data_factory.name("factor_cn", "aps_01")
        approval_id = None
        approval_processed = False

        try:
            submit_body = factor_resource_api.update_factor_with_approval(factor_id, {"cn_name": new_cn_name}).json()
            approval_id = ApprovalTestDataService.extract_approval_id(submit_body)
            if not approval_id:
                JSONResponseAssertionService.fail_with_api_json(submit_body)

            pending_detail = factor_resource_api.get_factor(factor_id).json()
            if pending_detail.get("data", {}).get("cn_name") == new_cn_name:
                JSONResponseAssertionService.fail_with_api_json(pending_detail)

            approve_body = approval_api.process_approval(approval_id, "approve", comment="auto approve").json()
            if approve_body.get("success") is not True:
                JSONResponseAssertionService.fail_with_api_json(approve_body)
            approval_processed = True

            final_detail = factor_resource_api.get_factor(factor_id).json()
            if final_detail.get("data", {}).get("cn_name") != new_cn_name:
                JSONResponseAssertionService.fail_with_two_json("审批通过 JSON", approve_body, "因子详情 JSON", final_detail)
            if original_cn_name == new_cn_name:
                JSONResponseAssertionService.fail_with_api_json(final_detail)
        finally:
            if approval_id and not approval_processed:
                approval_api.cancel_approval(approval_id)

    @allure.title("APS-02 with-approval 更新审批拒绝后母因子数据不生效")
    def test_aps_02_factor_update_approval_reject_keeps_original(
        self,
        factor_resource_api,
        approval_api,
        test_data_factory,
        resource_tracker,
    ):
        """Case ID: APS-02
        测试目的: 验证母因子 with-approval 更新被拒绝后业务数据不变。

        请求参数:
            自动化母因子 ID，cn_name 更新为唯一名称。
        返回值:
            审批 rejected 后母因子详情仍不是新 cn_name。
        """
        create_response = factor_resource_api.create_factor(
            FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "aps_02")
        )
        create_body = create_response.json()
        factor = create_body.get("data") if isinstance(create_body.get("data"), dict) else {}
        if create_response.status_code not in {200, 201} or create_body.get("success") is not True or not factor.get("id"):
            JSONResponseAssertionService.fail_with_api_json(create_body)
        factor_id = factor["id"]
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))
        new_cn_name = test_data_factory.name("factor_cn", "aps_02")

        submit_body = factor_resource_api.update_factor_with_approval(factor_id, {"cn_name": new_cn_name}).json()
        approval_id = ApprovalTestDataService.extract_approval_id(submit_body)
        if not approval_id:
            JSONResponseAssertionService.fail_with_api_json(submit_body)

        reject_body = approval_api.process_approval(approval_id, "reject", comment="auto reject").json()
        if reject_body.get("success") is not True:
            JSONResponseAssertionService.fail_with_api_json(reject_body)

        final_detail = factor_resource_api.get_factor(factor_id).json()
        if final_detail.get("data", {}).get("cn_name") == new_cn_name:
            JSONResponseAssertionService.fail_with_two_json("审批拒绝 JSON", reject_body, "因子详情 JSON", final_detail)

    @allure.title("APS-03 取消审批后释放对象占用")
    def test_aps_03_cancel_approval_releases_pending_target(
        self,
        factor_resource_api,
        approval_api,
        test_data_factory,
        resource_tracker,
    ):
        """Case ID: APS-03
        测试目的: 验证取消 pending 审批后，同一业务对象可以再次提交审批。

        请求参数:
            自动化母因子 ID，两次不同 cn_name 更新。
        返回值:
            第一次审批取消成功，第二次审批提交成功。
        """
        create_response = factor_resource_api.create_factor(
            FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "aps_03")
        )
        create_body = create_response.json()
        factor = create_body.get("data") if isinstance(create_body.get("data"), dict) else {}
        if create_response.status_code not in {200, 201} or create_body.get("success") is not True or not factor.get("id"):
            JSONResponseAssertionService.fail_with_api_json(create_body)
        factor_id = factor["id"]
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        first_body = factor_resource_api.update_factor_with_approval(
            factor_id,
            {"cn_name": test_data_factory.name("factor_cn", "aps_03a")},
        ).json()
        first_approval_id = ApprovalTestDataService.extract_approval_id(first_body)
        if not first_approval_id:
            JSONResponseAssertionService.fail_with_api_json(first_body)

        cancel_body = approval_api.cancel_approval(first_approval_id).json()
        if cancel_body.get("success") is not True:
            JSONResponseAssertionService.fail_with_api_json(cancel_body)

        second_body = factor_resource_api.update_factor_with_approval(
            factor_id,
            {"cn_name": test_data_factory.name("factor_cn", "aps_03b")},
        ).json()
        second_approval_id = ApprovalTestDataService.extract_approval_id(second_body)
        if not second_approval_id:
            JSONResponseAssertionService.fail_with_api_json(second_body)
        resource_tracker.track_approval_cancel(second_approval_id, approval_api)

    @allure.title("APS-04 同一对象已有更新审批时状态审批提交失败")
    @pytest.mark.xfail(
        raises=KnownCrossTypePendingApprovalAllowed,
        strict=True,
        reason="后端当前允许同一对象同时存在更新审批和状态审批，和已确认规则不一致。",
    )
    def test_aps_04_pending_update_blocks_status_approval(
        self,
        factor_resource_api,
        approval_api,
        test_data_factory,
        resource_tracker,
    ):
        """Case ID: APS-04
        测试目的: 验证同一业务对象任意 pending 审批都会阻止新的审批提交。

        请求参数:
            自动化母因子 ID，先提交更新审批，再提交状态审批。
        返回值:
            状态审批提交失败，原 pending 审批仍由清理器取消。
        """
        create_response = factor_resource_api.create_factor(
            FactorTestDataService.build_factor_payload(factor_resource_api, test_data_factory, "aps_04")
        )
        create_body = create_response.json()
        factor = create_body.get("data") if isinstance(create_body.get("data"), dict) else {}
        if create_response.status_code not in {200, 201} or create_body.get("success") is not True or not factor.get("id"):
            JSONResponseAssertionService.fail_with_api_json(create_body)
        factor_id = factor["id"]
        resource_tracker.track("factor", factor_id, lambda value: factor_resource_api.update_factor_status(value, 3))

        submit_body = factor_resource_api.update_factor_with_approval(
            factor_id,
            {"cn_name": test_data_factory.name("factor_cn", "aps_04")},
        ).json()
        approval_id = ApprovalTestDataService.extract_approval_id(submit_body)
        if not approval_id:
            JSONResponseAssertionService.fail_with_api_json(submit_body)
        resource_tracker.track_approval_cancel(approval_id, approval_api)

        try:
            response = factor_resource_api.update_factor_status_with_approval(factor_id, 2)
        except HTTPError as exc:
            response = HTTPResponseService.from_http_error(exc)

        if response.status_code not in {400, 409, 422}:
            body = response.json()
            unexpected_approval_id = ApprovalTestDataService.extract_approval_id(body)
            if unexpected_approval_id:
                approval_api.cancel_approval(unexpected_approval_id)
            if response.status_code in {200, 201} and body.get("success") is True:
                raise KnownCrossTypePendingApprovalAllowed("cross type pending approval was created")
            JSONResponseAssertionService.fail_with_api_json({"first_approval": submit_body, "second": body})
