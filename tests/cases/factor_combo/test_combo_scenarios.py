"""组合因子台跨接口和业务场景测试。

确定性场景使用测试环境提供的兼容 Worker 认领接口准备合法前置状态，只验证真实后端的接口链路和数据库结果；
真实 Agent 场景单独标记，绝不把模拟结果当成真实计算结果。
"""

from __future__ import annotations

from typing import Any

import pytest

from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_models import FlowOutcome, RealResearchFlowResult
from service.factor_combo_service import FactorComboService
from tools.http_response import read_json


@pytest.mark.integration
@pytest.mark.worker_contract
class TestFactorComboWorkflowScenarios:
    """验证组合因子从表单、版本、实验到最终决策的跨接口行为。"""

    def test_direct_sub_factor_selection_keeps_pool_version_and_experiment_consistent(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """从两个真实子因子提交表单并完成一次 Worker 链路，核对所有业务指针只指向本次版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        work_order_response = factor_combo_worker_service.get_work_order_request(
            worker_form.submitted.form_id
        )
        work_order = factor_combo_worker_service.require_work_order(
            work_order_response,
            worker_form.submitted,
        )
        version = factor_combo_worker_service.create_worker_version(worker_form)
        experiment_payload = factor_combo_worker_service.build_experiment_payload(worker_form)
        experiment_response = factor_combo_worker_service.write_experiment_request(
            worker_form.experiment_id,
            experiment_payload,
        )
        experiment = factor_combo_worker_service.require_completed_experiment(
            experiment_response,
            version,
            worker_form.experiment_id,
            expected_valid=True,
        )
        register_payload = factor_combo_worker_service.build_register_payload(experiment)
        register_response = factor_combo_worker_service.register_report_request(register_payload)
        register_body = read_json(register_response)

        assert work_order["form_id"] == worker_form.submitted.form_id, work_order
        assert len(work_order["pool_members"]) >= 2, work_order
        assert experiment_response.status_code == 201, {
            "api": read_json(experiment_response),
            "request": experiment_payload,
        }
        assert register_response.status_code == 201, register_body
        assert register_body.get("success") is True, register_body
        data = register_body.get("data")
        assert isinstance(data, dict), register_body
        assert data.get("factor_combo_version_id") == version.version_id, register_body
        assert data.get("combo_id") == version.combo_id, register_body

        form = factor_combo_repository.get_form(worker_form.submitted.form_id)
        pool_members = factor_combo_repository.get_pool_members(worker_form.submitted.form_id)
        stored_version = factor_combo_repository.get_combo_version(version.version_id)
        components = factor_combo_repository.get_components(version.version_id)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)
        registration = factor_combo_repository.get_registration(
            version.combo_id,
            version_id=version.version_id,
            combo_version_hash=version.combo_version_hash,
        )
        assert all(
            value is not None
            for value in (form, stored_version, stored_experiment, registration)
        ), {
            "api": register_body,
            "form": form,
            "version": stored_version,
            "experiment": stored_experiment,
            "registration": registration,
        }
        member_ids = [int(member["sub_factor_id"]) for member in pool_members]
        component_ids = [int(component["component_sub_factor_id"]) for component in components]
        assert len(member_ids) == len(set(member_ids)), {"api": register_body, "members": pool_members}
        assert set(component_ids) == set(member_ids), {
            "api": register_body,
            "members": pool_members,
            "components": components,
        }
        assert int(form["factor_combo_id"]) == version.version_id, {"api": register_body, "db": form}
        assert int(form["factor_combo_experiment_info_id"]) == experiment.experiment_info_id, {
            "api": register_body,
            "db": form,
        }
        assert int(stored_version["experiment_id"]) == experiment.experiment_info_id, {
            "api": register_body,
            "db": stored_version,
        }
        assert int(stored_experiment["combo_id"]) == version.combo_id, {
            "api": register_body,
            "db": stored_experiment,
        }
        assert int(registration["sub_factor_id"]) == int(data["sub_factor_id"]), {
            "api": register_body,
            "db": registration,
        }

    def test_parent_factor_expansion_is_complete(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交单个母因子，核对因子池包含其全部关联子因子且没有重复。"""

        parent_form, parent = factor_combo_worker_service.create_form_with_parent()
        parent_members = factor_combo_repository.get_pool_members(parent_form.form_id)
        expected_parent_ids = {choice.sub_factor_id for choice in parent.sub_factors}
        actual_parent_ids = [int(member["sub_factor_id"]) for member in parent_members]
        assert set(actual_parent_ids) == expected_parent_ids, {
            "expected": expected_parent_ids,
            "actual": actual_parent_ids,
        }
        assert len(actual_parent_ids) == len(set(actual_parent_ids)), parent_members

    def test_mixed_parent_and_child_selection_is_rejected(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交母因子与子因子混选请求，核对接口拒绝且不会创建表单。"""

        response, session_id, parent = (
            factor_combo_worker_service.submit_mixed_parent_and_sub_factor_for_rejection()
        )
        body = read_json(response)

        assert response.status_code == 422, {"api": body, "parent": parent}
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == 0, {
            "api": body,
            "session_id": session_id,
        }

    def test_multiple_parent_factors_merge_unique_children(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """选择多个母因子并核对池成员是各母因子展开结果的并集且没有重复。"""

        submitted, parents = factor_combo_worker_service.create_form_with_multiple_parents()
        members = factor_combo_repository.get_pool_members(submitted.form_id)
        expected_ids = {
            choice.sub_factor_id
            for parent in parents
            for choice in parent.sub_factors
        }
        actual_ids = [int(member["sub_factor_id"]) for member in members]
        assert set(actual_ids) == expected_ids, {"expected": expected_ids, "actual": actual_ids}
        assert len(actual_ids) == len(set(actual_ids)), members

    def test_method_and_parameter_configuration_is_preserved_across_form_and_work_order(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交多方法、双寻优目标和非预设参数，核对表单配置原样保存且工作单派生字段完整。"""

        method_groups: dict[str, Any] = {"rule_methods": ["equal_weight", "ic_weight", "pca"]}
        objectives = [
            {"code": "ts-ic-pearson", "priority": 1},
            {"code": "sharpe", "priority": 2},
        ]
        configuration = {
            "rolling_window": "custom-window-32-character-1234",
            "correlation_penalty": 0.1234,
            "transaction_cost": 0,
            "optimize_subfactor_params": True,
        }
        submitted, _ = factor_combo_worker_service.create_form_with_sub_factors(
            method_groups=method_groups,
            objectives=objectives,
            configuration_overrides=configuration,
        )
        form = factor_combo_repository.get_form(submitted.form_id)
        assert form is not None, submitted
        form_json = form.get("form_json")
        assert isinstance(form_json, dict), form
        assert form_json["method_groups"] == method_groups, form
        stored_configuration = form_json["configuration_parameters"]
        assert stored_configuration["objectives"] == [
            {"code": "ts-ic-pearson", "priority": 1},
            {"code": "sharpe", "priority": 2},
        ], form
        assert stored_configuration["rolling_window"] == configuration["rolling_window"], form
        assert float(stored_configuration["correlation_penalty"]) == pytest.approx(0.1234), form
        assert stored_configuration["transaction_cost"] == 0, form
        assert stored_configuration["optimize_subfactor_params"] is True, form

        work_order_response = factor_combo_worker_service.get_work_order_request(submitted.form_id)
        work_order = factor_combo_worker_service.require_work_order(work_order_response, submitted)
        data_spec = work_order["data_spec"]
        assert data_spec["combo_bar_interval"], work_order
        assert data_spec["return_bar_interval"], work_order
        assert int(data_spec["forward_return_bars"]) > 0, work_order

    def test_same_input_submissions_create_independent_business_tasks(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用相同来源和配置主动提交两次，核对会话、表单、因子池和成员彼此独立。"""

        first, choices = factor_combo_worker_service.create_form_with_sub_factors()
        second, _ = factor_combo_worker_service.create_form_with_sub_factors()
        first_row = factor_combo_repository.get_form(first.form_id)
        second_row = factor_combo_repository.get_form(second.form_id)
        first_members = factor_combo_repository.get_pool_members(first.form_id)
        second_members = factor_combo_repository.get_pool_members(second.form_id)

        assert first.form_id != second.form_id, {"first": first, "second": second}
        assert first.session_id != second.session_id, {"first": first, "second": second}
        assert first.pool_id != second.pool_id, {"first": first, "second": second}
        assert first_row is not None and second_row is not None, {"first": first_row, "second": second_row}
        assert first_row["idempotency_key"] != second_row["idempotency_key"], {
            "first": first_row,
            "second": second_row,
        }
        assert [int(row["sub_factor_id"]) for row in first_members] == [
            choice.sub_factor_id for choice in choices
        ], first_members
        assert [int(row["sub_factor_id"]) for row in second_members] == [
            choice.sub_factor_id for choice in choices
        ], second_members

    def test_first_feedback_iteration_can_create_and_register_a_new_version(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """完成第一轮实验后提交反馈、创建下一版本并登记，核对来源版本被拒绝且业务组合身份继承。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        source_version = claimed.experiment.version
        next_response = factor_combo_worker_service.create_next_version_request(claimed)
        next_body = read_json(next_response)
        next_version = factor_combo_worker_service.require_next_version(next_response, claimed)
        experiment_payload = factor_combo_worker_service.build_experiment_payload(claimed.worker_form)
        experiment_response = factor_combo_worker_service.write_experiment_request(
            claimed.worker_form.experiment_id,
            experiment_payload,
        )
        next_experiment = factor_combo_worker_service.require_completed_experiment(
            experiment_response,
            next_version,
            claimed.worker_form.experiment_id,
            expected_valid=True,
        )
        register_response = factor_combo_worker_service.register_report_request(
            factor_combo_worker_service.build_register_payload(next_experiment)
        )
        register_body = read_json(register_response)

        source_row = factor_combo_repository.get_combo_version(source_version.version_id)
        next_row = factor_combo_repository.get_combo_version(next_version.version_id)
        feedback_row = factor_combo_repository.get_feedback(claimed.feedback_id)
        form_row = factor_combo_repository.get_form(claimed.worker_form.submitted.form_id)
        assert next_response.status_code == 201, next_body
        assert experiment_response.status_code == 201, read_json(experiment_response)
        assert register_response.status_code == 201, register_body
        assert source_row is not None and next_row is not None and feedback_row is not None and form_row is not None, {
            "source": source_row,
            "next": next_row,
            "feedback": feedback_row,
            "form": form_row,
        }
        assert int(source_row["combo_id"]) == int(next_row["combo_id"]) == source_version.combo_id, {
            "source": source_row,
            "next": next_row,
        }
        assert int(source_row["id"]) != int(next_row["id"]), {"source": source_row, "next": next_row}
        assert source_row["combo_version_hash"] != next_row["combo_version_hash"], {
            "source": source_row,
            "next": next_row,
        }
        assert source_row["status"] == "rejected", source_row
        assert next_row["status"] == "active", next_row
        assert feedback_row["status"] == "completed", feedback_row
        assert int(feedback_row["next_experiment_info_id"]) == next_experiment.experiment_info_id, feedback_row
        assert int(form_row["factor_combo_id"]) == next_version.version_id, form_row
        assert int(form_row["factor_combo_experiment_info_id"]) == next_experiment.experiment_info_id, form_row

    def test_two_feedback_rounds_preserve_each_version_and_history(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """连续两轮提交反馈后在第三轮登记，核对三代版本和两条反馈历史均不互相覆盖。"""

        first_claimed = factor_combo_worker_service.create_claimed_feedback("autotest first feedback")
        first_next_response = factor_combo_worker_service.create_next_version_request(first_claimed)
        first_next_version = factor_combo_worker_service.require_next_version(first_next_response, first_claimed)
        first_experiment_response = factor_combo_worker_service.write_experiment_request(
            first_claimed.worker_form.experiment_id,
            factor_combo_worker_service.build_experiment_payload(first_claimed.worker_form),
        )
        first_experiment = factor_combo_worker_service.require_completed_experiment(
            first_experiment_response,
            first_next_version,
            first_claimed.worker_form.experiment_id,
            expected_valid=True,
        )

        second_feedback_response = factor_combo_worker_service.submit_feedback_request(
            factor_combo_worker_service.build_feedback_payload(first_experiment, "autotest second feedback")
        )
        second_feedback_data = factor_combo_worker_service.require_feedback_response(
            second_feedback_response,
            expected_form_id=first_experiment.version.worker_form.submitted.form_id,
            expected_experiment_info_id=first_experiment.experiment_info_id,
            expected_status="pending",
        )
        second_feedback_id = int(second_feedback_data["feedback_id"])
        second_claimed = factor_combo_worker_service.claim_feedback_for_worker(first_experiment, second_feedback_id)
        second_next_response = factor_combo_worker_service.create_next_version_request(second_claimed)
        second_next_version = factor_combo_worker_service.require_next_version(second_next_response, second_claimed)
        second_experiment_response = factor_combo_worker_service.write_experiment_request(
            second_claimed.worker_form.experiment_id,
            factor_combo_worker_service.build_experiment_payload(second_claimed.worker_form),
        )
        second_experiment = factor_combo_worker_service.require_completed_experiment(
            second_experiment_response,
            second_next_version,
            second_claimed.worker_form.experiment_id,
            expected_valid=True,
        )
        register_response = factor_combo_worker_service.register_report_request(
            factor_combo_worker_service.build_register_payload(second_experiment)
        )
        register_body = read_json(register_response)

        first_source = factor_combo_repository.get_combo_version(first_claimed.experiment.version.version_id)
        first_next = factor_combo_repository.get_combo_version(first_next_version.version_id)
        second_next = factor_combo_repository.get_combo_version(second_next_version.version_id)
        first_feedback = factor_combo_repository.get_feedback(first_claimed.feedback_id)
        second_feedback = factor_combo_repository.get_feedback(second_feedback_id)
        assert register_response.status_code == 201, register_body
        assert all(value is not None for value in (first_source, first_next, second_next, first_feedback, second_feedback)), {
            "first_source": first_source,
            "first_next": first_next,
            "second_next": second_next,
            "first_feedback": first_feedback,
            "second_feedback": second_feedback,
        }
        assert first_source["status"] == "rejected", first_source
        assert first_next["status"] == "rejected", first_next
        assert second_next["status"] == "active", second_next
        assert first_feedback["status"] == "completed", first_feedback
        assert second_feedback["status"] == "completed", second_feedback
        assert int(first_feedback["next_factor_combo_version_id"]) == first_next_version.version_id, first_feedback
        assert int(second_feedback["next_factor_combo_version_id"]) == second_next_version.version_id, second_feedback
        assert int(first_source["combo_id"]) == int(first_next["combo_id"]) == int(second_next["combo_id"]), {
            "first_source": first_source,
            "first_next": first_next,
            "second_next": second_next,
        }
        assert factor_combo_repository.count_versions_for_form(
            first_claimed.experiment.version.worker_form.submitted.form_id
        ) == 3

    def test_invalid_experiment_is_retained_but_cannot_be_registered(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """写入计算失败实验，验证失败结果保留、登记被拒绝且不会伪造因子库资源。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        version = factor_combo_worker_service.create_worker_version(worker_form)
        experiment_payload = factor_combo_worker_service.build_experiment_payload(
            worker_form,
            valid=False,
            failure_reason="autotest calculation failure",
        )
        experiment_response = factor_combo_worker_service.write_experiment_request(
            worker_form.experiment_id,
            experiment_payload,
        )
        experiment = factor_combo_worker_service.require_completed_experiment(
            experiment_response,
            version,
            worker_form.experiment_id,
            expected_valid=False,
        )
        register_response = factor_combo_worker_service.register_report_request(
            factor_combo_worker_service.build_register_payload(experiment)
        )
        register_body = read_json(register_response)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)
        stored_version = factor_combo_repository.get_combo_version(version.version_id)

        assert experiment_response.status_code == 201, read_json(experiment_response)
        assert register_response.status_code == 409, register_body
        assert register_body.get("success") is False, register_body
        assert stored_experiment is not None and bool(stored_experiment["valid"]) is False, stored_experiment
        assert stored_experiment["failure_reason"] == "autotest calculation failure", stored_experiment
        assert stored_version is not None and stored_version["status"] == "candidate", stored_version
        assert factor_combo_repository.get_registration(
            version.combo_id,
            version_id=version.version_id,
            combo_version_hash=version.combo_version_hash,
        ) is None, register_body

    def test_registered_composite_sub_factor_can_be_used_in_a_new_form(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """登记一个复合子因子后，把其真实名称与已有子因子再次提交，核对新池引用登记结果。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        register_response = factor_combo_worker_service.register_report_request(
            factor_combo_worker_service.build_register_payload(experiment)
        )
        register_body = read_json(register_response)
        assert register_response.status_code == 201, register_body
        registration_data = register_body.get("data")
        assert isinstance(registration_data, dict), register_body
        composite_id = int(registration_data["sub_factor_id"])
        composite = factor_combo_repository.get_registered_sub_factor(composite_id)
        choices = factor_combo_repository.find_sub_factor_pair()
        assert composite is not None and choices is not None, {
            "api": register_body,
            "composite": composite,
            "choices": choices,
        }
        composite_name = str(composite["sub_factor_name"]).strip()
        source_choice = next(choice for choice in choices if choice.sub_factor_id != composite_id)
        submitted, _ = factor_combo_worker_service.create_form_for_factor_names(
            [composite_name, source_choice.sub_factor_name],
            is_sub_factor=1,
        )
        members = factor_combo_repository.get_pool_members(submitted.form_id)
        member_ids = {int(member["sub_factor_id"]) for member in members}

        assert composite_id in member_ids, {"composite": composite, "members": members}
        assert source_choice.sub_factor_id in member_ids, {"source": source_choice, "members": members}
        assert len(member_ids) == len(members), members


@pytest.mark.integration
@pytest.mark.external_agent
class TestFactorComboRealAgentScenarios:
    """验证真实 Agent 结果、反馈和登记链路不使用测试代码伪造数据。"""

    def test_real_parent_factor_flow_reaches_a_classified_terminal_outcome(
        self,
        factor_combo_service: FactorComboService,
        privileged_account: Any,
        settings: Any,
    ) -> None:
        """以母因子展开后的真实池启动 Agent，并要求最终结果只能是已登记或明确无效。"""

        if not settings.factor_combo.agent_base_url:
            pytest.skip("真实 Agent 场景需要配置 AUTOMATION_FACTOR_COMBO_AGENT_BASE_URL")
        form, parent = factor_combo_service.create_form_with_parent()
        work_order = factor_combo_service.require_work_order(
            factor_combo_service.get_work_order_request(form.form_id),
            form,
        )
        flow = factor_combo_service.run_real_research_flow(
            form,
            privileged_account.user_id,
        )

        assert len(work_order["pool_members"]) == len(parent.sub_factors), work_order
        assert isinstance(flow, RealResearchFlowResult), flow
        assert flow.outcome in {FlowOutcome.PASS_REGISTERED, FlowOutcome.PASS_INVALID}, flow
        assert flow.rounds, flow
        assert flow.last_pipeline_result is not None, flow
        assert flow.last_pipeline_result.raw_data.get("pipeline_run_id") == flow.rounds[-1]["pipeline_run_id"], flow
        if flow.outcome == FlowOutcome.PASS_REGISTERED:
            assert flow.registration is not None, flow
            assert flow.registration.refresh.status == "completed", flow.registration
            assert flow.registration.database_refresh.matched_run_ids, flow.registration.database_refresh
        else:
            assert flow.registration is None, flow

    def test_real_multi_method_flow_uses_actual_report_without_result_backfill(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
        privileged_account: Any,
        settings: Any,
    ) -> None:
        """提交多类研究方法配置并执行真实 Run，核对报告来自接口而非测试代码补造。"""

        if not settings.factor_combo.agent_base_url:
            pytest.skip("真实 Agent 场景需要配置 AUTOMATION_FACTOR_COMBO_AGENT_BASE_URL")
        method_groups = {"rule_methods": ["equal_weight", "ic_weight"]}
        form, _ = factor_combo_service.create_form_with_sub_factors(method_groups=method_groups)
        form_row = factor_combo_repository.get_form(form.form_id)
        assert form_row is not None, form
        assert form_row["form_json"]["method_groups"] == method_groups, form_row
        factor_combo_service.require_work_order(
            factor_combo_service.get_work_order_request(form.form_id),
            form,
        )
        flow = factor_combo_service.run_real_research_flow(
            form,
            privileged_account.user_id,
        )

        assert flow.rounds, flow
        assert flow.last_pipeline_result is not None, flow
        report = flow.last_pipeline_result.report
        combo = report.get("combo")
        assert isinstance(combo, dict), report
        algorithms = combo.get("algorithms")
        assert isinstance(algorithms, list) and algorithms, report
        assert flow.outcome in {FlowOutcome.PASS_REGISTERED, FlowOutcome.PASS_INVALID}, flow
