"""获取组合任务结果接口测试。"""

from __future__ import annotations

from typing import Any

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from tools.http_response import read_json


@pytest.mark.integration
@pytest.mark.external_agent
class TestFactorComboRunResultAPI:
    """验证已完成真实 Run 的结构化报告和运行关联。"""

    def test_completed_run_returns_structured_result(
        self,
        factor_combo_completed_real_run_context: dict[str, Any],
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """读取已完成真实 Run，并验证报告、评审和有效性快照同时返回。"""

        run = factor_combo_completed_real_run_context["run"]

        form_before = factor_combo_repository.get_form(run.form.form_id)
        response = factor_combo_api.get_run_result(run.form.form_id, run.pipeline_run_id)
        body = read_json(response)
        form_after = factor_combo_repository.get_form(run.form.form_id)

        assert response.status_code == 200, body
        assert body.get("success") is True, body
        data = body.get("data")
        assert isinstance(data, dict), body
        assert data.get("form_id") == run.form.form_id, body
        assert data.get("pipeline_run_id") == run.pipeline_run_id, body
        assert data.get("pipeline_status") == "completed", body
        result = data.get("result")
        assert isinstance(result, dict), body
        report = result.get("factor_combo_report")
        review = result.get("factor_combo_review")
        validity = result.get("factor_validity_status")
        assert isinstance(report, dict), body
        assert isinstance(review, dict), body
        assert isinstance(validity, dict), body
        assert isinstance(report.get("factor_name"), str) and report["factor_name"].strip(), body
        assert isinstance(report.get("performance"), dict), body
        assert isinstance(review.get("experiment_valid"), bool), body
        assert isinstance(review.get("registration_ready"), bool), body
        assert not review["registration_ready"] or review["experiment_valid"] is True, body
        for field_name in ("time_series_is_valid", "cross_sectional_is_valid"):
            assert field_name in validity, body
            assert validity[field_name] is None or isinstance(validity[field_name], bool), body
        assert form_before is not None and form_after is not None, {
            "api": body,
            "before": form_before,
            "after": form_after,
        }
        for field_name in ("status", "factor_combo_id", "factor_combo_experiment_info_id", "pipeline_run_id"):
            assert form_before.get(field_name) == form_after.get(field_name), {
                "api": body,
                "before": form_before,
                "after": form_after,
            }

    def test_mismatched_run_id_cannot_read_result(
        self,
        factor_combo_real_run_context: dict[str, Any],
        factor_combo_api: FactorComboAPI,
    ) -> None:
        """使用不属于当前表单的运行 ID 获取结果，并验证接口返回关联校验错误。"""

        form = factor_combo_real_run_context["form"]

        response = factor_combo_api.get_run_result(form.form_id, "combo-999999999-0000000000000000")
        body = read_json(response)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body

    def test_unauthenticated_user_cannot_read_run_result(
        self,
        factor_combo_real_run_context: dict[str, Any],
        factor_combo_unauthenticated_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """不携带 JWT 读取真实 Run 结果，并验证返回 401 且表单关联保持不变。"""

        run = factor_combo_real_run_context["run"]
        form_before = factor_combo_repository.get_form(run.form.form_id)

        response = factor_combo_unauthenticated_api.get_run_result(run.form.form_id, run.pipeline_run_id)
        body = read_json(response)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.get_form(run.form.form_id) == form_before, {
            "api": body,
            "before": form_before,
            "after": factor_combo_repository.get_form(run.form.form_id),
        }

    def test_authenticated_non_owner_cannot_read_run_result(
        self,
        factor_combo_real_run_context: dict[str, Any],
        factor_combo_non_owner_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用另一已登录账号读取不属于自己的真实 Run 结果，并验证不泄露结果且不改变表单。"""

        run = factor_combo_real_run_context["run"]
        form_before = factor_combo_repository.get_form(run.form.form_id)

        response = factor_combo_non_owner_api.get_run_result(run.form.form_id, run.pipeline_run_id)
        body = read_json(response)

        assert response.status_code == 404, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert body.get("data") in (None, {}), body
        assert factor_combo_repository.get_form(run.form.form_id) == form_before, {
            "api": body,
            "before": form_before,
            "after": factor_combo_repository.get_form(run.form.form_id),
        }
