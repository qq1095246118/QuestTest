"""启动真实组合任务接口测试。"""

from __future__ import annotations

from typing import Any

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService
from tools.http_response import read_json


@pytest.mark.integration
@pytest.mark.external_agent
class TestStartFactorComboRunAPI:
    """验证真实 Run 首次启动、必填参数和当前轮幂等行为。"""

    def test_start_run_and_replay_return_same_pipeline_run(
        self,
        factor_combo_real_run_context: dict[str, Any],
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """首次启动真实 Run 后重放同一请求，并验证只生成同一个 Pipeline Run。"""

        work_order_response = factor_combo_real_run_context["work_order_response"]
        first_response = factor_combo_real_run_context["first_response"]
        replay_response = factor_combo_real_run_context["replay_response"]
        form = factor_combo_real_run_context["form"]
        work_order_body = read_json(work_order_response)
        first_body = read_json(first_response)
        replay_body = read_json(replay_response)

        assert work_order_response.status_code == 200, work_order_body
        assert first_response.status_code == 202, first_body
        assert first_body.get("success") is True, first_body
        first_data = first_body.get("data")
        assert isinstance(first_data, dict), first_body
        assert first_data.get("form_id") == form.form_id, first_body
        assert first_data.get("idempotent_replay") is False, first_body
        assert isinstance(first_data.get("pipeline_run_id"), str) and first_data["pipeline_run_id"], first_body
        assert replay_response.status_code == 200, replay_body
        assert replay_body.get("success") is True, replay_body
        replay_data = replay_body.get("data")
        assert isinstance(replay_data, dict), replay_body
        assert replay_data.get("idempotent_replay") is True, replay_body
        assert replay_data.get("form_id") == form.form_id, replay_body
        assert replay_data.get("pipeline_run_id") == first_data["pipeline_run_id"], {
            "first_response": first_body,
            "replay_response": replay_body,
        }
        database_form = factor_combo_repository.get_form(form.form_id)
        assert database_form is not None, {"first_response": first_body, "db": database_form}
        assert database_form.get("pipeline_run_id") == first_data["pipeline_run_id"], {
            "first_response": first_body,
            "replay_response": replay_body,
            "db": database_form,
        }
        if "agent_session_id" in replay_data and "agent_session_id" in first_data:
            assert str(replay_data.get("agent_session_id")) == str(first_data.get("agent_session_id")), {
                "first_response": first_body,
                "replay_response": replay_body,
            }

    def test_missing_agent_uid_is_rejected(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
    ) -> None:
        """提交缺少 agent_uid 的启动请求，并验证接口不接受运行任务。"""

        submitted, _ = factor_combo_service.create_form_with_sub_factors()

        response = factor_combo_api.start_run(
            submitted.form_id,
            {"force_fresh_pipeline_run": False},
        )
        body = read_json(response)

        assert response.status_code == 400, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body

    def test_unauthenticated_user_cannot_start_run(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_unauthenticated_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """不携带 JWT 启动已提交表单，并验证返回 401 且不写入 Pipeline 指针。"""

        submitted, _ = factor_combo_service.create_form_with_sub_factors()
        form_before = factor_combo_repository.get_form(submitted.form_id)

        response = factor_combo_unauthenticated_api.start_run(
            submitted.form_id,
            {"agent_uid": "authentication-boundary", "force_fresh_pipeline_run": False},
        )
        body = read_json(response)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.get_form(submitted.form_id) == form_before, {
            "api": body,
            "before": form_before,
            "after": factor_combo_repository.get_form(submitted.form_id),
        }

    def test_authenticated_non_owner_cannot_start_run(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_non_owner_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用另一已登录账号启动不属于自己的表单，并验证请求被拒绝且不建立运行关联。"""

        submitted, _ = factor_combo_service.create_form_with_sub_factors()
        form_before = factor_combo_repository.get_form(submitted.form_id)

        response = factor_combo_non_owner_api.start_run(
            submitted.form_id,
            {"agent_uid": "ownership-boundary", "force_fresh_pipeline_run": False},
        )
        body = read_json(response)

        assert response.status_code == 404, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.get_form(submitted.form_id) == form_before, {
            "api": body,
            "before": form_before,
            "after": factor_combo_repository.get_form(submitted.form_id),
        }
