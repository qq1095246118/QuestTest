"""登记组合因子报告接口测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import CompletedExperiment, FactorComboService


@pytest.mark.integration
@pytest.mark.worker_contract
class TestRegisterFactorComboReportAPI:
    """验证组合报告登记契约、持久化完整性、最终决策互斥和幂等。"""

    def test_register_report_creates_complete_factor_graph_and_activates_combo(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """登记完成实验报告，并核对子因子、详情、初始有效性、登记记录和组合状态。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        data = body.get("data")
        assert isinstance(data, dict), body
        assert data.get("registered") is True, body
        assert data.get("idempotent_replay") is False, body
        assert data.get("factor_combo_version_id") == experiment.version.version_id, body
        assert data.get("combo_id") == experiment.version.combo_id, body
        assert data.get("combo_version_hash") == experiment.version.combo_version_hash, body
        assert data.get("sub_factor_type") == 1, body
        assert isinstance(data.get("refresh_status"), str) and data["refresh_status"], body
        for field in ("sub_factor", "factor_detail", "factor_validity_status", "registration"):
            assert isinstance(data.get(field), dict), body
        sub_factor_id = int(data["sub_factor_id"])
        factor_detail_id = int(data["factor_detail_id"])
        validity_status_id = int(data["factor_validity_status_id"])
        registration_id = int(data["registration_id"])
        sub_factor = factor_combo_repository.get_registered_sub_factor(sub_factor_id)
        factor_detail = factor_combo_repository.get_registered_factor_detail(factor_detail_id)
        validity = factor_combo_repository.get_registered_validity_status(validity_status_id)
        registration = factor_combo_repository.get_registration(experiment.version.combo_id)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)
        components = factor_combo_repository.get_components(experiment.version.version_id)
        form = factor_combo_repository.get_form(experiment.version.worker_form.submitted.form_id)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)
        parent_relation_count = factor_combo_repository.count_parent_relations_for_sub_factor(sub_factor_id)
        assert all(
            item is not None
            for item in (sub_factor, factor_detail, validity, registration, version, form, stored_experiment)
        ), {
            "api": body,
            "sub_factor": sub_factor,
            "factor_detail": factor_detail,
            "validity": validity,
            "registration": registration,
            "version": version,
            "form": form,
            "experiment": stored_experiment,
        }
        assert int(sub_factor["id"]) == sub_factor_id, {"api": body, "db": sub_factor}
        assert sub_factor["sub_factor_name"] == payload["report"]["factor_name"], {"api": body, "db": sub_factor}
        assert int(sub_factor["type"]) == 1, {"api": body, "db": sub_factor}
        assert int(factor_detail["factor_id"]) == sub_factor_id, {"api": body, "db": factor_detail}
        assert bool(factor_detail["is_sub_factor_id"]) is True, {"api": body, "db": factor_detail}
        assert int(factor_detail["status"]) == 1, {"api": body, "db": factor_detail}
        assert int(validity["factor_id"]) == sub_factor_id, {"api": body, "db": validity}
        assert validity["time_series_status"] == "unknown", {"api": body, "db": validity}
        assert validity["time_series_is_valid"] is None, {"api": body, "db": validity}
        assert validity["cross_sectional_status"] == "unknown", {"api": body, "db": validity}
        assert validity["cross_sectional_is_valid"] is None, {"api": body, "db": validity}
        assert validity["overall_status"] == "unknown", {"api": body, "db": validity}
        assert validity["overall_is_valid"] is None, {"api": body, "db": validity}
        assert int(registration["id"]) == registration_id, {"api": body, "db": registration}
        assert int(registration["sub_factor_id"]) == sub_factor_id, {"api": body, "db": registration}
        assert registration["factor_id"] is None, {"api": body, "db": registration}
        assert registration["combo_version_hash"] == experiment.version.combo_version_hash, {
            "api": body,
            "db": registration,
        }
        assert factor_combo_repository.count_parent_relations_for_sub_factor(sub_factor_id) == 0, {
            "api": body,
            "db": registration,
        }
        assert version["status"] == "active", {"api": body, "db": version}
        assert int(data["sub_factor"]["id"]) == sub_factor_id, body
        assert int(data["factor_detail"]["id"]) == factor_detail_id, body
        assert int(data["factor_validity_status"]["id"]) == validity_status_id, body
        assert int(data["registration"]["id"]) == registration_id, body
        factor_combo_worker_service.validate_registration_persistence(
            data,
            payload,
            version,
            sub_factor,
            factor_detail,
            validity,
            registration,
            form_row=form,
            experiment_row=stored_experiment,
            parent_relation_count=parent_relation_count,
        )

    def test_identical_registration_replay_returns_same_resources(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """连续提交完全相同的登记请求，并验证第二次返回原有四类资源而不重复创建。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)

        first_response = factor_combo_worker_service.register_report_request(payload)
        first_body = first_response.json()
        replay_response = factor_combo_worker_service.register_report_request(payload)
        replay_body = replay_response.json()

        assert first_response.status_code == 201, first_body
        assert replay_response.status_code == 200, replay_body
        assert first_body["data"]["idempotent_replay"] is False, first_body
        assert replay_body["data"]["idempotent_replay"] is True, replay_body
        for field in (
            "sub_factor_id",
            "factor_detail_id",
            "factor_validity_status_id",
            "registration_id",
            "factor_combo_version_id",
        ):
            assert replay_body["data"][field] == first_body["data"][field], {
                "field": field,
                "first": first_body,
                "replay": replay_body,
            }
        registration = factor_combo_repository.get_registration(experiment.version.combo_id)
        assert registration is not None, {"first": first_body, "replay": replay_body}
        assert int(registration["id"]) == int(first_body["data"]["registration_id"]), registration

    @pytest.mark.parametrize("changed_section", ["report", "validity"])
    def test_registered_content_cannot_be_changed_by_replay(
        self,
        changed_section: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """登记后修改报告或有效性内容重放，并验证返回冲突且首次快照不被覆盖。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        first_response = factor_combo_worker_service.register_report_request(payload)
        first_body = first_response.json()
        changed_payload = deepcopy(payload)
        if changed_section == "report":
            changed_payload["report"]["conclusion"] = "changed conclusion must not overwrite registration"
        else:
            changed_payload["factor_validity_status"]["overall_score"] = 21

        response = factor_combo_worker_service.register_report_request(changed_payload)
        body = response.json()
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(first_body["data"]["sub_factor_id"]))
        validity = factor_combo_repository.get_registered_validity_status(
            int(first_body["data"]["factor_validity_status_id"])
        )

        assert first_response.status_code == 201, first_body
        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert sub_factor is not None and validity is not None, {"api": body, "sub_factor": sub_factor, "validity": validity}
        assert sub_factor["metadata"]["report"]["conclusion"] == payload["report"]["conclusion"], {
            "api": body,
            "db": sub_factor,
        }
        assert validity["overall_score"] == payload["factor_validity_status"]["overall_score"], {
            "api": body,
            "db": validity,
        }

    def test_invalid_experiment_cannot_be_registered(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """对 valid=false 的完成实验提交登记，并验证拒绝且组合仍为 candidate。"""

        experiment = factor_combo_worker_service.create_completed_experiment(
            valid=False,
            failure_reason="autotest invalid experiment",
        )
        payload = factor_combo_worker_service.build_register_payload(experiment)

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(experiment.version.combo_id) is None, body
        assert version is not None and version["status"] == "candidate", {"api": body, "db": version}

    @pytest.mark.parametrize(
        "mutation",
        ["negative_weight", "duplicate_sub_factor_code", "invalid_initial_validity", "unknown_report_field"],
    )
    def test_invalid_report_or_validity_contract_does_not_create_registration(
        self,
        mutation: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交负权重、重复成分、非法初始有效性或未知字段，并验证 422/400 前不落库。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        expected_status = 422
        if mutation == "negative_weight":
            payload["report"]["components"][0]["weight"] = -0.1
        elif mutation == "duplicate_sub_factor_code":
            payload["report"]["components"][1]["sub_factor_code"] = payload["report"]["components"][0][
                "sub_factor_code"
            ]
        elif mutation == "invalid_initial_validity":
            payload["factor_validity_status"]["time_series_status"] = "valid"
            payload["factor_validity_status"]["time_series_is_valid"] = False
        else:
            payload["report"]["extra"] = "unknown"
            expected_status = 400

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(experiment.version.combo_id) is None, body
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)
        assert version is not None and version["status"] == "candidate", {"api": body, "db": version}

    def test_existing_sub_factor_name_cannot_be_registered_again(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用数据库中已有子因子名称登记，并验证名称冲突不会复用或覆盖原实体。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        # 成功创建实验已经证明测试库存在可用子因子；名称准备失败应让用例失败，而不是把计划场景跳过。
        existing_name = factor_combo_repository.find_existing_sub_factor_name()
        assert existing_name is not None and existing_name.strip(), (
            "名称冲突场景的前置数据准备失败：实验已创建，但 sub_factors 没有可用名称"
        )
        payload = factor_combo_worker_service.build_register_payload(experiment)
        payload["report"]["factor_name"] = existing_name

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(experiment.version.combo_id) is None, body

    def test_unavailable_performance_metrics_can_be_registered_as_all_null(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交 metrics_status=unavailable 且全部指标为 null，并验证报告仍可登记。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment, metrics_available=False)

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(body["data"]["sub_factor_id"]))
        assert sub_factor is not None, {"api": body, "db": sub_factor}
        performance = sub_factor["metadata"]["report"]["performance"]
        assert performance["metrics_status"] == "unavailable", {"api": body, "db": sub_factor}
        assert all(value is None for key, value in performance.items() if key != "metrics_status"), {
            "api": body,
            "db": sub_factor,
        }

    def test_measured_performance_rejects_mixed_numeric_and_null_values(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """在 measured 绩效中混入 null，并验证契约拒绝不完整的实测指标。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        payload["report"]["performance"]["net_sharpe"] = None

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(experiment.version.combo_id) is None, body

    def test_period_start_after_period_end_is_rejected(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交开始时间晚于结束时间的有效性快照，并验证不创建登记资源。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        payload["factor_validity_status"]["period_start"] = "2026-08-02T00:00:00+08:00"
        payload["factor_validity_status"]["period_end"] = "2026-08-01T00:00:00+08:00"

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(experiment.version.combo_id) is None, body

    def test_timezone_aware_validity_period_can_be_registered(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交合法带时区评价区间，并验证后端接受并保存有序时间范围。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        payload["factor_validity_status"]["period_start"] = "2026-08-01T08:00:00+08:00"
        payload["factor_validity_status"]["period_end"] = "2026-08-02T08:00:00+08:00"

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        validity = factor_combo_repository.get_registered_validity_status(
            int(body["data"]["factor_validity_status_id"])
        )
        assert validity is not None, {"api": body, "db": validity}
        assert validity["period_start"] is not None and validity["period_end"] is not None, {
            "api": body,
            "db": validity,
        }
        assert validity["period_start"] < validity["period_end"], {"api": body, "db": validity}

    def test_unknown_statuses_with_null_flags_can_be_registered(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交 unknown 状态和 null 有效标志，并验证符合最新初始有效性契约。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        validity = payload["factor_validity_status"]
        for prefix in ("time_series", "cross_sectional", "overall"):
            validity[f"{prefix}_score"] = None
            validity[f"{prefix}_status"] = "unknown"
            validity[f"{prefix}_is_valid"] = None

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 201, body
        stored = factor_combo_repository.get_registered_validity_status(
            int(body["data"]["factor_validity_status_id"])
        )
        assert stored is not None, {"api": body, "db": stored}
        assert stored["time_series_status"] == "unknown" and stored["time_series_is_valid"] is None, stored
        assert stored["cross_sectional_status"] == "unknown" and stored["cross_sectional_is_valid"] is None, stored
        assert stored["overall_status"] == "unknown" and stored["overall_is_valid"] is None, stored

    def test_processing_form_without_experiment_cannot_be_registered(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """只创建候选版本但不写实验，并验证 processing 表单不能登记报告。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        version = factor_combo_worker_service.create_worker_version(worker_form)
        incomplete = CompletedExperiment(
            version=version,
            experiment_id=worker_form.experiment_id,
            experiment_info_id=0,
            form_status="processing",
            valid=False,
        )
        payload = factor_combo_worker_service.build_register_payload(incomplete)

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(version.combo_id) is None, body
        stored_version = factor_combo_repository.get_combo_version(version.version_id)
        assert stored_version is not None and stored_version["status"] == "candidate", {
            "api": body,
            "db": stored_version,
        }

    def test_pending_feedback_prevents_registration(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """先提交不满意反馈再登记同一来源报告，并验证两个最终决策互斥。"""

        pending = factor_combo_worker_service.create_pending_feedback("autotest feedback before registration")
        payload = factor_combo_worker_service.build_register_payload(pending.experiment)

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()
        feedback = factor_combo_repository.get_feedback(pending.feedback_id)
        version = factor_combo_repository.get_combo_version(pending.experiment.version.version_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(pending.experiment.version.combo_id) is None, body
        assert feedback is not None and feedback["status"] == "pending", {"api": body, "db": feedback}
        assert version is not None and version["status"] == "rejected", {"api": body, "db": version}

    def test_mismatched_session_or_pipeline_cannot_register_report(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别提交错误会话和错误运行 ID，并验证两次均无法登记且完成链路不变。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        wrong_session_payload = deepcopy(payload)
        wrong_session_payload["session_id"] = factor_combo_worker_service.create_session(
            "autotest-register-other-session"
        )
        wrong_session_response = factor_combo_worker_service.register_report_request(wrong_session_payload)
        wrong_session_body = wrong_session_response.json()
        wrong_run_payload = deepcopy(payload)
        wrong_run_payload["pipeline_run_id"] = "wrong-register-pipeline-run"

        wrong_run_response = factor_combo_worker_service.register_report_request(wrong_run_payload)
        wrong_run_body = wrong_run_response.json()
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)

        assert wrong_session_response.status_code == 404, wrong_session_body
        assert wrong_session_body.get("success") is False, wrong_session_body
        assert wrong_run_response.status_code == 404, wrong_run_body
        assert wrong_run_body.get("success") is False, wrong_run_body
        assert factor_combo_repository.get_registration(experiment.version.combo_id) is None, {
            "wrong_session": wrong_session_body,
            "wrong_run": wrong_run_body,
        }
        assert version is not None and version["status"] == "candidate", {
            "wrong_session": wrong_session_body,
            "wrong_run": wrong_run_body,
            "db": version,
        }

    def test_unauthenticated_registration_is_rejected_without_factor_creation(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_unauthenticated_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """不携带 Token 提交合法登记请求，并验证返回 401 且组合仍为 candidate。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)

        response = factor_combo_unauthenticated_api.register_report(payload)
        body = response.json()
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(experiment.version.combo_id) is None, body
        assert version is not None and version["status"] == "candidate", {"api": body, "db": version}
