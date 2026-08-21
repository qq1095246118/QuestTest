"""提交组合因子研究表单接口测试。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

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

    def test_submit_parent_factor_uses_ranked_top_twelve_children(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交一个母因子，并验证锁定池按最新时序评分选取最多十二个子因子。"""

        parent = factor_combo_repository.find_ranked_parent_with_sub_factors()
        assert parent is not None, "测试数据库需要至少一个拥有两个有效评分子因子的母因子"
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
        assert actual_ids == expected_ids, {"api": body, "expected_sub_factor_ids": expected_ids, "db": member_rows}
        assert 2 <= len(actual_ids) <= 12, {"api": body, "db": member_rows}
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

    def test_submit_mixed_parent_and_child_is_rejected_without_persistence(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """在同一请求中混用母因子和子因子，并验证类型约束拒绝且不产生表单。"""

        parent = factor_combo_repository.find_ranked_parent_with_sub_factors()
        assert parent is not None, "测试数据库需要至少一个拥有两个有效评分子因子的母因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [parent.factor_name, parent.sub_factors[0].sub_factor_name],
            is_sub_factor=0,
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
        """提交重复、非正数、类型错误或未定义目标，并验证不创建表单。"""

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
            {"code": "cs-ic-spearman", "priority": 1},
            {"code": "sharpe", "priority": 2},
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
        assert form_row["form_json"]["configuration_parameters"]["objectives"] == objectives, {
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

    def test_cycle_parameters_and_transaction_cost_match_work_order(
        self,
        factor_combo_service: FactorComboService,
        factor_combo_repository: FactorComboRepository,
    ) -> None:
        """提交周期与高精度交易成本，并验证工作单采用规范化后的同一组参数。"""

        choices = factor_combo_repository.find_sub_factor_pair()
        assert choices is not None, "测试数据库至少需要两个可用子因子"
        session_id = factor_combo_service.create_session()
        payload = factor_combo_service.build_form_payload(
            session_id,
            [choice.sub_factor_name for choice in choices],
            configuration_overrides={
                "combo_bar_interval": "4h",
                "return_bar_interval": "1d",
                "forward_return_bars": 3,
                "transaction_cost": 0.123456789,
            },
        )

        submit_response = factor_combo_service.submit_form(payload)
        submit_body = submit_response.json()
        assert submit_response.status_code == 202, submit_body
        submitted = factor_combo_service.require_submitted_form(submit_response, session_id)
        work_order_response = factor_combo_service.get_work_order_request(submitted.form_id)
        work_order_body = work_order_response.json()

        assert work_order_response.status_code == 200, work_order_body
        work_order = work_order_body.get("data")
        assert isinstance(work_order, dict), work_order_body
        data_spec = work_order.get("data_spec")
        assert isinstance(data_spec, dict), work_order_body
        assert data_spec.get("combo_bar_interval") == "4h", work_order_body
        assert data_spec.get("return_bar_interval") == "1d", work_order_body
        assert data_spec.get("forward_return_bars") == 3, work_order_body
        form_row = factor_combo_repository.get_form(submitted.form_id)
        assert form_row is not None, {"api": work_order_body, "db": form_row}
        stored_config = form_row["form_json"]["configuration_parameters"]
        assert float(stored_config["transaction_cost"]) == pytest.approx(0.12345), {
            "api": work_order_body,
            "db": form_row,
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
