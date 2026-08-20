"""获取组合工作单接口测试。"""

from __future__ import annotations

import pytest

from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService


@pytest.mark.integration
class TestFactorComboWorkOrderAPI:
    """验证工作单响应、锁定因子池映射和查询只读性。"""

    def test_get_work_order_matches_submitted_form_and_pool(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """查询已提交表单工作单，并验证返回组件与数据库因子池一致且不产生下游数据。"""

        submitted, _ = factor_combo_service.create_form_with_sub_factors()
        versions_before = factor_combo_repository.count_versions_for_form(submitted.form_id)
        feedback_before = factor_combo_repository.count_feedback_for_form(submitted.form_id)

        response = factor_combo_service.get_work_order_request(submitted.form_id)
        body = response.json()

        assert response.status_code == 200, body
        assert body.get("success") is True, body
        data = body.get("data")
        assert isinstance(data, dict), body
        assert data.get("form_id") == submitted.form_id, body
        assert data.get("factor_combo_pool_id") == submitted.pool_id, body
        assert data.get("form_status") == "submitted", body
        assert isinstance(data.get("pool_snapshot_hash"), str) and data["pool_snapshot_hash"], body
        assert isinstance(data.get("form_json"), dict), body
        assert isinstance(data.get("data_spec"), dict), body
        assert isinstance(data.get("pool_members"), list) and len(data["pool_members"]) >= 2, body
        api_member_ids = [int(item["sub_factor_id"]) for item in data["pool_members"]]
        db_members = factor_combo_repository.get_pool_members(submitted.form_id)
        db_member_ids = [int(item["sub_factor_id"]) for item in db_members]
        assert api_member_ids == db_member_ids, {"api": body, "db": db_members}
        assert factor_combo_repository.count_versions_for_form(submitted.form_id) == versions_before, {
            "api": body,
            "before_count": versions_before,
            "after_count": factor_combo_repository.count_versions_for_form(submitted.form_id),
        }
        assert factor_combo_repository.count_feedback_for_form(submitted.form_id) == feedback_before, {
            "api": body,
            "before_count": feedback_before,
            "after_count": factor_combo_repository.count_feedback_for_form(submitted.form_id),
        }

    def test_get_missing_work_order_returns_not_found(
        self,
        factor_combo_service: FactorComboService,
    ) -> None:
        """查询不存在的表单工作单，并验证接口返回 404 错误信封。"""

        response = factor_combo_service.get_work_order_request(9_999_999_999)
        body = response.json()

        assert response.status_code == 404, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
