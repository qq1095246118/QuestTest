"""提交组合因子研究表单接口测试。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from api.factor_combo_api import FactorComboAPI
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService


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
        body = response.json()

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
        body = response.json()

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

    def test_submit_mixed_parent_and_child_expands_and_deduplicates(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """在同一请求中混用母因子和其子因子，并验证展开结果去重后完整写入因子池。"""

        parent = factor_combo_repository.find_parent_with_sub_factors()
        assert parent is not None, "测试数据库需要至少一个拥有两个关联子因子的母因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [parent.factor_name, f"  {parent.sub_factors[0].sub_factor_name}  "],
            is_sub_factor=0,
        )

        response = factor_combo_service.submit_form(payload)
        body = response.json()

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
        assert len(actual_ids) == len(set(actual_ids)), {"api": body, "db": member_rows}

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
        body = response.json()

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
        first_body = first_response.json()
        replay_response = factor_combo_service.submit_form(deepcopy(payload))
        replay_body = replay_response.json()

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

    def test_normalized_form_replay_returns_existing_form(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """因子名和窗口仅增加空格且目标顺序变化时，验证规范化哈希仍复用原表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            objectives=[
                {"code": "sharpe", "priority": 2},
                {"code": "ts-score", "priority": 1},
            ],
            notes="autotest normalized form replay",
        )
        normalized_replay = deepcopy(payload)
        normalized_replay["factors_name"] = [f"  {name}  " for name in payload["factors_name"]]
        normalized_replay["configuration_parameters"]["rolling_window"] = "  12m  "
        normalized_replay["configuration_parameters"]["objectives"] = [
            {"code": "ts-score", "priority": 1},
            {"code": "sharpe", "priority": 2},
        ]

        first_response = factor_combo_service.submit_form(payload)
        first_body = first_response.json()
        replay_response = factor_combo_service.submit_form(normalized_replay)
        replay_body = replay_response.json()

        assert first_response.status_code == 202, first_body
        assert replay_response.status_code == 202, replay_body
        assert first_body.get("success") is True, first_body
        assert replay_body.get("success") is True, replay_body
        first_data = first_body.get("data")
        replay_data = replay_body.get("data")
        assert isinstance(first_data, dict) and isinstance(replay_data, dict), {
            "first": first_body,
            "replay": replay_body,
        }
        assert replay_data.get("form_id") == first_data.get("form_id"), {
            "first": first_body,
            "replay": replay_body,
        }
        assert replay_data.get("factor_combo_pool_id") == first_data.get("factor_combo_pool_id"), {
            "first": first_body,
            "replay": replay_body,
        }
        assert factor_combo_repository.count_forms_for_session(session_id) == 1, {
            "first": first_body,
            "replay": replay_body,
        }

    @pytest.mark.parametrize(
        ("case_name", "is_sub_factor", "expected_status"),
        [
            ("missing", "__missing__", 422),
            ("null", None, 422),
            ("negative", -1, 422),
            ("unsupported", 2, 422),
            ("string", "1", 400),
        ],
    )
    def test_invalid_factor_type_is_rejected_without_persistence(
        self,
        case_name: str,
        is_sub_factor: Any,
        expected_status: int,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交缺失、越界或类型错误的因子类型标识，并验证接口拒绝且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        if case_name == "missing":
            payload.pop("is_sub_factor")
        else:
            payload["is_sub_factor"] = is_sub_factor
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    @pytest.mark.parametrize(
        ("case_name", "factor_names", "expected_status"),
        [
            ("missing", "__missing__", 422),
            ("null", None, 422),
            ("empty_array", [], 422),
            ("string_instead_of_array", "factor-name", 400),
            ("non_string_item", [123], 400),
            ("blank_name", ["   "], 422),
            ("name_over_255_characters", ["x" * 256], 422),
        ],
    )
    def test_invalid_factor_name_collection_is_rejected_without_persistence(
        self,
        case_name: str,
        factor_names: Any,
        expected_status: int,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交缺失、空、类型错误或名称长度越界的因子数组，并验证不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        if case_name == "missing":
            payload.pop("factors_name")
        else:
            payload["factors_name"] = factor_names
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == expected_status, body
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
        body = response.json()

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
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        error_message = str(body.get("error", "")).lower()
        assert missing_name.lower() in error_message and "exist" in error_message, body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    @pytest.mark.parametrize(
        ("case_name", "session_value", "expected_status"),
        [
            ("missing", "__missing__", 422),
            ("null", None, 422),
            ("zero", 0, 422),
            ("negative", -1, 400),
            ("string", "10001", 400),
            ("not_found", 9_999_999_999, 404),
        ],
    )
    def test_invalid_session_does_not_create_form(
        self,
        case_name: str,
        session_value: Any,
        expected_status: int,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交缺失、类型错误或不存在的会话，并验证接口拒绝且不会产生表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        valid_session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            valid_session_id,
            [choice.sub_factor_name for choice in choices],
        )
        if case_name == "missing":
            payload.pop("session_id")
        else:
            payload["session_id"] = session_value
        before_count = factor_combo_repository.count_forms_for_session(valid_session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(valid_session_id) == before_count, {
            "api": body,
            "before_count": before_count,
            "after_count": factor_combo_repository.count_forms_for_session(valid_session_id),
        }

    @pytest.mark.parametrize(
        "method_groups",
        [
            {},
            {"unknown_group": ["custom_method", "custom_method"]},
            ["ridge", "lasso"],
            "custom-method-config",
            123,
            True,
            None,
        ],
    )
    def test_method_groups_json_values_are_persisted_without_shape_guessing(
        self,
        method_groups: Any,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交合法 JSON 的不同 method_groups 形态，并验证接口原样持久化而不由测试猜测结构。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            method_groups=method_groups,
        )

        response = factor_combo_service.submit_form(payload)
        body = response.json()

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

    def test_missing_method_groups_is_rejected_without_persistence(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """省略 method_groups 字段，并验证接口按新版契约拒绝请求且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        payload.pop("method_groups")
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert "method_groups" in str(body.get("error", "")), body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    @pytest.mark.parametrize(
        ("objectives", "expected_status"),
        [
            ([{"code": "ts-score", "priority": 1}, {"code": "sharpe", "priority": 1}], 422),
            ([{"code": "ts-score", "priority": 0}], 422),
            ([{"code": "ts-score", "priority": -1}], 422),
            ([{"code": "ts-score", "priority": "1"}], 400),
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
        """提交重复、非正数、类型错误的优先级或未定义目标，并验证不创建表单。"""

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
        body = response.json()

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
        body = response.json()

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
                "rolling_window": "custom-window-32-character-1234",
                "correlation_penalty": -9999.9999,
                "transaction_cost": 0.12345,
                "optimize_subfactor_params": True,
            },
        )

        submit_response = factor_combo_service.submit_form(payload)
        submit_body = submit_response.json()
        assert submit_response.status_code == 202, submit_body
        submitted = factor_combo_service.require_submitted_form(submit_response, session_id)
        form_row = factor_combo_repository.get_form(submitted.form_id)
        assert form_row is not None, {"api": submit_body, "db": form_row}
        stored_config = form_row["form_json"]["configuration_parameters"]
        assert stored_config["rolling_window"] == "custom-window-32-character-1234", {
            "api": submit_body,
            "db": form_row,
        }
        assert float(stored_config["correlation_penalty"]) == pytest.approx(-9999.9999), {
            "api": submit_body,
            "db": form_row,
        }
        assert float(stored_config["transaction_cost"]) == pytest.approx(0.12345), {
            "api": submit_body,
            "db": form_row,
        }
        assert stored_config["optimize_subfactor_params"] is True, {"api": submit_body, "db": form_row}

    @pytest.mark.parametrize(
        ("case_name", "configuration_parameters", "expected_status"),
        [
            ("missing", "__missing__", 422),
            ("null", None, 422),
            ("array", [], 400),
            ("string", "invalid-configuration", 400),
        ],
    )
    def test_invalid_configuration_container_is_rejected_without_persistence(
        self,
        case_name: str,
        configuration_parameters: Any,
        expected_status: int,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交缺失、空值或非对象的研究配置，并验证接口拒绝且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        if case_name == "missing":
            payload.pop("configuration_parameters")
        else:
            payload["configuration_parameters"] = configuration_parameters
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    @pytest.mark.parametrize(
        "missing_field",
        [
            "objectives",
            "rolling_window",
            "correlation_penalty",
            "transaction_cost",
            "optimize_subfactor_params",
        ],
    )
    def test_missing_required_configuration_field_is_rejected_without_persistence(
        self,
        missing_field: str,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """逐一省略研究配置中的必填字段，并验证接口拒绝且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        payload["configuration_parameters"].pop(missing_field)
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    @pytest.mark.parametrize(
        ("field_name", "invalid_value", "expected_status"),
        [
            ("objectives", [], 422),
            ("objectives", "ts-score", 400),
            ("rolling_window", 12, 400),
            ("correlation_penalty", "0.1", 400),
            ("transaction_cost", "0.001", 400),
            ("optimize_subfactor_params", 1, 400),
        ],
    )
    def test_invalid_configuration_field_type_is_rejected_without_persistence(
        self,
        field_name: str,
        invalid_value: Any,
        expected_status: int,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交空目标或类型不符合文档的研究配置字段，并验证接口拒绝且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            configuration_overrides={field_name: invalid_value},
        )
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    @pytest.mark.parametrize(
        ("field_name", "invalid_value"),
        [
            ("rolling_window", ""),
            ("rolling_window", "   "),
            ("rolling_window", "x" * 33),
            ("correlation_penalty", -10000),
            ("correlation_penalty", 10000),
            ("correlation_penalty", 0.12345),
            ("transaction_cost", -0.00001),
            ("transaction_cost", 0.123456),
            ("transaction_cost", 100_000_000_000),
        ],
    )
    def test_configuration_parameter_boundaries_are_rejected(
        self,
        field_name: str,
        invalid_value: object,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交超出新版边界的配置参数，并验证接口拒绝且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            configuration_overrides={field_name: invalid_value},
        )
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == 422, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    def test_notes_at_500_characters_is_persisted(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交正好 500 个字符的补充说明，并验证接口接受且数据库原样保存。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        notes = "n" * 500
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            notes=notes,
        )

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == 202, body
        assert body.get("success") is True, body
        submitted = factor_combo_service.require_submitted_form(response, session_id)
        form_row = factor_combo_repository.get_form(submitted.form_id)
        assert form_row is not None, {"api": body, "db": form_row}
        assert form_row["form_json"]["notes"] == notes, {"api": body, "db": form_row}

    def test_omitted_notes_is_accepted(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """省略可选的补充说明，并验证接口接受且对应表单成功持久化。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        payload.pop("notes")

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == 202, body
        assert body.get("success") is True, body
        submitted = factor_combo_service.require_submitted_form(response, session_id)
        form_row = factor_combo_repository.get_form(submitted.form_id)
        assert form_row is not None, {"api": body, "db": form_row}
        assert int(form_row["session_id"]) == session_id, {"api": body, "db": form_row}

    @pytest.mark.parametrize(
        ("notes", "expected_status"),
        [
            ("n" * 501, 422),
            (123, 400),
        ],
    )
    def test_invalid_notes_is_rejected_without_persistence(
        self,
        notes: Any,
        expected_status: int,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交长度超过 500 或类型错误的补充说明，并验证接口拒绝且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        payload["notes"] = notes
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == expected_status, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }

    def test_unknown_top_level_field_is_rejected(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交未知顶层字段，并验证接口返回格式错误且不创建表单。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
        )
        payload = deepcopy(payload)
        payload["research_type"] = "machine_learning"
        before_count = factor_combo_repository.count_forms_for_session(session_id)

        response = factor_combo_service.submit_form(payload)
        body = response.json()

        assert response.status_code == 400, body
        assert body.get("success") is False, body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
            "after_count": factor_combo_repository.count_forms_for_session(session_id),
        }

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
        body = response.json()

        assert response.status_code == 401, body
        assert body.get("success") is False, body
        assert isinstance(body.get("error"), str) and body["error"], body
        assert factor_combo_repository.count_forms_for_session(session_id) == before_count, {
            "api": body,
            "before_count": before_count,
        }
