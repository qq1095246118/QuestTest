"""创建初始组合版本接口测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService


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
        response = factor_combo_worker_service.create_initial_version_request(worker_form)
        body = response.json()

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
        body = response.json()

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
        body = response.json()
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
        body = response.json()
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
        body = response.json()
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
        body = response.json()
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
        body = response.json()
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
        first_body = first_response.json()
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
        body = response.json()
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
        body = response.json()
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
        body = response.json()
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
        outside = factor_combo_repository.find_sub_factor_outside_pool(worker_form.submitted.pool_id)
        if outside is None:
            pytest.skip("测试数据库没有当前锁定池以外的可用子因子")
        components = deepcopy(list(worker_form.components))
        components[1]["component_factor_id"] = outside.parent_factor_id
        components[1]["component_sub_factor_id"] = outside.sub_factor_id

        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=components)
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "locked pool" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}
        assert form["factor_combo_experiment_info_id"] is None, {"api": body, "db": form}

    def test_unrelated_parent_factor_with_pool_sub_factor_is_allowed(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用真实存在但未关联该子因子的母因子，并验证当前契约只要求 ID 存在和子因子在池内。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        sub_factor_id = int(components[0]["component_sub_factor_id"])
        current_parent_id = int(components[0]["component_factor_id"])
        unrelated_parent_id = factor_combo_repository.find_unrelated_parent_factor(sub_factor_id, current_parent_id)
        if unrelated_parent_id is None:
            pytest.skip("测试数据库没有可用于父子不关联场景的其他母因子")
        components[0]["component_factor_id"] = unrelated_parent_id

        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=components)
        body = response.json()
        assert response.status_code == 201, body
        assert body.get("success") is True, body
        version_id = int(body["data"]["factor_combo_version_id"])
        stored_components = factor_combo_repository.get_components(version_id)
        stored = next(
            (item for item in stored_components if int(item["component_sub_factor_id"]) == sub_factor_id),
            None,
        )

        assert stored is not None, {"api": body, "db": stored_components}
        assert int(stored["component_factor_id"]) == unrelated_parent_id, {"api": body, "db": stored}

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
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "direction" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    @pytest.mark.parametrize("transform", [None, [], "zscore", 1], ids=["null", "array", "string", "number"])
    def test_non_object_transform_is_rejected_without_partial_write(
        self,
        transform: Any,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别提交 null、数组、字符串和数字 transform，并验证必须是 JSON 对象。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[1]["transform"] = transform

        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=components)
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "transform" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_optional_null_weight_and_empty_transform_are_persisted(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交允许的空 transform、null weight 和省略 weight，并验证数据库保留可选值。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[0]["transform"] = {}
        components[0]["weight"] = None
        components[1].pop("weight")

        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=components)
        body = response.json()
        assert response.status_code == 201, body
        assert body.get("success") is True, body
        version_id = int(body["data"]["factor_combo_version_id"])
        stored_components = factor_combo_repository.get_components(version_id)
        stored = next(
            item for item in stored_components
            if int(item["component_sub_factor_id"]) == int(components[0]["component_sub_factor_id"])
        )

        assert stored["transform_json"] == {}, {"api": body, "db": stored_components}
        assert stored["weight"] is None, {"api": body, "db": stored_components}
        omitted_weight_stored = next(
            item
            for item in stored_components
            if int(item["component_sub_factor_id"]) == int(components[1]["component_sub_factor_id"])
        )
        assert omitted_weight_stored["weight"] is None, {"api": body, "db": stored_components}

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
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "generation_method" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    @pytest.mark.parametrize(
        "field",
        ["pipeline_run_id", "combo_id", "generation_method", "components"],
    )
    def test_missing_required_top_level_field_is_rejected_without_write(
        self,
        field: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别删除四个顶层必填字段，并验证接口返回参数错误且不产生版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": list(worker_form.components),
        }
        payload.pop(field)

        response = factor_combo_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert field in str(body.get("error", "")), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("pipeline_run_id", None),
            ("combo_id", None),
            ("generation_method", None),
            ("components", None),
            ("components", []),
        ],
        ids=["pipeline-null", "combo-null", "method-null", "components-null", "components-empty"],
    )
    def test_null_or_empty_required_top_level_value_is_rejected_without_write(
        self,
        field: str,
        value: Any,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别提交顶层必填字段的 null 或空值，并验证接口拒绝且不写入版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": list(worker_form.components),
        }
        payload[field] = value

        response = factor_combo_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert field in str(body.get("error", "")), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("pipeline_run_id", 123),
            ("combo_id", "123"),
            ("generation_method", 123),
            ("components", {}),
            ("__unknown__", "unexpected"),
        ],
        ids=["pipeline-type", "combo-type", "method-type", "components-type", "unknown-field"],
    )
    def test_invalid_top_level_type_or_unknown_field_is_rejected_without_write(
        self,
        field: str,
        value: Any,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别提交顶层字段错误类型或未声明字段，并验证统一 400 错误和无写入。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": list(worker_form.components),
        }
        if field == "__unknown__":
            payload["unexpected"] = value
        else:
            payload[field] = value

        response = factor_combo_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 400, body
        assert body.get("success") is False, body
        assert body.get("error") == "invalid JSON request body", body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    @pytest.mark.parametrize(
        "pipeline_run_id",
        ["   ", "x" * 256],
        ids=["whitespace", "too-long"],
    )
    def test_pipeline_run_id_invalid_length_is_rejected_without_write(
        self,
        pipeline_run_id: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别提交空白和超过 255 字符的 Pipeline Run ID，并验证不创建版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = {
            "pipeline_run_id": pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": list(worker_form.components),
        }

        response = factor_combo_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "pipeline_run_id" in str(body.get("error", "")), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    def test_pipeline_run_id_outer_whitespace_is_normalized_on_success(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交带首尾空格但内容匹配的 Pipeline Run ID，并验证响应和数据库保存规范化值。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = {
            "pipeline_run_id": f"  {worker_form.pipeline_run_id}  ",
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": list(worker_form.components),
        }

        response = factor_combo_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        assert body["data"]["pipeline_run_id"] == worker_form.pipeline_run_id, body
        assert form is not None and form["pipeline_run_id"] == worker_form.pipeline_run_id, {
            "api": body,
            "db": form,
        }

    @pytest.mark.parametrize(
        ("combo_id", "expected_status"),
        [(0, 422), (-1, 400)],
        ids=["zero", "negative"],
    )
    def test_non_positive_combo_id_is_rejected_without_write(
        self,
        combo_id: int,
        expected_status: int,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别提交零和负数组合 ID，并验证接口拒绝且不产生版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": combo_id,
            "generation_method": "ml",
            "components": list(worker_form.components),
        }

        response = factor_combo_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    @pytest.mark.parametrize(
        ("form_id", "expected_status", "error_fragment"),
        [
            (0, 422, "form_id"),
            (-1, 422, "form_id"),
            ("abc", 422, "form_id"),
            (9_999_999_999, 404, "not found"),
        ],
        ids=["zero", "negative", "non-integer", "not-found"],
    )
    def test_invalid_form_path_is_rejected_without_write(
        self,
        form_id: Any,
        expected_status: int,
        error_fragment: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别提交非法或不存在的表单路径 ID，并验证接口返回路径错误且不影响真实表单。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": list(worker_form.components),
        }

        response = factor_combo_api.create_initial_version(form_id, payload)
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert error_fragment in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    @pytest.mark.parametrize(
        "field",
        ["component_factor_id", "component_sub_factor_id", "direction", "transform"],
    )
    def test_missing_component_field_is_rejected_without_write(
        self,
        field: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别删除组件必填字段，并验证接口拒绝且不写入孤立版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[0].pop(field)
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": components,
        }

        response = factor_combo_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert field in str(body.get("error", "")), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("component_factor_id", "invalid"),
            ("component_sub_factor_id", "invalid"),
            ("direction", "1"),
            ("unexpected", "unexpected"),
        ],
        ids=["factor-id-type", "sub-factor-id-type", "direction-type", "unknown-field"],
    )
    def test_invalid_component_type_or_unknown_field_is_rejected_without_write(
        self,
        field: str,
        value: Any,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别提交组件 ID、direction 的错误类型或未声明字段，并验证统一 400 错误和无业务写入。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[0][field] = value
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": components,
        }

        response = factor_combo_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 400, body
        assert body.get("success") is False, body
        assert body.get("error") == "invalid JSON request body", body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

    @pytest.mark.parametrize(
        ("weight", "error_fragment"),
        [
            (1.0000001, "between -1 and 1"),
            (-1.0000001, "between -1 and 1"),
            (0.12345678901, "decimal(20,10)"),
        ],
        ids=["above-one", "below-minus-one", "eleven-decimals"],
    )
    def test_weight_range_or_precision_is_rejected_without_write(
        self,
        weight: float,
        error_fragment: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别提交超出范围或超过十位小数的 weight，并验证接口拒绝且不产生版本。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[0]["weight"] = weight
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": components,
        }

        response = factor_combo_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert error_fragment in str(body.get("error", "")).lower(), body
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
        body = response.json()
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
        body = response.json()
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
        first_body = first_response.json()
        assert first_response.status_code == 201, first_body
        assert first_body.get("success") is True, first_body
        replay_response = factor_combo_worker_service.create_initial_version_request(worker_form)
        replay_body = replay_response.json()
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

    def test_reordered_components_keep_same_hash_and_version(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """仅交换 components 数组顺序重放，并验证规范化哈希和具体版本不变。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        first_response = factor_combo_worker_service.create_initial_version_request(worker_form)
        first_body = first_response.json()
        assert first_response.status_code == 201, first_body
        assert first_body.get("success") is True, first_body
        reordered = list(reversed(worker_form.components))
        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=reordered)
        body = response.json()

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
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 1, body

    def test_reordered_nested_transform_keys_keep_same_hash(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """仅调整嵌套 transform 对象字段顺序重放，并验证递归规范化幂等。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        first_components = deepcopy(list(worker_form.components))
        first_components[0]["transform"] = {
            "normalization": "zscore",
            "clip": {"min": -3, "max": 3},
        }
        first_response = factor_combo_worker_service.create_initial_version_request(
            worker_form,
            components=first_components,
        )
        first_body = first_response.json()
        assert first_response.status_code == 201, first_body
        assert first_body.get("success") is True, first_body
        reordered_components = deepcopy(first_components)
        reordered_components[0]["transform"] = {
            "clip": {"max": 3, "min": -3},
            "normalization": "zscore",
        }
        replay_response = factor_combo_worker_service.create_initial_version_request(
            worker_form,
            components=reordered_components,
        )
        replay_body = replay_response.json()

        assert replay_response.status_code == 200, replay_body
        assert replay_body["data"]["idempotent_replay"] is True, replay_body
        assert replay_body["data"]["factor_combo_version_id"] == first_body["data"]["factor_combo_version_id"], {
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

    def test_identical_initial_version_requests_concurrently_are_idempotent(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """并发提交两次完全相同的初始版本请求，并验证只创建一个版本且一请求成功一请求重放。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                factor_combo_worker_service.create_initial_version_request,
                worker_form,
            )
            second_future = executor.submit(
                factor_combo_worker_service.create_initial_version_request,
                worker_form,
            )
            responses = [first_future.result(), second_future.result()]
        bodies = [response.json() for response in responses]
        statuses = sorted(response.status_code for response in responses)

        assert statuses == [200, 201], bodies
        assert all(body.get("success") is True for body in bodies), bodies
        version_ids = {int(body["data"]["factor_combo_version_id"]) for body in bodies}

        assert len(version_ids) == 1, bodies
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 1, bodies
        version_id = next(iter(version_ids))
        assert factor_combo_repository.count_components(version_id) == len(worker_form.components), bodies

    def test_weight_ten_decimal_places_are_stored_exactly(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交范围内十位小数权重，并验证 DECIMAL(20,10) 的精确值。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        components = deepcopy(list(worker_form.components))
        components[0]["weight"] = 0.1234567890
        components[1]["weight"] = -0.9876543210
        response = factor_combo_worker_service.create_initial_version_request(worker_form, components=components)
        body = response.json()
        assert response.status_code == 201, body
        assert body.get("success") is True, body
        version_id = int(body["data"]["factor_combo_version_id"])
        stored_components = factor_combo_repository.get_components(version_id)
        stored_weights = {
            int(component["component_sub_factor_id"]): Decimal(str(component["weight"]))
            for component in stored_components
        }

        for component in components:
            assert stored_weights[int(component["component_sub_factor_id"])] == Decimal(str(component["weight"])), {
                "api": body,
                "db": stored_components,
            }

    def test_authenticated_without_research_permission_cannot_create_version(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_restricted_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用已登录但没有研究权限的账号调用接口，并验证返回 403 且不改变原表单。

        当前测试账号缺少 ``use_research_agent`` 权限，因此不能据此验证“有权限但非所有者”的 404 场景；该场景需要另一个具备该权限的非表单所有者账号。
        """

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = {
            "pipeline_run_id": worker_form.pipeline_run_id,
            "combo_id": worker_form.combo_id,
            "generation_method": "ml",
            "components": list(worker_form.components),
        }

        response = factor_combo_restricted_api.create_initial_version(
            worker_form.submitted.form_id,
            payload,
        )
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 403, body
        assert body.get("success") is False, body
        assert "permission" in str(body.get("error", "")).lower(), body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}

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
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body
        assert form is not None and form["factor_combo_id"] is None, {"api": body, "db": form}
