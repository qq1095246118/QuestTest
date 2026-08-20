"""写入组合因子实验结果接口测试。"""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService


@pytest.mark.integration
@pytest.mark.worker_contract
class TestWriteFactorComboExperimentAPI:
    """验证 Worker 实验回写契约、幂等规则和数据库关联。"""

    def test_successful_experiment_persists_payload_and_links_version_and_form(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """写入有效实验，并核对响应、实验 JSON、Artifact 及表单和版本指针。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        version = factor_combo_worker_service.create_worker_version(worker_form)
        payload = factor_combo_worker_service.build_experiment_payload(worker_form)

        response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        data = body.get("data")
        assert isinstance(data, dict), body
        assert data.get("experiment_id") == worker_form.experiment_id, body
        assert data.get("form_id") == worker_form.submitted.form_id, body
        assert data.get("factor_combo_version_id") == version.version_id, body
        assert data.get("combo_id") == version.combo_id, body
        assert data.get("form_status") == "completed", body
        assert data.get("combo_status") == "candidate", body
        assert data.get("idempotent_replay") is False, body
        experiment_info_id = int(data["experiment_info_id"])
        experiment = factor_combo_repository.get_experiment(experiment_info_id)
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)
        stored_version = factor_combo_repository.get_combo_version(version.version_id)
        assert experiment is not None and form is not None and stored_version is not None, {
            "api": body,
            "experiment": experiment,
            "form": form,
            "version": stored_version,
        }
        assert experiment["experiment_id"] == worker_form.experiment_id, {"api": body, "db": experiment}
        assert int(experiment["combo_id"]) == version.combo_id, {"api": body, "db": experiment}
        assert bool(experiment["valid"]) is True, {"api": body, "db": experiment}
        assert experiment["evaluation_config_json"] == payload["evaluation_config"], {"api": body, "db": experiment}
        assert experiment["metrics_json"] == payload["metrics"], {"api": body, "db": experiment}
        assert experiment["train_config_json"] == payload["train_config"], {"api": body, "db": experiment}
        assert experiment["artifact_uri"] == payload["artifact"]["uri"], {"api": body, "db": experiment}
        assert experiment["artifact_hash"] == payload["artifact"]["sha256"].lower(), {"api": body, "db": experiment}
        assert int(form["factor_combo_id"]) == version.version_id, {"api": body, "db": form}
        assert int(form["factor_combo_experiment_info_id"]) == experiment_info_id, {"api": body, "db": form}
        assert int(stored_version["experiment_id"]) == experiment_info_id, {"api": body, "db": stored_version}

    def test_identical_experiment_replay_returns_same_record(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """连续提交完全相同的 experiment_id 和请求体，并验证第二次为同记录幂等重放。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        factor_combo_worker_service.create_worker_version(worker_form)
        payload = factor_combo_worker_service.build_experiment_payload(worker_form)

        first_response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        first_body = first_response.json()
        replay_response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        replay_body = replay_response.json()

        assert first_response.status_code == 201, first_body
        assert replay_response.status_code == 200, replay_body
        assert replay_body.get("success") is True, replay_body
        assert first_body["data"]["idempotent_replay"] is False, first_body
        assert replay_body["data"]["idempotent_replay"] is True, replay_body
        assert replay_body["data"]["experiment_info_id"] == first_body["data"]["experiment_info_id"], {
            "first": first_body,
            "replay": replay_body,
        }
        assert factor_combo_repository.count_experiments_by_artifact_uri(worker_form.artifact_uri) == 1, {
            "first": first_body,
            "replay": replay_body,
        }

    def test_same_experiment_id_with_different_metrics_conflicts_without_overwrite(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """用相同 experiment_id 改写指标，并验证返回冲突且首次实验内容保持不变。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        factor_combo_worker_service.create_worker_version(worker_form)
        payload = factor_combo_worker_service.build_experiment_payload(worker_form)
        first_response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        first_body = first_response.json()
        changed_payload = deepcopy(payload)
        changed_payload["metrics"]["overall"]["return_rate"] = 0.99

        conflict_response = factor_combo_worker_service.write_experiment_request(
            worker_form.experiment_id,
            changed_payload,
        )
        conflict_body = conflict_response.json()
        stored = factor_combo_repository.get_experiment(int(first_body["data"]["experiment_info_id"]))

        assert first_response.status_code == 201, first_body
        assert conflict_response.status_code == 409, conflict_body
        assert conflict_body.get("success") is False, conflict_body
        assert stored is not None, {"first": first_body, "conflict": conflict_body}
        assert stored["metrics_json"] == payload["metrics"], {"api": conflict_body, "db": stored}

    def test_form_without_combo_version_cannot_receive_experiment(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """只认领表单但不创建组合版本，并验证实验写入被拒绝且没有部分记录。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        payload = factor_combo_worker_service.build_experiment_payload(worker_form)

        response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        body = response.json()

        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_experiment_by_external_id(worker_form.experiment_id) is None, body
        assert factor_combo_repository.count_versions_for_form(worker_form.submitted.form_id) == 0, body

    def test_mismatched_pipeline_run_id_cannot_write_experiment(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交不属于当前表单的运行 ID，并验证无法定位目标版本且不新增实验。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        factor_combo_worker_service.create_worker_version(worker_form)
        payload = factor_combo_worker_service.build_experiment_payload(worker_form)
        payload["pipeline_run_id"] = f"wrong-run-{uuid4().hex}"

        response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        body = response.json()

        assert response.status_code == 404, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_experiment_by_external_id(worker_form.experiment_id) is None, body

    @pytest.mark.parametrize(
        ("field_name", "invalid_value"),
        [
            ("evaluation_config", None),
            ("metrics", []),
            ("train_config", "ElasticNet"),
        ],
    )
    def test_required_json_object_fields_reject_other_types(
        self,
        field_name: str,
        invalid_value: object,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """将必需 JSON 对象字段改为 null、数组或字符串，并验证 422 且不落库。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        factor_combo_worker_service.create_worker_version(worker_form)
        payload = factor_combo_worker_service.build_experiment_payload(worker_form)
        payload[field_name] = invalid_value

        response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_experiment_by_external_id(worker_form.experiment_id) is None, body

    @pytest.mark.parametrize(
        ("mutation", "expected_status"),
        [
            ("valid_with_failure_reason", 422),
            ("unsupported_artifact_type", 422),
            ("unknown_top_level_field", 400),
        ],
    )
    def test_invalid_experiment_contract_is_rejected_without_partial_write(
        self,
        mutation: str,
        expected_status: int,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交有效性冲突、非法 Artifact 类型或未知字段，并验证没有实验记录。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        factor_combo_worker_service.create_worker_version(worker_form)
        payload = factor_combo_worker_service.build_experiment_payload(worker_form)
        if mutation == "valid_with_failure_reason":
            payload["failure_reason"] = "training failed"
        elif mutation == "unsupported_artifact_type":
            payload["artifact"]["type"] = "file"
        else:
            payload["experiment_id"] = "must-only-be-in-path"

        response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        body = response.json()

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_experiment_by_external_id(worker_form.experiment_id) is None, body

    def test_failed_experiment_is_persisted_with_failure_reason(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """写入 valid=false 和非空失败原因，并验证失败实验仍完整关联但保持无效。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        version = factor_combo_worker_service.create_worker_version(worker_form)
        payload = factor_combo_worker_service.build_experiment_payload(
            worker_form,
            valid=False,
            failure_reason="autotest model calculation failed",
        )

        response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        body = response.json()

        assert response.status_code == 201, body
        experiment = factor_combo_repository.get_experiment(int(body["data"]["experiment_info_id"]))
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)
        stored_version = factor_combo_repository.get_combo_version(version.version_id)
        assert experiment is not None and form is not None and stored_version is not None, {
            "api": body,
            "experiment": experiment,
            "form": form,
            "version": stored_version,
        }
        assert bool(experiment["valid"]) is False, {"api": body, "db": experiment}
        assert experiment["failure_reason"] == payload["failure_reason"], {"api": body, "db": experiment}
        assert int(form["factor_combo_experiment_info_id"]) == int(experiment["id"]), {"api": body, "db": form}
        assert int(stored_version["experiment_id"]) == int(experiment["id"]), {"api": body, "db": stored_version}

    def test_new_experiment_id_cannot_replace_already_linked_experiment(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """表单已有实验后改用新路径幂等键重写，并验证原实验及两个关联指针不变。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        version = factor_combo_worker_service.create_worker_version(worker_form)
        payload = factor_combo_worker_service.build_experiment_payload(worker_form)
        first_response = factor_combo_worker_service.write_experiment_request(worker_form.experiment_id, payload)
        first_body = first_response.json()
        first_experiment_id = int(first_body["data"]["experiment_info_id"])
        replacement_id = f"replacement-{uuid4().hex}"

        response = factor_combo_worker_service.write_experiment_request(replacement_id, payload)
        body = response.json()
        form = factor_combo_repository.get_form(worker_form.submitted.form_id)
        stored_version = factor_combo_repository.get_combo_version(version.version_id)

        assert first_response.status_code == 201, first_body
        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_experiment_by_external_id(replacement_id) is None, body
        assert form is not None and int(form["factor_combo_experiment_info_id"]) == first_experiment_id, {
            "api": body,
            "db": form,
        }
        assert stored_version is not None and int(stored_version["experiment_id"]) == first_experiment_id, {
            "api": body,
            "db": stored_version,
        }

    def test_artifact_uri_cannot_be_reused_by_another_experiment(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """在第二条独立链路复用首条合法 Artifact URI，并验证全局唯一约束拒绝写入。"""

        first_worker = factor_combo_worker_service.create_worker_form()
        factor_combo_worker_service.create_worker_version(first_worker)
        first_payload = factor_combo_worker_service.build_experiment_payload(first_worker)
        first_response = factor_combo_worker_service.write_experiment_request(first_worker.experiment_id, first_payload)
        first_body = first_response.json()
        second_worker = factor_combo_worker_service.create_worker_form()
        factor_combo_worker_service.create_worker_version(second_worker)
        second_payload = factor_combo_worker_service.build_experiment_payload(
            second_worker,
            artifact_uri=first_worker.artifact_uri,
            artifact_sha256=first_worker.artifact_sha256,
        )

        response = factor_combo_worker_service.write_experiment_request(second_worker.experiment_id, second_payload)
        body = response.json()

        assert first_response.status_code == 201, first_body
        assert response.status_code == 409, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_experiment_by_external_id(second_worker.experiment_id) is None, body
        assert factor_combo_repository.count_experiments_by_artifact_uri(first_worker.artifact_uri) == 1, body

    def test_artifact_sha256_cannot_be_reused_by_another_experiment(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用不同 URI 复用已有实验的 SHA256，并验证重复内容也被拒绝且不新增实验。"""

        first_worker = factor_combo_worker_service.create_worker_form()
        second_worker = factor_combo_worker_service.create_worker_form()
        factor_combo_worker_service.create_worker_version(first_worker)
        factor_combo_worker_service.create_worker_version(second_worker)
        first_payload = factor_combo_worker_service.build_experiment_payload(first_worker)
        second_payload = factor_combo_worker_service.build_experiment_payload(second_worker)
        second_payload["artifact"]["uri"] = f"s3://test-factor-combo/different-uri-{uuid4().hex}"
        second_payload["artifact"]["sha256"] = first_worker.artifact_sha256

        first_response = factor_combo_worker_service.write_experiment_request(first_worker.experiment_id, first_payload)
        first_body = first_response.json()
        second_response = factor_combo_worker_service.write_experiment_request(second_worker.experiment_id, second_payload)
        second_body = second_response.json()

        assert first_response.status_code == 201, first_body
        assert second_response.status_code == 409, second_body
        assert second_body.get("success") is False, second_body
        assert factor_combo_repository.get_experiment_by_external_id(second_worker.experiment_id) is None, second_body
        assert factor_combo_repository.count_experiments_by_artifact_hash(first_worker.artifact_sha256) == 1, {
            "first": first_body,
            "second": second_body,
        }

    def test_unauthenticated_experiment_write_is_rejected(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_unauthenticated_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """不携带 Token 写入合法实验，并验证返回 401 且不会产生实验记录。"""

        worker_form = factor_combo_worker_service.create_worker_form()
        factor_combo_worker_service.create_worker_version(worker_form)
        payload = factor_combo_worker_service.build_experiment_payload(worker_form)

        response = factor_combo_unauthenticated_api.write_experiment(worker_form.experiment_id, payload)
        body = response.json()

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_experiment_by_external_id(worker_form.experiment_id) is None, body
