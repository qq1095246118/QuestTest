"""创建下一轮组合版本接口测试。"""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService


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

        response = factor_combo_worker_service.create_next_version_request(claimed)
        body = response.json()

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
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        version = factor_combo_repository.get_combo_version(int(body["data"]["factor_combo_version_id"]))
        assert version is not None and version["generation_method"] == generation_method, {
            "api": body,
            "db": version,
        }

    def test_unrelated_parent_and_pool_sub_factor_pair_is_allowed(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用存在但无父子关系的母因子和池内子因子，并验证下一版本不强制关系表配对。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        components = deepcopy(list(claimed.worker_form.components))
        first_sub_factor_id = int(components[0]["component_sub_factor_id"])
        current_parent_id = int(components[0]["component_factor_id"])
        unrelated_parent_id = factor_combo_repository.find_unrelated_parent_factor(
            first_sub_factor_id,
            current_parent_id,
        )
        if unrelated_parent_id is None:
            pytest.skip("测试数据库没有可用于父子不关联场景的其他母因子")
        components[0]["component_factor_id"] = unrelated_parent_id

        response = factor_combo_worker_service.create_next_version_request(claimed, components=components)
        body = response.json()

        assert response.status_code == 201, body
        version_id = int(body["data"]["factor_combo_version_id"])
        stored_components = factor_combo_repository.get_components(version_id)
        stored = next(
            item for item in stored_components if int(item["component_sub_factor_id"]) == first_sub_factor_id
        )
        assert int(stored["component_factor_id"]) == unrelated_parent_id, {"api": body, "db": stored_components}

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
        else:
            outside = factor_combo_repository.find_sub_factor_outside_pool(claimed.worker_form.submitted.pool_id)
            if outside is None:
                pytest.skip("测试数据库没有当前锁定池以外的可用子因子")
            components[0]["component_factor_id"] = outside.parent_factor_id
            components[0]["component_sub_factor_id"] = outside.sub_factor_id

        response = factor_combo_worker_service.create_next_version_request(
            claimed,
            components=components,
            generation_method=generation_method,
        )
        body = response.json()
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)
        form = factor_combo_repository.get_form(claimed.worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 1, body
        assert feedback is not None and feedback["next_factor_combo_version_id"] is None, {
            "api": body,
            "db": feedback,
        }
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

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
        body = response.json()
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
        body = response.json()
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)
        form = factor_combo_repository.get_form(claimed.worker_form.submitted.form_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 1, body
        assert feedback is not None and feedback["next_pipeline_run_id"] is None, {"api": body, "db": feedback}
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
        body = response.json()

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
        first_body = first_response.json()
        replay_response = factor_combo_worker_service.create_next_version_request(claimed)
        replay_body = replay_response.json()

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

    def test_reordered_components_replay_same_next_version(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """仅交换 components 数组顺序重放，并验证规范化哈希及具体版本不变。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        first_response = factor_combo_worker_service.create_next_version_request(claimed)
        first_body = first_response.json()
        reordered = list(reversed(claimed.worker_form.components))

        response = factor_combo_worker_service.create_next_version_request(claimed, components=reordered)
        body = response.json()

        assert first_response.status_code == 201, first_body
        assert response.status_code == 200, body
        assert body["data"]["idempotent_replay"] is True, body
        assert body["data"]["factor_combo_version_id"] == first_body["data"]["factor_combo_version_id"], {
            "first": first_body,
            "replay": body,
        }
        assert body["data"]["combo_version_hash"] == first_body["data"]["combo_version_hash"], {
            "first": first_body,
            "replay": body,
        }
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 2, body

    def test_different_content_after_target_is_bound_conflicts(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """目标版本已绑定后修改组件内容再次提交，并验证不覆盖既有候选版本。"""

        claimed = factor_combo_worker_service.create_claimed_feedback()
        first_response = factor_combo_worker_service.create_next_version_request(claimed)
        first_body = first_response.json()
        changed_components = deepcopy(list(claimed.worker_form.components))
        changed_components[0]["weight"] = 0.123456789

        response = factor_combo_worker_service.create_next_version_request(claimed, components=changed_components)
        body = response.json()
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
        first_body = first_response.json()
        next_version = factor_combo_worker_service.require_next_version(first_response, claimed)
        experiment_payload = factor_combo_worker_service.build_experiment_payload(claimed.worker_form)
        experiment_response = factor_combo_worker_service.write_experiment_request(
            claimed.worker_form.experiment_id,
            experiment_payload,
        )
        experiment_body = experiment_response.json()

        replay_response = factor_combo_worker_service.create_next_version_request(claimed)
        replay_body = replay_response.json()
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
        body = response.json()
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(claimed.worker_form.submitted.form_id) == 1, body
        assert feedback is not None and feedback["next_pipeline_run_id"] is None, {"api": body, "db": feedback}
        assert feedback["next_factor_combo_version_id"] is None, {"api": body, "db": feedback}
