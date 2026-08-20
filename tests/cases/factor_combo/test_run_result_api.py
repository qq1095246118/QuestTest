"""获取组合任务结果接口测试。"""

from __future__ import annotations

from typing import Any

import pytest

from api.factor_combo_api import FactorComboAPI


@pytest.mark.integration
@pytest.mark.external_agent
class TestFactorComboRunResultAPI:
    """验证已完成真实 Run 的结构化报告和运行关联。"""

    def test_completed_run_returns_structured_result(
        self,
        factor_combo_completed_real_run_context: dict[str, Any],
        factor_combo_api: FactorComboAPI,
    ) -> None:
        """读取已完成真实 Run，并验证报告、评审和有效性快照同时返回。"""

        run = factor_combo_completed_real_run_context["run"]

        response = factor_combo_api.get_run_result(run.form.form_id, run.pipeline_run_id)
        body = response.json()

        assert response.status_code == 200, body
        assert body.get("success") is True, body
        data = body.get("data")
        assert isinstance(data, dict), body
        assert data.get("form_id") == run.form.form_id, body
        assert data.get("pipeline_run_id") == run.pipeline_run_id, body
        assert data.get("pipeline_status") == "completed", body
        result = data.get("result")
        assert isinstance(result, dict), body
        assert isinstance(result.get("factor_combo_report"), dict), body
        assert isinstance(result.get("factor_combo_review"), dict), body
        assert isinstance(result.get("factor_validity_status"), dict), body

    def test_mismatched_run_id_cannot_read_result(
        self,
        factor_combo_real_run_context: dict[str, Any],
        factor_combo_api: FactorComboAPI,
    ) -> None:
        """使用不属于当前表单的运行 ID 获取结果，并验证接口返回关联校验错误。"""

        form = factor_combo_real_run_context["form"]

        response = factor_combo_api.get_run_result(form.form_id, "combo-999999999-0000000000000000")
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
