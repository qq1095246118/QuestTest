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
        assert isinstance(data.get("refresh_task_id"), str) and data["refresh_task_id"].strip(), body
        assert isinstance(data.get("refresh_status"), str) and data["refresh_status"].strip(), body
        for field in ("sub_factor", "factor_detail", "factor_validity_status", "registration"):
            assert isinstance(data.get(field), dict), body
        sub_factor_id = int(data["sub_factor_id"])
        factor_detail_id = int(data["factor_detail_id"])
        validity_status_id = int(data["factor_validity_status_id"])
        registration_id = int(data["registration_id"])
        sub_factor = factor_combo_repository.get_registered_sub_factor(sub_factor_id)
        factor_detail = factor_combo_repository.get_registered_factor_detail(factor_detail_id)
        validity = factor_combo_repository.get_registered_validity_status(validity_status_id)
        registration = factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        )
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
        stored_performance = sub_factor["metadata"]["report"]["performance"]
        expected_performance = {
            field: value
            for field, value in payload["report"]["performance"].items()
            if value is not None
            or field
            in {"ts_ic", "return_rate", "out_of_sample_icir", "net_sharpe", "max_drawdown", "annual_turnover"}
        }
        assert stored_performance == expected_performance, {"api": body, "db": sub_factor}
        assert set(stored_performance) == {
            "metrics_status",
            "ts_ic",
            "return_rate",
            "annualized_return",
            "out_of_sample_icir",
            "net_sharpe",
            "benchmark_sharpe",
            "max_drawdown",
            "calmar",
            "profit_loss_ratio",
            "annual_turnover",
            "positive_return_rate",
            "observations",
            "trade_observations",
            "decay_ratio",
            "metric_mode",
            "universe_key",
            "symbols",
        }, {"api": body, "db": sub_factor}
        assert int(factor_detail["factor_id"]) == sub_factor_id, {"api": body, "db": factor_detail}
        assert bool(factor_detail["is_sub_factor_id"]) is True, {"api": body, "db": factor_detail}
        assert int(factor_detail["status"]) == 1, {"api": body, "db": factor_detail}
        assert int(validity["factor_id"]) == sub_factor_id, {"api": body, "db": validity}
        assert validity["factor_bar_interval"] == payload["factor_validity_status"]["factor_bar_interval"], {
            "api": body,
            "db": validity,
        }
        assert str(validity["factor_window_bars"]) == str(
            payload["factor_validity_status"]["factor_window_bars"]
        ), {"api": body, "db": validity}
        assert sub_factor["factor_bar_interval"] == payload["factor_validity_status"]["factor_bar_interval"], {
            "api": body,
            "db": sub_factor,
        }
        assert str(sub_factor["window"]) == str(payload["factor_validity_status"]["factor_window_bars"]), {
            "api": body,
            "db": sub_factor,
        }
        assert validity["time_series_status"] == "unknown", {"api": body, "db": validity}
        assert validity["time_series_is_valid"] is None, {"api": body, "db": validity}
        assert validity["cross_sectional_status"] == "unknown", {"api": body, "db": validity}
        assert validity["cross_sectional_is_valid"] is None, {"api": body, "db": validity}
        assert validity["overall_status"] == "unknown", {"api": body, "db": validity}
        assert validity["overall_is_valid"] is None, {"api": body, "db": validity}
        assert int(registration["id"]) == registration_id, {"api": body, "db": registration}
        assert int(registration["sub_factor_id"]) == sub_factor_id, {"api": body, "db": registration}
        assert registration["factor_id"] is None and parent_relation_count == 0, {
            "api": body,
            "registration": registration,
            "parent_relation_count": parent_relation_count,
            "components": components,
        }
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
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body
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
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body
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
        body = response.json()

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
        )

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

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
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

    @pytest.mark.parametrize("null_field", ["cs_rank_ic", "cs_icir"])
    def test_cross_sectional_measured_metrics_cannot_be_null(
        self,
        null_field: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """将截面实测必填指标显式设为 null，并验证请求被拒绝且不创建登记记录。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(
            experiment,
            metric_mode="cross_sectional",
        )
        payload["report"]["performance"][null_field] = None

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [("universe_key", ""), ("symbols", [])],
    )
    def test_cross_sectional_context_rejects_blank_universe_or_empty_symbols(
        self,
        field: str,
        invalid_value: object,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交空币池或空币种数组，并验证截面上下文校验失败且不创建登记记录。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(
            experiment,
            metric_mode="cross_sectional",
        )
        payload["report"]["performance"][field] = invalid_value

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

    def test_cross_sectional_symbols_accept_unvalidated_values_and_duplicates(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交空字符串和重复币种，并验证当前文档声明的宽松 symbols 规则及原样持久化。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(
            experiment,
            metric_mode="cross_sectional",
        )
        payload["report"]["performance"]["symbols"] = ["", "BTCUSDT", "BTCUSDT"]

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(body["data"]["sub_factor_id"]))
        assert sub_factor is not None, {"api": body, "db": sub_factor}
        assert sub_factor["metadata"]["report"]["performance"]["symbols"] == [
            "",
            "BTCUSDT",
            "BTCUSDT",
        ], {"api": body, "db": sub_factor}

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
        body = response.json()

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

    def test_omitted_metric_mode_defaults_to_time_series(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """省略指标模式提交完整时序指标，并验证后端按默认时序模式接受请求。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        del payload["report"]["performance"]["metric_mode"]

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(body["data"]["sub_factor_id"]))
        assert sub_factor is not None, {"api": body, "db": sub_factor}
        stored_performance = sub_factor["metadata"]["report"]["performance"]
        assert "metric_mode" not in stored_performance, {"api": body, "db": sub_factor}
        expected_performance = {
            field: value
            for field, value in payload["report"]["performance"].items()
            if value is not None
            or field
            in {"ts_ic", "return_rate", "out_of_sample_icir", "net_sharpe", "max_drawdown", "annual_turnover"}
        }
        assert stored_performance == expected_performance, {"api": body, "db": sub_factor}

    def test_omitted_metrics_status_defaults_to_measured(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """省略绩效可用状态提交数值指标，并验证后端按 measured 规则接受且不补写请求字段。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        del payload["report"]["performance"]["metrics_status"]

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(body["data"]["sub_factor_id"]))
        assert sub_factor is not None, {"api": body, "db": sub_factor}
        stored_performance = sub_factor["metadata"]["report"]["performance"]
        assert "metrics_status" not in stored_performance, {"api": body, "db": sub_factor}
        expected_performance = {
            field: value
            for field, value in payload["report"]["performance"].items()
            if value is not None
            or field
            in {"ts_ic", "return_rate", "out_of_sample_icir", "net_sharpe", "max_drawdown", "annual_turnover"}
        }
        assert stored_performance == expected_performance, {"api": body, "db": sub_factor}

    @pytest.mark.parametrize(
        "optional_field",
        [
            "annualized_return",
            "benchmark_sharpe",
            "calmar",
            "profit_loss_ratio",
            "observations",
            "trade_observations",
            "decay_ratio",
        ],
    )
    def test_optional_performance_field_can_be_omitted(
        self,
        optional_field: str,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """逐一省略新版可选绩效字段，并验证登记成功且数据库不会补造该字段。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        del payload["report"]["performance"][optional_field]

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(body["data"]["sub_factor_id"]))
        assert sub_factor is not None, {"api": body, "db": sub_factor}
        stored_performance = sub_factor["metadata"]["report"]["performance"]
        assert optional_field not in stored_performance, {"api": body, "db": sub_factor}
        expected_performance = {
            field: value
            for field, value in payload["report"]["performance"].items()
            if value is not None
            or field
            in {"ts_ic", "return_rate", "out_of_sample_icir", "net_sharpe", "max_drawdown", "annual_turnover"}
        }
        assert stored_performance == expected_performance, {"api": body, "db": sub_factor}

    def test_rolling_oos_win_rate_can_replace_positive_return_rate(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """仅提交兼容正收益率字段，并验证登记成功且数据库原样保存该字段。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        del payload["report"]["performance"]["positive_return_rate"]
        payload["report"]["performance"]["rolling_oos_win_rate"] = 0.79

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 201, body
        assert body.get("success") is True, body
        sub_factor = factor_combo_repository.get_registered_sub_factor(int(body["data"]["sub_factor_id"]))
        assert sub_factor is not None, {"api": body, "db": sub_factor}
        stored_performance = sub_factor["metadata"]["report"]["performance"]
        assert "positive_return_rate" not in stored_performance, {"api": body, "db": sub_factor}
        assert stored_performance.get("rolling_oos_win_rate") == 0.79, {"api": body, "db": sub_factor}

    def test_both_positive_return_rate_fields_missing_are_rejected(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """同时省略正收益率主字段和兼容字段，并验证后端拒绝不完整报告。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        del payload["report"]["performance"]["positive_return_rate"]

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("annualized_return", -1.0001),
            ("profit_loss_ratio", -0.01),
            ("positive_return_rate", 1.01),
            ("rolling_oos_win_rate", 1.01),
            ("observations", -1),
            ("observations", 1.5),
            ("trade_observations", -1),
            ("trade_observations", 1.5),
            ("decay_ratio", -0.01),
            ("metric_mode", "hybrid"),
            ("cs_rank_ic", 1.01),
            ("calmar", "4.24"),
            ("cs_score", "68.4"),
            ("universe_key", None),
            ("symbols", None),
        ],
    )
    def test_new_performance_fields_reject_invalid_types_or_ranges(
        self,
        field: str,
        invalid_value: object,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交新增绩效字段的非法类型或边界值，并验证请求失败且不创建登记记录。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        payload["report"]["performance"][field] = invalid_value

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

    def test_symbols_with_non_string_member_is_rejected_during_json_parsing(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """在 symbols 中提交非字符串成员，并验证严格 JSON 解析返回 400 且不创建登记记录。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment)
        payload["report"]["performance"]["symbols"] = ["BTCUSDT", 1]

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 400, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

    def test_unavailable_performance_rejects_non_null_numeric_metric(
        self,
        factor_combo_worker_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """在 unavailable 模式提交非空新增数值指标，并验证请求失败且不创建登记记录。"""

        experiment = factor_combo_worker_service.create_completed_experiment()
        payload = factor_combo_worker_service.build_register_payload(experiment, metrics_available=False)
        payload["report"]["performance"]["annualized_return"] = 0.42

        response = factor_combo_worker_service.register_report_request(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

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
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

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
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body

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
        body = response.json()
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
        body = response.json()
        version = factor_combo_repository.get_combo_version(experiment.version.version_id)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert factor_combo_repository.get_registration(
            experiment.version.combo_id,
            version_id=experiment.version.version_id,
            combo_version_hash=experiment.version.combo_version_hash,
        ) is None, body
        assert version is not None and version["status"] == "candidate", {"api": body, "db": version}
