"""创建初始组合版本接口测试。"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService
from tools.http_response import read_json


@pytest.mark.integration
@pytest.mark.worker_contract
class TestCreateInitialFactorComboVersionAPI:
    """验证初始版本接口的前置条件、组件契约、幂等和持久化结果。"""

    def test_create_initial_version_persists_complete_response_and_graph(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用满足全部前置条件的临时表单创建版本，并核对响应和三张业务表的完整映射。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        request_payload = factor_combo_worker_service.build_initial_version_payload(worker_form)
        response = factor_combo_worker_service.create_initial_version_request(worker_form)
        body = read_json(response)

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        data = body.get("data")
        assert isinstance(data, dict), body
        version_id = int(data["factor_combo_version_id"])
        assert data.get("form_id") == worker_form.submitted.form_id, body
        assert data.get("form_status") == "processing", body
        assert data.get("pipeline_run_id") == worker_form.pipeline_run_id, body
        assert data.get("factor_combo_version_id") == version_id, body
        assert data.get("combo_id") == worker_form.combo_id, body
        assert data.get("combo_family_key") == f"factor-combo-form:{worker_form.submitted.form_id}", body
        assert data.get("pool_id") == worker_form.submitted.pool_id, body
        assert isinstance(data.get("combo_version_hash"), str), body
        assert len(data["combo_version_hash"]) == 64, body
        assert data.get("combo_status") == "candidate", body
        assert data.get("component_count") == len(worker_form.components), body
        assert data.get("idempotent_replay") is False, body

        version = factor_combo_repository.get_combo_version(version_id)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)
        components = factor_combo_repository.get_components(version_id)
        assert version is not None and form is not None, {"api": body, "version": version, "form": form}
        assert int(version["id"]) == version_id, {"api": body, "db": version}
        assert int(version["combo_id"]) == worker_form.combo_id, {"api": body, "db": version}
        assert version["combo_family_key"] == data["combo_family_key"], {"api": body, "db": version}
        assert int(version["initial_form_id"]) == worker_form.submitted.form_id, {"api": body, "db": version}
        assert int(version["pool_id"]) == worker_form.submitted.pool_id, {"api": body, "db": version}
        assert version["generation_method"] == "ml", {"api": body, "db": version}
        assert version["experiment_id"] is None, {"api": body, "db": version}
        assert version["combo_version_hash"] == data["combo_version_hash"], {"api": body, "db": version}
        assert version["status"] == "candidate", {"api": body, "db": version}
        assert int(form["factor_combo_id"]) == version_id, {"api": body, "db": form}
        assert form["factor_combo_experiment_info_id"] is None, {"api": body, "db": form}
        assert form["pipeline_run_id"] == worker_form.pipeline_run_id, {"api": body, "db": form}
        assert len(components) == len(worker_form.components), {"api": body, "db": components}
        factor_combo_worker_service.validate_combo_version_persistence(
            data,
            request_payload,
            form,
            version,
            components,
        )

        expected_components = {
            int(component["component_sub_factor_id"]): component for component in worker_form.components
        }
        for stored in components:
            expected = expected_components[int(stored["component_sub_factor_id"])]
            assert int(stored["component_factor_id"]) == int(expected["component_factor_id"]), {
                "api": body,
                "expected": expected,
                "db": stored,
            }
            assert int(stored["direction"]) == int(expected["direction"]), {
                "api": body,
                "expected": expected,
                "db": stored,
            }
            assert stored["transform_json"] == expected["transform"], {
                "api": body,
                "expected": expected,
                "db": stored,
            }
            if expected.get("weight") is None:
                assert stored["weight"] is None, {"api": body, "expected": expected, "db": stored}
            else:
                assert Decimal(str(stored["weight"])) == Decimal(str(expected["weight"])), {
                    "api": body,
                    "expected": expected,
                    "db": stored,
                }

    @pytest.mark.parametrize("generation_method", ["manual", "dark_random", "hybrid"])
    def test_supported_generation_methods_create_initial_version(
        self,
        generation_method: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别使用文档支持的 manual、dark_random 和 hybrid，并验证生成方式持久化。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        response = factor_combo_worker_service.create_initial_version_request(
            worker_form,
            generation_method=generation_method,
        )
        body = read_json(response)

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        version = factor_combo_repository.get_combo_version(int(body["data"]["factor_combo_version_id"]))
        assert version is not None, {"api": body, "db": version}
        assert version["generation_method"] == generation_method, {"api": body, "db": version}
        assert version["status"] == "candidate", {"api": body, "db": version}

    def test_form_not_processing_is_rejected_without_write(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """仅将已认领表单改为 submitted，并验证非 processing 状态被拒绝且不写版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        factor_combo_repository.set_form_status_for_test(worker_form.submitted.form_id, "submitted")

        response = factor_combo_worker_service.create_initial_version_request(worker_form)
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert "status" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["status"] == "submitted", {"api": body, "db": form}
        assert form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_unlocked_pool_is_rejected_without_changing_form_or_pool(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """仅将已认领表单的因子池改为未锁定，并验证接口拒绝且不改变业务状态。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        factor_combo_repository.set_pool_status_for_test(worker_form.submitted.pool_id, "draft")

        response = factor_combo_worker_service.create_initial_version_request(worker_form)
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)
        pool = factor_combo_repository.get_pool(worker_form.submitted.pool_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "pool" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["status"] == "processing", {"api": body, "db": form}
        assert form["factor_combo_id"] is None, {"api": body, "db": form}
        assert pool is not None and pool["status"] == "draft", {"api": body, "db": pool}

    def test_pipeline_run_id_mismatch_is_rejected_without_pointer_change(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用错误的 Pipeline Run ID，并验证接口拒绝且表单运行关联保持不变。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        response = factor_combo_worker_service.create_initial_version_request(
            worker_form,
            pipeline_run_id=f"wrong-initial-run-{worker_form.submitted.form_id}",
        )
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert "pipeline_run_id" in str(body.get("error", "")), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["pipeline_run_id"] == worker_form.pipeline_run_id, {
            "api": body,
            "db": form,
        }
        assert form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_form_with_existing_experiment_is_rejected_without_overwrite(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """表单已有实验结果时重新提交不同版本内容，并验证原实验与版本指针不被覆盖。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        form_id = experiment.version.worker_form.submitted.form_id
        factor_combo_repository.set_form_status_for_test(form_id, "processing")
        changed_components = deepcopy(list(experiment.version.worker_form.components))
        changed_components[0]["weight"] = 0.1111111111

        response = factor_combo_worker_service.create_initial_version_request(
            experiment.version.worker_form,
            components=changed_components,
        )
        body = read_json(response)
        form = factor_combo_repository.get_form(form_id)
        stored_experiment = factor_combo_repository.get_experiment(experiment.experiment_info_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(form_id) == 1, body
        assert form is not None, {"api": body, "db": form}
        assert int(form["factor_combo_experiment_info_id"]) == experiment.experiment_info_id, {
            "api": body,
            "db": form,
        }
        assert int(form["factor_combo_id"]) == experiment.version.version_id, {"api": body, "db": form}
        assert stored_experiment is not None and stored_experiment["experiment_id"] == experiment.experiment_id, {
            "api": body,
            "db": stored_experiment,
        }

    def test_form_with_feedback_history_is_rejected_without_new_version(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """表单已有反馈历史但尚未创建下一版本时调用初始版本接口，并验证不重复创建。"""

        claimed = factor_combo_worker_service.create_claimed_feedback("autotest initial feedback history")
        form_id = claimed.worker_form.submitted.form_id
        factor_combo_repository.set_form_pipeline_run_for_test(
            form_id,
            claimed.worker_form.pipeline_run_id,
        )

        response = factor_combo_worker_service.create_initial_version_request(claimed.worker_form)
        body = read_json(response)
        feedback = factor_combo_repository.get_feedback(claimed.feedback_id)
        form = factor_combo_repository.get_form(form_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert "feedback" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(form_id) == 1, body
        assert feedback is not None and feedback["status"] == "processing", {"api": body, "db": feedback}
        assert feedback["next_factor_combo_version_id"] is None, {"api": body, "db": feedback}
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_changed_content_after_initial_version_conflicts_without_overwrite(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """初始版本已绑定后修改权重再次提交，并验证返回 409 且原组件保持不变。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        first_response = factor_combo_worker_service.create_initial_version_request(worker_form)
        first_body = read_json(first_response)
        assert first_response.status_code == 201, first_body
        assert first_body.get("success") is True, first_body
        version_id = int(first_body["data"]["factor_combo_version_id"])
        original_components = factor_combo_repository.get_components(version_id)
        changed_components = deepcopy(list(worker_form.components))
        changed_components[0]["weight"] = -0.321

        response = factor_combo_worker_service.create_initial_version_request(
            worker_form,
            components=changed_components,
        )
        body = read_json(response)
        current_components = factor_combo_repository.get_components(version_id)

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        error_text = str(body.get("error", "")).lower()
        assert "version" in error_text and ("different" in error_text or "linked" in error_text), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 1, body
        assert current_components == original_components, {
            "api": body,
            "before": original_components,
            "after": current_components,
        }

    def test_one_component_is_rejected_without_partial_write(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """仅保留一个合法组件，并验证最少两个组件规则和无部分写入。"""

        worker_form = factor_combo_worker_service.create_worker_form_from_parent()
        response = factor_combo_worker_service.create_initial_version_request(
            worker_form,
            components=list(worker_form.components[:1]),
        )
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert any(
            phrase in str(body.get("error", "")).lower()
            for phrase in ("at least 2", "single component")
        ), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_duplicate_sub_factor_is_rejected_without_partial_write(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """仅将第二个组件改为重复子因子，并验证唯一性校验和无部分写入。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[1]["component_sub_factor_id"] = components[0]["component_sub_factor_id"]

        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=components)
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "unique" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_pool_outside_sub_factor_rolls_back_all_writes(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """将第二个组件改为池外子因子，并验证返回错误且版本、组件和表单指针全部回滚。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        outside = factor_combo_repository.find_sub_factor_outside_pool(worker_form.submitted.pool_id)
        assert outside is not None, {
            "form_id": worker_form.submitted.form_id,
            "pool_id": worker_form.submitted.pool_id,
            "reason": "测试库没有可用于构造池外组件的真实子因子",
        }
        outside_sub_factor_id = outside.sub_factor_id
        components[1]["component_factor_id"] = outside.parent_factor_id
        components[1]["component_sub_factor_id"] = outside_sub_factor_id
        response = factor_combo_worker_service.create_initial_version_request(
            worker_form,
            components=components,
        )
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)
        pool_members = factor_combo_repository.get_pool_members(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "locked pool" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}
        assert form["factor_combo_experiment_info_id"] is None, {"api": body, "db": form}
        assert all(int(member["sub_factor_id"]) != outside_sub_factor_id for member in pool_members), {
            "api": body,
            "pool_members": pool_members,
        }

    def test_invalid_direction_is_rejected_without_partial_write(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """将 direction 改为枚举外数值，并验证接口拒绝且不产生版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[1]["direction"] = 0

        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=components)
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "direction" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_unsupported_generation_method_is_rejected_without_write(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交文档未支持的生成方式，并验证接口返回 422 且不创建版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        response = factor_combo_worker_service.create_initial_version_request(
            worker_form,
            generation_method="xgboost",
        )
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "generation_method" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_nonexistent_component_factor_id_is_rejected_without_write(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """将池内子因子与不存在的母因子 ID 组合，并验证 ID 存在性校验。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[0]["component_factor_id"] = 9_999_999_999

        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=components)
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_nonexistent_component_sub_factor_id_is_rejected_without_write(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """将组件子因子 ID 改为不存在的正整数，并验证接口拒绝且不产生版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[0]["component_sub_factor_id"] = 9_999_999_999

        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=components)
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_identical_initial_version_replay_is_idempotent_without_duplicate_rows(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """重复提交完全相同的初始版本请求，并验证只保留一条版本及其组件。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        first_response = factor_combo_worker_service.create_initial_version_request(worker_form)
        first_body = read_json(first_response)
        assert first_response.status_code == 201, first_body
        assert first_body.get("success") is True, first_body
        replay_response = factor_combo_worker_service.create_initial_version_request(worker_form)
        replay_body = read_json(replay_response)
        version_id = int(first_body["data"]["factor_combo_version_id"])

        assert replay_response.status_code == 200, replay_body
        assert first_body["data"]["idempotent_replay"] is False, first_body
        assert replay_body.get("success") is True, replay_body
        assert replay_body["data"]["idempotent_replay"] is True, replay_body
        assert replay_body["data"]["factor_combo_version_id"] == version_id, {
            "first": first_body,
            "replay": replay_body,
        }
        assert replay_body["data"]["combo_version_hash"] == first_body["data"]["combo_version_hash"], {
            "first": first_body,
            "replay": replay_body,
        }
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 1, {
            "first": first_body,
            "replay": replay_body,
        }
        assert factor_combo_repository.count_components(version_id) == len(worker_form.components), {
            "first": first_body,
            "replay": replay_body,
        }

    def test_missing_token_is_rejected_without_write(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_unauthenticated_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """不携带 JWT 调用初始版本接口，并验证返回 401 且不产生业务写入。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": list(worker_form.components),
        }

        response = factor_combo_unauthenticated_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = read_json(response)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}
