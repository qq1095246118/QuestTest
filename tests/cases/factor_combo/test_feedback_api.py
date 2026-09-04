"""提交组合报告反馈接口测试。"""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService
from tools.http_response import read_json


@pytest.mark.integration
@pytest.mark.worker_contract
class TestSubmitFactorComboReportFeedbackAPI:
    """验证不满意反馈的字段契约、状态流转、互斥和幂等行为。"""

    def test_feedback_rejects_combo_and_resets_form_for_next_round(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """对有效完成实验提交 reply=2，并核对组合、实验、表单和反馈历史的完整流转。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        feedback_text = f"autotest reduce drawdown {uuid4().hex}"
        payload = factor_combo_worker_service.build_feedback_payload(experiment, feedback_text)

        response = factor_combo_worker_service.submit_feedback_request(payload)
        body = read_json(response)

        assert response.status_code == 200, body
        assert body.get("success") is True, body
        data = body.get("data")
        assert isinstance(data, dict), body
        assert data.get("feedback_recorded") is True, body
        assert data.get("idempotent_replay") is False, body
        assert data.get("feedback_status") == "pending", body
        assert data.get("reply") == 2, body
        assert data.get("form_id") == experiment.version.worker_form.submitted.form_id, body
        assert data.get("form_status") == "processing", body
        assert data.get("rejected_factor_combo_version_id") == experiment.version.version_id, body
        assert data.get("factor_combo_experiment_info_id") == experiment.experiment_info_id, body
        assert data.get("experiment_valid") is False, body
        feedback_id = int(data["feedback_id"])
        feedback = factor_combo_repository.get_feedback(feedback_id)
        form = factor_combo_repository.get_form(experiment.version.worker_form.submitted.form_id)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)
        assert feedback is not None and form is not None and version is not None and stored_experiment is not None, {
            "api": body,
            "feedback": feedback,
            "form": form,
            "version": version,
            "experiment": stored_experiment,
        }
        assert feedback["status"] == "pending", {"api": body, "db": feedback}
        assert int(feedback["form_id"]) == experiment.version.worker_form.submitted.form_id, {
            "api": body,
            "db": feedback,
        }
        assert int(feedback["source_factor_combo_version_id"]) == experiment.version.version_id, {
            "api": body,
            "db": feedback,
        }
        assert version["status"] == "rejected", {"api": body, "db": version}
        assert bool(stored_experiment["valid"]) is False, {"api": body, "db": stored_experiment}
        assert stored_experiment["failure_reason"] == feedback_text, {"api": body, "db": stored_experiment}
        assert form["status"] == "processing", {"api": body, "db": form}
        assert form["pipeline_run_id"] is None, {"api": body, "db": form}
        assert form["factor_combo_id"] is None, {"api": body, "db": form}
        assert form["factor_combo_experiment_info_id"] is None, {"api": body, "db": form}
        factor_combo_worker_service.validate_feedback_persistence(
            data,
            payload,
            feedback,
            form,
            stored_experiment,
            version,
        )

    def test_identical_feedback_replay_returns_same_history_record(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """重复提交相同来源和相同正文，并验证只保留一条反馈且第二次标记幂等。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_feedback_payload(
            experiment,
            f"autotest idempotent feedback {uuid4().hex}",
        )

        first_response = factor_combo_worker_service.submit_feedback_request(payload)
        first_body = read_json(first_response)
        replay_response = factor_combo_worker_service.submit_feedback_request(payload)
        replay_body = read_json(replay_response)

        assert first_response.status_code == 200, first_body
        assert replay_response.status_code == 200, replay_body
        assert first_body["data"]["idempotent_replay"] is False, first_body
        assert replay_body["data"]["idempotent_replay"] is True, replay_body
        assert replay_body["data"]["feedback_id"] == first_body["data"]["feedback_id"], {
            "first": first_body,
            "replay": replay_body,
        }
        assert replay_body["data"]["feedback_round"] == first_body["data"]["feedback_round"], {
            "first": first_body,
            "replay": replay_body,
        }
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 1, {"first": first_body, "replay": replay_body}

    def test_different_feedback_for_same_experiment_conflicts_without_overwrite(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """同一来源实验第二次提交不同正文，并验证冲突且首次失败原因保持不变。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        first_text = f"autotest first feedback {uuid4().hex}"
        first_payload = factor_combo_worker_service.build_feedback_payload(experiment, first_text)
        first_response = factor_combo_worker_service.submit_feedback_request(first_payload)
        first_body = read_json(first_response)
        second_payload = deepcopy(first_payload)
        second_payload["feedback"] = f"autotest different feedback {uuid4().hex}"

        response = factor_combo_worker_service.submit_feedback_request(second_payload)
        body = read_json(response)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)

        assert first_response.status_code == 200, first_body
        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 1, body
        assert stored_experiment is not None and stored_experiment["failure_reason"] == first_text, {
            "api": body,
            "db": stored_experiment,
        }

    def test_authenticated_non_owner_cannot_submit_feedback_for_another_users_form(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_non_owner_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用另一个已登录账号提交当前账号的合法反馈，并验证所有权隔离且完成链路保持不变。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_feedback_payload(
            experiment,
            f"autotest cross-user feedback {uuid4().hex}",
        )

        response = factor_combo_non_owner_api.submit_feedback(payload)
        body = read_json(response)
        form = factor_combo_repository.get_form(experiment.version.worker_form.submitted.form_id)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)

        assert response.status_code == 404, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 0, body
        assert form is not None and form["status"] == "completed", {"api": body, "db": form}
        assert version is not None and version["status"] == "candidate", {"api": body, "db": version}
        assert stored_experiment is not None and bool(stored_experiment["valid"]) is True, {
            "api": body,
            "db": stored_experiment,
        }

    def test_feedback_accepts_exactly_16000_characters(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交正好 16000 个字符的反馈正文，并验证新版长度上界可成功持久化。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_feedback_payload(experiment, "x" * 16000)

        response = factor_combo_worker_service.submit_feedback_request(payload)
        body = read_json(response)

        assert response.status_code == 200, body
        assert body.get("success") is True, body
        assert body["data"]["feedback_status"] == "pending", body
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 1, body

    def test_feedback_over_16000_characters_is_rejected_without_state_change(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交超过 16000 个字符的反馈正文，并验证接口拒绝且原组合状态不变。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_feedback_payload(experiment, "x" * 16001)

        response = factor_combo_worker_service.submit_feedback_request(payload)
        body = read_json(response)
        form = factor_combo_repository.get_form(experiment.version.worker_form.submitted.form_id)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 0, body
        assert form is not None and form["status"] == "completed", {"api": body, "db": form}
        assert version is not None and version["status"] == "candidate", {"api": body, "db": version}
        assert stored_experiment is not None and bool(stored_experiment["valid"]) is True, {
            "api": body,
            "db": stored_experiment,
        }

    def test_form_from_another_session_cannot_receive_feedback(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """将完成表单与另一个有效会话组合提交，并验证关联查询失败且原链路不变。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        other_session_id = factor_combo_worker_service.create_session("autotest-factor-combo-other-session")
        payload = factor_combo_worker_service.build_feedback_payload(experiment, "autotest wrong session")
        payload["session_id"] = other_session_id

        response = factor_combo_worker_service.submit_feedback_request(payload)
        body = read_json(response)
        form = factor_combo_repository.get_form(experiment.version.worker_form.submitted.form_id)

        assert response.status_code == 404, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 0, body
        assert form is not None and form["status"] == "completed", {"api": body, "db": form}

    def test_mismatched_pipeline_run_id_cannot_receive_feedback(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交与完成表单不匹配的运行 ID，并验证不新增反馈或修改当前指针。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_feedback_payload(experiment, "autotest wrong run")
        payload["pipeline_run_id"] = f"wrong-run-{uuid4().hex}"

        response = factor_combo_worker_service.submit_feedback_request(payload)
        body = read_json(response)
        form = factor_combo_repository.get_form(experiment.version.worker_form.submitted.form_id)

        assert response.status_code == 404, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 0, body
        assert form is not None and form["pipeline_run_id"] == experiment.version.worker_form.pipeline_run_id, {
            "api": body,
            "db": form,
        }

    def test_processing_form_without_experiment_cannot_receive_feedback(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """仅创建候选版本但不写入实验，并验证 processing 表单无法提交报告反馈。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        version = factor_combo_worker_service.create_worker_version(worker_form)
        payload = {
            "session_id": worker_form.submitted.session_id,
            "form_id": worker_form.submitted.form_id,
            "pipeline_run_id": worker_form.pipeline_run_id,
            "reply": 2,
            "feedback": "autotest report is not completed",
        }

        response = factor_combo_worker_service.submit_feedback_request(payload)
        body = read_json(response)
        stored_version = factor_combo_repository.get_combo_version(version.version_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_feedback_for_form(worker_form.submitted.form_id) == 0, body
        assert stored_version is not None and stored_version["status"] == "candidate", {
            "api": body,
            "db": stored_version,
        }

    def test_failed_experiment_can_receive_feedback_for_next_round(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """对计算失败的完成实验提交反馈，并验证失败结果仍可进入下一轮组合流程。"""

        experiment = factor_combo_worker_service.create_completed_experiment(
            valid=False,
            failure_reason="autotest source calculation failed",
        )
        payload = factor_combo_worker_service.build_feedback_payload(
            experiment,
            f"autotest feedback for failed experiment {uuid4().hex}",
        )

        response = factor_combo_worker_service.submit_feedback_request(payload)
        body = read_json(response)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)

        assert response.status_code == 200, body
        assert body.get("success") is True, body
        data = factor_combo_worker_service.require_feedback_response(
            response,
            expected_form_id=experiment.version.worker_form.submitted.form_id,
            expected_experiment_info_id=experiment.experiment_info_id,
            expected_status="pending",
        )
        assert data.get("experiment_valid") is False, body
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 1, body
        assert version is not None and version["status"] == "rejected", {"api": body, "db": version}
        assert stored_experiment is not None and bool(stored_experiment["valid"]) is False, {
            "api": body,
            "db": stored_experiment,
        }
        assert isinstance(stored_experiment.get("failure_reason"), str)
        assert stored_experiment["failure_reason"].strip(), {"api": body, "db": stored_experiment}

    def test_registered_report_cannot_receive_feedback(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """先登记独立组合报告再提交不满意反馈，并验证登记与反馈最终决策互斥。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        register_payload = factor_combo_worker_service.build_register_payload(experiment)
        register_response = factor_combo_worker_service.register_report_request(register_payload)
        register_body = read_json(register_response)
        feedback_payload = factor_combo_worker_service.build_feedback_payload(
            experiment,
            f"autotest feedback after registration {uuid4().hex}",
        )

        response = factor_combo_worker_service.submit_feedback_request(feedback_payload)
        body = read_json(response)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)
        registration = factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        )

        assert register_response.status_code == 201, register_body
        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 0, body
        assert version is not None and version["status"] == "active", {"api": body, "db": version}
        assert registration is not None, {"api": body, "db": registration}

    def test_unauthenticated_feedback_is_rejected_without_state_change(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_unauthenticated_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """不携带 Token 提交合法反馈，并验证返回 401 且完成链路保持原状。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_feedback_payload(experiment, "autotest unauthenticated feedback")

        response = factor_combo_unauthenticated_api.submit_feedback(payload)
        body = read_json(response)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_feedback_for_form(
            experiment.version.worker_form.submitted.form_id
        ) == 0, body
        assert version is not None and version["status"] == "candidate", {"api": body, "db": version}
        assert stored_experiment is not None and bool(stored_experiment["valid"]) is True, {
            "api": body,
            "db": stored_experiment,
        }
