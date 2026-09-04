"""提交组合因子研究表单接口测试。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService
from tools.http_response import read_json


@pytest.mark.integration
class TestSubmitFactorComboFormAPI:
    """验证表单提交参数、因子解析和数据库持久化。"""

    def test_submit_direct_sub_factors_creates_exact_pool_members(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交两个真实子因子，并验证响应、表单和因子池成员完全一致。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 202, body
        assert body.get("success") is True, body
        data = body.get("data")
        assert isinstance(data, dict), body
        assert data.get("status") == "submitted", body
        assert isinstance(data.get("form_id"), int) and data["form_id"] > 0, body
        assert isinstance(data.get("factor_combo_pool_id"), int) and data["factor_combo_pool_id"] > 0, body
        submitted = factor_combo_service.require_submitted_form(response, session_id)
        form_row = factor_combo_repository.get_form(submitted.form_id)
        pool_row = factor_combo_repository.get_pool(submitted.pool_id)
        member_rows = factor_combo_repository.get_pool_members(submitted.form_id)
        assert form_row is not None and pool_row is not None, {"api": body, "form": form_row, "pool": pool_row}
        assert int(form_row["session_id"]) == session_id, {"api": body, "db": form_row}
        assert int(form_row["factor_combo_pool_id"]) == submitted.pool_id, {"api": body, "db": form_row}
        assert int(pool_row["factor_combo_form_id"]) == submitted.form_id, {"api": body, "db": pool_row}
        assert [int(row["sub_factor_id"]) for row in member_rows] == [
            choice.sub_factor_id for choice in choices
        ], {"api": body, "db": member_rows}
        factor_combo_service.validate_submitted_form_persistence(
            body["data"],
            payload,
            submitted,
            form_row,
            pool_row,
            member_rows,
        )

    def test_submit_parent_factor_expands_all_children(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交一个母因子，并验证锁定池包含该母因子关联的全部子因子且不依赖评分。"""

        parent = factor_combo_repository.find_parent_with_sub_factors()
        assert parent is not None, "测试数据库需要至少一个拥有两个关联子因子的母因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(session_id, [parent.factor_name], is_sub_factor=0)

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 202, body
        assert body.get("success") is True, body
        submitted = factor_combo_service.require_submitted_form(response, session_id)
        member_rows = factor_combo_repository.get_pool_members(submitted.form_id)
        actual_ids = [int(row["sub_factor_id"]) for row in member_rows]
        expected_ids = [choice.sub_factor_id for choice in parent.sub_factors]
        assert set(actual_ids) == set(expected_ids), {
            "api": body,
            "expected_sub_factor_ids": expected_ids,
            "db": member_rows,
        }
        assert len(actual_ids) == len(expected_ids), {
            "api": body,
            "expected_sub_factor_ids": expected_ids,
            "db": member_rows,
        }
        assert len(actual_ids) >= 2, {"api": body, "db": member_rows}
        assert len(actual_ids) == len(set(actual_ids)), {"api": body, "db": member_rows}
        form_row = factor_combo_repository.get_form(submitted.form_id)
        pool_row = factor_combo_repository.get_pool(submitted.pool_id)
        assert form_row is not None and pool_row is not None, {
            "api": body,
            "form": form_row,
            "pool": pool_row,
        }
        factor_combo_service.validate_submitted_form_persistence(
            body["data"],
            payload,
            submitted,
            form_row,
            pool_row,
            member_rows,
        )

    def test_submit_mixed_parent_and_child_is_rejected(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """在同一请求中混用母因子和子因子，并验证接口拒绝且不创建表单。"""

        response, session_id, parent = factor_combo_service.submit_mixed_parent_and_sub_factor_for_rejection()
        body = read_json(response)

        assert response.status_code == 422, {"api": body, "parent": parent}
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == 0, {
            "api": body,
            "session_id": session_id,
        }

    @pytest.mark.parametrize("is_sub_factor", [0, 1])
    def test_valid_factor_type_flag_is_persisted(
        self,
        is_sub_factor: int,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """分别以母因子和子因子类型标识提交，并核对请求标识原样保存在表单配置中。"""

        parent = factor_combo_repository.find_parent_with_sub_factors()
        choices = factor_combo_repository.find_sub_factor_pair()
        assert parent is not None and choices is not None, "测试数据库需要可用母因子和子因子"
        names = [parent.factor_name] if is_sub_factor == 0 else [choice.sub_factor_name for choice in choices]
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(session_id, names, is_sub_factor=is_sub_factor)

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 202, body
        submitted = factor_combo_service.require_submitted_form(response, session_id)
        form_row = factor_combo_repository.get_form(submitted.form_id)
        assert form_row is not None, {"api": body, "db": form_row}
        assert form_row["form_json"]["is_sub_factor"] == is_sub_factor, {
            "api": body,
            "request": payload,
            "db": form_row,
        }

    def test_duplicate_factor_names_are_rejected_case_and_space_insensitively(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """重复提交同一子因子名称的大小写和首尾空格变体，并验证不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choices[0].sub_factor_name, f"  {choices[0].sub_factor_name.upper()}  "],
        )
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    def test_identical_form_replay_returns_existing_form_without_duplicate_pool_members(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """完全相同的表单请求重放时，验证接口复用表单、因子池和成员记录。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            notes="autotest identical form replay",
        )

        first_response = factor_combo_service.submit_form(payload)
        first_body = read_json(first_response)
        replay_response = factor_combo_service.submit_form(deepcopy(payload))
        replay_body = read_json(replay_response)

        assert first_response.status_code == 202, first_body
        assert replay_response.status_code == 202, replay_body
        assert first_body.get("success") is True, first_body
        assert replay_body.get("success") is True, replay_body
        assert replay_body.get("data") == first_body.get("data"), {
            "first": first_body,
            "replay": replay_body,
        }
        submitted = factor_combo_service.require_submitted_form(first_response, session_id)
        member_rows = factor_combo_repository.get_pool_members(submitted.form_id)
        assert factor_combo_repository.count_forms_for_session(session_id) == 1, {
            "first": first_body,
            "replay": replay_body,
        }
        assert [int(row["sub_factor_id"]) for row in member_rows] == [
            choice.sub_factor_id for choice in choices
        ], {
            "first": first_body,
            "replay": replay_body,
            "db": member_rows,
        }

    def test_blank_factor_name_is_rejected_without_persistence(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交空白因子名称，并验证接口拒绝且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        payload["factors_name"] = ["   "]
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    def test_single_sub_factor_is_rejected_after_resolution_without_persistence(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """只提交一个真实子因子，并验证展开去重后不足两个子因子时不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(session_id, [choices[0].sub_factor_name])
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        error_message = str(body.get("error", "")).lower()
        assert "factor" in error_message and "2" in error_message, body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    def test_nonexistent_factor_name_is_rejected_without_persistence(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交不存在的因子名称，并验证接口返回语义错误且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        missing_name = "__questtest_missing_factor_name__"
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choices[0].sub_factor_name, missing_name],
        )
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        error_message = str(body.get("error", "")).lower()
        assert missing_name.lower() in error_message and "exist" in error_message, body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    def test_supported_method_groups_are_persisted_without_shape_loss(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交文档支持的规则方法分组，并验证接口与数据库保留完整配置。"""

        method_groups = {"rule_methods": ["equal_weight", "ic_weight", "pca"]}
        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            method_groups=method_groups,
        )

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 202, body
        assert body.get("success") is True, body
        submitted = factor_combo_service.require_submitted_form(response, session_id)
        form_row = factor_combo_repository.get_form(submitted.form_id)
        pool_row = factor_combo_repository.get_pool(submitted.pool_id)
        member_rows = factor_combo_repository.get_pool_members(submitted.form_id)
        assert form_row is not None and pool_row is not None, {
            "api": body,
            "form": form_row,
            "pool": pool_row,
        }
        factor_combo_service.validate_submitted_form_persistence(
            body["data"],
            payload,
            submitted,
            form_row,
            pool_row,
            member_rows,
        )
        assert form_row["form_json"]["method_groups"] == method_groups, {
            "api": body,
            "request": method_groups,
            "db": form_row,
        }

    @pytest.mark.parametrize(
        ("objectives", "expected_status"),
        [
            ([{"code": "ts-score", "priority": 1}, {"code": "sharpe", "priority": 1}], 422),
            ([{"code": "ts-score", "priority": 0}], 422),
            ([{"code": "unknown-objective", "priority": 1}], 422),
        ],
    )
    def test_invalid_objectives_are_rejected_without_persistence(
        self,
        objectives: list[dict[str, Any]],
        expected_status: int,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交重复、非正数或未定义目标，并验证不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            objectives=objectives,
        )
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
            "after_count": factor_combo_repository.count_forms_for_session(session_id),
        }

    def test_cross_sectional_objectives_are_persisted(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交截面预测指标和经济代理指标，并验证目标和优先级完整持久化。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        objectives = [
            {"code": "sharpe", "priority": 2},
            {"code": "cs-ic-spearman", "priority": 1},
        ]
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            objectives=objectives,
        )

        response = factor_combo_service.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 202, body
        submitted = factor_combo_service.require_submitted_form(response, session_id)
        form_row = factor_combo_repository.get_form(submitted.form_id)
        assert form_row is not None, {"api": body, "db": form_row}
        assert form_row["form_json"]["configuration_parameters"]["objectives"] == [
            {"code": "cs-ic-spearman", "priority": 1},
            {"code": "sharpe", "priority": 2},
        ], {
            "api": body,
            "db": form_row,
        }
        pool_row = factor_combo_repository.get_pool(submitted.pool_id)
        member_rows = factor_combo_repository.get_pool_members(submitted.form_id)
        assert pool_row is not None, {"api": body, "db": pool_row}
        factor_combo_service.validate_submitted_form_persistence(
            body["data"],
            payload,
            submitted,
            form_row,
            pool_row,
            member_rows,
        )

    def test_configuration_parameter_boundaries_are_persisted(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交新版滚动窗口、相关性惩罚、交易成本和参数寻优开关，并核对数据库原始配置。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            configuration_overrides={
                "rolling_window": "6m",
                "correlation_penalty": 0.25,
                "transaction_cost": 0.001,
                "optimize_subfactor_params": True,
            },
        )

        submit_response = factor_combo_service.submit_form(payload)
        submit_body = read_json(submit_response)
        assert submit_response.status_code == 202, submit_body
        submitted = factor_combo_service.require_submitted_form(submit_response, session_id)
        form_row = factor_combo_repository.get_form(submitted.form_id)
        assert form_row is not None, {"api": submit_body, "db": form_row}
        stored_config = form_row["form_json"]["configuration_parameters"]
        assert stored_config["rolling_window"] == "6m", {
            "api": submit_body,
            "db": form_row,
        }
        assert float(stored_config["correlation_penalty"]) == pytest.approx(0.25), {
            "api": submit_body,
            "db": form_row,
        }
        assert float(stored_config["transaction_cost"]) == pytest.approx(0.001), {
            "api": submit_body,
            "db": form_row,
        }
        assert stored_config["optimize_subfactor_params"] is True, {"api": submit_body, "db": form_row}

    def test_unauthenticated_form_submission_is_rejected_without_persistence(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_unauthenticated_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """不携带登录 Token 提交合法表单，并验证返回 401 且数据库没有新增表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_unauthenticated_api.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    def test_authenticated_non_owner_cannot_submit_form_to_another_users_session(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_non_owner_api: FactorComboAPI,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """使用另一个已登录账号向当前账号会话提交表单，并验证所有权隔离且不产生持久化数据。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session("autotest-submit-owned-session")
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_non_owner_api.submit_form(payload)
        body = read_json(response)

        assert response.status_code == 404, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }
