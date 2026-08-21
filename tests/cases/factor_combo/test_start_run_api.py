"""启动真实组合任务接口测试。"""

from __future__ import annotations

from typing import Any

import pytest

from api.factor_combo_api import FactorComboAPI
from service.factor_combo_service import FactorComboService


@pytest.mark.integration
@pytest.mark.external_agent
class TestStartFactorComboRunAPI:
    """验证真实 Run 首次启动、必填参数和当前轮幂等行为。"""

    def test_start_run_and_replay_return_same_pipeline_run(
        self,
        factor_combo_real_run_context: dict[str, Any],
    ) -> None:
        """首次启动真实 Run 后重放同一请求，并验证只生成同一个 Pipeline Run。"""

        work_order_response = factor_combo_real_run_context["work_order_response"]
        first_response = factor_combo_real_run_context["first_response"]
        replay_response = factor_combo_real_run_context["replay_response"]
        form = factor_combo_real_run_context["form"]
        work_order_body = work_order_response.json()
        first_body = first_response.json()
        replay_body = replay_response.json()

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
        assert replay_data.get("pipeline_run_id") == first_data["pipeline_run_id"], {
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
        body = response.json()

        assert response.status_code == 400, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
