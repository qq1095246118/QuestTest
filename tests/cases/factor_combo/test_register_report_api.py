"""登记组合因子报告接口测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import CompletedExperiment, FactorComboService
from tools.http_response import read_json


@pytest.mark.integration
@pytest.mark.worker_contract
class TestRegisterFactorComboReportAPI:
    """验证组合报告登记契约、持久化完整性、最终决策互斥和幂等。"""

    def test_identical_registration_replay_returns_same_resources(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """连续提交完全相同的登记请求，并验证第二次返回原有四类资源而不重复创建。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)

        first_response = factor_combo_worker_service.register_report_request(payload)
        first_body = read_json(first_response)
        replay_response = factor_combo_worker_service.register_report_request(payload)
        replay_body = read_json(replay_response)

        assert first_response.status_code == 201, first_body
        assert replay_response.status_code == 200, replay_body
        assert first_body["data"]["idempotent_replay"] is False, first_body
        assert replay_body["data"]["idempotent_replay"] is True, replay_body
        assert isinstance(first_body["data"].get("refresh_task_id"), str), first_body
        assert first_body["data"]["refresh_task_id"].strip(), first_body
        assert replay_body["data"].get("refresh_task_id") == first_body["data"]["refresh_task_id"], {
            "first": first_body,
            "replay": replay_body,
        }
        assert isinstance(first_body["data"].get("refresh_status"), str), first_body
        assert first_body["data"]["refresh_status"].strip(), first_body
        assert isinstance(replay_body["data"].get("refresh_status"), str), replay_body
        assert replay_body["data"]["refresh_status"].strip(), replay_body
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
        registration = factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        )
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
        first_body = read_json(first_response)
        changed_payload = deepcopy(payload)
        if changed_section == "report":
            changed_payload["report"]["conclusion"] = "changed conclusion must not overwrite registration"
        else:
            changed_payload["factor_validity_status"]["overall_score"] = 21

        response = factor_combo_worker_service.register_report_request(changed_payload)
        body = read_json(response)
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
        body = read_json(response)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body
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
        body = read_json(response)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

    def test_unavailable_performance_metrics_can_be_registered_as_all_null(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交 metrics_status=unavailable 且全部指标为 null，并验证报告仍可登记。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment, metrics_available=False)

        response = factor_combo_worker_service.register_report_request(payload)
        body = read_json(response)

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(body["data"]["sub_factor_id"]))
        assert sub_factor is not None, {"api": body, "db": sub_factor}
        performance = sub_factor["metadata"]["report"]["performance"]
        assert performance["metrics_status"] == "unavailable", {"api": body, "db": sub_factor}
        for field in (
            "ts_ic",
            "return_rate",
            "out_of_sample_icir",
            "net_sharpe",
            "max_drawdown",
            "annual_turnover",
        ):
            assert performance[field] is None, {"field": field, "api": body, "db": sub_factor}
        assert all(
            field not in performance
            for field in (
                "annualized_return",
                "benchmark_sharpe",
                "calmar",
                "profit_loss_ratio",
                "positive_return_rate",
                "observations",
                "trade_observations",
                "decay_ratio",
                "cs_rank_ic",
                "cs_icir",
                "cs_score",
            )
        ), {"api": body, "db": sub_factor}
        assert performance["metric_mode"] == "time_series", {"api": body, "db": sub_factor}
        assert performance["universe_key"] == "main", {"api": body, "db": sub_factor}
        assert performance["symbols"] == ["BTCUSDT"], {
            "api": body,
            "db": sub_factor,
        }

    def test_cross_sectional_performance_is_registered_with_mode_specific_metrics(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交完整截面绩效，并验证截面指标、币池和币种列表原样写入报告 JSON。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(
            experiment,
            metric_mode="cross_sectional",
            validity_state="unknown",
        )

        response = factor_combo_worker_service.register_report_request(payload)
        body = read_json(response)

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(body["data"]["sub_factor_id"]))
        assert sub_factor is not None, {"api": body, "db": sub_factor}
        performance = sub_factor["metadata"]["report"]["performance"]
        assert performance == payload["report"]["performance"], {"api": body, "db": sub_factor}
        assert performance["metric_mode"] == "cross_sectional", {"api": body, "db": sub_factor}
        assert performance["ts_ic"] is None, {"api": body, "db": sub_factor}
        assert performance["cs_rank_ic"] == 0.08, {"api": body, "db": sub_factor}
        assert performance["cs_icir"] == 1.92, {"api": body, "db": sub_factor}
        assert performance["cs_score"] == 68.4, {"api": body, "db": sub_factor}
        assert performance["universe_key"] == "main", {"api": body, "db": sub_factor}
        assert performance["symbols"] == ["BTCUSDT", "ETHUSDT"], {"api": body, "db": sub_factor}

    @pytest.mark.parametrize("missing_field", ["cs_rank_ic", "cs_icir", "universe_key", "symbols"])
    def test_cross_sectional_performance_requires_mode_specific_fields(
        self,
        missing_field: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """逐一省略截面模式必填字段，并验证请求被拒绝且不创建登记记录。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(
            experiment,
            metric_mode="cross_sectional",
        )
        del payload["report"]["performance"][missing_field]

        response = factor_combo_worker_service.register_report_request(payload)
        body = read_json(response)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

    def test_time_series_performance_allows_cross_sectional_context_to_be_omitted(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """在时序模式省略截面指标及币池上下文，并验证登记成功且数据库未补造这些字段。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        performance = payload["report"]["performance"]
        for field in ("cs_rank_ic", "cs_icir", "cs_score", "universe_key", "symbols"):
            del performance[field]

        response = factor_combo_worker_service.register_report_request(payload)
        body = read_json(response)

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(body["data"]["sub_factor_id"]))
        assert sub_factor is not None, {"api": body, "db": sub_factor}
        stored_performance = sub_factor["metadata"]["report"]["performance"]
        assert stored_performance == performance, {"api": body, "db": sub_factor}
        assert all(
            field not in stored_performance
            for field in ("cs_rank_ic", "cs_icir", "cs_score", "universe_key", "symbols")
        ), {"api": body, "db": sub_factor}

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
        body = read_json(response)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

    def test_unknown_statuses_with_null_flags_can_be_registered(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交两个维度均 unknown/null 的快照，并验证登记接口按契约保存尚未判定状态。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment, validity_state="unknown")
        validity = payload["factor_validity_status"]
        for prefix in ("time_series", "cross_sectional", "overall"):
            validity[f"{prefix}_score"] = None
            validity[f"{prefix}_status"] = "unknown"
            validity[f"{prefix}_is_valid"] = None

        response = factor_combo_worker_service.register_report_request(payload)
        body = read_json(response)
        assert response.status_code == 201, body
        assert body.get("success") is True, body
        stored = factor_combo_repository.get_registered_validity_status(
            int(body["data"]["factor_validity_status_id"])
        )
        assert stored is not None, {"api": body, "db": stored}
        assert stored["time_series_status"] == "unknown" and stored["time_series_is_valid"] is None, stored
        assert stored["cross_sectional_status"] == "unknown" and stored["cross_sectional_is_valid"] is None, stored
        assert stored["overall_status"] == "unknown" and stored["overall_is_valid"] is None, stored

    def test_both_invalid_validity_dimensions_can_be_registered(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交时序和截面均失效的快照，并验证无效性快照可以随报告登记保存。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment, validity_state="both_invalid")

        response = factor_combo_worker_service.register_report_request(payload)
        body = read_json(response)
        assert response.status_code == 201, body
        assert body.get("success") is True, body
        stored = factor_combo_repository.get_registered_validity_status(
            int(body["data"]["factor_validity_status_id"])
        )
        assert stored is not None, {"api": body, "db": stored}
        assert stored["time_series_status"] == "invalid" and bool(stored["time_series_is_valid"]) is False, stored
        assert stored["cross_sectional_status"] == "invalid" and bool(stored["cross_sectional_is_valid"]) is False, stored

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
        body = read_json(response)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            version.combo_id,
            version_id=version.version_id,
            combo_version_hash=version.combo_version_hash,
        ) is None, body
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
        body = read_json(response)
        feedback = factor_combo_repository.get_feedback(pending.feedback_id)
        version = factor_combo_repository.get_combo_version(pending.experiment.version.version_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            pending.experiment.version.combo_id,
            version_id=pending.experiment.version.version_id,
            combo_version_hash=pending.experiment.version.combo_version_hash,
        ) is None, body
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
        wrong_session_body = read_json(wrong_session_response)
        wrong_run_payload = deepcopy(payload)
        wrong_run_payload["pipeline_run_id"] = "wrong-register-pipeline-run"

        wrong_run_response = factor_combo_worker_service.register_report_request(wrong_run_payload)
        wrong_run_body = read_json(wrong_run_response)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)

        assert wrong_session_response.status_code == 404, wrong_session_body
        assert wrong_session_body.get("success") is False, wrong_session_body
        assert wrong_run_response.status_code == 404, wrong_run_body
        assert wrong_run_body.get("success") is False, wrong_run_body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, {
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
        body = read_json(response)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body
        assert version is not None and version["status"] == "candidate", {"api": body, "db": version}

    def test_user_without_research_agent_permission_cannot_register_report(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_restricted_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用缺少 use_research_agent 的已登录账号登记合法报告，并验证权限拒绝发生在任何登记写入之前。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)

        response = factor_combo_restricted_api.register_report(payload)
        body = read_json(response)
        form = factor_combo_repository.get_form(experiment.version.worker_form.submitted.form_id)
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)

        assert response.status_code == 403, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body
        assert form is not None and form["status"] == "completed", {"api": body, "db": form}
        assert int(form["factor_combo_id"]) == experiment.version.version_id, {"api": body, "db": form}
        assert int(form["factor_combo_experiment_info_id"]) == experiment.experiment_info_id, {
            "api": body,
            "db": form,
        }
        assert version is not None and version["status"] == "candidate", {"api": body, "db": version}
