"""查询组合任务状态接口测试。"""

from __future__ import annotations

from typing import Any

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from tools.http_response import read_json


@pytest.mark.integration
@pytest.mark.external_agent
class TestFactorComboRunStatusAPI:
    """验证真实 Run 状态响应、进度字段、运行关联和查询只读性。"""

    def test_poll_run_status_returns_consistent_progress_without_mutation(
        self,
        factor_combo_completed_real_run_context: dict[str, Any],
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """轮询真实 Run 至完成，并验证每次状态响应关联一致且额外查询不改变业务指针。"""

        run = factor_combo_completed_real_run_context["run"]
        snapshots = factor_combo_completed_real_run_context["status_snapshots"]
        assert snapshots, "状态轮询至少应返回一个快照"
        for snapshot in snapshots:
            assert snapshot.get("form_id") == run.form.form_id, snapshot
            assert snapshot.get("pipeline_run_id") == run.pipeline_run_id, snapshot
            assert isinstance(snapshot.get("pipeline_status"), str) and snapshot["pipeline_status"], snapshot
            assert snapshot.get("recommended_action") in {"wait", "wait_result", "read_result", "retry_run"}, snapshot
            for field in ("total_step_count", "completed_step_count", "failed_step_count", "running_step_count"):
                assert isinstance(snapshot.get(field), int) and snapshot[field] >= 0, snapshot
        before_row = factor_combo_repository.get_form(run.form.form_id)

        response = factor_combo_api.get_run_status(run.form.form_id, run.pipeline_run_id)
        body = read_json(response)
        after_row = factor_combo_repository.get_form(run.form.form_id)

        assert response.status_code == 200, body
        assert body.get("success") is True, body
        assert body.get("data", {}).get("pipeline_status") == "completed", body
        assert body.get("data", {}).get("recommended_action") == "read_result", body
        assert before_row is not None and after_row is not None, {"api": body, "before": before_row, "after": after_row}
        for field in ("status", "factor_combo_id", "factor_combo_experiment_info_id", "pipeline_run_id"):
            assert before_row.get(field) == after_row.get(field), {
                "api": body,
                "before": before_row,
                "after": after_row,
            }

    def test_mismatched_run_id_is_rejected(
        self,
        factor_combo_real_run_context: dict[str, Any],
        factor_combo_api: FactorComboAPI,
    ) -> None:
        """使用不属于当前表单的运行 ID 查询状态，并验证接口拒绝关联错误。"""

        form = factor_combo_real_run_context["form"]

        response = factor_combo_api.get_run_status(form.form_id, "combo-999999999-0000000000000000")
        body = read_json(response)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body

    def test_unauthenticated_user_cannot_read_run_status(
        self,
        factor_combo_real_run_context: dict[str, Any],
        factor_combo_unauthenticated_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """不携带 JWT 查询真实 Run，并验证返回 401 且表单运行指针不变。"""

        run = factor_combo_real_run_context["run"]
        form_before = factor_combo_repository.get_form(run.form.form_id)

        response = factor_combo_unauthenticated_api.get_run_status(run.form.form_id, run.pipeline_run_id)
        body = read_json(response)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.get_form(run.form.form_id) == form_before, {
            "api": body,
            "before": form_before,
            "after": factor_combo_repository.get_form(run.form.form_id),
        }

    def test_authenticated_non_owner_cannot_read_run_status(
        self,
        factor_combo_real_run_context: dict[str, Any],
        factor_combo_non_owner_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用另一已登录账号查询不属于自己的真实 Run，并验证不返回运行数据且不改变表单。"""

        run = factor_combo_real_run_context["run"]
        form_before = factor_combo_repository.get_form(run.form.form_id)

        response = factor_combo_non_owner_api.get_run_status(run.form.form_id, run.pipeline_run_id)
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
