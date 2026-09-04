"""组合因子接口响应与数据库持久化结果对账。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from service.factor_combo_models import FactorComboFlowError, FlowOutcome, SubmittedForm
from service.factor_combo_refresh import _DATETIME_IDENTITY_FIELDS, _REFRESH_RESPONSE_STATUSES


# Run 标识由 Agent/IC 刷新服务生成，虽然字段名以 ``_id`` 结尾，但不是数据库自增整数。
_STRING_RUN_ID_FIELDS = {"run_id"}


_WORK_ORDER_REQUIRED_FIELDS = (
    "form_id",
    "form_no",
    "form_status",
    "factor_combo_pool_id",
    "pool_snapshot_hash",
    "form_json",
    "data_spec",
    "pool_members",
)
_WORK_ORDER_SPEC_REQUIRED_FIELDS = (
    "symbol",
    "interval",
    "combo_bar_interval",
    "return_bar_interval",
    "forward_return_bars",
    "alignment_policy",
    "source_availability_rule",
)
_WORK_ORDER_MEMBER_REQUIRED_FIELDS = (
    "component_id",
    "factor_id",
    "sub_factor_id",
    "factor_code",
    "sub_factor_code",
    "name",
    "feature_column",
    "factor_bar_interval",
    "direction",
)
_VERSION_RESULT_REQUIRED_FIELDS = (
    "form_id",
    "form_status",
    "pipeline_run_id",
    "factor_combo_version_id",
    "combo_id",
    "combo_family_key",
    "pool_id",
    "combo_version_hash",
    "combo_status",
    "component_count",
    "idempotent_replay",
)
_NEXT_VERSION_RESULT_REQUIRED_FIELDS = (
    *_VERSION_RESULT_REQUIRED_FIELDS,
    "feedback_id",
    "feedback_round",
    "feedback_status",
)
_EXPERIMENT_RESULT_REQUIRED_FIELDS = (
    "experiment_info_id",
    "experiment_id",
    "form_id",
    "factor_combo_version_id",
    "combo_id",
    "form_status",
    "combo_status",
    "idempotent_replay",
)
_FEEDBACK_RESULT_REQUIRED_FIELDS = (
    "feedback_recorded",
    "idempotent_replay",
    "feedback_id",
    "feedback_round",
    "feedback_status",
    "reply",
    "form_id",
    "form_status",
    "factor_combo_experiment_info_id",
    "rejected_factor_combo_version_id",
    "experiment_valid",
)
_REGISTRATION_RESULT_REQUIRED_FIELDS = (
    "registered",
    "idempotent_replay",
    "factor_combo_version_id",
    "combo_id",
    "combo_version_hash",
    "sub_factor_id",
    "factor_detail_id",
    "registration_id",
    "factor_validity_status_id",
    "sub_factor_type",
    "refresh_task_id",
    "refresh_status",
    "sub_factor",
    "factor_detail",
    "factor_validity_status",
    "registration",
)

# 登记接口返回的嵌套对象必须使用这些明确的数据库列。这里不把 API 字段名自动当作列名，避免新增字段
# 恰好与另一张表的同名字段相等时把错误对账成成功。
_REGISTRATION_SUB_FACTOR_FIELD_MAP: dict[str, str | Sequence[str]] = {
    "id": "id",
    "serial_number": "serial_number",
    "serial_prefix": "serial_prefix",
    "sub_factor_name": "sub_factor_name",
    "cn_name": "cn_name",
    "type": "type",
    "window": "window",
    "factor_bar_interval": "factor_bar_interval",
    "formula_summary": "formula_summary",
    "sub_factor_tags": "sub_factor_tags",
    "level": "level",
    "max_level": "max_level",
    "child_factor_count": "child_factor_count",
    "mining_method": "mining_method",
    "data_source": "data_source",
    "metadata": "metadata",
    "created_by": "created_by",
    "created_by_uid": "created_by_uid",
    "operator_by": "operator_by",
    "operator_by_uid": "operator_by_uid",
    "created_at": "created_at",
    "updated_at": "updated_at",
}
_REGISTRATION_FACTOR_DETAIL_FIELD_MAP: dict[str, str | Sequence[str]] = {
    "id": "id",
    "factor_id": "factor_id",
    "is_sub_factor_id": ("is_sub_factor_id", "is_sub_factor"),
    "serial_number": "serial_number",
    "name": "name",
    "description": "description",
    "data_source": "data_source",
    "calc_function": "calc_function",
    "calc_logic": "calc_logic",
    "params": "params",
    "explanation": "explanation",
    "update_interval": "update_interval",
    "hit_count": "hit_count",
    "strategy_status": "strategy_status",
    "status": "status",
    "created_at": "created_at",
    "updated_at": "updated_at",
}
_REGISTRATION_VALIDITY_FIELD_MAP: dict[str, str | Sequence[str]] = {
    "id": "id",
    "run_id": "run_id",
    "factor_id": "factor_id",
    "is_sub_factor_id": "is_sub_factor_id",
    "serial_number": "serial_number",
    "universe_key": "universe_key",
    "factor_bar_interval": "factor_bar_interval",
    "factor_window_bars": "factor_window_bars",
    "return_bar_interval": "return_bar_interval",
    "forward_return_bars": "forward_return_bars",
    "window_scope": "window_scope",
    "period_start": "period_start",
    "period_end": "period_end",
    "time_series_scoring_version": "time_series_scoring_version",
    "time_series_score": "time_series_score",
    "time_series_status": "time_series_status",
    "time_series_is_valid": "time_series_is_valid",
    "cross_sectional_scoring_version": "cross_sectional_scoring_version",
    "cross_sectional_score": "cross_sectional_score",
    "cross_sectional_status": "cross_sectional_status",
    "cross_sectional_is_valid": "cross_sectional_is_valid",
    "overall_score": "overall_score",
    "overall_status": "overall_status",
    "overall_is_valid": "overall_is_valid",
    "validity_threshold": "validity_threshold",
    "status_reason_json": ("status_reason_json", "status_reason"),
    "time_series_summary_id": "time_series_summary_id",
    "cross_sectional_summary_id": "cross_sectional_summary_id",
    "created_at": "created_at",
    "updated_at": "updated_at",
}
_REGISTRATION_MAPPING_FIELD_MAP: dict[str, str | Sequence[str]] = {
    "id": "id",
    "combo_id": "combo_id",
    "combo_version_hash": "combo_version_hash",
    "factor_id": "factor_id",
    "sub_factor_id": "sub_factor_id",
    "registered_by": "registered_by",
    "registered_at": "registered_at",
    "created_by": "created_by",
    "created_at": "created_at",
    "updated_by": "updated_by",
    "updated_at": "updated_at",
}


class FactorComboPersistenceMixin:
    """提供组合表单、版本、实验、反馈及登记结果的 API/DB 对账能力。

    宿主 Service 提供通用响应字段校验和标量比较能力；本类只比较接口明确返回或请求明确提交的字段，
    不执行 SQL、不发起 HTTP 请求，也不包含 pytest 断言。
    """

    def validate_submitted_form_persistence(
        self,
        response_data: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        submitted: SubmittedForm,
        form_row: Mapping[str, Any],
        pool_row: Mapping[str, Any],
        member_rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """深度核对表单提交响应、请求参数和三组数据库实体。

        参数 ``response_data`` 是提交接口的 data，``request_payload`` 是原始请求体，``submitted`` 是已解析的表单上下文，
        ``form_row``、``pool_row`` 和 ``member_rows`` 分别是表单、因子池及池成员的完整数据库记录。返回比较诊断；
        缺少实体、请求 JSON 未持久化、ID/状态/会话/成员归属不一致时抛出 ``FactorComboFlowError(FAIL_CONTRACT)``。

        该方法不对接口未返回的成员详情臆造值，但会验证数据库已经保存的完整请求 JSON、池归属和成员身份，避免只用
        ``form_id`` 或成员数量判断提交成功。
        """

        response_fields = self._compare_explicit_fields(
            response_data,
            form_row,
            {
                "form_id": "id",
                "form_no": "form_no",
                "status": "status",
                "factor_combo_pool_id": "factor_combo_pool_id",
            },
            "factor combo form response/database",
            required_fields=("form_id", "form_no", "status", "factor_combo_pool_id"),
            reject_unmapped_api_fields=True,
        )
        if not isinstance(request_payload, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form request payload must be an object",
                request_payload,
            )
        request_session_id = self._required_response_int(
            request_payload,
            "session_id",
            "factor combo form request",
        )
        normalized_request_payload = self._normalize_form_request_for_persistence(request_payload)
        form_identity = self._compare_explicit_fields(
            {"session_id": request_session_id, "form_json": normalized_request_payload},
            form_row,
            {"session_id": "session_id", "form_json": "form_json"},
            "factor combo form request/database",
            required_fields=("session_id", "form_json"),
            # 后端可以在 form_json 中补充兼容性默认配置；请求中明确提供的字段仍逐项严格对账。
            allow_database_json_extra=True,
        )
        if request_session_id != submitted.session_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "submitted form session_id does not match the request context",
                {"request": request_payload, "submitted": submitted, "db": dict(form_row)},
            )
        pool_fields = self._compare_explicit_fields(
            {
                "factor_combo_pool_id": response_data.get("factor_combo_pool_id"),
                "form_id": response_data.get("form_id"),
            },
            pool_row,
            {
                "factor_combo_pool_id": "pool_id",
                "form_id": "factor_combo_form_id",
            },
            "factor combo pool response/database",
            required_fields=("factor_combo_pool_id", "form_id"),
        )
        if not isinstance(member_rows, Sequence) or isinstance(member_rows, (str, bytes)):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo pool members database result must be a sequence",
                member_rows,
            )
        if "factor_combo_pool_id" not in form_row or form_row.get("factor_combo_pool_id") is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form database row is missing factor_combo_pool_id",
                dict(form_row),
            )
        if not self._same_identity_scalar(
            "factor_combo_pool_id",
            form_row.get("factor_combo_pool_id"),
            pool_row.get("pool_id"),
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form and pool point to different pool IDs",
                {"form": dict(form_row), "pool": dict(pool_row)},
            )
        if len(member_rows) < 2:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form persisted fewer than two pool members",
                {"form_id": submitted.form_id, "members": list(member_rows)},
            )
        member_ids: list[int] = []
        sort_orders: list[int] = []
        for row in member_rows:
            member_form_id = self._positive_int_or_failure(
                row.get("factor_combo_form_id"),
                FlowOutcome.FAIL_CONTRACT,
                "factor combo pool member is missing factor_combo_form_id",
                row,
            )
            if member_form_id != submitted.form_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo pool member belongs to another form",
                    {"expected_form_id": submitted.form_id, "member": dict(row)},
                )
            member_id = self._positive_int_or_failure(
                row.get("sub_factor_id"),
                FlowOutcome.FAIL_CONTRACT,
                "factor combo pool member is missing sub_factor_id",
                row,
            )
            if member_id in member_ids:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo pool contains duplicate sub_factor_id",
                    {"sub_factor_id": member_id, "members": list(member_rows)},
                )
            member_ids.append(member_id)
            member_pool_id = self._positive_int_or_failure(
                row.get("pool_id"),
                FlowOutcome.FAIL_CONTRACT,
                "factor combo pool member is missing pool_id",
                row,
            )
            if member_pool_id != submitted.pool_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo pool member belongs to another pool",
                    {"expected_pool_id": submitted.pool_id, "member": dict(row)},
                )
            # 新版表单文档明确规定 pool member.factor_detail_id 当前保持 NULL。只有后端实际写入了详情 ID
            # 时才核对关联；不能把文档规定的 NULL 当成自动化失败。
            detail_id_value = row.get("factor_detail_id")
            detail_alias_value = row.get("factor_detail_record_id")
            detail_factor_value = row.get("factor_detail_factor_id")
            if detail_id_value is not None:
                detail_id = self._positive_int_or_failure(
                    detail_id_value,
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo pool member factor_detail_id must be a positive integer when present",
                    row,
                )
                if detail_alias_value is not None and not self._same_identity_scalar(
                    "factor_detail_id", detail_id, detail_alias_value
                ):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "factor combo pool member detail aliases conflict",
                        {"member": dict(row)},
                    )
                if detail_factor_value is not None and not self._same_identity_scalar(
                    "factor_id", member_id, detail_factor_value
                ):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "factor combo pool member detail points to another sub-factor",
                        {"member": dict(row)},
                    )
            elif detail_alias_value is not None or detail_factor_value is not None:
                # LEFT JOIN 不应在主键为空时返回详情列；这种半残关联仍属于 DB 对账错误。
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo pool member has orphaned factor detail aliases",
                    {"member": dict(row)},
                )
            if "sort_order" not in row or row.get("sort_order") is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo pool member is missing sort_order",
                    {"member": dict(row)},
                )
            sort_order = self._non_negative_int_or_failure(
                row.get("sort_order"),
                FlowOutcome.FAIL_CONTRACT,
                "factor combo pool member sort_order must be a non-negative integer",
                row,
            )
            if sort_order in sort_orders:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo pool contains duplicate sort_order",
                    {"members": list(member_rows)},
                )
            sort_orders.append(sort_order)
            for snapshot_field in (
                "definition_snapshot_json",
                "metrics_snapshot_json",
                "validity_snapshot_json",
            ):
                if snapshot_field in row:
                    self._parse_json_value(row[snapshot_field], f"factor combo pool member.{snapshot_field}")

        expected_sort_orders = list(range(len(member_rows)))
        if sorted(sort_orders) != expected_sort_orders:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo pool member sort_order must be continuous from zero",
                {"sort_orders": sort_orders, "expected_sort_orders": expected_sort_orders},
            )

        filter_field = next(
            (field_name for field_name in ("filter_json", "filter", "factor_filter_json") if field_name in pool_row),
            None,
        )
        if filter_field is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo pool has no persisted filter JSON source",
                {"pool": dict(pool_row), "member_sub_factor_ids": member_ids},
            )
        persisted_filter = self._parse_json_value(pool_row[filter_field], f"factor combo pool.{filter_field}")
        filter_ids = self._extract_sub_factor_ids_from_filter(persisted_filter)
        if filter_ids != member_ids:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo pool filter does not preserve the persisted member identity/order",
                {
                    "filter_field": filter_field,
                    "filter_sub_factor_ids": filter_ids,
                    "member_sub_factor_ids": member_ids,
                    "pool": dict(pool_row),
                },
            )

        request_factor_names = request_payload.get("factors_name")
        if not isinstance(request_factor_names, list) or not request_factor_names:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form request factors_name must be a non-empty array",
                dict(request_payload),
            )
        request_is_sub_factor = request_payload.get("is_sub_factor")
        if request_is_sub_factor not in (0, 1):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form request is_sub_factor must be 0 or 1",
                dict(request_payload),
            )
        # 只有请求名称数量与最终成员数量一致时，才能按“直接子因子逐项对应”核对名称；母因子请求的名称
        # 需要先展开为池成员，不能把请求名称列表直接与成员名称列表比较。
        if request_is_sub_factor == 1 and len(request_factor_names) == len(member_rows):
            persisted_names = [str(row.get("sub_factor_name", "")).strip() for row in member_rows]
            if persisted_names != [str(value).strip() for value in request_factor_names]:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "direct sub-factor request names differ from persisted pool members",
                    {"request_names": request_factor_names, "database_names": persisted_names},
                )
        return {
            "response_fields": tuple(response_fields),
            "form_fields": tuple(form_identity),
            "pool_fields": tuple(pool_fields),
            "member_count": len(member_rows),
            "member_sub_factor_ids": tuple(member_ids),
            "member_sort_orders": tuple(sort_orders),
            "pool_filter_sub_factor_ids": tuple(filter_ids),
        }

    @staticmethod
    def _normalize_form_request_for_persistence(request_payload: Mapping[str, Any]) -> dict[str, Any]:
        """按表单接口文档的规范化规则构造数据库对账基线。

        参数 ``request_payload`` 是原始表单请求体。返回不修改原对象的规范化副本：因子名称和滚动窗口去除首尾
        空格，优化目标按 ``priority`` 升序排列；无法解释优先级时保留原顺序并交由接口负责返回契约错误。该方法
        不补造业务字段，也不执行接口或数据库操作。
        """

        normalized = deepcopy(dict(request_payload))
        factor_names = normalized.get("factors_name")
        if isinstance(factor_names, list):
            normalized["factors_name"] = [
                value.strip() if isinstance(value, str) else value for value in factor_names
            ]
        configuration = normalized.get("configuration_parameters")
        if not isinstance(configuration, Mapping):
            return normalized
        normalized_configuration = dict(configuration)
        rolling_window = normalized_configuration.get("rolling_window")
        if isinstance(rolling_window, str):
            normalized_configuration["rolling_window"] = rolling_window.strip()
        objectives = normalized_configuration.get("objectives")
        if isinstance(objectives, list) and all(isinstance(item, Mapping) for item in objectives):
            try:
                normalized_configuration["objectives"] = sorted(
                    objectives,
                    key=lambda item: Decimal(str(item["priority"])),
                )
            except (KeyError, InvalidOperation, TypeError, ValueError):
                pass
        normalized["configuration_parameters"] = normalized_configuration
        return normalized

    def validate_work_order_persistence(
        self,
        response_data: Mapping[str, Any],
        form_row: Mapping[str, Any],
        pool_row: Mapping[str, Any],
        member_rows: Sequence[Mapping[str, Any]],
        *,
        database_data_spec: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """深度核对 Work Order 的完整返回结构和数据库快照。

        参数 ``response_data`` 是工作单 data，``form_row``、``pool_row`` 和 ``member_rows`` 是对应完整数据库记录；
        ``database_data_spec`` 是数据库或 Repository 明确保存的 data_spec 快照，未提供时方法会尝试从表单记录的
        ``data_spec``/``data_spec_json`` 读取。返回比较诊断；顶层身份、form_json、data_spec、成员字段无法逐项对齐时
        抛出 ``FAIL_CONTRACT``，不会因为能匹配几个子因子 ID 就通过。
        """

        self._require_response_fields(response_data, _WORK_ORDER_REQUIRED_FIELDS, "factor combo work order")
        form_fields = self._compare_explicit_fields(
            response_data,
            form_row,
            {
                "form_id": "id",
                "form_no": "form_no",
                "form_status": "status",
                "factor_combo_pool_id": "factor_combo_pool_id",
                "form_json": "form_json",
            },
            "factor combo work order/form",
            required_fields=("form_id", "form_no", "form_status", "factor_combo_pool_id", "form_json"),
            reject_unmapped_api_fields=True,
            allowed_unmapped_api_fields=(
                "pool_snapshot_hash",
                "data_spec",
                "pool_members",
                "pool_status",
                "pool_member_count",
                "created_at",
                "updated_at",
            ),
        )
        pool_fields = self._compare_explicit_fields(
            {"factor_combo_pool_id": response_data["factor_combo_pool_id"]},
            pool_row,
            {"factor_combo_pool_id": "pool_id"},
            "factor combo work order/pool",
            required_fields=("factor_combo_pool_id",),
        )
        snapshot_hash = response_data["pool_snapshot_hash"]
        pool_hash_fields = self._compare_explicit_fields(
            {"pool_snapshot_hash": snapshot_hash},
            pool_row,
            {"pool_snapshot_hash": ("pool_snapshot_hash", "snapshot_hash", "pool_hash")},
            "factor combo work order pool snapshot",
            required_fields=("pool_snapshot_hash",),
        )
        data_spec = response_data["data_spec"]
        if not isinstance(data_spec, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo work order data_spec must be an object",
                response_data,
            )
        stored_data_spec: Any = database_data_spec
        if stored_data_spec is None:
            for field_name in ("data_spec", "data_spec_json", "work_order_json"):
                if field_name in form_row:
                    stored_data_spec = form_row[field_name]
                    break
        if stored_data_spec is None:
            # 新版接口的 data_spec 是根据表单和因子池动态组装的工作单字段，当前表结构没有独立的
            # 持久化来源。先完成 API 侧结构校验；只有 Repository 明确提供快照时才做 DB 对账，避免
            # 把“数据库没有该字段”错误地报告成后端数据不一致。
            self._validate_work_order_data_spec_shape(data_spec)
            data_spec_fields: list[str] = []
        else:
            parsed_data_spec = self._parse_json_value(stored_data_spec, "factor combo work order data_spec/database")
            if not isinstance(parsed_data_spec, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "persisted factor combo work order data_spec must be an object",
                    {"api": dict(data_spec), "database": stored_data_spec},
                )
            data_spec_fields = self._compare_explicit_fields(
                data_spec,
                parsed_data_spec,
                {field_name: field_name for field_name in data_spec},
                "factor combo work order data_spec/database",
                required_fields=tuple(data_spec.keys()),
            )
        api_members = response_data["pool_members"]
        if not isinstance(api_members, list):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo work order pool_members must be a list",
                response_data,
            )
        member_fields = self._compare_work_order_members(
            api_members,
            member_rows,
            expected_form_id=self._required_response_int(response_data, "form_id", "factor combo work order"),
            expected_pool_id=self._required_response_int(
                response_data,
                "factor_combo_pool_id",
                "factor combo work order",
            ),
        )
        return {
            "form_fields": tuple(form_fields),
            "pool_fields": tuple(pool_fields),
            "pool_snapshot_fields": tuple(pool_hash_fields),
            "data_spec_fields": tuple(data_spec_fields),
            "member_fields": tuple(member_fields),
        }

    def validate_combo_version_persistence(
        self,
        response_data: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        form_row: Mapping[str, Any],
        version_row: Mapping[str, Any],
        component_rows: Sequence[Mapping[str, Any]],
        *,
        feedback_row: Mapping[str, Any] | None = None,
        source_version_row: Mapping[str, Any] | None = None,
        source_component_rows: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """深度核对组合版本响应、请求、版本主表、成分和关联指针。

        参数 ``response_data`` 是初始或下一版本接口 data，``request_payload`` 是原始版本请求体，``form_row``、
        ``version_row`` 和 ``component_rows`` 是完整数据库实体；``feedback_row`` 是下一版本场景的反馈记录，
        ``source_version_row`` 是该反馈对应的上一版本记录，``source_component_rows`` 是上一版本的完整成分记录。
        返回比较诊断；版本字段、表单指针、Feedback 全部下一轮指针、成分身份或 transform/weight 任一明确字段不一致
        时抛出契约异常。下一版本调用必须同时提供来源版本和来源成分，才能证明新旧版本确实属于同一组合且没有复用
        上一版本的完整成分内容。
        """

        is_next_version = feedback_row is not None
        if is_next_version and source_version_row is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo persistence validation requires the source version row",
                {"api": dict(response_data), "feedback": dict(feedback_row or {})},
            )
        if is_next_version and source_component_rows is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo persistence validation requires source component rows",
                {"api": dict(response_data), "feedback": dict(feedback_row or {})},
            )
        required_response_fields = _NEXT_VERSION_RESULT_REQUIRED_FIELDS if is_next_version else _VERSION_RESULT_REQUIRED_FIELDS
        self._require_response_fields(response_data, required_response_fields, "factor combo version")
        allowed_unmapped_api_fields = (
            "form_id",
            "form_status",
            "pipeline_run_id",
            "component_count",
            "idempotent_replay",
        )
        if is_next_version:
            allowed_unmapped_api_fields += ("feedback_id", "feedback_round", "feedback_status")
        response_fields = self._compare_explicit_fields(
            response_data,
            version_row,
            {
                "factor_combo_version_id": "id",
                "combo_id": "combo_id",
                "combo_family_key": "combo_family_key",
                "pool_id": "pool_id",
                "combo_version_hash": "combo_version_hash",
                "combo_status": "status",
            },
            "factor combo version/database",
            required_fields=(
                "factor_combo_version_id",
                "combo_id",
                "combo_family_key",
                "pool_id",
                "combo_version_hash",
                "combo_status",
            ),
            reject_unmapped_api_fields=True,
            allowed_unmapped_api_fields=allowed_unmapped_api_fields,
        )
        form_fields = self._compare_explicit_fields(
            response_data,
            form_row,
            {"form_id": "id", "form_status": "status", "pipeline_run_id": "pipeline_run_id"},
            "factor combo version/form pointer",
            required_fields=("form_id", "form_status", "pipeline_run_id"),
        )
        request_version_field_map: dict[str, str | Sequence[str]] = {
            "generation_method": "generation_method",
        }
        required_request_version_fields = ["generation_method"]
        if not is_next_version:
            request_version_field_map["combo_id"] = "combo_id"
            required_request_version_fields.append("combo_id")
        request_fields = self._compare_explicit_fields(
            request_payload,
            version_row,
            request_version_field_map,
            "factor combo version request/database",
            required_fields=required_request_version_fields,
        )
        request_fields.extend(
            self._compare_explicit_fields(
                request_payload,
                form_row,
                {"pipeline_run_id": "pipeline_run_id"},
                "factor combo version request/form pipeline",
                required_fields=("pipeline_run_id",),
            )
        )
        version_id = self._required_response_int(
            response_data,
            "factor_combo_version_id",
            "factor combo version",
        )
        form_id = self._required_response_int(response_data, "form_id", "factor combo version")
        if not is_next_version and not self._same_identity_scalar(
            "initial_form_id",
            version_row.get("initial_form_id"),
            form_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version initial_form_id does not point to the response form",
                {"api": dict(response_data), "version": dict(version_row), "form": dict(form_row)},
            )
        if "factor_combo_id" not in form_row:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form is missing its concrete version pointer",
                {"api": dict(response_data), "form": dict(form_row)},
            )
        if not self._same_identity_scalar("factor_combo_version_id", form_row.get("factor_combo_id"), version_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form factor_combo_id does not point to the response version",
                {"api": dict(response_data), "version": dict(version_row), "form": dict(form_row)},
            )
        if not self._same_identity_scalar("pool_id", form_row.get("factor_combo_pool_id"), version_row.get("pool_id")):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version and form point to different pools",
                {"api": dict(response_data), "version": dict(version_row), "form": dict(form_row)},
            )
        component_fields = self._compare_component_collections(
            request_payload.get("components"),
            component_rows,
            "factor combo version components",
            expected_combo_id=self._required_response_int(
                response_data,
                "factor_combo_version_id",
                "factor combo version",
            ),
        )
        component_count = self._required_response_int(response_data, "component_count", "factor combo version")
        request_components = request_payload.get("components")
        if not isinstance(request_components, Sequence) or isinstance(request_components, (str, bytes)):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version request components must be an array",
                dict(request_payload),
            )
        if component_count != len(component_rows) or component_count != len(request_components):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version component_count differs from database component count",
                {
                    "api": dict(response_data),
                    "db_component_count": len(component_rows),
                    "request_component_count": len(request_components),
                },
            )
        source_component_fields: tuple[str, ...] = ()
        if is_next_version:
            source_component_fields = self._validate_next_version_component_lineage(
                source_version_row or {},
                source_component_rows or (),
                component_rows,
            )
        if "experiment_id" in version_row and version_row.get("experiment_id") is not None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "new factor combo version unexpectedly points to an experiment",
                {"api": dict(response_data), "version": dict(version_row)},
            )
        feedback_fields: list[str] = []
        if feedback_row is not None:
            feedback_fields = self._compare_explicit_fields(
                {
                    key: response_data[key]
                    for key in ("feedback_id", "feedback_round", "feedback_status")
                },
                feedback_row,
                {
                    "feedback_id": "id",
                    "feedback_round": "feedback_round",
                    "feedback_status": "status",
                },
                "factor combo next version/feedback pointer",
                required_fields=("feedback_id", "feedback_round", "feedback_status"),
            )
            self._validate_next_version_feedback_links(
                response_data,
                feedback_row,
                version_row,
                source_version_row,
                form_row,
            )
        return {
            "response_fields": tuple(response_fields),
            "form_fields": tuple(form_fields),
            "request_fields": tuple(request_fields),
            "component_fields": tuple(component_fields),
            "source_component_fields": source_component_fields,
            "feedback_fields": tuple(feedback_fields),
        }

    @classmethod
    def _validate_next_version_component_lineage(
        cls,
        source_version_row: Mapping[str, Any],
        source_component_rows: Sequence[Mapping[str, Any]],
        new_component_rows: Sequence[Mapping[str, Any]],
    ) -> tuple[str, ...]:
        """核对下一版本的成分内容确实不同于被拒绝的来源版本。

        参数 ``source_version_row`` 是被拒绝版本记录，``source_component_rows`` 和 ``new_component_rows`` 分别是
        来源版本与新版本的完整数据库成分。返回参与比较的字段名；来源版本 ID 缺失、成分不属于对应版本、成分集合
        为空或两版本的完整成分内容完全相同时抛出 ``FAIL_CONTRACT``。仅改变成分顺序不算内容变化，权重、方向或
        transform 的合法变化会形成不同签名。
        """

        source_version_id = cls._required_persisted_int(
            source_version_row,
            ("id",),
            "next factor combo source version",
        )
        # 来源版本的 ID 由来源主表提供；新版本组件归属已由调用方的完整组件对账校验。
        source_signature = cls._component_content_signatures(
            source_component_rows,
            expected_combo_id=source_version_id,
            resource_name="next factor combo source components",
        )
        new_signature = cls._component_content_signatures(
            new_component_rows,
            expected_combo_id=None,
            resource_name="next factor combo new components",
        )
        if source_signature == new_signature:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version reuses the complete source component content",
                {
                    "source_version_id": source_version_id,
                    "source_components": source_signature,
                    "new_components": new_signature,
                },
            )
        return (
            "source_component_factor_id",
            "source_component_sub_factor_id",
            "source_component_direction",
            "source_component_transform",
            "source_component_weight",
        )

    @classmethod
    def _component_content_signatures(
        cls,
        component_rows: Sequence[Mapping[str, Any]],
        *,
        expected_combo_id: int | None,
        resource_name: str,
    ) -> frozenset[tuple[Any, ...]]:
        """把数据库成分规范化为可比较的内容签名集合。

        参数 ``component_rows`` 是一个具体组合版本的成分记录，``expected_combo_id`` 是可选的版本主键，
        ``resource_name`` 用于异常定位。返回不依赖数据库行顺序的签名集合；缺失身份、重复子因子、非法方向、
        transform 或权重时抛出 ``FAIL_CONTRACT``。
        """

        if not isinstance(component_rows, Sequence) or isinstance(component_rows, (str, bytes)):
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, f"{resource_name} must be an array", component_rows)
        signatures: set[tuple[Any, ...]] = set()
        seen_sub_factor_ids: set[int] = set()
        for row in component_rows:
            if not isinstance(row, Mapping):
                raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, f"{resource_name} row must be an object", row)
            if expected_combo_id is not None:
                combo_id = cls._required_persisted_int(
                    row,
                    ("combo_id",),
                    f"{resource_name} combo pointer",
                )
                if combo_id != expected_combo_id:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{resource_name} points to another version",
                        {"expected_combo_id": expected_combo_id, "row": dict(row)},
                    )
            factor_id = cls._required_persisted_int(
                row,
                ("component_factor_id",),
                f"{resource_name} factor identity",
            )
            sub_factor_id = cls._required_persisted_int(
                row,
                ("component_sub_factor_id",),
                f"{resource_name} sub-factor identity",
            )
            if sub_factor_id in seen_sub_factor_ids:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} contains duplicate component_sub_factor_id",
                    component_rows,
                )
            seen_sub_factor_ids.add(sub_factor_id)
            direction_value = row.get("direction")
            if isinstance(direction_value, bool) or direction_value is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} direction must be -1 or 1",
                    row,
                )
            if isinstance(direction_value, Decimal) and direction_value != direction_value.to_integral_value():
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} direction must be -1 or 1",
                    row,
                )
            if isinstance(direction_value, float) and not direction_value.is_integer():
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} direction must be -1 or 1",
                    row,
                )
            if isinstance(direction_value, str) and not re.fullmatch(r"[+-]?[0-9]+", direction_value.strip()):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} direction must be -1 or 1",
                    row,
                )
            try:
                direction = int(direction_value)
            except (TypeError, ValueError, OverflowError) as error:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} direction must be -1 or 1",
                    row,
                ) from error
            if direction not in {-1, 1}:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} direction must be -1 or 1",
                    row,
                )
            transform_value = row.get("transform_json", row.get("transform"))
            if transform_value is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} is missing transform",
                    row,
                )
            transform = cls._parse_json_value(transform_value, f"{resource_name}.transform")
            weight_value = row.get("weight")
            if weight_value is not None and (
                isinstance(weight_value, bool) or cls._coerce_decimal(weight_value) is None
            ):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} weight is not numeric",
                    row,
                )
            normalized_weight = None if weight_value is None else str(cls._coerce_decimal(weight_value))
            normalized_transform = json.dumps(transform, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            signatures.add((factor_id, sub_factor_id, direction, normalized_transform, normalized_weight))
        if not signatures:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} must contain at least one component",
                component_rows,
            )
        return frozenset(signatures)

    def _validate_next_version_feedback_links(
        self,
        response_data: Mapping[str, Any],
        feedback_row: Mapping[str, Any],
        version_row: Mapping[str, Any],
        source_version_row: Mapping[str, Any],
        form_row: Mapping[str, Any],
    ) -> None:
        """核对下一版本与来源版本、Feedback 和表单之间的完整指针图。

        参数 ``response_data`` 是下一版本接口返回的 data，``feedback_row`` 是数据库中的反馈记录，``version_row``
        是新版本记录，``source_version_row`` 是被拒绝的旧版本记录，``form_row`` 是当前表单记录。不返回值；任一
        来源身份、反馈轮次、下一轮指针、表单运行 ID 或新旧版本关系不一致时抛出 ``FAIL_CONTRACT``。
        该校验不根据时间或“最新记录”推断来源，所有关系都必须由明确外键/业务字段证明。
        """

        version_id = self._required_response_int(response_data, "factor_combo_version_id", "next factor combo version")
        source_version_id = self._required_persisted_int(
            source_version_row,
            ("id",),
            "next factor combo source version",
        )
        if version_id == source_version_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version reuses the source version ID",
                {"api": dict(response_data), "source_version": dict(source_version_row)},
            )
        source_hash = self._required_sha256_or_failure(
            source_version_row.get("combo_version_hash"),
            FlowOutcome.FAIL_CONTRACT,
            "next factor combo source version has no valid hash",
            source_version_row,
        )
        new_hash = self._required_sha256_or_failure(
            response_data.get("combo_version_hash"),
            FlowOutcome.FAIL_CONTRACT,
            "next factor combo response has no valid hash",
            response_data,
        )
        if source_hash == new_hash:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version reuses the source content hash",
                {"api": dict(response_data), "source_version": dict(source_version_row)},
            )

        for field_name in ("combo_id", "combo_family_key", "pool_id"):
            if field_name not in source_version_row:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"next factor combo source version is missing {field_name}",
                    source_version_row,
                )
            if not self._same_identity_scalar(
                field_name,
                response_data.get(field_name),
                source_version_row.get(field_name),
            ):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"next factor combo {field_name} differs from source version",
                    {
                        "api": dict(response_data),
                        "source_version": dict(source_version_row),
                        "field": field_name,
                    },
                )

        source_status = str(source_version_row.get("status", "")).strip().lower()
        if source_status != "rejected":
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo source version must be rejected before a next version is created",
                source_version_row,
            )
        required_feedback_fields = (
            "id",
            "form_id",
            "feedback_round",
            "status",
            "source_factor_combo_version_id",
            "source_experiment_info_id",
            "next_factor_combo_version_id",
            "next_pipeline_run_id",
            "next_experiment_info_id",
        )
        missing_feedback_fields = [field for field in required_feedback_fields if field not in feedback_row]
        if missing_feedback_fields:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback row is missing fields required for next-version reconciliation",
                {"missing_fields": missing_feedback_fields, "feedback": dict(feedback_row)},
            )

        expected_feedback_id = self._required_response_int(
            response_data,
            "feedback_id",
            "next factor combo version",
        )
        actual_feedback_id = self._required_persisted_int(
            feedback_row,
            ("id",),
            "next factor combo feedback",
        )
        if expected_feedback_id != actual_feedback_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo response feedback_id does not match the feedback row",
                {"api": dict(response_data), "feedback": dict(feedback_row)},
            )
        if not self._same_identity_scalar(
            "factor_combo_version_id",
            feedback_row.get("source_factor_combo_version_id"),
            source_version_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback source_factor_combo_version_id does not point to the rejected source version",
                {"feedback": dict(feedback_row), "source_version": dict(source_version_row)},
            )
        if not self._same_identity_scalar(
            "factor_combo_experiment_info_id",
            feedback_row.get("source_experiment_info_id"),
            source_version_row.get("experiment_id"),
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback source_experiment_info_id does not point to the source version experiment",
                {"feedback": dict(feedback_row), "source_version": dict(source_version_row)},
            )
        response_form_id = self._required_response_int(response_data, "form_id", "next factor combo version")
        if not self._same_identity_scalar("form_id", feedback_row.get("form_id"), response_form_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback form_id does not match the next-version response form",
                {"api": dict(response_data), "feedback": dict(feedback_row)},
            )
        if not self._same_identity_scalar(
            "factor_combo_version_id",
            feedback_row.get("next_factor_combo_version_id"),
            version_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback next_factor_combo_version_id does not point to the created version",
                {"feedback": dict(feedback_row), "version_id": version_id},
            )
        response_run_id = self._required_response_string(
            response_data,
            "pipeline_run_id",
            "next factor combo version",
        )
        persisted_next_run_id = feedback_row.get("next_pipeline_run_id")
        if not isinstance(persisted_next_run_id, str) or not persisted_next_run_id.strip():
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback next_pipeline_run_id is missing",
                dict(feedback_row),
            )
        if persisted_next_run_id.strip() != response_run_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback next_pipeline_run_id does not match the next-version response",
                {"api": dict(response_data), "feedback": dict(feedback_row)},
            )
        if feedback_row.get("next_experiment_info_id") is not None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback next_experiment_info_id must remain NULL until the next experiment is written",
                dict(feedback_row),
            )
        if "factor_combo_id" not in form_row or not self._same_identity_scalar(
            "factor_combo_version_id",
            form_row.get("factor_combo_id"),
            version_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "form factor_combo_id does not point to the next version",
                {"api": dict(response_data), "form": dict(form_row)},
            )

    def validate_experiment_persistence(
        self,
        response_data: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        experiment_row: Mapping[str, Any],
        form_row: Mapping[str, Any],
        version_row: Mapping[str, Any],
        *,
        expected_experiment_id: str | None = None,
    ) -> dict[str, Any]:
        """深度核对实验响应、请求字段和实验/表单/版本数据库记录。

        参数 ``response_data`` 是实验写入响应 data，``request_payload`` 是完整实验请求，``experiment_row`` 是
        ``factor_combo_experiment_info`` 完整记录，``form_row`` 和 ``version_row`` 是其两个关联实体，
        ``expected_experiment_id`` 是请求路径中的外部实验 ID。返回比较诊断；所有请求到普通列、JSON 列和 Artifact
        列的映射都会被核对，实验、版本和表单三方指针缺失或部分写入会抛出契约异常。SHA256 只按内容值对账，不作为
        唯一性条件。
        """

        self._require_response_fields(response_data, _EXPERIMENT_RESULT_REQUIRED_FIELDS, "factor combo experiment")
        response_experiment_id = self._required_response_string(
            response_data,
            "experiment_id",
            "factor combo experiment",
        )
        if expected_experiment_id is not None and response_experiment_id != str(expected_experiment_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment response experiment_id does not match the request path",
                {"expected_experiment_id": expected_experiment_id, "api": dict(response_data)},
            )
        response_version_id = self._required_response_int(
            response_data,
            "factor_combo_version_id",
            "factor combo experiment",
        )
        response_combo_id = self._required_response_int(response_data, "combo_id", "factor combo experiment")
        response_form_id = self._required_response_int(response_data, "form_id", "factor combo experiment")
        # 新版实验接口响应契约没有要求返回 experiment_valid。有效性以请求 valid 和数据库 valid 为权威；
        # 如果后端额外返回该字段，则把它当作可选扩展字段校验，不能反过来把它设为必填。
        response_valid: bool | None = None
        if "experiment_valid" in response_data:
            response_valid = self._required_response_bool(
                response_data,
                "experiment_valid",
                "factor combo experiment",
            )
        response_fields = self._compare_explicit_fields(
            response_data,
            experiment_row,
            {
                "experiment_info_id": "id",
                "experiment_id": "experiment_id",
            },
            "factor combo experiment response/database",
            # factor_combo_experiment_info 没有独立的 form_id 持久化列；响应中的 form_id 由下面的
            # response/form 对账验证，不能要求实验表伪造该字段。
            required_fields=("experiment_info_id", "experiment_id"),
            reject_unmapped_api_fields=True,
            allowed_unmapped_api_fields=(
                "factor_combo_version_id",
                "combo_id",
                "form_status",
                "combo_status",
                "idempotent_replay",
                "experiment_valid",
                # form_id 由下面的 response/form 对账校验，实验表本身没有该列。
                "form_id",
            ),
        )
        database_business_combo_id = self._required_persisted_int(
            experiment_row,
            ("combo_id",),
            "factor combo experiment database business combo id",
        )
        persisted_version_id = self._required_persisted_int(
            version_row,
            ("id",),
            "factor combo version primary key",
        )
        if database_business_combo_id != response_combo_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment business combo_id differs from API response",
                {
                    "api": dict(response_data),
                    "experiment": dict(experiment_row),
                    "version": dict(version_row),
                },
            )
        if response_version_id != persisted_version_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment version id differs from the version database primary key",
                {
                    "api": dict(response_data),
                    "experiment": dict(experiment_row),
                    "version": dict(version_row),
                },
            )
        version_business_id = self._required_persisted_int(
            version_row,
            ("combo_id",),
            "factor combo version business id",
        )
        if response_combo_id != version_business_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment business combo_id differs from version database record",
                {"api": dict(response_data), "version": dict(version_row)},
            )
        form_fields = self._compare_explicit_fields(
            response_data,
            form_row,
            {"form_id": "id", "form_status": "status"},
            "factor combo experiment/form pointer",
            required_fields=("form_id", "form_status"),
        )
        if self._required_response_string(response_data, "form_status", "factor combo experiment") != "completed":
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment response form_status must be completed",
                dict(response_data),
            )
        version_fields = self._compare_explicit_fields(
            {"factor_combo_version_id": response_version_id, "combo_id": response_combo_id},
            version_row,
            {"factor_combo_version_id": "id", "combo_id": "combo_id"},
            "factor combo experiment/version pointer",
            required_fields=("factor_combo_version_id", "combo_id"),
        )
        experiment_info_id = self._required_response_int(
            response_data,
            "experiment_info_id",
            "factor combo experiment",
        )
        if not self._same_identity_scalar("id", version_row.get("experiment_id"), experiment_info_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version experiment_id does not point to the persisted experiment",
                {"api": dict(response_data), "version": dict(version_row), "experiment": dict(experiment_row)},
            )
        if not self._same_identity_scalar(
            "factor_combo_experiment_info_id",
            form_row.get("factor_combo_experiment_info_id"),
            experiment_info_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form experiment pointer does not match the persisted experiment",
                {"api": dict(response_data), "form": dict(form_row), "experiment": dict(experiment_row)},
            )
        if not self._same_identity_scalar("factor_combo_version_id", form_row.get("factor_combo_id"), persisted_version_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo form version pointer does not match the persisted version",
                {"api": dict(response_data), "form": dict(form_row), "version": dict(version_row)},
            )
        request_pipeline_run_id = request_payload.get("pipeline_run_id")
        if not isinstance(request_pipeline_run_id, str) or not request_pipeline_run_id.strip():
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment request pipeline_run_id must be a non-empty string",
                dict(request_payload),
            )
        if not self._same_scalar(request_pipeline_run_id.strip(), form_row.get("pipeline_run_id")):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment request pipeline_run_id does not match the form run",
                {"api": dict(response_data), "request": dict(request_payload), "form": dict(form_row)},
            )
        if "valid" not in experiment_row:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment database row is missing valid",
                {"api": dict(response_data), "experiment": dict(experiment_row)},
            )
        request_valid = request_payload.get("valid")
        if type(request_valid) is not bool:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment request valid must be a JSON boolean",
                dict(request_payload),
            )
        if not self._same_persisted_value(request_valid, experiment_row.get("valid"), field_name="valid"):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment request valid differs from database valid",
                {"request": dict(request_payload), "experiment": dict(experiment_row)},
            )
        if response_valid is not None:
            if not self._same_persisted_value(response_valid, experiment_row.get("valid"), field_name="valid"):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo experiment response experiment_valid differs from database valid",
                    {"api": dict(response_data), "experiment": dict(experiment_row)},
                )
            if response_valid is not request_valid:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo experiment response experiment_valid differs from request valid",
                    {"api": dict(response_data), "request": dict(request_payload)},
                )
        failure_reason = request_payload.get("failure_reason")
        if request_valid is False and (not isinstance(failure_reason, str) or not failure_reason.strip()):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "invalid factor combo experiment must include a non-empty failure_reason",
                dict(request_payload),
            )
        if request_valid is True and failure_reason not in (None, ""):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "valid factor combo experiment must not include failure_reason",
                dict(request_payload),
            )
        response_combo_status = self._required_response_string(response_data, "combo_status", "factor combo experiment")
        if not self._same_scalar(response_combo_status, version_row.get("status")):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment combo_status differs from the version status",
                {"api": dict(response_data), "version": dict(version_row)},
            )
        request_fields = self._compare_experiment_request_to_database(request_payload, experiment_row)
        return {
            "response_fields": tuple(response_fields),
            "form_fields": tuple(form_fields),
            "version_fields": tuple(version_fields),
            "request_fields": tuple(request_fields),
        }

    def validate_feedback_persistence(
        self,
        response_data: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        feedback_row: Mapping[str, Any],
        form_row: Mapping[str, Any],
        experiment_row: Mapping[str, Any],
        version_row: Mapping[str, Any],
    ) -> dict[str, Any]:
        """深度核对反馈响应、反馈请求及反馈后状态指针。

        参数 ``response_data`` 是反馈接口 data，``request_payload`` 是原始反馈请求，``feedback_row`` 是反馈历史记录，
        ``form_row``、``experiment_row`` 和 ``version_row`` 是反馈后关联实体。返回比较诊断；反馈正文、reply、实验失效、
        版本拒绝、表单清空和反馈指针任一不一致时抛出契约异常。
        """

        self._require_response_fields(response_data, _FEEDBACK_RESULT_REQUIRED_FIELDS, "factor combo feedback")
        response_reply = self._required_response_int(response_data, "reply", "factor combo feedback")
        request_reply = self._required_response_int(request_payload, "reply", "factor combo feedback request")
        if response_reply != 2 or request_reply != 2 or response_reply != request_reply:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo feedback request and response reply must both equal 2",
                {"response_reply": response_reply, "request_reply": request_reply},
            )
        response_fields = self._compare_explicit_fields(
            response_data,
            feedback_row,
            {
                "feedback_id": "id",
                "feedback_round": "feedback_round",
                "feedback_status": "status",
                "form_id": "form_id",
                "factor_combo_experiment_info_id": "source_experiment_info_id",
                "rejected_factor_combo_version_id": "source_factor_combo_version_id",
            },
            "factor combo feedback response/database",
            required_fields=(
                "feedback_id",
                "feedback_round",
                "feedback_status",
                "reply",
                "form_id",
                "factor_combo_experiment_info_id",
                "rejected_factor_combo_version_id",
            ),
            reject_unmapped_api_fields=True,
            allowed_unmapped_api_fields=(
                "feedback_recorded",
                "idempotent_replay",
                "experiment_valid",
                "form_status",
                "reply",
                "session_id",
                "pipeline_run_id",
                "feedback",
                "failure_reason",
                "source_factor_combo_version_id",
                "next_factor_combo_version_id",
                "next_pipeline_run_id",
                "next_experiment_info_id",
            ),
        )
        request_fields = self._compare_explicit_fields(
            request_payload,
            feedback_row,
            {
                "form_id": "form_id",
                "pipeline_run_id": "source_pipeline_run_id",
                "feedback": "feedback_text",
            },
            "factor combo feedback request/database",
            required_fields=("session_id", "form_id", "pipeline_run_id", "reply", "feedback"),
        )
        response_optional_fields = {
            field_name: response_data[field_name]
            for field_name in (
                "pipeline_run_id",
                "feedback",
                "source_factor_combo_version_id",
                "next_factor_combo_version_id",
                "next_pipeline_run_id",
                "next_experiment_info_id",
            )
            if field_name in response_data
        }
        response_optional_map: dict[str, str | Sequence[str]] = {
            "pipeline_run_id": "source_pipeline_run_id",
            "feedback": "feedback_text",
            "source_factor_combo_version_id": "source_factor_combo_version_id",
            "next_factor_combo_version_id": "next_factor_combo_version_id",
            "next_pipeline_run_id": "next_pipeline_run_id",
            "next_experiment_info_id": "next_experiment_info_id",
        }
        response_optional_fields_result = self._compare_explicit_fields(
            response_optional_fields,
            feedback_row,
            response_optional_map,
            "factor combo feedback response/extended pointers",
        )
        request_session_id = self._required_response_int(
            request_payload,
            "session_id",
            "factor combo feedback request",
        )
        if "session_id" not in form_row or not self._same_identity_scalar(
            "session_id",
            request_session_id,
            form_row.get("session_id"),
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback request session_id does not match the form session",
                {"request": dict(request_payload), "form": dict(form_row)},
            )
        if "session_id" in response_data and not self._same_identity_scalar(
            "session_id",
            response_data.get("session_id"),
            form_row.get("session_id"),
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback response session_id does not match the form session",
                {"api": dict(response_data), "form": dict(form_row)},
            )
        if "failure_reason" in response_data and not self._same_persisted_value(
            response_data.get("failure_reason"),
            experiment_row.get("failure_reason"),
            field_name="failure_reason",
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback response failure_reason differs from the experiment",
                {"api": dict(response_data), "experiment": dict(experiment_row)},
            )
        form_fields = self._compare_explicit_fields(
            response_data,
            form_row,
            {"form_id": "id", "form_status": "status"},
            "factor combo feedback/form pointer",
            required_fields=("form_id", "form_status"),
        )
        experiment_fields = self._compare_explicit_fields(
            {"factor_combo_experiment_info_id": response_data["factor_combo_experiment_info_id"]},
            experiment_row,
            {"factor_combo_experiment_info_id": "id"},
            "factor combo feedback/experiment pointer",
            required_fields=("factor_combo_experiment_info_id",),
        )
        experiment_valid = self._required_response_bool(response_data, "experiment_valid", "factor combo feedback")
        if experiment_valid is not False:
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, "feedback must invalidate the experiment", response_data)
        invalid_fields = self._compare_explicit_fields(
            {"valid": False},
            experiment_row,
            {"valid": "valid"},
            "factor combo feedback/experiment invalidation",
            required_fields=("valid",),
        )
        version_fields = self._compare_explicit_fields(
            {"status": "rejected"},
            version_row,
            {"status": "status"},
            "factor combo feedback/rejected version",
            required_fields=("status",),
        )
        version_id = self._required_persisted_int(
            version_row,
            ("id",),
            "factor combo feedback source version",
        )
        rejected_version_id = self._required_response_int(
            response_data,
            "rejected_factor_combo_version_id",
            "factor combo feedback",
        )
        if rejected_version_id != version_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback rejected_factor_combo_version_id does not match the source version",
                {"api": dict(response_data), "version": dict(version_row)},
            )
        source_experiment_id = self._required_persisted_int(
            version_row,
            ("experiment_id",),
            "factor combo feedback source experiment pointer",
        )
        if not self._same_identity_scalar(
            "factor_combo_experiment_info_id",
            feedback_row.get("source_experiment_info_id"),
            source_experiment_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback row source_experiment_info_id does not point to the source version experiment",
                {"feedback": dict(feedback_row), "version": dict(version_row)},
            )
        if not self._same_identity_scalar(
            "form_id",
            feedback_row.get("form_id"),
            form_row.get("id"),
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback row form_id does not match the persisted form",
                {"feedback": dict(feedback_row), "form": dict(form_row)},
            )
        required_next_pointer_fields = (
            "next_factor_combo_version_id",
            "next_pipeline_run_id",
            "next_experiment_info_id",
        )
        missing_next_pointer_fields = [field for field in required_next_pointer_fields if field not in feedback_row]
        if missing_next_pointer_fields:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback row is missing next-round pointer fields",
                {"missing_fields": missing_next_pointer_fields, "feedback": dict(feedback_row)},
            )
        for field_name in required_next_pointer_fields:
            if feedback_row.get(field_name) is not None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"feedback {field_name} must be NULL before the next version is created",
                    {"feedback": dict(feedback_row), "api": dict(response_data)},
                )
        if "source_factor_combo_version_id" in feedback_row and not self._same_identity_scalar(
            "factor_combo_version_id", feedback_row.get("source_factor_combo_version_id"), version_id
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback source version pointer does not match the rejected version",
                {"feedback": dict(feedback_row), "version": dict(version_row)},
            )
        request_feedback = request_payload.get("feedback")
        if not isinstance(request_feedback, str) or not request_feedback.strip():
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback request text must be a non-empty string",
                dict(request_payload),
            )
        if not self._same_persisted_value(
            request_feedback,
            experiment_row.get("failure_reason"),
            field_name="failure_reason",
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback text was not persisted as experiment failure_reason",
                {
                    "request_feedback": request_feedback,
                    "experiment_failure_reason": experiment_row.get("failure_reason"),
                },
            )
        for pointer_name in ("pipeline_run_id", "factor_combo_id", "factor_combo_experiment_info_id"):
            if pointer_name not in form_row or form_row.get(pointer_name) is not None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo feedback must clear form.{pointer_name}",
                    {"form": dict(form_row), "api": dict(response_data)},
                )
        return {
            "response_fields": tuple(response_fields),
            "request_fields": tuple(request_fields),
            "response_optional_fields": tuple(response_optional_fields_result),
            "form_fields": tuple(form_fields),
            "experiment_fields": tuple(experiment_fields),
            "invalid_fields": tuple(invalid_fields),
            "version_fields": tuple(version_fields),
        }

    def validate_registration_persistence(
        self,
        response_data: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        version_row: Mapping[str, Any],
        sub_factor_row: Mapping[str, Any],
        factor_detail_row: Mapping[str, Any],
        validity_row: Mapping[str, Any],
        registration_row: Mapping[str, Any],
        *,
        form_row: Mapping[str, Any] | None = None,
        experiment_row: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """深度核对登记响应、请求内容和四个完整落库实体。

        参数 ``response_data`` 是登记接口 data，``request_payload`` 是登记请求，``version_row`` 是具体组合版本，后四个
        参数依次是完整的子因子、因子详情、有效性快照和登记映射记录；``form_row`` 和 ``experiment_row`` 是同一登记
        链路的表单和实验记录。返回逐实体比较诊断；响应嵌套对象中的每个明确字段都必须能在对应 DB 记录中找到并保持
        一致，且必须证明表单、实验、版本、登记四者指向同一具体版本。未提供表单或实验行时无法完成深层对账，直接
        抛出契约异常，而不是退化成只校验四个登记资源。
        """

        if form_row is None or experiment_row is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo registration persistence validation requires form and experiment rows",
                {
                    "form_row_provided": form_row is not None,
                    "experiment_row_provided": experiment_row is not None,
                    "api": dict(response_data),
                },
            )
        self._require_response_fields(response_data, _REGISTRATION_RESULT_REQUIRED_FIELDS, "factor combo registration")
        registered = self._required_response_bool(response_data, "registered", "factor combo registration")
        if registered is not True:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo registration response registered must be true",
                dict(response_data),
            )
        self._required_response_bool(response_data, "idempotent_replay", "factor combo registration")
        sub_factor_type = self._required_response_int(response_data, "sub_factor_type", "factor combo registration")
        if sub_factor_type != 1:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo registration sub_factor_type must be 1",
                dict(response_data),
            )
        refresh_status = self._required_response_string(
            response_data,
            "refresh_status",
            "factor combo registration",
        )
        normalized_refresh_status = refresh_status.casefold()
        allowed_refresh_statuses = _REFRESH_RESPONSE_STATUSES | {"not_configured", "submit_failed"}
        if normalized_refresh_status not in allowed_refresh_statuses:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo registration refresh_status is outside the supported enum",
                dict(response_data),
            )
        if response_data.get("refresh_submit_error") is not None and not isinstance(
            response_data.get("refresh_submit_error"), str
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo registration refresh_submit_error must be a string or null",
                dict(response_data),
            )
        self._required_identifier_string_or_failure(
            response_data.get("refresh_task_id"),
            FlowOutcome.FAIL_CONTRACT,
            "factor combo registration refresh_task_id is invalid",
            response_data,
        )
        top_fields = self._compare_explicit_fields(
            response_data,
            version_row,
            {
                "factor_combo_version_id": "id",
                "combo_id": "combo_id",
                "combo_version_hash": "combo_version_hash",
            },
            "factor combo registration/version identity",
            required_fields=("factor_combo_version_id", "combo_id", "combo_version_hash"),
            reject_unmapped_api_fields=True,
            allowed_unmapped_api_fields=(
                "registered",
                "idempotent_replay",
                "sub_factor_id",
                "factor_detail_id",
                "registration_id",
                "factor_validity_status_id",
                "sub_factor_type",
                "refresh_task_id",
                "refresh_status",
                "refresh_submit_error",
                "sub_factor",
                "factor_detail",
                "factor_validity_status",
                "registration",
            ),
        )
        registration_identity_fields = self._compare_explicit_fields(
            response_data,
            registration_row,
            {
                "registration_id": "id",
                "combo_version_hash": "combo_version_hash",
                "sub_factor_id": "sub_factor_id",
            },
            "factor combo registration/database",
            required_fields=("registration_id", "combo_version_hash", "sub_factor_id"),
        )
        factor_detail_id = self._required_response_int(
            response_data,
            "factor_detail_id",
            "factor combo registration",
        )
        validity_status_id = self._required_response_int(
            response_data,
            "factor_validity_status_id",
            "factor combo registration",
        )
        sub_factor_id = self._required_response_int(
            response_data,
            "sub_factor_id",
            "factor combo registration",
        )
        registration_id = self._required_response_int(
            response_data,
            "registration_id",
            "factor combo registration",
        )
        response_combo_id = self._required_response_int(
            response_data,
            "combo_id",
            "factor combo registration",
        )
        response_version_id = self._required_response_int(
            response_data,
            "factor_combo_version_id",
            "factor combo registration",
        )
        if not self._same_identity_scalar("id", factor_detail_row.get("id"), factor_detail_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration factor_detail_id does not match the factor detail row",
                {"api": dict(response_data), "factor_detail": dict(factor_detail_row)},
            )
        if not self._same_identity_scalar("id", validity_row.get("id"), validity_status_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration factor_validity_status_id does not match the validity row",
                {"api": dict(response_data), "validity": dict(validity_row)},
            )
        if not self._same_identity_scalar("id", sub_factor_row.get("id"), sub_factor_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration sub_factor_id does not match the sub-factor row",
                {"api": dict(response_data), "sub_factor": dict(sub_factor_row)},
            )
        if "type" not in sub_factor_row or not self._same_identity_scalar("type", sub_factor_row.get("type"), 1):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered composite sub-factor database type must be 1",
                {"api": dict(response_data), "sub_factor": dict(sub_factor_row)},
            )
        if "is_sub_factor_id" not in factor_detail_row or not self._same_scalar(
            factor_detail_row.get("is_sub_factor_id"), True
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor detail must be marked as a sub-factor",
                {"api": dict(response_data), "factor_detail": dict(factor_detail_row)},
            )
        if "status" not in factor_detail_row or not self._same_identity_scalar(
            "status", factor_detail_row.get("status"), 1
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor detail must have status=1",
                {"api": dict(response_data), "factor_detail": dict(factor_detail_row)},
            )
        if "is_sub_factor_id" not in validity_row or not self._same_scalar(
            validity_row.get("is_sub_factor_id"), True
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered validity status must be marked as a sub-factor",
                {"api": dict(response_data), "validity": dict(validity_row)},
            )
        if not self._same_identity_scalar("combo_id", registration_row.get("combo_id"), response_combo_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration mapping combo_id must match the factor_combo business ID",
                {"api": dict(response_data), "registration": dict(registration_row), "version": dict(version_row)},
            )
        if "status" in version_row and str(version_row.get("status", "")).strip().lower() != "active":
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor combo version must be active",
                {"api": dict(response_data), "version": dict(version_row)},
            )
        self._validate_registration_cross_entity_pointers(
            response_data,
            request_payload,
            version_row,
            form_row,
            experiment_row,
            sub_factor_id=sub_factor_id,
            registration_id=registration_id,
            response_combo_id=response_combo_id,
        )
        for summary_field in ("time_series_summary_id", "cross_sectional_summary_id"):
            if summary_field in validity_row and validity_row.get(summary_field) is not None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"initial registration validity snapshot {summary_field} must be NULL before refresh",
                    {"api": dict(response_data), "validity": dict(validity_row)},
                )
        version_fields = self._compare_explicit_fields(
            {
                "factor_combo_version_id": response_data["factor_combo_version_id"],
                "combo_id": response_data["combo_id"],
                "combo_version_hash": response_data["combo_version_hash"],
            },
            version_row,
            {
                "factor_combo_version_id": "id",
                "combo_id": "combo_id",
                "combo_version_hash": "combo_version_hash",
            },
            "factor combo registration/version",
            required_fields=("factor_combo_version_id", "combo_id", "combo_version_hash"),
        )
        nested_fields: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {
            "sub_factor": (response_data["sub_factor"], sub_factor_row),
            "factor_detail": (response_data["factor_detail"], factor_detail_row),
            "factor_validity_status": (response_data["factor_validity_status"], validity_row),
            "registration": (response_data["registration"], registration_row),
        }
        nested_results: dict[str, tuple[str, ...]] = {}
        for entity_name, (api_entity, db_entity) in nested_fields.items():
            if not isinstance(api_entity, Mapping) or not api_entity:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"registration response {entity_name} must be a non-empty object",
                    response_data,
                )
            if not isinstance(db_entity, Mapping) or not db_entity:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"registration database {entity_name} must be a non-empty object",
                    db_entity,
                )
            aliases_by_entity: dict[str, Mapping[str, str | Sequence[str]]] = {
                "sub_factor": _REGISTRATION_SUB_FACTOR_FIELD_MAP,
                "factor_detail": _REGISTRATION_FACTOR_DETAIL_FIELD_MAP,
                "factor_validity_status": _REGISTRATION_VALIDITY_FIELD_MAP,
                "registration": _REGISTRATION_MAPPING_FIELD_MAP,
            }
            aliases = aliases_by_entity[entity_name]
            nested_results[entity_name] = tuple(
                self._compare_explicit_fields(
                    api_entity,
                    db_entity,
                    aliases,
                    f"factor combo registration/{entity_name}",
                    allow_database_json_extra=entity_name in {"sub_factor", "factor_detail", "factor_validity_status"},
                    reject_unmapped_api_fields=True,
                )
            )
        request_results = self._compare_registration_request_to_database(
            request_payload,
            sub_factor_row,
            factor_detail_row,
            validity_row,
            registration_row,
        )
        return {
            "top_fields": tuple(top_fields),
            "registration_identity_fields": tuple(registration_identity_fields),
            "version_fields": tuple(version_fields),
            "nested_fields": nested_results,
            "request_fields": tuple(request_results),
        }

    def validate_registered_formula_and_sources(
        self,
        report: Mapping[str, Any],
        sub_factor_row: Mapping[str, Any],
        factor_detail_row: Mapping[str, Any],
        version_row: Mapping[str, Any],
        component_rows: Sequence[Mapping[str, Any]],
        source_graph: Mapping[str, Any],
    ) -> dict[str, Any]:
        """核对登记后的公式、组件方向/权重和生成因子的来源关系。

        参数 ``report`` 是真实 Pipeline 返回的 ``factor_combo_report``，``sub_factor_row``、``factor_detail_row`` 和
        ``version_row`` 是登记后从数据库读取的子因子、因子详情和具体组合版本，``component_rows`` 是该版本的全部
        组件，``source_graph`` 是 Repository 返回的登记版本和父级来源关系图。返回公式、组件及来源关系诊断；公式
        未原样落入 ``formula_summary``/``calc_logic``、组件身份或方向/静态权重不一致、模型权重缺少可重放契约，或
        生成子因子无法追溯到本次组合组件时抛出 ``FactorComboFlowError(FAIL_CONTRACT)``。方法只比较真实报告和 DB
        数据，不重新计算公式或指标，也不要求生成子因子的 ``factor_id`` 为空。
        """

        if not isinstance(report, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor combo report must be an object",
                report,
            )
        combo = report.get("combo")
        if not isinstance(combo, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor combo report is missing combo object",
                dict(report),
            )
        formula = combo.get("formula")
        if not isinstance(formula, str) or not formula.strip():
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor combo report is missing a non-empty formula",
                dict(report),
            )

        formula_fields: dict[str, str] = {
            "sub_factors.formula_summary": str(sub_factor_row.get("formula_summary"))
            if sub_factor_row.get("formula_summary") is not None
            else "",
            "factors_details.calc_logic": str(factor_detail_row.get("calc_logic"))
            if factor_detail_row.get("calc_logic") is not None
            else "",
        }
        for field_name, database_formula in formula_fields.items():
            if not database_formula or database_formula != formula:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"registered formula differs at {field_name}",
                    {
                        "report_formula": formula,
                        "database_formula": database_formula,
                        "field": field_name,
                    },
                )

        def read_json_object(value: Any, field_name: str) -> Mapping[str, Any]:
            """读取登记实体中的 JSON 对象并统一报告错误。"""

            parsed = self._parse_json_value(value, field_name)
            if not isinstance(parsed, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{field_name} must be a JSON object",
                    {"field": field_name, "value": value},
                )
            return parsed

        params = read_json_object(factor_detail_row.get("params"), "factors_details.params")
        metadata = read_json_object(sub_factor_row.get("metadata"), "sub_factors.metadata")

        def nested_formula(container: Mapping[str, Any]) -> str | None:
            """从登记报告快照的已知结构中读取公式，不递归猜测任意字段。"""

            candidates: list[Any] = [container.get("formula")]
            combo_value = container.get("combo")
            if isinstance(combo_value, Mapping):
                candidates.append(combo_value.get("formula"))
            report_value = container.get("report")
            if isinstance(report_value, Mapping):
                candidates.append(report_value.get("formula"))
                report_combo = report_value.get("combo")
                if isinstance(report_combo, Mapping):
                    candidates.append(report_combo.get("formula"))
            for candidate in candidates:
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
            return None

        params_formula = nested_formula(params)
        metadata_formula = nested_formula(metadata)
        for field_name, persisted_formula in (
            ("factors_details.params", params_formula),
            ("sub_factors.metadata", metadata_formula),
        ):
            if persisted_formula is None or persisted_formula != formula:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"registered formula snapshot differs at {field_name}",
                    {
                        "report_formula": formula,
                        "database_formula": persisted_formula,
                        "field": field_name,
                    },
                )

        report_components = report.get("components")
        if report_components is None:
            report_components = combo.get("components")
        if not isinstance(report_components, Sequence) or isinstance(report_components, (str, bytes)):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor combo report components must be an array",
                dict(report),
            )
        if not isinstance(component_rows, Sequence) or isinstance(component_rows, (str, bytes)):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor combo database components must be an array",
                component_rows,
            )
        if not report_components or len(report_components) != len(component_rows):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor combo report and database component counts differ",
                {"report_count": len(report_components), "database_count": len(component_rows)},
            )

        def normalized_code(value: Any) -> str:
            """规范化组件编码以兼容 PF-/SF- 展示前缀。"""

            normalized = str(value or "").strip().casefold()
            for prefix in ("pf-", "sf-"):
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix) :]
            return normalized

        def optional_positive_int(value: Any, field_name: str) -> int | None:
            """读取可选组件 ID；值存在但非法时抛出契约异常。"""

            if value is None:
                return None
            try:
                normalized = int(value)
            except (TypeError, ValueError, OverflowError) as error:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{field_name} must be a positive integer when present",
                    value,
                ) from error
            if normalized <= 0:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{field_name} must be a positive integer when present",
                    value,
                )
            return normalized

        unmatched_database = list(range(len(component_rows)))
        component_diagnostics: list[dict[str, Any]] = []
        for report_index, report_component in enumerate(report_components):
            if not isinstance(report_component, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "registered factor combo report component must be an object",
                    report_components,
                )
            report_sub_factor_id = optional_positive_int(
                report_component.get("component_sub_factor_id", report_component.get("sub_factor_id")),
                f"report.components[{report_index}].sub_factor_id",
            )
            report_sub_factor_code = normalized_code(report_component.get("sub_factor_code"))
            report_name = normalized_code(report_component.get("name"))
            candidates: list[int] = []
            for database_index in unmatched_database:
                database_component = component_rows[database_index]
                database_sub_factor_id = optional_positive_int(
                    database_component.get("component_sub_factor_id"),
                    f"database.components[{database_index}].component_sub_factor_id",
                )
                database_codes = {
                    normalized_code(database_component.get("sub_factor_code")),
                    normalized_code(database_component.get("sub_factor_name")),
                    normalized_code(database_component.get("sub_factor_serial_number")),
                }
                if report_sub_factor_id is not None and database_sub_factor_id == report_sub_factor_id:
                    candidates.append(database_index)
                elif report_sub_factor_id is None and (
                    (report_sub_factor_code and report_sub_factor_code in database_codes)
                    or (report_name and report_name in database_codes)
                ):
                    candidates.append(database_index)
            if len(candidates) != 1:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "registered report component cannot be matched to exactly one database component",
                    {
                        "report_index": report_index,
                        "report_component": dict(report_component),
                        "candidate_indexes": candidates,
                        "database_components": [dict(row) for row in component_rows],
                    },
                )
            database_index = candidates[0]
            unmatched_database.remove(database_index)
            database_component = component_rows[database_index]

            report_factor_id = optional_positive_int(
                report_component.get("component_factor_id", report_component.get("factor_id")),
                f"report.components[{report_index}].factor_id",
            )
            database_factor_id = optional_positive_int(
                database_component.get("component_factor_id"),
                f"database.components[{database_index}].component_factor_id",
            )
            if report_factor_id is not None and report_factor_id != database_factor_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "registered report component factor identity differs from database",
                    {"report": dict(report_component), "database": dict(database_component)},
                )

            try:
                report_direction = int(report_component.get("direction"))
                database_direction = int(database_component.get("direction"))
            except (TypeError, ValueError, OverflowError) as error:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "registered component direction is not an integer",
                    {"report": dict(report_component), "database": dict(database_component)},
                ) from error
            if report_direction != database_direction:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "registered component direction differs from database",
                    {"report": dict(report_component), "database": dict(database_component)},
                )

            transform = read_json_object(
                database_component.get("transform_json", database_component.get("transform")),
                f"database.components[{database_index}].transform_json",
            )
            weight_contract = str(transform.get("weight_contract", "static")).strip().casefold()
            database_weight = database_component.get("weight")
            report_weight = report_component.get("weight")
            if database_weight is None:
                if weight_contract != "model_artifact":
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "database component has NULL weight without model_artifact contract",
                        {"report": dict(report_component), "database": dict(database_component)},
                    )
                if report_weight is not None:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "model_artifact component must not expose a static report weight",
                        {"report": dict(report_component), "database": dict(database_component)},
                    )
                if not any(
                    transform.get(field_name) is not None
                    for field_name in ("algorithm", "model_replay", "feature_column", "model_uri", "artifact_uri")
                ):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "model_artifact component lacks algorithm, feature or replay trace",
                        {"report": dict(report_component), "database": dict(database_component)},
                    )
            else:
                database_decimal = self._coerce_decimal(database_weight)
                report_decimal = self._coerce_decimal(report_weight)
                if database_decimal is None or report_decimal is None:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "static component weight must be numeric in both report and database",
                        {"report": dict(report_component), "database": dict(database_component)},
                    )
                if abs(abs(database_decimal) - abs(report_decimal)) > Decimal("0.00000001"):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "static component weight differs from database",
                        {"report": dict(report_component), "database": dict(database_component)},
                    )
            component_diagnostics.append(
                {
                    "report_index": report_index,
                    "database_index": database_index,
                    "sub_factor_id": database_component.get("component_sub_factor_id"),
                    "direction": database_direction,
                    "weight_contract": weight_contract,
                    "weight": database_weight,
                }
            )

        if unmatched_database:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "database contains components absent from the registered report",
                {"unmatched_indexes": unmatched_database, "database_components": [dict(row) for row in component_rows]},
            )

        if not isinstance(source_graph, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered source relation graph must be an object",
                source_graph,
            )
        graph_version = source_graph.get("version")
        if isinstance(graph_version, Mapping):
            for field_name in ("id", "combo_id", "combo_version_hash"):
                if field_name in version_row and field_name in graph_version and not self._same_identity_scalar(
                    field_name,
                    graph_version.get(field_name),
                    version_row.get(field_name),
                ):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"source relation graph version differs at {field_name}",
                        {"version": dict(version_row), "graph_version": dict(graph_version)},
                    )

        registered_sub_factor_id = optional_positive_int(
            sub_factor_row.get("id"),
            "registered sub_factor.id",
        )
        component_factor_ids = {
            optional_positive_int(row.get("component_factor_id"), "database component factor id")
            for row in component_rows
        }
        component_factor_ids.discard(None)
        component_sub_factor_ids = {
            optional_positive_int(row.get("component_sub_factor_id"), "database component sub-factor id")
            for row in component_rows
        }
        component_sub_factor_ids.discard(None)
        valid_parent_factor_relations: list[dict[str, Any]] = []
        for relation in source_graph.get("parent_factor_relations", ()):
            if not isinstance(relation, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "source relation graph parent factor row must be an object",
                    source_graph,
                )
            relation_factor_id = optional_positive_int(relation.get("factor_id"), "source relation factor_id")
            relation_child_id = optional_positive_int(relation.get("sub_factor_id"), "source relation sub_factor_id")
            if relation_child_id != registered_sub_factor_id:
                continue
            if relation_factor_id in component_factor_ids:
                valid_parent_factor_relations.append(dict(relation))

        valid_parent_sub_factor_relations: list[dict[str, Any]] = []
        for relation in source_graph.get("parent_sub_factor_relations", ()):
            if not isinstance(relation, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "source relation graph parent sub-factor row must be an object",
                    source_graph,
                )
            parent_id = optional_positive_int(relation.get("parent_sub_factor_id"), "source parent sub-factor id")
            child_id = optional_positive_int(relation.get("sub_factor_id"), "source child sub-factor id")
            if child_id == registered_sub_factor_id and parent_id in component_sub_factor_ids:
                valid_parent_sub_factor_relations.append(dict(relation))

        if not valid_parent_factor_relations and not valid_parent_sub_factor_relations:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered composite sub-factor has no traceable source relation to this combination",
                {
                    "sub_factor_id": registered_sub_factor_id,
                    "component_factor_ids": sorted(component_factor_ids),
                    "component_sub_factor_ids": sorted(component_sub_factor_ids),
                    "source_graph": dict(source_graph),
                },
            )

        return {
            "formula": formula,
            "formula_snapshots": {
                "sub_factors.formula_summary": formula_fields["sub_factors.formula_summary"],
                "factors_details.calc_logic": formula_fields["factors_details.calc_logic"],
                "factors_details.params": params_formula,
                "sub_factors.metadata": metadata_formula,
            },
            "components": tuple(component_diagnostics),
            "source_relations": {
                "parent_factor": tuple(valid_parent_factor_relations),
                "parent_sub_factor": tuple(valid_parent_sub_factor_relations),
            },
        }

    def _validate_registration_cross_entity_pointers(
        self,
        response_data: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        version_row: Mapping[str, Any],
        form_row: Mapping[str, Any],
        experiment_row: Mapping[str, Any],
        *,
        sub_factor_id: int,
        registration_id: int,
        response_combo_id: int,
    ) -> None:
        """核对登记生成资源与表单、实验和具体版本的跨表关系。

        参数 ``response_data`` 和 ``request_payload`` 是登记接口响应及请求，``version_row``、``form_row``、
        ``experiment_row`` 是同一链路的组合版本、表单和实验记录，后三个关键字参数是登记响应中的规范化身份。
        不返回值；会话、表单、Pipeline Run、版本、实验和登记映射任一指针缺失或不一致时抛出 ``FAIL_CONTRACT``。
        此方法只比较明确持久化关系，不根据时间或其他记录猜测来源。
        """

        form_id = self._required_response_int(response_data, "form_id", "factor combo registration")
        pipeline_run_id = self._required_response_string(
            request_payload,
            "pipeline_run_id",
            "factor combo registration request",
        )
        session_id = self._required_response_int(
            request_payload,
            "session_id",
            "factor combo registration request",
        )
        if not self._same_identity_scalar("id", form_row.get("id"), form_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response form_id does not match the database form",
                {"api": dict(response_data), "form": dict(form_row)},
            )
        for field_name, expected in (
            ("session_id", session_id),
            ("pipeline_run_id", pipeline_run_id),
        ):
            if field_name not in form_row or not self._same_identity_scalar(field_name, form_row.get(field_name), expected):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"registration form {field_name} does not match the request",
                    {"api": dict(response_data), "request": dict(request_payload), "form": dict(form_row)},
                )
        if "factor_combo_id" not in form_row or not self._same_identity_scalar(
            "factor_combo_version_id",
            form_row.get("factor_combo_id"),
            version_row.get("id"),
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration form factor_combo_id does not point to the registered version",
                {"api": dict(response_data), "form": dict(form_row), "version": dict(version_row)},
            )

        version_id = self._required_persisted_int(version_row, ("id",), "registered factor combo version")
        version_combo_id = self._required_persisted_int(
            version_row,
            ("combo_id",),
            "registered factor combo business id",
        )
        if response_combo_id != version_combo_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response combo_id does not match the version business ID",
                {"api": dict(response_data), "version": dict(version_row)},
            )
        if not self._same_identity_scalar(
            "factor_combo_version_id",
            response_data.get("factor_combo_version_id"),
            version_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response version ID does not match the version row",
                {"api": dict(response_data), "version": dict(version_row)},
            )
        version_hash = self._required_sha256_or_failure(
            version_row.get("combo_version_hash"),
            FlowOutcome.FAIL_CONTRACT,
            "registered factor combo version has no valid hash",
            version_row,
        )
        response_hash = self._required_sha256_or_failure(
            response_data.get("combo_version_hash"),
            FlowOutcome.FAIL_CONTRACT,
            "registration response has no valid hash",
            response_data,
        )
        if version_hash != response_hash:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response hash does not match the version row",
                {"api": dict(response_data), "version": dict(version_row)},
            )

        experiment_id = self._required_persisted_int(
            experiment_row,
            ("id",),
            "registered factor combo experiment",
        )
        experiment_combo_id = self._required_persisted_int(
            experiment_row,
            ("combo_id",),
            "registered factor combo experiment business combo ID",
        )
        if experiment_combo_id != version_combo_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered experiment combo_id does not match the registered version business ID",
                {"experiment": dict(experiment_row), "version": dict(version_row)},
            )
        if "experiment_id" in version_row and not self._same_identity_scalar(
            "factor_combo_experiment_info_id",
            version_row.get("experiment_id"),
            experiment_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered version experiment_id does not point to the experiment row",
                {"experiment": dict(experiment_row), "version": dict(version_row)},
            )
        if "factor_combo_experiment_info_id" not in form_row or not self._same_identity_scalar(
            "factor_combo_experiment_info_id",
            form_row.get("factor_combo_experiment_info_id"),
            experiment_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration form experiment pointer does not point to the experiment row",
                {"experiment": dict(experiment_row), "form": dict(form_row)},
            )
        if "valid" not in experiment_row or not self._same_scalar(experiment_row.get("valid"), True):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered experiment must be valid",
                {"experiment": dict(experiment_row), "api": dict(response_data)},
            )
        if "sub_factor_id" in response_data and not self._same_identity_scalar(
            "sub_factor_id",
            response_data.get("sub_factor_id"),
            sub_factor_id,
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration sub_factor_id is inconsistent with the persisted identity",
                {"api": dict(response_data), "sub_factor_id": sub_factor_id},
            )
        nested_registration = response_data.get("registration")
        if isinstance(nested_registration, Mapping) and "id" in nested_registration:
            if not self._same_identity_scalar("registration_id", nested_registration.get("id"), registration_id):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "registration nested ID does not match registration_id",
                    dict(response_data),
                )

    @classmethod
    def _required_persisted_int(
        cls,
        database_row: Mapping[str, Any],
        candidate_fields: Sequence[str],
        resource_name: str,
    ) -> int:
        """从数据库记录中读取必须存在且唯一的正整数身份字段。

        参数 ``database_row`` 是 Repository 返回的一行实体数据，``candidate_fields`` 是同一业务字段在不同
        数据库版本中的候选列名，``resource_name`` 用于错误定位。返回正整数；候选列缺失、值非法或多个非空
        候选值不一致时抛出 ``FactorComboFlowError(FAIL_CONTRACT)``，不会静默选择一个可能错误的 ID。
        """

        if not isinstance(database_row, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} database row must be an object",
                database_row,
            )
        values: list[tuple[str, int]] = []
        for field_name in candidate_fields:
            if field_name not in database_row or database_row[field_name] is None:
                continue
            raw_value = database_row[field_name]
            if isinstance(raw_value, bool):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} field {field_name} must be a positive integer",
                    database_row,
                )
            if isinstance(raw_value, Decimal) and raw_value != raw_value.to_integral_value():
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} field {field_name} must be an integer",
                    database_row,
                )
            if isinstance(raw_value, float) and not raw_value.is_integer():
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} field {field_name} must be an integer",
                    database_row,
                )
            if isinstance(raw_value, str) and not re.fullmatch(r"[+]?[0-9]+", raw_value.strip()):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} field {field_name} must be an integer",
                    database_row,
                )
            try:
                normalized = int(raw_value)
            except (TypeError, ValueError, OverflowError) as error:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} field {field_name} must be an integer",
                    database_row,
                ) from error
            if normalized <= 0:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} field {field_name} must be positive",
                    database_row,
                )
            values.append((field_name, normalized))
        if not values:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} is missing a positive database identity field",
                {"candidate_fields": tuple(candidate_fields), "database_row": dict(database_row)},
            )
        distinct_values = {value for _, value in values}
        if len(distinct_values) != 1:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} has conflicting database identity fields",
                {"values": values, "database_row": dict(database_row)},
            )
        return values[0][1]

    @classmethod
    def _compare_explicit_fields(
        cls,
        api_data: Mapping[str, Any],
        database_row: Mapping[str, Any],
        field_map: Mapping[str, str | Sequence[str]],
        resource_name: str,
        *,
        required_fields: Iterable[str] = (),
        allow_database_json_extra: bool = False,
        reject_unmapped_api_fields: bool = False,
        allowed_unmapped_api_fields: Iterable[str] = (),
    ) -> list[str]:
        """逐字段比较接口明确返回值和数据库持久化值。

        参数 ``api_data`` 是接口返回或请求中的对象，``database_row`` 是对应数据库实体，``field_map`` 将 API 字段
        映射到一个或多个明确的数据库列，``resource_name`` 用于错误定位；``required_fields`` 指定接口侧必须出现
        的字段，``allow_database_json_extra`` 仅允许数据库 JSON 在接口对象之外追加后端审计字段，
        ``reject_unmapped_api_fields`` 表示是否拒绝接口返回但没有数据库映射的字段，``allowed_unmapped_api_fields`` 是
        已由其他专用逻辑核对或明确不落库的字段白名单。返回实际比较的字段名称列表；接口字段缺失、未知字段、数据库
        列缺失、显式 ``null`` 不一致、JSON 非法或值不一致时抛出 ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        if not isinstance(api_data, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} API value must be an object",
                api_data,
            )
        if not isinstance(database_row, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} database value must be an object",
                database_row,
            )
        required = tuple(required_fields)
        missing_api_fields = [field_name for field_name in required if field_name not in api_data]
        if missing_api_fields:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} API response is missing required fields",
                {"missing_fields": missing_api_fields, "api": dict(api_data)},
            )
        if reject_unmapped_api_fields:
            allowed_fields = set(allowed_unmapped_api_fields)
            unmapped_fields = sorted(set(api_data).difference(field_map).difference(allowed_fields))
            if unmapped_fields:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} API contains unmapped fields",
                    {
                        "unmapped_fields": unmapped_fields,
                        "mapped_fields": sorted(field_map),
                        "allowed_unmapped_fields": sorted(allowed_fields),
                        "api": dict(api_data),
                    },
                )

        compared: list[str] = []
        for api_field, mapped_fields in field_map.items():
            if api_field not in api_data:
                continue
            candidates = (mapped_fields,) if isinstance(mapped_fields, str) else tuple(mapped_fields)
            if not candidates:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} has no database mapping for {api_field}",
                    {"api_field": api_field, "api": dict(api_data)},
                )
            present_candidates = [field_name for field_name in candidates if field_name in database_row]
            if not present_candidates:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} database is missing field for API field {api_field}",
                    {
                        "api_field": api_field,
                        "database_candidates": candidates,
                        "database_row": dict(database_row),
                    },
                )

            api_value = api_data[api_field]
            for database_field in present_candidates:
                database_value = database_row[database_field]
                json_field = (
                    isinstance(api_value, (Mapping, list, tuple))
                    or isinstance(database_value, (Mapping, list, tuple))
                    or api_field.endswith("_json")
                    or database_field.endswith("_json")
                    or api_field
                    in {
                        "form_json",
                        "transform",
                        "definition_snapshot",
                        "metrics_snapshot",
                        "validity_snapshot",
                        "params",
                        "status_reason_json",
                    }
                )
                left_value = cls._parse_json_value(api_value, f"{resource_name}.{api_field}") if json_field else api_value
                right_value = (
                    cls._parse_json_value(database_value, f"{resource_name}.{database_field}")
                    if json_field
                    else database_value
                )
                if not cls._same_persisted_value(
                    left_value,
                    right_value,
                    field_name=api_field,
                    allow_database_extra=allow_database_json_extra and json_field,
                ):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{resource_name} differs at {api_field}",
                        {
                            "field": api_field,
                            "database_field": database_field,
                            "api": api_value,
                            "database": database_value,
                        },
                    )
            compared.append(api_field)
        return compared

    @staticmethod
    def _parse_json_value(value: Any, field_name: str) -> Any:
        """解析明确属于 JSON 的数据库字段或接口对象。

        参数 ``value`` 是接口或数据库中的 JSON 值，``field_name`` 用于错误定位。返回 Python 字典、列表或标量；
        已经是结构化对象的值原样返回，字符串和字节串必须是合法 JSON，非法 JSON 抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        if isinstance(value, (Mapping, list, tuple)) or value is None:
            return value
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError as error:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{field_name} database JSON is not UTF-8",
                    {"field": field_name},
                ) from error
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as error:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{field_name} is not valid JSON",
                    {"field": field_name, "value": value},
                ) from error
        return value

    @classmethod
    def _extract_sub_factor_ids_from_filter(cls, value: Any) -> list[int]:
        """从因子池持久化过滤器中提取按顺序保存的子因子 ID。

        参数 ``value`` 是数据库中的 ``filter_json`` 已解析对象。返回过滤器明确包含的正整数子因子 ID 列表；只
        读取名称中明确包含 ``sub_factor_id`` 的键，不根据因子名称或数组位置猜测 ID。遇到非法 ID 时抛出契约异常，
        这样过滤器损坏不会被成员数量断言掩盖。
        """

        found: list[int] = []

        def visit(current: Any) -> None:
            """递归读取过滤器中的显式子因子 ID 字段。"""

            if isinstance(current, Mapping):
                for key, child in current.items():
                    normalized_key = str(key).strip().lower()
                    if "sub_factor_id" in normalized_key:
                        values = child if isinstance(child, (list, tuple)) else [child]
                        for item in values:
                            normalized = cls._required_persisted_int(
                                {"value": item},
                                ("value",),
                                "factor combo pool filter sub_factor_id",
                            )
                            found.append(normalized)
                    elif isinstance(child, (Mapping, list, tuple)):
                        visit(child)
            elif isinstance(current, (list, tuple)):
                for child in current:
                    visit(child)

        visit(value)
        if len(found) != len(set(found)):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo pool filter contains duplicate sub_factor_id",
                value,
            )
        return found

    @classmethod
    def _same_persisted_value(
        cls,
        left: Any,
        right: Any,
        *,
        field_name: str = "",
        allow_database_extra: bool = False,
    ) -> bool:
        """按持久化业务语义递归比较两个值。

        参数 ``left`` 和 ``right`` 是接口与数据库的值，``field_name`` 用于选择 ID、哈希、时间、布尔或数值规则，
        ``allow_database_extra`` 表示右侧 JSON 可以包含后端追加字段。返回值表示两者是否一致；映射键、数组顺序和
        数组长度默认严格一致，``None`` 只与 ``None`` 相等，数值允许明确的小数容差，交易成本按持久化前五位小数
        截断规则比较。
        """

        if left is None or right is None:
            return left is None and right is None
        if isinstance(left, Mapping) or isinstance(right, Mapping):
            if not isinstance(left, Mapping) or not isinstance(right, Mapping):
                return False
            if allow_database_extra:
                if not set(left).issubset(set(right)):
                    return False
                keys = left.keys()
            else:
                if set(left) != set(right):
                    return False
                keys = left.keys()
            return all(
                cls._same_persisted_value(
                    left[key],
                    right[key],
                    field_name=f"{field_name}.{key}" if field_name else str(key),
                    allow_database_extra=allow_database_extra,
                )
                for key in keys
            )
        if isinstance(left, (list, tuple)) or isinstance(right, (list, tuple)):
            if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)):
                return False
            if len(left) != len(right):
                return False
            return all(
                cls._same_persisted_value(
                    left[index],
                    right[index],
                    field_name=f"{field_name}[{index}]",
                    allow_database_extra=allow_database_extra,
                )
                for index in range(len(left))
            )

        normalized_field = field_name.rsplit(".", 1)[-1].lower()
        if isinstance(left, bool) or isinstance(right, bool) or normalized_field in {
            "valid",
            "registered",
            "idempotent_replay",
            "is_sub_factor_id",
            "is_sub_factor",
            "overall_is_valid",
            "time_series_is_valid",
            "cross_sectional_is_valid",
        }:
            left_boolean = cls._coerce_boolean(left)
            right_boolean = cls._coerce_boolean(right)
            if left_boolean is None or right_boolean is None:
                return False
            return left_boolean == right_boolean

        if normalized_field in _DATETIME_IDENTITY_FIELDS or normalized_field.endswith("_at"):
            return cls._same_datetime_identity(left, right)

        if normalized_field == "transaction_cost":
            left_decimal = cls._coerce_decimal(left)
            right_decimal = cls._coerce_decimal(right)
            if left_decimal is None or right_decimal is None:
                return False
            quantizer = Decimal("0.00001")
            return left_decimal.quantize(quantizer, rounding=ROUND_DOWN) == right_decimal.quantize(
                quantizer,
                rounding=ROUND_DOWN,
            )

        if (
            (normalized_field == "id" or normalized_field.endswith("_id"))
            and normalized_field not in {"experiment_id", "agent_session_id", *_STRING_RUN_ID_FIELDS}
            and not normalized_field.endswith("_run_id")
        ):
            try:
                return cls._required_persisted_int(
                    {"value": left},
                    ("value",),
                    f"{field_name or 'identity'} API value",
                ) == cls._required_persisted_int(
                    {"value": right},
                    ("value",),
                    f"{field_name or 'identity'} database value",
                )
            except FactorComboFlowError:
                return False

        if "hash" in normalized_field or normalized_field in {"sha256", "sha"}:
            return str(left).strip().casefold() == str(right).strip().casefold()

        numeric_field = (
            normalized_field in {
                "weight",
                "direction",
                "forward_return_bars",
                "factor_window_bars",
                "update_interval",
                "hit_count",
                "strategy_status",
                "type",
                "level",
                "max_level",
                "child_factor_count",
                "validity_threshold",
                "composite_factor_score",
            }
            or normalized_field.endswith("_score")
            or normalized_field.endswith("_count")
            or normalized_field.endswith("_bars")
            or normalized_field.endswith("_return")
            or normalized_field.endswith("_rate")
            or normalized_field.endswith("_ratio")
            or normalized_field.endswith("_cost")
        )
        if numeric_field or not isinstance(left, str) or not isinstance(right, str):
            left_decimal = cls._coerce_decimal(left)
            right_decimal = cls._coerce_decimal(right)
            if left_decimal is not None and right_decimal is not None:
                return abs(left_decimal - right_decimal) <= Decimal("0.00000001")
        if isinstance(left, bytes):
            try:
                left = left.decode("utf-8")
            except UnicodeDecodeError:
                return False
        if isinstance(right, bytes):
            try:
                right = right.decode("utf-8")
            except UnicodeDecodeError:
                return False
        return left == right

    @classmethod
    def _compare_component_collections(
        cls,
        request_components: Any,
        database_rows: Sequence[Mapping[str, Any]],
        resource_name: str,
        *,
        expected_combo_id: int | None = None,
    ) -> list[str]:
        """按子因子身份严格比较版本请求组件和数据库组件集合。

        参数 ``request_components`` 是版本请求中的 ``components`` 数组，``database_rows`` 是同一具体版本的组件记录，
        ``resource_name`` 用于错误定位，``expected_combo_id`` 是具体 ``factor_combo.id``；返回实际比较的字段清单。
        两侧数量、子因子唯一性、组件归属、方向、转换和权重任一不一致时抛出 ``FAIL_CONTRACT``，不按列表位置猜测组件。
        """

        if not isinstance(request_components, Sequence) or isinstance(request_components, (str, bytes)):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} request components must be an array",
                request_components,
            )
        if not isinstance(database_rows, Sequence) or isinstance(database_rows, (str, bytes)):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} database components must be an array",
                database_rows,
            )
        if len(request_components) != len(database_rows):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} component count differs",
                {"request_count": len(request_components), "database_count": len(database_rows)},
            )

        request_by_sub_factor: dict[int, Mapping[str, Any]] = {}
        for component in request_components:
            if not isinstance(component, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} request component must be an object",
                    request_components,
                )
            sub_factor_id = cls._required_persisted_int(
                component,
                ("component_sub_factor_id",),
                f"{resource_name} request component",
            )
            if sub_factor_id in request_by_sub_factor:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} request contains duplicate component_sub_factor_id",
                    request_components,
                )
            request_by_sub_factor[sub_factor_id] = component

        database_by_sub_factor: dict[int, Mapping[str, Any]] = {}
        for row in database_rows:
            if not isinstance(row, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} database component must be an object",
                    database_rows,
                )
            sub_factor_id = cls._required_persisted_int(
                row,
                ("component_sub_factor_id",),
                f"{resource_name} database component",
            )
            if sub_factor_id in database_by_sub_factor:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{resource_name} database contains duplicate component_sub_factor_id",
                    database_rows,
                )
            if expected_combo_id is not None:
                stored_combo_id = cls._required_persisted_int(
                    row,
                    ("combo_id",),
                    f"{resource_name} database component combo pointer",
                )
                if stored_combo_id != int(expected_combo_id):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{resource_name} database component points to another version",
                        {"expected_combo_id": expected_combo_id, "row": dict(row)},
                    )
            database_by_sub_factor[sub_factor_id] = row

        if set(request_by_sub_factor) != set(database_by_sub_factor):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} request and database component identities differ",
                {
                    "request_sub_factor_ids": sorted(request_by_sub_factor),
                    "database_sub_factor_ids": sorted(database_by_sub_factor),
                },
            )

        compared: list[str] = []
        field_map: dict[str, str | Sequence[str]] = {
            "component_factor_id": "component_factor_id",
            "component_sub_factor_id": "component_sub_factor_id",
            "direction": "direction",
            "transform": "transform_json",
            "weight": "weight",
        }
        for sub_factor_id, request_component in request_by_sub_factor.items():
            database_component = database_by_sub_factor[sub_factor_id]
            normalized_request = dict(request_component)
            # OpenAPI 将 weight 定义为可选 nullable；后端若未传入必须持久化为 NULL，不能把缺失字段当成无需核对。
            normalized_request.setdefault("weight", None)
            compared.extend(
                f"{sub_factor_id}.{field_name}"
                for field_name in cls._compare_explicit_fields(
                    normalized_request,
                    database_component,
                    field_map,
                    f"{resource_name} component {sub_factor_id}",
                    required_fields=(
                        "component_factor_id",
                        "component_sub_factor_id",
                        "direction",
                        "transform",
                        "weight",
                    ),
                )
            )
        return compared

    @classmethod
    def _compare_work_order_members(
        cls,
        api_members: Sequence[Mapping[str, Any]],
        database_rows: Sequence[Mapping[str, Any]],
        *,
        expected_form_id: int | None = None,
        expected_pool_id: int | None = None,
    ) -> list[str]:
        """按 ``sub_factor_id`` 严格比较 Work Order 成员及数据库快照。

        参数 ``api_members`` 是 Work Order 返回的成员数组，``database_rows`` 是因子池成员查询结果；返回实际比较的
        成员字段清单。``expected_form_id`` 和 ``expected_pool_id`` 是工作单顶层归属，可选但在真实接口对账时应提供。
        成员数量、重复子因子 ID、组件 ID 的接口唯一性、父级集合、母因子/子因子编码、特征列、K 线级别、方向
        和三个快照字段无法从数据库明确对齐时抛出 ``FAIL_CONTRACT``，不通过成员数量或单个 ID 的弱校验。
        ``component_id`` 是 Work Order 的业务标识；除非数据库明确提供同名业务字段，否则不与因子池成员自增主键
        比较。
        """

        if len(api_members) != len(database_rows):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo work order member count differs from database",
                {"api_count": len(api_members), "database_count": len(database_rows)},
            )

        api_by_sub_factor: dict[int, Mapping[str, Any]] = {}
        for member in api_members:
            if not isinstance(member, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo work order API member must be an object",
                    api_members,
                )
            sub_factor_id = cls._required_persisted_int(
                member,
                ("sub_factor_id",),
                "factor combo work order API member",
            )
            if sub_factor_id in api_by_sub_factor:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo work order API contains duplicate sub_factor_id",
                    api_members,
                )
            api_by_sub_factor[sub_factor_id] = member

        db_by_sub_factor: dict[int, Mapping[str, Any]] = {}
        for row in database_rows:
            if not isinstance(row, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo work order database member must be an object",
                    database_rows,
                )
            sub_factor_id = cls._required_persisted_int(
                row,
                ("sub_factor_id",),
                "factor combo work order database member",
            )
            if sub_factor_id in db_by_sub_factor:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo work order database contains duplicate sub_factor_id",
                    database_rows,
                )
            member_form_id = cls._required_persisted_int(
                row,
                ("member_form_id", "factor_combo_form_id"),
                "factor combo work order database member form pointer",
            )
            member_pool_id = cls._required_persisted_int(
                row,
                ("member_pool_id", "pool_id"),
                "factor combo work order database member pool pointer",
            )
            if expected_form_id is not None and member_form_id != int(expected_form_id):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo work order database member belongs to another form",
                    {"expected_form_id": expected_form_id, "row": dict(row)},
                )
            if expected_pool_id is not None and member_pool_id != int(expected_pool_id):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo work order database member belongs to another pool",
                    {"expected_pool_id": expected_pool_id, "row": dict(row)},
                )
            # factor_combo_pool_member.factor_detail_id 按新版表单文档保持 NULL。若当前环境扩展写入了
            # 详情关联，则验证它的完整性；NULL 本身是预期存储状态。
            stored_detail_id_value = row.get("factor_detail_id")
            detail_alias_value = row.get("factor_detail_record_id")
            detail_factor_value = row.get("factor_detail_factor_id")
            if stored_detail_id_value is not None:
                stored_detail_id = cls._required_persisted_int(
                    {"value": stored_detail_id_value},
                    ("value",),
                    "factor combo work order database member factor_detail_id",
                )
                if detail_alias_value is not None:
                    detail_id = cls._required_persisted_int(
                        {"value": detail_alias_value},
                        ("value",),
                        "factor combo work order database member detail pointer",
                    )
                    if detail_id != stored_detail_id:
                        raise FactorComboFlowError(
                            FlowOutcome.FAIL_CONTRACT,
                            "factor combo work order member detail aliases conflict",
                            {"row": dict(row)},
                        )
                if detail_factor_value is not None:
                    detail_factor_id = cls._required_persisted_int(
                        {"value": detail_factor_value},
                        ("value",),
                        "factor combo work order database detail factor pointer",
                    )
                    if detail_factor_id != sub_factor_id:
                        raise FactorComboFlowError(
                            FlowOutcome.FAIL_CONTRACT,
                            "factor combo work order member detail points to another sub-factor",
                            {"sub_factor_id": sub_factor_id, "row": dict(row)},
                        )
            elif detail_alias_value is not None or detail_factor_value is not None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo work order member has orphaned factor detail aliases",
                    {"sub_factor_id": sub_factor_id, "row": dict(row)},
                )
            if (
                stored_detail_id_value is not None
                and "factor_detail_is_sub_factor_id" in row
                and row.get("factor_detail_is_sub_factor_id") is not None
                and not cls._same_scalar(row.get("factor_detail_is_sub_factor_id"), True)
            ):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo work order member detail is not marked as a sub-factor",
                    {"sub_factor_id": sub_factor_id, "row": dict(row)},
                )
            db_by_sub_factor[sub_factor_id] = row
        if set(api_by_sub_factor) != set(db_by_sub_factor):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo work order member identities differ from database",
                {
                    "api_sub_factor_ids": sorted(api_by_sub_factor),
                    "database_sub_factor_ids": sorted(db_by_sub_factor),
                },
            )

        compared: list[str] = []
        for sub_factor_id, api_member in api_by_sub_factor.items():
            database_member = dict(db_by_sub_factor[sub_factor_id])
            # Work Order 的 ``name`` 是面向 Agent 的展示名称，对应 ``sub_factors.cn_name``。离线替身和旧查询若
            # 没有单独提供 cn_name，只有在确实缺少该列时才回退到已有的名称字段；真实 Repository 会返回
            # ``sub_factor_cn_name``，不会把内部英文名误当成展示名。
            if "sub_factor_cn_name" not in database_member and "sub_factor_name" in database_member:
                database_member["sub_factor_cn_name"] = database_member["sub_factor_name"]
            api_factor_id = cls._required_persisted_int(
                api_member,
                ("factor_id",),
                f"factor combo work order member {sub_factor_id} parent factor",
            )
            parent_ids = database_member.get("parent_factor_ids")
            if not isinstance(parent_ids, Sequence) or isinstance(parent_ids, (str, bytes)):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo work order member {sub_factor_id} has no complete parent factor set in database",
                    {"api": dict(api_member), "database": database_member},
                )
            normalized_parent_ids = [
                cls._required_persisted_int(
                    {"value": value},
                    ("value",),
                    f"factor combo work order member {sub_factor_id} parent factor set",
                )
                for value in parent_ids
            ]
            if len(set(normalized_parent_ids)) != len(normalized_parent_ids):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo work order member {sub_factor_id} has duplicate parent factor IDs",
                    {"parent_factor_ids": normalized_parent_ids, "database": database_member},
                )
            relation_count = database_member.get("parent_factor_relation_count")
            distinct_relation_count = database_member.get("parent_factor_distinct_count")
            if relation_count is None or distinct_relation_count is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo work order member {sub_factor_id} has no parent relation counts",
                    {"database": database_member},
                )
            normalized_relation_count = cls._non_negative_int_or_failure(
                relation_count,
                FlowOutcome.FAIL_CONTRACT,
                f"factor combo work order member {sub_factor_id} parent relation count is invalid",
                database_member,
            )
            normalized_distinct_relation_count = cls._non_negative_int_or_failure(
                distinct_relation_count,
                FlowOutcome.FAIL_CONTRACT,
                f"factor combo work order member {sub_factor_id} distinct parent relation count is invalid",
                database_member,
            )
            if (
                normalized_relation_count != normalized_distinct_relation_count
                or normalized_relation_count != len(normalized_parent_ids)
            ):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo work order member {sub_factor_id} parent relation aggregation is inconsistent",
                    {
                        "relation_count": normalized_relation_count,
                        "distinct_relation_count": normalized_distinct_relation_count,
                        "parent_factor_ids": normalized_parent_ids,
                        "database": database_member,
                    },
                )
            if api_factor_id not in normalized_parent_ids:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo work order member {sub_factor_id} factor_id is not one of its database parents",
                    {
                        "api_factor_id": api_factor_id,
                        "database_parent_factor_ids": normalized_parent_ids,
                        "api": dict(api_member),
                    },
                )
            parent_serials = database_member.get("parent_factor_serial_numbers")
            if not isinstance(parent_serials, Sequence) or isinstance(parent_serials, (str, bytes)):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo work order member {sub_factor_id} has no parent factor code source in database",
                    {"api": dict(api_member), "database": database_member},
                )
            if len(parent_serials) != len(normalized_parent_ids):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo work order member {sub_factor_id} parent IDs and codes cannot be aligned",
                    {"parent_ids": normalized_parent_ids, "parent_serials": list(parent_serials)},
                )
            parent_names = database_member.get("parent_factor_names")
            if not isinstance(parent_names, Sequence) or isinstance(parent_names, (str, bytes)):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo work order member {sub_factor_id} has no parent factor name source in database",
                    {"api": dict(api_member), "database": database_member},
                )
            if len(parent_names) != len(normalized_parent_ids):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor combo work order member {sub_factor_id} parent IDs and names cannot be aligned",
                    {"parent_ids": normalized_parent_ids, "parent_names": list(parent_names)},
                )
            parent_index = normalized_parent_ids.index(api_factor_id)
            database_member["_api_parent_factor_serial_number"] = parent_serials[parent_index]

            # Work Order 的 feature_column、direction 只有在成员快照中明确保存时才能对账；不根据名称拼接或默认值推断。
            for api_field, snapshot_names in (
                ("feature_column", ("feature_column", "feature_column_name")),
                ("direction", ("direction",)),
            ):
                snapshot_values: list[tuple[str, Any]] = []
                for snapshot_field in ("definition_snapshot_json", "metrics_snapshot_json", "validity_snapshot_json"):
                    snapshot = database_member.get(snapshot_field)
                    if not isinstance(snapshot, Mapping):
                        continue
                    for key in snapshot_names:
                        if key in snapshot:
                            snapshot_values.append((f"{snapshot_field}.{key}", snapshot[key]))
                if len(snapshot_values) > 1:
                    first_value = snapshot_values[0][1]
                    conflicting_values = [
                        item for item in snapshot_values[1:] if not cls._same_scalar(first_value, item[1])
                    ]
                    if conflicting_values:
                        raise FactorComboFlowError(
                            FlowOutcome.FAIL_CONTRACT,
                            f"factor combo work order member {sub_factor_id} has conflicting {api_field} snapshots",
                            {
                                "field": api_field,
                                "values": snapshot_values,
                                "database": database_member,
                            },
                        )
                if api_field not in database_member and snapshot_values:
                    database_member[api_field] = snapshot_values[0][1]

            # ``feature_column`` 没有独立的成员表列时，Work Order 实际以子因子序列号作为特征列标识；这是
            # 已存在的明确身份字段，不是根据名称临时拼接。``direction`` 若没有列或快照来源，则只在
            # require_work_order 中完成接口级类型/枚举校验，不把 API 默认值写入比较基线。
            feature_column_source: str | None = next(
                (
                    field_name
                    for field_name in ("feature_column", "feature_column_name", "sub_factor_serial_number")
                    if field_name in database_member
                ),
                None,
            )
            member_field_map: dict[str, str | Sequence[str]] = {
                "sub_factor_id": "sub_factor_id",
                "factor_code": "_api_parent_factor_serial_number",
                "sub_factor_code": "sub_factor_serial_number",
                "name": "sub_factor_cn_name",
                "factor_bar_interval": "sub_factor_bar_interval",
                "definition_snapshot": ("definition_snapshot", "definition_snapshot_json"),
                "metrics_snapshot": ("metrics_snapshot", "metrics_snapshot_json"),
                "validity_snapshot": ("validity_snapshot", "validity_snapshot_json"),
            }
            if feature_column_source is not None:
                member_field_map["feature_column"] = feature_column_source
            if "direction" in database_member:
                member_field_map["direction"] = "direction"

            # 只映射 API 明确出现的字段；必填成员字段由 required_fields 强制存在。
            compared.extend(
                f"{sub_factor_id}.{field_name}"
                for field_name in cls._compare_explicit_fields(
                    api_member,
                    database_member,
                    member_field_map,
                    f"factor combo work order member {sub_factor_id}",
                    required_fields=_WORK_ORDER_MEMBER_REQUIRED_FIELDS,
                    allow_database_json_extra=True,
                )
            )
        return compared

    @classmethod
    def _compare_experiment_request_to_database(
        cls,
        request_payload: Mapping[str, Any],
        experiment_row: Mapping[str, Any],
    ) -> list[str]:
        """比较实验写入请求的全部持久化字段和实验主表。

        参数 ``request_payload`` 是实验接口完整请求体，``experiment_row`` 是 ``factor_combo_experiment_info`` 记录。
        返回实际比较字段清单；普通列、JSON 配置、Artifact、显式空值和外部表单/Run 身份缺失或不一致时抛出
        ``FAIL_CONTRACT``。Artifact SHA256 只按内容值比较，不把它误当成唯一键。
        """

        if not isinstance(request_payload, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment request must be an object",
                request_payload,
            )
        artifact = request_payload.get("artifact")
        if not isinstance(artifact, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment artifact must be an object",
                request_payload,
            )
        normalized = dict(request_payload)
        normalized.pop("artifact", None)
        fields: list[str] = list(
            cls._compare_explicit_fields(
                normalized,
                experiment_row,
                {
                    "data_version": "data_version",
                    "data_directory": "data_directory",
                    "evaluation_config": "evaluation_config_json",
                    "metrics": "metrics_json",
                    "experiment_config": "experiment_config_json",
                    "experiment_description": "experiment_description",
                    "implementation_method": "implementation_method",
                    "experiment_conclusion": "experiment_conclusion",
                    "composite_factor_score": "composite_factor_score",
                    "valid": "valid",
                    "remark": "remark",
                    "train_config": "train_config_json",
                    "failure_reason": "failure_reason",
                },
                "factor combo experiment request/database",
                required_fields=(
                    "data_version",
                    "evaluation_config",
                    "metrics",
                    "valid",
                    "train_config",
                ),
                allow_database_json_extra=False,
            )
        )
        fields.extend(
            cls._compare_explicit_fields(
                artifact,
                experiment_row,
                {
                    "type": "artifact_type",
                    "uri": "artifact_uri",
                    "sha256": "artifact_hash",
                },
                "factor combo experiment artifact/database",
                required_fields=("type", "uri", "sha256"),
            )
        )
        return fields

    @classmethod
    def _compare_registration_request_to_database(
        cls,
        request_payload: Mapping[str, Any],
        sub_factor_row: Mapping[str, Any],
        factor_detail_row: Mapping[str, Any],
        validity_row: Mapping[str, Any],
        registration_row: Mapping[str, Any],
    ) -> list[str]:
        """比较登记请求报告、有效性输入和四张登记实体中的完整持久化内容。

        参数 ``request_payload`` 是登记请求，后四个参数依次是子因子、因子详情、有效性快照和登记映射数据库记录。
        返回实际比较字段清单；报告先按接口规则删除可省略的空绩效字段，再与 ``factors_details.params`` 和 metadata
        对账；有效性普通列/原因 JSON、登记身份和其他显式空值仍严格比较。任一内容无法对齐时抛出
        ``FAIL_CONTRACT``；数据库允许追加审计字段，但不得覆盖请求字段而不被发现。
        """

        if not isinstance(request_payload, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo registration request must be an object",
                request_payload,
            )
        report = request_payload.get("report")
        validity = request_payload.get("factor_validity_status")
        if not isinstance(report, Mapping) or not isinstance(validity, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo registration request report and validity must be objects",
                request_payload,
            )
        normalized_report = cls._normalize_registration_report_for_persistence(report)
        fields: list[str] = []
        fields.extend(
            f"report.{field_name}"
            for field_name in cls._compare_explicit_fields(
                {"report": normalized_report},
                {"params": factor_detail_row.get("params")},
                {"report": "params"},
                "factor combo registration report/database",
                required_fields=("report",),
                allow_database_json_extra=True,
            )
        )
        metadata_value = sub_factor_row.get("metadata")
        if metadata_value is not None:
            parsed_metadata = cls._parse_json_value(
                metadata_value,
                "factor combo registration sub_factor.metadata",
            )
            if not isinstance(parsed_metadata, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "registered sub-factor metadata must be a JSON object",
                    {"metadata": metadata_value, "sub_factor": dict(sub_factor_row)},
                )
            if "report" in parsed_metadata:
                fields.extend(
                    f"metadata.report.{field_name}"
                    for field_name in cls._compare_explicit_fields(
                        {"report": normalized_report},
                        {"report": parsed_metadata["report"]},
                        {"report": "report"},
                        "factor combo registration sub-factor metadata/report",
                        required_fields=("report",),
                        allow_database_json_extra=True,
                    )
                )

        validity_field_map: dict[str, str | Sequence[str]] = {
            "universe_key": "universe_key",
            "factor_bar_interval": "factor_bar_interval",
            "factor_window_bars": "factor_window_bars",
            "return_bar_interval": "return_bar_interval",
            "forward_return_bars": "forward_return_bars",
            "window_scope": "window_scope",
            "period_start": "period_start",
            "period_end": "period_end",
            "time_series_scoring_version": "time_series_scoring_version",
            "time_series_score": "time_series_score",
            "time_series_status": "time_series_status",
            "time_series_is_valid": "time_series_is_valid",
            "cross_sectional_scoring_version": "cross_sectional_scoring_version",
            "cross_sectional_score": "cross_sectional_score",
            "cross_sectional_status": "cross_sectional_status",
            "cross_sectional_is_valid": "cross_sectional_is_valid",
            "overall_score": "overall_score",
            "overall_status": "overall_status",
            "overall_is_valid": "overall_is_valid",
            "validity_threshold": "validity_threshold",
            "status_reason_json": ("status_reason_json", "status_reason"),
        }
        normalized_validity = dict(validity)
        fields.extend(
            f"validity.{field_name}"
            for field_name in cls._compare_explicit_fields(
                normalized_validity,
                validity_row,
                validity_field_map,
                "factor combo registration validity/database",
                required_fields=tuple(normalized_validity.keys()),
                allow_database_json_extra=True,
            )
        )
        fields.extend(
            f"registration.{field_name}"
            for field_name in cls._compare_explicit_fields(
                {
                    "sub_factor_id": registration_row.get("sub_factor_id"),
                    "combo_version_hash": registration_row.get("combo_version_hash"),
                },
                registration_row,
                {"sub_factor_id": "sub_factor_id", "combo_version_hash": "combo_version_hash"},
                "factor combo registration identity/database",
                required_fields=("sub_factor_id", "combo_version_hash"),
            )
        )
        return fields

    @classmethod
    def _normalize_registration_report_for_persistence(cls, report: Mapping[str, Any]) -> dict[str, Any]:
        """按登记接口的报告标准化规则生成 DB 对账副本。

        参数 ``report`` 是前端提交的完整组合报告。返回不修改原请求的深拷贝；仅删除 ``performance`` 中允许省略且
        显式为 ``null`` 的字段，保留六个始终必填指标的 ``null``。这是报告哈希和 JSON 持久化的文档化标准化行为，
        不影响其他实体字段的显式 ``null`` 严格对账；报告或绩效不是对象时抛出 ``FactorComboFlowError``。
        """

        normalized = deepcopy(dict(report))
        performance = normalized.get("performance")
        if not isinstance(performance, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo registration report.performance must be an object",
                report,
            )
        persisted_null_fields = {
            "ts_ic",
            "return_rate",
            "out_of_sample_icir",
            "net_sharpe",
            "max_drawdown",
            "annual_turnover",
        }
        normalized_performance = {
            field_name: value
            for field_name, value in performance.items()
            if value is not None or field_name in persisted_null_fields
        }
        normalized["performance"] = normalized_performance
        return normalized
