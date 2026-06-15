from service.factor_library.approval.approval_test_data import ApprovalTestDataService


class TestApprovalTestDataService:
    """Approval 测试数据服务单元测试。

    请求参数:
        使用固定业务对象 ID 和名称调用 payload 构造方法。
    返回值:
        无返回值；pytest 根据 payload 字段断言判断是否通过。
    """

    def test_build_update_approval_payload(self):
        """验证更新审批 payload 字段完整。

        请求参数:
            target_type=factor、target_id=123、target_name=auto_factor。
        返回值:
            payload 应包含 request_type、target_type、target_id、before_data、after_data 和 change_summary。
        """
        payload = ApprovalTestDataService.build_update_approval_payload(
            target_type="factor",
            target_id=123,
            target_name="auto_factor",
            before_data={"cn_name": "old"},
            after_data={"cn_name": "new"},
        )

        assert payload["request_type"] == "edit_factor"
        assert payload["target_type"] == "factor"
        assert payload["target_id"] == 123
        assert payload["target_name"] == "auto_factor"
        assert payload["before_data"] == {"cn_name": "old"}
        assert payload["after_data"] == {"cn_name": "new"}
        assert "change_summary" in payload

    def test_build_status_approval_payload(self):
        """验证状态审批 payload 字段完整。

        请求参数:
            target_type=sub_factor、target_id=456、before_status=1、after_status=3。
        返回值:
            payload 应标识状态变更，并在 before_data 和 after_data 中记录状态值。
        """
        payload = ApprovalTestDataService.build_status_approval_payload(
            target_type="sub_factor",
            target_id=456,
            target_name="auto_sub_factor",
            before_status=1,
            after_status=3,
        )

        assert payload["request_type"] == "status_change_sub_factor"
        assert payload["target_type"] == "sub_factor"
        assert payload["target_id"] == 456
        assert payload["target_name"] == "auto_sub_factor"
        assert payload["before_data"] == {"status": 1}
        assert payload["after_data"] == {"status": 3}
        assert "change_summary" in payload

    def test_extract_approval_id_from_direct_approval_response(self):
        """验证可从直接审批响应中提取审批 ID。

        请求参数:
            body.data.id=123 的接口响应字典。
        返回值:
            应返回审批 ID 123。
        """
        approval_id = ApprovalTestDataService.extract_approval_id({"data": {"id": 123}})

        assert approval_id == 123

    def test_extract_approval_id_from_nested_approval_response(self):
        """验证可从嵌套 approval 响应中提取审批 ID。

        请求参数:
            body.data.approval.id=456 的接口响应字典。
        返回值:
            应返回审批 ID 456。
        """
        approval_id = ApprovalTestDataService.extract_approval_id({"data": {"approval": {"id": 456}}})

        assert approval_id == 456

    def test_extract_approval_id_from_approval_id_response(self):
        """验证可从 approval_id 响应中提取审批 ID。

        请求参数:
            body.data.approval_id=789 的接口响应字典。
        返回值:
            应返回审批 ID 789。
        """
        approval_id = ApprovalTestDataService.extract_approval_id({"data": {"approval_id": 789}})

        assert approval_id == 789
