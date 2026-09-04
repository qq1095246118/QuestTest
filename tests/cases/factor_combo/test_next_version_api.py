"""创建下一轮组合版本接口测试。"""

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
class TestCreateNextFactorComboVersionAPI:
    """验证 Feedback 下一版本的前置状态、继承规则、组件约束和幂等。"""

    def test_next_version_inherits_business_identity_and_links_feedback_and_form(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """为已认领反馈创建候选版本，并核对组合身份继承及 Feedback、表单指针。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        source = claimed.experiment.version

        request_payload = factor_combo_worker_service.build_next_version_payload(claimed)
        response = factor_combo_worker_service.create_next_version_request(claimed)
        body = read_json(response)

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        data = body.get("data")
        assert isinstance(data, dict), body
        assert data.get("form_id") == claimed.worker_form.submitted.form_id, body
        assert data.get("form_status") == "processing", body
        assert data.get("feedback_id") == claimed.feedback_id, body
        assert data.get("feedback_status") == "processing", body
        assert data.get("pipeline_run_id") == claimed.worker_form.pipeline_run_id, body
        assert data.get("combo_id") == source.combo_id, body
        assert data.get("combo_family_key") == source.combo_family_key, body
        assert data.get("pool_id") == source.pool_id, body
        assert data.get("combo_status") == "candidate", body
        assert data.get("component_count") == len(claimed.worker_form.components), body
        assert data.get("idempotent_replay") is False, body
        next_version_id = int(data["factor_combo_version_id"])
        assert next_version_id != source.version_id, body
        assert data.get("combo_version_hash") != source.combo_version_hash, body
        next_version = factor_combo_repository.get_combo_version(next_version_id)
        source_version = factor_combo_repository.get_combo_version(source.version_id)
        source_components = factor_combo_repository.get_components(source.version_id)
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)
        form = factor_combo_repository.get_form(claimed.worker_form.submitted.form_id)
        components = factor_combo_repository.get_components(next_version_id)
        assert all(item is not None for item in (next_version, source_version, feedback, form)), {
            "api": body,
            "next_version": next_version,
            "source_version": source_version,
            "feedback": feedback,
            "form": form,
        }
        assert source_components, {"api": body, "source_version": source_version}
        assert int(next_version["combo_id"]) == source.combo_id, {"api": body, "db": next_version}
        assert next_version["combo_family_key"] == source.combo_family_key, {"api": body, "db": next_version}
        assert int(next_version["pool_id"]) == source.pool_id, {"api": body, "db": next_version}
        assert next_version["status"] == "candidate", {"api": body, "db": next_version}
        assert source_version["status"] == "rejected", {"api": body, "db": source_version}
        assert feedback["status"] == "processing", {"api": body, "db": feedback}
        assert feedback["next_pipeline_run_id"] == claimed.worker_form.pipeline_run_id, {"api": body, "db": feedback}
        assert int(feedback["next_factor_combo_version_id"]) == next_version_id, {"api": body, "db": feedback}
        assert feedback["next_experiment_info_id"] is None, {"api": body, "db": feedback}
        assert form["status"] == "processing", {"api": body, "db": form}
        assert form["pipeline_run_id"] == claimed.worker_form.pipeline_run_id, {"api": body, "db": form}
        assert int(form["factor_combo_id"]) == next_version_id, {"api": body, "db": form}
        assert form["factor_combo_experiment_info_id"] is None, {"api": body, "db": form}
        assert len(components) == len(claimed.worker_form.components), {"api": body, "db": components}
        factor_combo_worker_service.validate_combo_version_persistence(
            data,
            request_payload,
            form,
            next_version,
            components,
            feedback_row=feedback,
            source_version_row=source_version,
            source_component_rows=source_components,
        )

    @pytest.mark.parametrize("generation_method", ["manual", "dark_random", "hybrid"])
    def test_supported_generation_methods_create_next_version(
        self,
        generation_method: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别使用 manual、dark_random 和 hybrid，并验证生成方式按请求持久化。"""

        claimed = factor_combo_worker_service.create_claimed_feedback(
            f"autotest next method {generation_method} {uuid4().hex}"
        )

        response = factor_combo_worker_service.create_next_version_request(
            claimed,
            generation_method=generation_method,
        )
        body = read_json(response)

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        version = factor_combo_repository.get_combo_version(int(body["data"]["factor_combo_version_id"]))
        assert version is not None and version["generation_method"] == generation_method, {
            "api": body,
            "db": version,
        }

    @pytest.mark.parametrize(
        "mutation",
        ["unsupported_method", "one_component", "duplicate_sub_factor", "outside_pool"],
    )
    def test_invalid_generation_or_components_do_not_create_next_version(
        self,
        mutation: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交非法生成方式、单组件、重复子因子或池外子因子，并验证没有部分版本。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        components = deepcopy(list(claimed.worker_form.components))
        generation_method = "ml"
        if mutation == "unsupported_method":
            generation_method = "xgboost"
        elif mutation == "one_component":
            components = components[:1]
        elif mutation == "duplicate_sub_factor":
            components[1]["component_sub_factor_id"] = components[0]["component_sub_factor_id"]
        if mutation == "outside_pool":
            outside = factor_combo_repository.find_sub_factor_outside_pool(
                claimed.worker_form.submitted.pool_id,
            )
            assert outside is not None, {
                "form_id": claimed.worker_form.submitted.form_id,
                "pool_id": claimed.worker_form.submitted.pool_id,
                "reason": "测试库没有可用于构造池外组件的真实子因子",
            }
            outside_sub_factor_id = outside.sub_factor_id
            components[0]["component_factor_id"] = outside.parent_factor_id
            components[0]["component_sub_factor_id"] = outside_sub_factor_id

        response = factor_combo_worker_service.create_next_version_request(
            claimed,
            components=components,
            generation_method=generation_method,
        )
        body = read_json(response)
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)
        form = factor_combo_repository.get_form(claimed.worker_form.submitted.form_id)
        restored_members = factor_combo_repository.get_pool_members(claimed.worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 1, body
        assert feedback is not None and feedback["next_factor_combo_version_id"] is None, {
            "api": body,
            "db": feedback,
        }
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}
        if mutation == "outside_pool":
            assert all(int(member["sub_factor_id"]) != outside_sub_factor_id for member in restored_members), {
                "api": body,
                "pool_members": restored_members,
            }

    def test_unclaimed_feedback_cannot_create_next_version(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """对 status=pending 且 claimed_at 为空的反馈调用下一版本接口，并验证前置状态拒绝。"""

        pending = factor_combo_worker_service.create_pending_feedback("autotest pending feedback")
        components = deepcopy(list(pending.experiment.version.worker_form.components))
        components[0]["transform"] = {"feedback_round": 1, "variant": "unclaimed"}
        payload = {
            "pipeline_run_id": f"unclaimed-feedback-{pending.feedback_id}",
            "generation_method": "manual",
            "components": components,
        }

        response = factor_combo_api.create_next_version(pending.feedback_id, payload)
        body = read_json(response)
        feedback = factor_combo_repository.get_feedback(pending.feedback_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(
            pending.experiment.version.worker_form.submitted.form_id
        ) == 1, body
        assert feedback is not None and feedback["status"] == "pending", {"api": body, "db": feedback}
        assert feedback["claimed_at"] is None, {"api": body, "db": feedback}
        assert feedback["next_factor_combo_version_id"] is None, {"api": body, "db": feedback}

    def test_pipeline_run_id_must_match_claimed_feedback(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用与认领记录不同的运行 ID，并验证不创建版本或改写反馈和表单指针。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()

        response = factor_combo_worker_service.create_next_version_request(
            claimed,
            pipeline_run_id=f"wrong-next-run-{uuid4().hex}",
        )
        body = read_json(response)
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)
        form = factor_combo_repository.get_form(claimed.worker_form.submitted.form_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 1, body
        assert feedback is not None and feedback["next_pipeline_run_id"] == claimed.worker_form.pipeline_run_id, {
            "api": body,
            "db": feedback,
        }
        assert feedback["next_factor_combo_version_id"] is None, {"api": body, "db": feedback}
        assert form is not None and form["pipeline_run_id"] is None, {"api": body, "db": form}
        assert form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_next_version_cannot_equal_rejected_source_version(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交与来源版本规范化后完全相同的生成方式和组件，并验证拒绝复用历史版本。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        source_components = deepcopy(list(claimed.experiment.version.worker_form.components))

        response = factor_combo_worker_service.create_next_version_request(
            claimed,
            components=source_components,
            generation_method="ml",
        )
        body = read_json(response)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 1, body
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)
        assert feedback is not None and feedback["next_factor_combo_version_id"] is None, {
            "api": body,
            "db": feedback,
        }

    def test_identical_next_version_replay_is_idempotent(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """重复提交完全相同的下一版本请求，并验证只创建一个目标版本和一组组件。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()

        first_response = factor_combo_worker_service.create_next_version_request(claimed)
        first_body = read_json(first_response)
        replay_response = factor_combo_worker_service.create_next_version_request(claimed)
        replay_body = read_json(replay_response)

        assert first_response.status_code == 201, first_body
        assert replay_response.status_code == 200, replay_body
        assert first_body["data"]["idempotent_replay"] is False, first_body
        assert replay_body["data"]["idempotent_replay"] is True, replay_body
        assert replay_body["data"]["factor_combo_version_id"] == first_body["data"]["factor_combo_version_id"], {
            "first": first_body,
            "replay": replay_body,
        }
        assert replay_body["data"]["combo_version_hash"] == first_body["data"]["combo_version_hash"], {
            "first": first_body,
            "replay": replay_body,
        }
        version_id = int(first_body["data"]["factor_combo_version_id"])
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 2, {
            "first": first_body,
            "replay": replay_body,
        }
        assert factor_combo_repository.count_components(version_id) == len(claimed.worker_form.components), {
            "first": first_body,
            "replay": replay_body,
        }

    def test_different_content_after_target_is_bound_conflicts(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """目标版本已绑定后修改组件内容再次提交，并验证不覆盖既有候选版本。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        first_response = factor_combo_worker_service.create_next_version_request(claimed)
        first_body = read_json(first_response)
        changed_components = deepcopy(list(claimed.worker_form.components))
        changed_components[0]["weight"] = 0.123456789

        response = factor_combo_worker_service.create_next_version_request(claimed, components=changed_components)
        body = read_json(response)
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)

        assert first_response.status_code == 201, first_body
        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 2, body
        assert feedback is not None and int(feedback["next_factor_combo_version_id"]) == int(
            first_body["data"]["factor_combo_version_id"]
        ), {"api": body, "db": feedback}

    def test_completed_feedback_replays_original_next_version_without_mutation(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """完成下一版本实验后重放原请求，并验证返回原版本且不覆盖实验和完成状态。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        first_response = factor_combo_worker_service.create_next_version_request(claimed)
        first_body = read_json(first_response)
        next_version = factor_combo_worker_service.require_next_version(first_response, claimed)
        experiment_payload = factor_combo_worker_service.build_experiment_payload(claimed.worker_form)
        experiment_response = factor_combo_worker_service.write_experiment_request(
            claimed.worker_form.experiment_id,
            experiment_payload,
        )
        experiment_body = read_json(experiment_response)

        replay_response = factor_combo_worker_service.create_next_version_request(claimed)
        replay_body = read_json(replay_response)
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)
        form = factor_combo_repository.get_form(claimed.worker_form.submitted.form_id)
        stored_version = factor_combo_repository.get_combo_version(next_version.version_id)

        assert first_response.status_code == 201, first_body
        assert experiment_response.status_code == 201, experiment_body
        assert replay_response.status_code == 200, replay_body
        assert replay_body["data"]["idempotent_replay"] is True, replay_body
        assert replay_body["data"]["feedback_status"] == "completed", replay_body
        assert replay_body["data"]["form_status"] == "completed", replay_body
        assert replay_body["data"]["factor_combo_version_id"] == next_version.version_id, replay_body
        assert feedback is not None and feedback["status"] == "completed", {"api": replay_body, "db": feedback}
        assert int(feedback["next_experiment_info_id"]) == int(experiment_body["data"]["experiment_info_id"]), {
            "api": replay_body,
            "db": feedback,
        }
        assert form is not None and form["status"] == "completed", {"api": replay_body, "db": form}
        assert stored_version is not None and int(stored_version["experiment_id"]) == int(
            experiment_body["data"]["experiment_info_id"]
        ), {"api": replay_body, "db": stored_version}
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 2, replay_body

    def test_unauthenticated_next_version_is_rejected_without_pointers(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_unauthenticated_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """不携带 Token 对已认领反馈创建下一版本，并验证返回 401 且不写任何目标指针。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        payload = {
            "pipeline_run_id": claimed.worker_form.pipeline_run_id,
            "generation_method": "ml",
            "components": list(claimed.worker_form.components),
        }

        response = factor_combo_unauthenticated_api.create_next_version(claimed.feedback_id, payload)
        body = read_json(response)
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 1, body
        assert feedback is not None and feedback["next_pipeline_run_id"] == claimed.worker_form.pipeline_run_id, {
            "api": body,
            "db": feedback,
        }
        assert feedback["next_factor_combo_version_id"] is None, {"api": body, "db": feedback}

    def test_authenticated_non_owner_cannot_create_next_version_for_another_users_feedback(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_non_owner_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用另一个已登录账号访问当前账号的已认领反馈，并验证所有权隔离且不更新任何目标指针。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        payload = factor_combo_worker_service.build_next_version_payload(claimed)

        response = factor_combo_non_owner_api.create_next_version(claimed.feedback_id, payload)
        body = read_json(response)
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)
        form = factor_combo_repository.get_form(claimed.worker_form.submitted.form_id)

        assert response.status_code == 404, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 1, body
        assert feedback is not None and feedback["next_factor_combo_version_id"] is None, {
            "api": body,
            "db": feedback,
        }
        assert form is not None and form["pipeline_run_id"] is None, {"api": body, "db": form}
        assert form["factor_combo_id"] is None, {"api": body, "db": form}
