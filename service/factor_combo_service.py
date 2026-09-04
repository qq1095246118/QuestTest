"""组合因子台测试流程编排。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import uuid4

import requests

from api.agent_api import AgentAPI
from api.chat_api import ChatAPI
from api.factor_combo_api import FactorComboAPI
from api.performance_api import PerformanceAPI
from api.sub_factor_api import SubFactorAPI
from config.settings import FactorComboSettings
from db.factor_combo_repository import (
    FactorComboRepository,
    ParentFactorChoice,
    RegisteredFactorChoice,
    SubFactorChoice,
)
from service.factor_combo_models import (
    AgentSelection,
    ClaimedFeedback,
    ComboVersion,
    CompletedExperiment,
    DatabaseRefreshEvidence,
    FactorComboFlowError,
    FlowOutcome,
    PendingFeedback,
    PerformanceRefreshResult,
    RealFeedback,
    RealPipelineResult,
    RealResearchFlowResult,
    RealRun,
    RegisteredFlowResult,
    ResourceScope,
    SubmittedForm,
    WorkerForm,
)
from service.factor_combo_refresh import (
    FactorComboRefreshMixin,
    _PIPELINE_FAILED_STATUSES,
    _REFRESH_RESPONSE_STATUSES,
    _TRANSIENT_HTTP_STATUSES,
)
from tools.http_response import read_json, read_json_or_diagnostic
from service.factor_combo_persistence import (
    FactorComboPersistenceMixin,
    _EXPERIMENT_RESULT_REQUIRED_FIELDS,
    _FEEDBACK_RESULT_REQUIRED_FIELDS,
    _NEXT_VERSION_RESULT_REQUIRED_FIELDS,
    _VERSION_RESULT_REQUIRED_FIELDS,
    _WORK_ORDER_MEMBER_REQUIRED_FIELDS,
    _WORK_ORDER_REQUIRED_FIELDS,
    _WORK_ORDER_SPEC_REQUIRED_FIELDS,
)


_REAL_PIPELINE_RUN_ID = re.compile(r"^combo-[1-9][0-9]*-[0-9a-f]{16}$")
_COMPLETED_REGISTRATION_MARKERS = (
    "already registered",
    "already completed",
    "registration completed",
    "registration already",
    "已完成",
    "已登记",
    "已注册",
)

# ``None`` 是接口允许发送的 JSON null，不能再同时用来表示调用方省略参数。
_METHOD_GROUPS_UNSET = object()
_EXPERIMENT_CONFIG_UNSET = object()

_FORM_STATUS_VALUES = {"submitted", "processing", "completed"}
_COMBO_STATUS_VALUES = {"draft", "testing", "candidate", "rejected", "active", "deprecated"}
_FEEDBACK_STATUS_VALUES = {"pending", "processing", "completed", "failed", "legacy_failed"}

_PERFORMANCE_REQUIRED_NUMERIC_FIELDS = (
    "ts_ic",
    "return_rate",
    "out_of_sample_icir",
    "net_sharpe",
    "max_drawdown",
    "annual_turnover",
)
_PERFORMANCE_COMPATIBILITY_RATE_FIELDS = (
    "positive_return_rate",
    "rolling_oos_win_rate",
)
_PERFORMANCE_OPTIONAL_NUMERIC_FIELDS = (
    *_PERFORMANCE_COMPATIBILITY_RATE_FIELDS,
    "annualized_return",
    "benchmark_sharpe",
    "calmar",
    "profit_loss_ratio",
    "observations",
    "trade_observations",
    "decay_ratio",
    "cs_rank_ic",
    "cs_icir",
    "cs_score",
)
_PERFORMANCE_NUMERIC_FIELDS = _PERFORMANCE_REQUIRED_NUMERIC_FIELDS + _PERFORMANCE_OPTIONAL_NUMERIC_FIELDS
_PERFORMANCE_ALLOWED_FIELDS = {
    "metrics_status",
    *_PERFORMANCE_NUMERIC_FIELDS,
    "metric_mode",
    "universe_key",
    "symbols",
}


class FactorComboService(FactorComboPersistenceMixin, FactorComboRefreshMixin):
    """编排组合因子测试所需的会话、表单、Worker 状态和真实 Run。"""

    def __init__(
        self,
        chat_api: ChatAPI,
        factor_combo_api: FactorComboAPI,
        repository: FactorComboRepository,
        settings: FactorComboSettings,
        scope: ResourceScope,
        agent_api: AgentAPI | None = None,
        performance_api: PerformanceAPI | None = None,
        sub_factor_api: SubFactorAPI | None = None,
        user_id: int | None = None,
    ) -> None:
        """初始化组合因子业务编排服务。

        参数 ``chat_api``、``factor_combo_api``、``agent_api``、``performance_api`` 和 ``sub_factor_api`` 是协议封装，
        ``repository`` 是测试库仓储，``settings`` 是组合运行配置，``scope`` 记录本次测试创建的资源，``user_id`` 是
        当前 JWT 对应的用户 ID。后三个 API 和用户 ID 仅在真实 Agent/登记后验收流程中需要；不返回值，服务不执行测试断言。
        """

        self._chat_api = chat_api
        self._factor_combo_api = factor_combo_api
        self._repository = repository
        self._settings = settings
        self.scope = scope
        self._agent_api = agent_api
        self._performance_api = performance_api
        self._sub_factor_api = sub_factor_api
        self._user_id = user_id

    def create_session(self, title_prefix: str = "autotest-factor-combo") -> int:
        """创建一个用于组合表单的 Factor 会话。

        参数 ``title_prefix`` 是会话标题前缀；服务会追加唯一后缀。
        返回会话主键；接口返回非成功响应或缺少 ID 时抛出 ``RuntimeError``。
        """

        title = f"{title_prefix}-{uuid4().hex}"
        response = self._chat_api.create_session(title)
        data = self._require_success_data(response, {200, 201}, "create Factor session")
        session_id = self._required_int(data, "id", "Factor session")
        self.scope.track_session(session_id)
        return session_id

    def build_form_payload(
        self,
        session_id: int,
        factor_names: list[str],
        *,
        is_sub_factor: int = 1,
        method_groups: Any = _METHOD_GROUPS_UNSET,
        objectives: list[dict[str, Any]] | None = None,
        notes: str | None = None,
        configuration_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造包含因子类型标识且不包含已废弃 ``research_type`` 的组合表单请求体。

        参数 ``session_id`` 是当前用户 Factor 会话主键，``factor_names`` 是真实母因子或子因子名称列表，
        ``is_sub_factor`` 是接口规定的因子类型标识（1 表示子因子，0 表示母因子），``method_groups``、``objectives``、
        ``notes`` 和 ``configuration_overrides`` 是可选接口配置。
        ``method_groups`` 省略时使用默认规则方法；显式传入 ``None`` 时保留为 JSON ``null``，其他 JSON 值原样保留。
        返回可直接传给表单提交接口的请求字典；因子类型不是 0 或 1 时抛出 ``ValueError``，不执行网络请求或数据库写入。
        """

        if is_sub_factor not in (0, 1):
            raise ValueError("is_sub_factor must be 0 (parent factor) or 1 (sub-factor)")

        configuration_parameters: dict[str, Any] = {
            "objectives": objectives
            if objectives is not None
            else [
                {"code": "ts-score", "priority": 1},
                {"code": "sharpe", "priority": 2},
            ],
            "rolling_window": "12m",
            "correlation_penalty": 0.1,
            "transaction_cost": 0.001,
            "optimize_subfactor_params": False,
        }
        if configuration_overrides:
            configuration_parameters.update(configuration_overrides)
        stored_method_groups = (
            {"rule_methods": ["ic_weight"]}
            if method_groups is _METHOD_GROUPS_UNSET
            else method_groups
        )
        return {
            "session_id": session_id,
            "is_sub_factor": is_sub_factor,
            "factors_name": list(factor_names),
            "method_groups": stored_method_groups,
            "configuration_parameters": configuration_parameters,
            "notes": notes if notes is not None else f"autotest-factor-combo-{uuid4().hex}",
        }

    def create_form_for_factor_names(
        self,
        factor_names: Sequence[str],
        *,
        is_sub_factor: int = 1,
        method_groups: Any = _METHOD_GROUPS_UNSET,
        objectives: list[dict[str, Any]] | None = None,
        configuration_overrides: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> tuple[SubmittedForm, int]:
        """按真实因子名称提交一个可继续执行的组合表单。

        参数 ``factor_names`` 是一个或多个母因子/子因子名称，``is_sub_factor`` 是接口要求的来源类型标识；其余参数
        直接对应表单请求中的方法、目标、配置和备注。返回已提交的 ``SubmittedForm`` 与会话 ID；方法只编排创建会话、
        构造请求和提交接口，不执行断言，任何接口或响应契约错误都向调用方抛出异常。
        """

        normalized_names = [str(name) for name in factor_names]
        if not normalized_names:
            raise ValueError("factor_names must not be empty")
        session_id = self.create_session()
        payload = self.build_form_payload(
            session_id,
            normalized_names,
            is_sub_factor=is_sub_factor,
            method_groups=method_groups,
            objectives=objectives,
            notes=notes,
            configuration_overrides=configuration_overrides,
        )
        submitted = self.require_submitted_form(self.submit_form(payload), session_id)
        return submitted, session_id

    def submit_form(self, payload: dict[str, Any]) -> requests.Response:
        """发送组合研究表单提交请求。

        参数 ``payload`` 是表单接口完整 JSON 请求体。
        返回原始 HTTP 响应；状态码和响应字段由对应 pytest 用例断言。响应中只要带有可识别的表单 ID，就会先把它
        登记到当前资源 Scope，确保“本应失败却意外创建资源”的负向用例也能清理；响应格式异常不会在此处转换成异常。
        """

        response = self._factor_combo_api.submit_form(payload)
        self._track_form_response_for_cleanup(response, payload)
        return response

    def _track_form_response_for_cleanup(
        self,
        response: requests.Response,
        payload: Mapping[str, Any],
    ) -> None:
        """从表单响应中尽力登记测试资源，不参与接口契约判定。

        参数 ``response`` 是表单接口原始响应，``payload`` 是本次请求体。不返回值；只有请求 session_id 和响应 form_id
        都是正整数时才写入 Scope。非 JSON、缺字段或非法类型会被忽略并留给测试用例断言，不会掩盖原始响应。
        """

        try:
            body = read_json(response, "track factor combo form response")
        except ValueError:
            return
        if not isinstance(body, Mapping):
            return
        data = body.get("data")
        if not isinstance(data, Mapping):
            return
        session_id = payload.get("session_id")
        form_id = data.get("form_id")
        if isinstance(session_id, bool) or isinstance(form_id, bool):
            return
        try:
            normalized_session_id = int(session_id)
            normalized_form_id = int(form_id)
        except (TypeError, ValueError):
            return
        if normalized_session_id <= 0 or normalized_form_id <= 0:
            return
        self.scope.track_form(normalized_session_id, normalized_form_id)

    def get_work_order_request(self, form_id: int) -> requests.Response:
        """发送组合工作单查询请求。

        参数 ``form_id`` 是已提交表单主键。
        返回原始 HTTP 响应；工作单字段和数据库只读性由对应测试用例断言。
        """

        return self._call_flow_request(
            "read factor combo work order",
            lambda: self._factor_combo_api.get_work_order(form_id),
        )

    def discover_agent(self, user_id: int, preferred_agent_uid: str | None = None) -> AgentSelection:
        """从当前账号可见的 Agent 列表中确定一个可执行投研 Agent。

        参数 ``user_id`` 必须与 Factor JWT 对应，``preferred_agent_uid`` 是可选的预先指定 Agent UID。
        返回唯一的 ``AgentSelection``；列表请求失败、指定 UID 不可见、候选为空或同名候选超过一个时抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``，绝不随机选择 Agent。
        """

        if self._agent_api is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "Agent API is not configured",
            )
        response = self._call_flow_request(
            "list research agents",
            lambda: self._agent_api.list_agents(user_id),
        )
        if response.status_code != 200:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"Agent list request returned HTTP {response.status_code}",
                self._safe_json(response),
            )
        payload = self._safe_json(response)
        items: Any = payload
        if isinstance(payload, dict):
            if payload.get("success") is False:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Agent list response is an unsuccessful JSON envelope",
                    payload,
                )
            data = payload.get("data")
            if isinstance(data, dict):
                items = data.get("items", data.get("agents"))
            else:
                items = data if data is not None else payload.get("items")
        if not isinstance(items, list):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "Agent list response must be a JSON array",
                payload,
            )

        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Agent list contains a non-object item",
                    payload,
                )
            uid = str(item.get("agent_uid", "")).strip()
            if not uid:
                continue
            if item.get("enabled") is False or item.get("is_enabled") is False:
                continue
            normalized_items.append(dict(item))

        if preferred_agent_uid:
            normalized_uid = str(preferred_agent_uid).strip()
            matches = [item for item in normalized_items if str(item.get("agent_uid")).strip() == normalized_uid]
            if len(matches) != 1:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"configured agent_uid is not uniquely visible to current user: {normalized_uid}",
                    payload,
                )
            selected = matches[0]
        else:
            preferred_names = {"投研agent", "投研agent(内置)"}
            candidates = [
                item
                for item in normalized_items
                if "".join(str(item.get("name", "")).split()).casefold() in preferred_names
            ]
            if len(candidates) != 1:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "current user must have exactly one preferred research Agent; configure agent_uid when ambiguous",
                    {"candidate_count": len(candidates), "agents": normalized_items},
                )
            selected = candidates[0]

        return AgentSelection(
            agent_uid=str(selected["agent_uid"]).strip(),
            name=str(selected.get("name", "")).strip(),
            raw=selected,
        )

    def require_work_order(self, response: requests.Response, form: SubmittedForm) -> dict[str, Any]:
        """解析并校验启动真实 Pipeline 前必须完整的 Work Order。

        参数 ``response`` 是 Work Order 查询原始响应，``form`` 是提交表单上下文。
        返回 Work Order 的 data 对象；表单 ID、因子池成员、因子 ID、子因子 ID、特征列或 K 线级别缺失时抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``，调用方不应继续启动 Pipeline。
        """

        data = self._require_flow_data(response, {200}, "read factor combo work order")
        self._require_response_fields(data, _WORK_ORDER_REQUIRED_FIELDS, "factor combo work order")
        returned_form_id = self._required_response_int(data, "form_id", "factor combo work order")
        if returned_form_id != form.form_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order form_id does not match the submitted form",
                data,
            )
        form_no = self._required_response_string(data, "form_no", "factor combo work order")
        form_status = self._required_response_string(data, "form_status", "factor combo work order")
        if form_status not in _FORM_STATUS_VALUES:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order form_status is outside the documented enum",
                data,
            )
        if form.form_no and form_no != form.form_no:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order form_no does not match the submitted form",
                data,
            )
        pool_id = self._required_response_int(data, "factor_combo_pool_id", "factor combo work order")
        if pool_id != form.pool_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order factor_combo_pool_id does not match the submitted form",
                data,
            )
        self._required_response_string(data, "pool_snapshot_hash", "factor combo work order")
        form_json = data["form_json"]
        if not isinstance(form_json, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order form_json must be an object",
                data,
            )
        data_spec = data["data_spec"]
        if not isinstance(data_spec, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order data_spec must be an object",
                data,
            )
        self._require_response_fields(data_spec, _WORK_ORDER_SPEC_REQUIRED_FIELDS, "factor combo work order data_spec")
        self._required_response_string(data_spec, "symbol", "factor combo work order data_spec")
        self._required_response_string(data_spec, "interval", "factor combo work order data_spec")
        for field_name in (
            "combo_bar_interval",
            "return_bar_interval",
            "alignment_policy",
            "source_availability_rule",
        ):
            self._required_response_string(data_spec, field_name, "factor combo work order data_spec")
        forward_return_bars = self._required_response_int(
            data_spec,
            "forward_return_bars",
            "factor combo work order data_spec",
        )
        if forward_return_bars < 1:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order data_spec.forward_return_bars must be positive",
                data,
            )
        pool_members = data.get("pool_members")
        if not isinstance(pool_members, list) or len(pool_members) < 2:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order must contain at least two pool members",
                data,
            )
        component_ids: set[str] = set()
        sub_factor_ids: set[int] = set()
        for member in pool_members:
            if not isinstance(member, dict):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "work order pool member must be an object",
                    data,
                )
            self._require_response_fields(member, _WORK_ORDER_MEMBER_REQUIRED_FIELDS, "factor combo work order member")
            component_id = self._required_response_string(
                member,
                "component_id",
                "factor combo work order member",
            )
            if component_id in component_ids:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "work order pool_members contains duplicate component_id",
                    data,
                )
            component_ids.add(component_id)
            self._required_response_int(member, "factor_id", "factor combo work order member")
            sub_factor_id = self._required_response_int(member, "sub_factor_id", "factor combo work order member")
            if sub_factor_id in sub_factor_ids:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "work order pool_members contains duplicate sub_factor_id",
                    data,
                )
            sub_factor_ids.add(sub_factor_id)
            for field_name in ("factor_code", "sub_factor_code", "name", "feature_column", "factor_bar_interval"):
                self._required_response_string(member, field_name, "factor combo work order member")
            direction = self._required_response_int(member, "direction", "factor combo work order member")
            if direction not in {-1, 1}:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "work order member direction must be -1 or 1",
                    data,
                )
            for field_name in ("definition_snapshot", "metrics_snapshot", "validity_snapshot"):
                if field_name in member and member[field_name] is not None and not isinstance(member[field_name], dict):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"work order member {field_name} must be an object or null",
                        data,
                    )
        return data

    def require_submitted_form(self, response: requests.Response, session_id: int) -> SubmittedForm:
        """把成功的表单响应转换为可继续编排的表单对象。

        参数 ``response`` 是表单提交接口响应，``session_id`` 是本次请求使用的会话主键。
        返回 ``SubmittedForm`` 并登记资源；响应非 2xx、字段缺失或数据库回读失败时抛出 ``RuntimeError``。
        """

        data = self._require_success_data(response, {200, 202}, "submit factor combo form")
        self._require_response_fields(
            data,
            ("form_id", "form_no", "status", "factor_combo_pool_id"),
            "submitted factor combo form",
        )
        form_id = self._required_response_int(data, "form_id", "submitted factor combo form")
        pool_id = self._required_response_int(data, "factor_combo_pool_id", "submitted factor combo form")
        form_no = self._required_response_string(data, "form_no", "submitted factor combo form")
        status = self._required_response_string(data, "status", "submitted factor combo form")
        if status not in _FORM_STATUS_VALUES:
            raise RuntimeError(f"submitted factor combo form response has invalid status: {status!r}")
        submitted = SubmittedForm(
            session_id=int(session_id),
            form_id=form_id,
            pool_id=pool_id,
            status=status,
            form_no=form_no,
        )
        # 200 可能是首次 POST 已落库但响应丢失后的幂等重放；Scope 只会接受归属于当前测试会话的表单。
        self.scope.track_form(submitted.session_id, submitted.form_id)
        return submitted

    def create_form_with_sub_factors(
        self,
        *,
        method_groups: Any = _METHOD_GROUPS_UNSET,
        objectives: list[dict[str, Any]] | None = None,
        configuration_overrides: dict[str, Any] | None = None,
    ) -> tuple[SubmittedForm, tuple[SubFactorChoice, SubFactorChoice]]:
        """创建一个由两个真实子因子组成的独立组合表单。

        不接收参数。
        返回表单和实际选中的两个子因子；测试数据库不足两个可用子因子或接口准备失败时抛出 ``RuntimeError``。
        """

        choices = self._repository.find_sub_factor_pair()
        if choices is None:
            raise RuntimeError("Test database has fewer than two usable sub-factors")
        submitted, _ = self.create_form_for_factor_names(
            [choice.sub_factor_name for choice in choices],
            method_groups=method_groups,
            objectives=objectives,
            configuration_overrides=configuration_overrides,
        )
        return submitted, choices

    def create_form_with_parent(
        self,
        *,
        method_groups: Any = _METHOD_GROUPS_UNSET,
        objectives: list[dict[str, Any]] | None = None,
        configuration_overrides: dict[str, Any] | None = None,
    ) -> tuple[SubmittedForm, ParentFactorChoice]:
        """创建一个只选择母因子的独立组合表单。

        不接收参数。
        返回表单和母因子及其完整子因子集合；测试库没有足够关联数据或接口准备失败时抛出 ``RuntimeError``。
        """

        session_id = self.create_session()
        parents = self._repository.ensure_parent_choices_for_test(
            session_id,
            minimum_sub_factors=2,
            minimum_parent_count=1,
        )
        parent = parents[0]
        payload = self.build_form_payload(
            session_id,
            [parent.factor_name],
            is_sub_factor=0,
            method_groups=method_groups,
            objectives=objectives,
            configuration_overrides=configuration_overrides,
        )
        submitted = self.require_submitted_form(self.submit_form(payload), session_id)
        return submitted, parent

    def create_form_with_multiple_parents(
        self,
        *,
        method_groups: Any = _METHOD_GROUPS_UNSET,
        objectives: list[dict[str, Any]] | None = None,
        configuration_overrides: dict[str, Any] | None = None,
    ) -> tuple[SubmittedForm, tuple[ParentFactorChoice, ...]]:
        """创建选择多个母因子的组合表单，并确保测试前置数据完整。

        不接收因子名称参数；服务优先使用真实母因子，测试库不足时仅在当前测试环境创建带唯一标记的临时母因子
        关系。返回已提交表单和实际使用的母因子集合；无法准备完整前置或接口调用失败时抛出 ``RuntimeError``，
        不将数据不足转换为跳过。
        """

        session_id = self.create_session()
        parents = self._repository.ensure_parent_choices_for_test(
            session_id,
            minimum_sub_factors=2,
            minimum_parent_count=2,
        )
        payload = self.build_form_payload(
            session_id,
            [parent.factor_name for parent in parents],
            is_sub_factor=0,
            method_groups=method_groups,
            objectives=objectives,
            configuration_overrides=configuration_overrides,
        )
        submitted = self.require_submitted_form(self.submit_form(payload), session_id)
        return submitted, parents

    def submit_mixed_parent_and_sub_factor_for_rejection(
        self,
        *,
        method_groups: Any = _METHOD_GROUPS_UNSET,
        objectives: list[dict[str, Any]] | None = None,
        configuration_overrides: dict[str, Any] | None = None,
    ) -> tuple[requests.Response, int, ParentFactorChoice]:
        """提交母因子与子因子混选请求，作为当前规则下的拒绝场景。

        参数 ``method_groups``、``objectives`` 和 ``configuration_overrides`` 与表单接口请求配置一致。
        返回原始 HTTP 响应、请求使用的会话 ID 和实际选择的母因子；该方法不要求提交成功，便于用例直接
        校验 HTTP 状态、错误响应和数据库未落库结果。测试数据准备或会话创建失败时抛出 ``RuntimeError``。
        """

        session_id = self.create_session()
        parents = self._repository.ensure_parent_choices_for_test(
            session_id,
            minimum_sub_factors=2,
            minimum_parent_count=1,
        )
        parent = parents[0]
        payload = self.build_form_payload(
            session_id,
            [parent.factor_name, parent.sub_factors[0].sub_factor_name],
            is_sub_factor=0,
            method_groups=method_groups,
            objectives=objectives,
            configuration_overrides=configuration_overrides,
        )
        return self.submit_form(payload), session_id, parent

    def create_worker_form(
        self,
        *,
        method_groups: Any = _METHOD_GROUPS_UNSET,
        objectives: list[dict[str, Any]] | None = None,
        configuration_overrides: dict[str, Any] | None = None,
    ) -> WorkerForm:
        """创建表单并通过兼容认领接口准备 Worker 回调前置。

        不接收参数。
        返回包含后端分配组合 ID、组件、实验 ID 和 Artifact 的 ``WorkerForm``；认领失败时抛出 ``RuntimeError``。
        """

        submitted, _ = self.create_form_with_sub_factors(
            method_groups=method_groups,
            objectives=objectives,
            configuration_overrides=configuration_overrides,
        )
        return self._claim_initial_worker_form(submitted)

    def create_worker_form_from_parent(self) -> WorkerForm:
        """创建母因子展开后的多成员表单并准备 Worker 回调前置。

        不接收参数。
        返回包含至少两个同一母因子来源池成员的 ``WorkerForm``；测试数据库没有满足展开条件的母因子或认领失败时抛出 ``RuntimeError``。
        """

        submitted, _ = self.create_form_with_parent()
        return self._claim_initial_worker_form(submitted)

    def create_worker_form_for_factor_names(
        self,
        factor_names: Sequence[str],
        *,
        is_sub_factor: int = 1,
        method_groups: Any = _METHOD_GROUPS_UNSET,
        objectives: list[dict[str, Any]] | None = None,
        configuration_overrides: dict[str, Any] | None = None,
    ) -> WorkerForm:
        """按指定真实因子名称创建并认领一个 Worker 前置表单。

        参数 ``factor_names``、``is_sub_factor``、``method_groups``、``objectives`` 和 ``configuration_overrides`` 与表单
        接口语义一致。返回含兼容运行 ID、组合 ID、组件和 Artifact 的 ``WorkerForm``；只用于独立 Worker 合约或确定性
        跨接口场景，不代表真实 Agent 计算结果。
        """

        submitted, _ = self.create_form_for_factor_names(
            factor_names,
            is_sub_factor=is_sub_factor,
            method_groups=method_groups,
            objectives=objectives,
            configuration_overrides=configuration_overrides,
        )
        return self._claim_initial_worker_form(submitted)

    def _claim_initial_worker_form(self, submitted: SubmittedForm) -> WorkerForm:
        """通过兼容认领接口为已提交表单准备初始 Worker 上下文。

        参数 ``submitted`` 是已提交的组合表单。
        返回包含运行 ID、组合 ID、组件和 Artifact 的 ``WorkerForm``；认领失败或响应字段缺失时抛出 ``RuntimeError``。
        """

        pipeline_run_id = f"legacy-simulated-form-{submitted.form_id}-initial"
        response = self._factor_combo_api.claim_legacy_pipeline(
            submitted.form_id,
            {
                "session_id": submitted.session_id,
                "pipeline_run_id": pipeline_run_id,
                "simulation_mode": True,
            },
        )
        data = self._require_success_data(response, {200}, "claim initial compatibility pipeline")
        return self._worker_form_from_claim(submitted, data)

    def build_components_from_pool(self, form_id: int, *, reverse: bool = False) -> list[dict[str, Any]]:
        """根据锁定池成员构造 Worker 版本接口的两个组件。

        参数 ``form_id`` 是已提交表单主键，``reverse`` 为真时反转组件请求顺序以验证哈希规范化。
        返回至少两个包含真实母因子、子因子、方向、转换和权重的组件字典；池成员不足时抛出 ``RuntimeError``。
        """

        members = self._repository.get_pool_members(form_id)
        if len(members) < 2:
            raise RuntimeError(f"Factor combo pool has fewer than two members: form={form_id}")
        components: list[dict[str, Any]] = []
        for index, member in enumerate(members[:2]):
            parent_factor_id = member.get("parent_factor_id")
            if parent_factor_id is None:
                raise RuntimeError(f"Pool member has no parent factor: {member}")
            components.append(
                {
                    "component_factor_id": int(parent_factor_id),
                    "component_sub_factor_id": int(member["sub_factor_id"]),
                    "direction": 1 if index == 0 else -1,
                    "transform": {"normalization": "zscore", "window": 24 + index},
                    "weight": 0.4 if index == 0 else -0.2,
                }
            )
        if reverse:
            components.reverse()
        return components

    def create_initial_version_request(
        self,
        worker_form: WorkerForm,
        *,
        components: list[dict[str, Any]] | None = None,
        generation_method: str = "ml",
        combo_id: int | None = None,
        pipeline_run_id: str | None = None,
    ) -> requests.Response:
        """发送初始组合版本 Worker 回写请求。

        参数 ``worker_form`` 提供表单和模拟运行 ID，``components`` 是可选组件列表，``generation_method`` 是生成方式，
        ``combo_id`` 是可选业务组合 ID，``pipeline_run_id`` 可覆盖认领值以验证运行关联；两者缺省时使用认领结果。
        返回原始 HTTP 响应；响应由用例或 ``require_combo_version`` 处理。
        """

        payload = self.build_initial_version_payload(
            worker_form,
            components=components,
            generation_method=generation_method,
            combo_id=combo_id,
            pipeline_run_id=pipeline_run_id,
        )
        return self._factor_combo_api.create_initial_version(worker_form.submitted.form_id, payload)

    def build_initial_version_payload(
        self,
        worker_form: WorkerForm,
        *,
        components: list[dict[str, Any]] | None = None,
        generation_method: str = "ml",
        combo_id: int | None = None,
        pipeline_run_id: str | None = None,
    ) -> dict[str, Any]:
        """构造初始组合版本接口的完整请求体。

        参数 ``worker_form`` 提供表单、组合和认领运行上下文，``components`` 是可选组件列表，``generation_method`` 是
        生成方式，``combo_id`` 是可选业务组合 ID，``pipeline_run_id`` 可覆盖认领值。返回可直接发送给初始版本接口的
        JSON 字典；该方法不发送网络请求，也不执行数据库写入。
        """

        return {
            "pipeline_run_id": pipeline_run_id or worker_form.pipeline_run_id,
            "combo_id": combo_id if combo_id is not None else worker_form.combo_id,
            "generation_method": generation_method,
            "components": components if components is not None else list(worker_form.components),
        }

    def require_combo_version(
        self,
        response: requests.Response,
        worker_form: WorkerForm,
        *,
        expected_components: Sequence[Mapping[str, Any]] | None = None,
    ) -> ComboVersion:
        """把版本接口成功响应转换为组合版本对象。

        参数 ``response`` 是初始版本接口响应，``worker_form`` 是对应独立表单上下文，``expected_components`` 是
        本次请求实际提交的组件集合；未传入时兼容使用认领表单中的组件集合。返回 ``ComboVersion``；响应非成功或
        缺少版本字段时抛出 ``RuntimeError``。调用方传入自定义组件时必须传入该集合，不能让解析器回退到旧组件。
        """

        data = self._require_success_data(response, {200, 201}, "create factor combo version")
        self._require_response_fields(data, _VERSION_RESULT_REQUIRED_FIELDS, "factor combo version")
        form_id = self._required_response_int(data, "form_id", "factor combo version")
        if form_id != worker_form.submitted.form_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version form_id does not match the claimed form",
                data,
            )
        form_status = self._required_response_string(data, "form_status", "factor combo version")
        if form_status not in {"processing", "completed"}:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version form_status is outside the documented enum",
                data,
            )
        pipeline_run_id = self._required_response_string(data, "pipeline_run_id", "factor combo version")
        if pipeline_run_id != worker_form.pipeline_run_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version pipeline_run_id does not match the claimed run",
                data,
            )
        combo_id = self._required_response_int(data, "combo_id", "factor combo version")
        if combo_id != worker_form.combo_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version combo_id does not match the claimed combination",
                data,
            )
        combo_family_key = self._required_response_string(data, "combo_family_key", "factor combo version")
        pool_id = self._required_response_int(data, "pool_id", "factor combo version")
        if pool_id != worker_form.submitted.pool_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version pool_id does not match the claimed pool",
                data,
            )
        combo_version_hash = self._required_sha256_or_failure(
            data.get("combo_version_hash"),
            FlowOutcome.FAIL_CONTRACT,
            "factor combo version combo_version_hash is missing or invalid",
            data,
        )
        combo_status = self._required_response_string(data, "combo_status", "factor combo version")
        if combo_status not in _COMBO_STATUS_VALUES:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version combo_status is outside the documented enum",
                data,
            )
        component_count = self._required_response_int(data, "component_count", "factor combo version")
        component_source = worker_form.components if expected_components is None else expected_components
        if component_count != len(component_source):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version component_count does not match the claimed component set",
                {
                    "component_count": component_count,
                    "expected_component_count": len(component_source),
                    "data": data,
                },
            )
        idempotent_replay = self._required_response_bool(data, "idempotent_replay", "factor combo version")
        expected_replay = response.status_code == 200
        if idempotent_replay is not expected_replay:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo version HTTP status and idempotent_replay are inconsistent",
                data,
            )
        return ComboVersion(
            worker_form=worker_form,
            version_id=self._required_response_int(data, "factor_combo_version_id", "factor combo version"),
            combo_id=combo_id,
            combo_family_key=combo_family_key,
            pool_id=pool_id,
            combo_version_hash=combo_version_hash,
            form_status=form_status,
            combo_status=combo_status,
            component_count=component_count,
            idempotent_replay=idempotent_replay,
        )

    def create_worker_version(self, worker_form: WorkerForm) -> ComboVersion:
        """为独立 Worker 表单创建一个初始候选版本。

        参数 ``worker_form`` 是已锁定因子池且未关联版本的临时表单。
        返回创建的 ``ComboVersion``；接口前置条件或数据契约不满足时抛出 ``RuntimeError``。
        """

        response = self.create_initial_version_request(worker_form)
        return self.require_combo_version(response, worker_form, expected_components=worker_form.components)

    def build_experiment_payload(
        self,
        worker_form: WorkerForm,
        *,
        valid: bool = True,
        failure_reason: str | None = None,
        artifact_uri: str | None = None,
        artifact_sha256: str | None = None,
        experiment_config: Any = _EXPERIMENT_CONFIG_UNSET,
    ) -> dict[str, Any]:
        """构造实验结果写入接口的完整请求体。

        参数 ``worker_form`` 提供表单和运行 ID，``valid`` 与 ``failure_reason`` 描述实验结论，``artifact_uri`` 和
        ``artifact_sha256`` 可覆盖认领接口返回的产物标识；``experiment_config`` 省略时使用默认对象，显式传入
        ``None`` 时生成 JSON ``null``，也可以传入对象覆盖默认配置。返回不包含路径 ``experiment_id`` 的实验请求字典。
        """

        payload: dict[str, Any] = {
            "form_id": worker_form.submitted.form_id,
            "pipeline_run_id": worker_form.pipeline_run_id,
            "data_version": "autotest-crypto-1h-v1",
            "data_directory": "s3://test-factor-combo/autotest-data/crypto/1h",
            "evaluation_config": {
                "universe": "main",
                "bar_interval": "1h",
                "transaction_cost": 0.001,
                "slippage": 0.0002,
            },
            "metrics": {
                "in_sample": {"ts_ic": 0.12, "icir": 0.91, "sharpe": 1.52, "max_drawdown": -0.073},
                "out_of_sample": {"ts_ic": 0.09, "icir": 0.73, "sharpe": 1.31, "max_drawdown": -0.087},
                "overall": {"return_rate": 0.28, "annual_turnover": 0.76, "rolling_win_rate": 0.79},
            },
            "experiment_description": "autotest combination experiment",
            "implementation_method": "elastic_net",
            "experiment_conclusion": "autotest experiment result",
            "composite_factor_score": 0.8235,
            "valid": valid,
            "remark": "autotest worker contract",
            "artifact": {
                "type": "bundle",
                "uri": artifact_uri or worker_form.artifact_uri,
                "sha256": artifact_sha256 or worker_form.artifact_sha256,
            },
            "train_config": {"model": "ElasticNet", "alpha": 0.001, "l1_ratio": 0.5, "max_iterations": 10000},
            "failure_reason": failure_reason if not valid else None,
        }
        if experiment_config is _EXPERIMENT_CONFIG_UNSET:
            payload["experiment_config"] = {
                "algorithm": "ElasticNet",
                "random_seed": 42,
                "component_count": len(worker_form.components),
            }
        elif experiment_config is None:
            payload["experiment_config"] = None
        elif isinstance(experiment_config, Mapping):
            payload["experiment_config"] = dict(experiment_config)
        else:
            # 保留调用方传入的非法类型，交给接口契约用例验证，而不是在构造器中替接口做业务判断。
            payload["experiment_config"] = experiment_config
        return payload

    @staticmethod
    def build_equivalent_artifact_uris(artifact_uri: str) -> tuple[str, str]:
        """为同一个可读本地产物生成两个不同的路径表示。

        参数 ``artifact_uri`` 是数据库中已有的后端绝对路径产物 URI；返回两个只在字符串形式上不同、解析后仍指向
        同一文件的路径，供验证“不同 URI、相同 SHA256 可以登记”使用。输入不是带文件名的绝对路径时抛出
        ``ValueError``，不发起网络或数据库操作。
        """

        normalized_uri = str(artifact_uri).strip()
        if not normalized_uri.startswith("/") or "/" not in normalized_uri.rstrip("/"):
            raise ValueError("artifact_uri must be an absolute readable path")
        source_directory, source_name = normalized_uri.rsplit("/", 1)
        if not source_directory or not source_name:
            raise ValueError("artifact_uri must include a directory and file name")
        first_dots = "/".join(["", ".", "."])
        second_dots = "/".join(["", ".", ".", "."])
        return (
            f"{source_directory}{first_dots}/{source_name}",
            f"{source_directory}{second_dots}/{source_name}",
        )

    def write_experiment_request(
        self,
        experiment_id: str,
        payload: dict[str, Any],
    ) -> requests.Response:
        """发送组合实验结果写入请求。

        参数 ``experiment_id`` 是接口路径幂等键，``payload`` 是完整实验 JSON 请求体。
        返回原始 HTTP 响应；状态和数据库映射由对应测试用例断言。
        """

        return self._factor_combo_api.write_experiment(experiment_id, payload)

    def require_completed_experiment(
        self,
        response: requests.Response,
        version: ComboVersion,
        experiment_id: str,
        *,
        expected_valid: bool | None = None,
    ) -> CompletedExperiment:
        """把有效实验写入成功响应转换为反馈或登记前置对象。

        参数 ``response`` 是实验接口响应，``version`` 是已创建组合版本，``experiment_id`` 是本次请求幂等键。
        返回 ``CompletedExperiment``；响应非 201 或缺少实验 ID 时抛出 ``RuntimeError``。
        """

        data = self._require_success_data(response, {200, 201}, "write factor combo experiment")
        self._require_response_fields(data, _EXPERIMENT_RESULT_REQUIRED_FIELDS, "factor combo experiment")
        experiment_info_id = self._required_response_int(data, "experiment_info_id", "factor combo experiment")
        returned_experiment_id = self._required_response_string(data, "experiment_id", "factor combo experiment")
        if returned_experiment_id != str(experiment_id).strip():
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment response experiment_id does not match the path id",
                data,
            )
        returned_form_id = self._required_response_int(data, "form_id", "factor combo experiment")
        if returned_form_id != version.worker_form.submitted.form_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment response form_id does not match the claimed form",
                data,
            )
        returned_version_id = self._required_response_int(
            data,
            "factor_combo_version_id",
            "factor combo experiment",
        )
        if returned_version_id != version.version_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment response version does not match the created version",
                data,
            )
        returned_combo_id = self._required_response_int(data, "combo_id", "factor combo experiment")
        if returned_combo_id != version.combo_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment response combo_id does not match the created version",
                data,
            )
        form_status = self._required_response_string(data, "form_status", "factor combo experiment")
        if form_status != "completed":
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment response form_status must be completed",
                data,
            )
        combo_status = self._required_response_string(data, "combo_status", "factor combo experiment")
        if combo_status not in _COMBO_STATUS_VALUES:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment response combo_status is outside the documented enum",
                data,
            )
        idempotent_replay = self._required_response_bool(data, "idempotent_replay", "factor combo experiment")
        if idempotent_replay is not (response.status_code == 200):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment HTTP status and idempotent_replay are inconsistent",
                data,
            )
        if "experiment_valid" in data:
            valid = self._required_response_bool(data, "experiment_valid", "factor combo experiment")
            if expected_valid is not None and valid is not expected_valid:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo experiment response validity differs from the submitted request",
                    data,
                )
        elif expected_valid is not None:
            valid = expected_valid
        else:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo experiment response does not expose validity and no request expectation was supplied",
                data,
            )
        return CompletedExperiment(
            version=version,
            experiment_id=returned_experiment_id,
            experiment_info_id=experiment_info_id,
            form_status=form_status,
            valid=valid,
        )

    def create_completed_experiment(
        self,
        *,
        valid: bool = True,
        failure_reason: str | None = None,
    ) -> CompletedExperiment:
        """创建一条实验结果作为反馈和登记接口的独立前置数据。

        参数 ``valid`` 指定实验有效标志，``failure_reason`` 是无效实验的失败原因。
        返回已关联 candidate 组合版本且表单完成的 ``CompletedExperiment``；任一步失败时抛出 ``RuntimeError``。
        """

        worker_form = self.create_worker_form()
        version = self.create_worker_version(worker_form)
        payload = self.build_experiment_payload(
            worker_form,
            valid=valid,
            failure_reason=failure_reason or ("autotest calculation failed" if not valid else None),
        )
        response = self.write_experiment_request(worker_form.experiment_id, payload)
        completed = self.require_completed_experiment(
            response,
            version,
            worker_form.experiment_id,
            expected_valid=valid,
        )
        return CompletedExperiment(
            version=completed.version,
            experiment_id=completed.experiment_id,
            experiment_info_id=completed.experiment_info_id,
            form_status=completed.form_status,
            valid=valid,
        )

    def build_feedback_payload(self, experiment: CompletedExperiment, feedback: str | None = None) -> dict[str, Any]:
        """构造报告反馈接口请求体。

        参数 ``experiment`` 是表单已完成且实验有效的组合链路，``feedback`` 是可选反馈正文。
        返回完整反馈 JSON 请求体；缺省正文包含唯一后缀，避免跨测试幂等键碰撞。
        """

        return {
            "session_id": experiment.version.worker_form.submitted.session_id,
            "form_id": experiment.version.worker_form.submitted.form_id,
            "pipeline_run_id": experiment.version.worker_form.pipeline_run_id,
            "reply": 2,
            "feedback": feedback or f"autotest feedback {uuid4().hex}",
        }

    def submit_feedback_request(self, payload: dict[str, Any]) -> requests.Response:
        """发送组合报告反馈请求。

        参数 ``payload`` 是反馈接口完整 JSON 请求体。
        返回原始 HTTP 响应；响应状态和状态流转由反馈测试用例断言。
        """

        return self._factor_combo_api.submit_feedback(payload)

    def require_feedback_id(self, response: requests.Response) -> int:
        """从反馈成功响应中读取反馈主键。

        参数 ``response`` 是反馈接口响应。
        返回 ``feedback_id``；响应非 200 或缺少 ID 时抛出 ``RuntimeError``。
        """

        data = self.require_feedback_response(response)
        return self._required_response_int(data, "feedback_id", "factor combo feedback")

    def require_feedback_response(
        self,
        response: requests.Response,
        *,
        expected_form_id: int | None = None,
        expected_experiment_info_id: int | None = None,
        expected_status: str | None = None,
    ) -> dict[str, Any]:
        """严格解析反馈成功响应并核对反馈、表单和实验身份。

        参数 ``response`` 是反馈接口原始响应；``expected_form_id`` 和 ``expected_experiment_info_id`` 是可选的当前链路
        身份；``expected_status`` 用于要求本次流程返回指定反馈状态。返回完整的 ``data`` 字典；缺失必填字段、类型
        不严格、状态码或业务身份不一致时抛出 ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        data = self._require_success_data(response, {200}, "submit factor combo feedback")
        self._require_response_fields(data, _FEEDBACK_RESULT_REQUIRED_FIELDS, "factor combo feedback")
        if self._required_response_bool(data, "feedback_recorded", "factor combo feedback") is not True:
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, "feedback_recorded must be true", data)
        self._required_response_bool(data, "idempotent_replay", "factor combo feedback")
        feedback_status = self._required_response_string(data, "feedback_status", "factor combo feedback")
        if feedback_status not in _FEEDBACK_STATUS_VALUES:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback_status is outside the documented enum",
                data,
            )
        if expected_status is not None and feedback_status != expected_status:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback_status does not match the expected flow state",
                data,
            )
        reply = self._required_response_int(data, "reply", "factor combo feedback")
        if reply != 2:
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, "feedback reply must be 2", data)
        form_id = self._required_response_int(data, "form_id", "factor combo feedback")
        if expected_form_id is not None and form_id != int(expected_form_id):
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, "feedback form_id does not match the flow", data)
        experiment_info_id = self._required_response_int(
            data,
            "factor_combo_experiment_info_id",
            "factor combo feedback",
        )
        if expected_experiment_info_id is not None and experiment_info_id != int(expected_experiment_info_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback experiment_info_id does not match the flow",
                data,
            )
        form_status = self._required_response_string(data, "form_status", "factor combo feedback")
        if form_status not in {"processing", "completed", "failed"}:
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, "feedback form_status is invalid", data)
        if self._required_response_bool(data, "experiment_valid", "factor combo feedback") is not False:
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, "feedback experiment_valid must be false", data)
        self._required_response_int(data, "feedback_id", "factor combo feedback")
        self._required_response_int(data, "feedback_round", "factor combo feedback")
        self._required_response_int(data, "rejected_factor_combo_version_id", "factor combo feedback")
        return data






















    def claim_feedback_for_worker(
        self,
        experiment: CompletedExperiment,
        feedback_id: int,
    ) -> ClaimedFeedback:
        """通过兼容认领接口取得 Feedback 下一轮任务。

        参数 ``experiment`` 是反馈来源实验，``feedback_id`` 是反馈接口返回的主键。
        返回带下一轮运行 ID、组件和 Artifact 的 ``ClaimedFeedback``；认领失败时抛出 ``RuntimeError``。
        """

        submitted = experiment.version.worker_form.submitted
        pipeline_run_id = f"legacy-simulated-form-{submitted.form_id}-feedback-{feedback_id}"
        response = self._factor_combo_api.claim_legacy_pipeline(
            submitted.form_id,
            {
                "session_id": submitted.session_id,
                "pipeline_run_id": pipeline_run_id,
                "feedback_id": feedback_id,
                "simulation_mode": True,
            },
        )
        data = self._require_success_data(response, {200}, "claim feedback compatibility pipeline")
        worker_form = self._worker_form_from_claim(submitted, data)
        return ClaimedFeedback(experiment=experiment, feedback_id=feedback_id, worker_form=worker_form)

    def create_claimed_feedback(self, feedback_text: str | None = None) -> ClaimedFeedback:
        """创建有效实验、提交不满意反馈并认领下一轮任务。

        参数 ``feedback_text`` 是可选反馈正文。
        返回可直接用于下一版本接口的 ``ClaimedFeedback``；任一步失败时抛出 ``RuntimeError``。
        """

        pending_feedback = self.create_pending_feedback(feedback_text)
        return self.claim_feedback_for_worker(pending_feedback.experiment, pending_feedback.feedback_id)

    def create_pending_feedback(
        self,
        feedback_text: str | None = None,
        *,
        valid_experiment: bool = True,
    ) -> PendingFeedback:
        """创建完成实验并提交一条尚未被 Worker 认领的反馈。

        参数 ``feedback_text`` 是可选反馈正文，``valid_experiment`` 控制来源实验写入时的有效标志。
        返回包含来源实验和反馈主键的 ``PendingFeedback``；实验或反馈接口失败时抛出 ``RuntimeError``。
        """

        experiment = self.create_completed_experiment(
            valid=valid_experiment,
            failure_reason="autotest source experiment failed" if not valid_experiment else None,
        )
        response = self.submit_feedback_request(self.build_feedback_payload(experiment, feedback_text))
        feedback_data = self.require_feedback_response(
            response,
            expected_form_id=experiment.version.worker_form.submitted.form_id,
            expected_experiment_info_id=experiment.experiment_info_id,
            expected_status="pending",
        )
        feedback_id = self._required_response_int(feedback_data, "feedback_id", "factor combo feedback")
        return PendingFeedback(experiment=experiment, feedback_id=feedback_id)

    def create_next_version_request(
        self,
        feedback: ClaimedFeedback,
        *,
        components: list[dict[str, Any]] | None = None,
        generation_method: str = "ml",
        pipeline_run_id: str | None = None,
    ) -> requests.Response:
        """发送 Feedback 下一轮组合版本请求。

        参数 ``feedback`` 是已通过兼容接口认领的独立反馈，``components`` 是可选新组件，
        ``generation_method`` 是下一轮生成方式，``pipeline_run_id`` 可覆盖认领值以验证关联校验。
        返回原始 HTTP 响应；状态由用例断言。
        """

        payload = self.build_next_version_payload(
            feedback,
            components=components,
            generation_method=generation_method,
            pipeline_run_id=pipeline_run_id,
        )
        return self._factor_combo_api.create_next_version(feedback.feedback_id, payload)

    def build_next_version_payload(
        self,
        feedback: ClaimedFeedback,
        *,
        components: list[dict[str, Any]] | None = None,
        generation_method: str = "ml",
        pipeline_run_id: str | None = None,
    ) -> dict[str, Any]:
        """构造下一轮组合版本接口的完整请求体。

        参数 ``feedback`` 提供反馈、表单和认领运行上下文，``components`` 是可选的新组件列表，``generation_method`` 是
        生成方式，``pipeline_run_id`` 可覆盖认领值。返回可直接发送给下一版本接口的 JSON 字典；该方法不发送网络请求，
        也不执行数据库写入。
        """

        return {
            "pipeline_run_id": pipeline_run_id or feedback.worker_form.pipeline_run_id,
            "generation_method": generation_method,
            "components": components if components is not None else list(feedback.worker_form.components),
        }

    def require_next_version(
        self,
        response: requests.Response,
        feedback: ClaimedFeedback,
        *,
        expected_components: Sequence[Mapping[str, Any]] | None = None,
    ) -> ComboVersion:
        """把下一轮版本接口成功响应转换为组合版本对象。

        参数 ``response`` 是下一轮版本接口响应，``feedback`` 是对应的反馈上下文，``expected_components`` 是本次
        请求实际提交的新组件集合；未传入时兼容使用认领结果中的组件集合。返回新 ``ComboVersion``；响应非成功或
        字段不完整时抛出 ``RuntimeError``。自定义组件请求必须传入本次集合，不能按上一轮组件数量判断响应。
        """

        data = self._require_success_data(response, {200, 201}, "create next factor combo version")
        self._require_response_fields(data, _NEXT_VERSION_RESULT_REQUIRED_FIELDS, "next factor combo version")
        form_id = self._required_response_int(data, "form_id", "next factor combo version")
        if form_id != feedback.worker_form.submitted.form_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version form_id does not match the feedback form",
                data,
            )
        form_status = self._required_response_string(data, "form_status", "next factor combo version")
        if form_status not in {"processing", "completed"}:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version form_status is outside the documented enum",
                data,
            )
        pipeline_run_id = self._required_response_string(data, "pipeline_run_id", "next factor combo version")
        if pipeline_run_id != feedback.worker_form.pipeline_run_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version pipeline_run_id does not match the claimed feedback run",
                data,
            )
        returned_feedback_id = self._required_response_int(data, "feedback_id", "next factor combo version")
        if returned_feedback_id != feedback.feedback_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version feedback_id does not match the claimed feedback",
                data,
            )
        feedback_round = self._required_response_int(data, "feedback_round", "next factor combo version")
        if feedback.worker_form.feedback_round is not None and feedback_round != feedback.worker_form.feedback_round:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version feedback_round does not match the claimed feedback",
                {
                    "expected_feedback_round": feedback.worker_form.feedback_round,
                    "actual_feedback_round": feedback_round,
                    "data": data,
                },
            )
        feedback_status = self._required_response_string(data, "feedback_status", "next factor combo version")
        if feedback_status not in _FEEDBACK_STATUS_VALUES:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version feedback_status is invalid",
                data,
            )
        combo_id = self._required_response_int(data, "combo_id", "next factor combo version")
        if combo_id != feedback.experiment.version.combo_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version combo_id does not match the source version",
                data,
            )
        combo_family_key = self._required_response_string(data, "combo_family_key", "next factor combo version")
        pool_id = self._required_response_int(data, "pool_id", "next factor combo version")
        if pool_id != feedback.experiment.version.pool_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version pool_id does not match the source version",
                data,
            )
        combo_version_hash = self._required_sha256_or_failure(
            data.get("combo_version_hash"),
            FlowOutcome.FAIL_CONTRACT,
            "next factor combo version combo_version_hash is missing or invalid",
            data,
        )
        source_version = feedback.experiment.version
        if self._required_response_int(data, "factor_combo_version_id", "next factor combo version") == source_version.version_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version must have a new concrete version ID",
                data,
            )
        if combo_version_hash == source_version.combo_version_hash:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version must have a new content hash",
                data,
            )
        combo_status = self._required_response_string(data, "combo_status", "next factor combo version")
        if combo_status not in _COMBO_STATUS_VALUES:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version combo_status is outside the documented enum",
                data,
            )
        component_count = self._required_response_int(data, "component_count", "next factor combo version")
        component_source = feedback.worker_form.components if expected_components is None else expected_components
        if component_count != len(component_source):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version component_count does not match the claimed component set",
                {
                    "component_count": component_count,
                    "expected_component_count": len(component_source),
                    "data": data,
                },
            )
        idempotent_replay = self._required_response_bool(data, "idempotent_replay", "next factor combo version")
        if idempotent_replay is not (response.status_code == 200):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "next factor combo version HTTP status and idempotent_replay are inconsistent",
                data,
            )
        return ComboVersion(
            worker_form=feedback.worker_form,
            version_id=self._required_response_int(data, "factor_combo_version_id", "next factor combo version"),
            combo_id=combo_id,
            combo_family_key=combo_family_key,
            pool_id=pool_id,
            combo_version_hash=combo_version_hash,
            form_status=form_status,
            combo_status=combo_status,
            component_count=component_count,
            idempotent_replay=idempotent_replay,
        )

    def build_register_payload(
        self,
        experiment: CompletedExperiment,
        *,
        metrics_available: bool = True,
        metric_mode: str = "time_series",
        validity_state: str = "unknown",
        factor_bar_interval: str = "4h",
        factor_window_bars: str | int = "24",
    ) -> dict[str, Any]:
        """构造组合报告登记接口请求体。

        参数 ``experiment`` 是实验完成的组合链路，``metrics_available`` 决定绩效字段使用数值还是全部为空，
        ``metric_mode`` 只能是 ``time_series`` 或 ``cross_sectional``，``validity_state`` 支持 ``invalid``（两个维度均失效）
        和 ``unknown``；登记接口的前端有效性快照不应由 Worker 测试伪造为 ``valid``，真实 Pipeline 结果必须通过
        ``build_real_register_payload`` 原样传递。``factor_bar_interval`` 和 ``factor_window_bars`` 是登记有效性快照
        及复合子因子要写入的周期参数。默认构造一个状态未判定但结构完整的可登记结果；指标模式或有效性状态非法时抛出
        ``ValueError``，真实 Agent 流程不得调用此模拟构造方法。
        """

        normalized_metric_mode = str(metric_mode).strip().lower()
        if normalized_metric_mode not in {"time_series", "cross_sectional"}:
            raise ValueError("metric_mode must be time_series or cross_sectional")
        version = experiment.version
        components = self._repository.get_components(version.version_id)
        report_components = [
            {
                "factor_code": str(component.get("factor_name") or component["component_factor_id"]),
                "sub_factor_code": str(
                    component.get("sub_factor_name") or component["component_sub_factor_id"]
                ),
                "name": str(component.get("sub_factor_name") or component["component_sub_factor_id"]),
                "direction": int(component["direction"]),
                "weight": abs(float(component["weight"])) if component.get("weight") is not None else None,
            }
            for component in components
        ]
        performance = {
            "metrics_status": "measured" if metrics_available else "unavailable",
            "ts_ic": 0.1 if metrics_available and normalized_metric_mode == "time_series" else None,
            "return_rate": 0.3 if metrics_available else None,
            "annualized_return": 0.42 if metrics_available else None,
            "out_of_sample_icir": 0.87 if metrics_available else None,
            "net_sharpe": 1.39 if metrics_available else None,
            "benchmark_sharpe": 1.12 if metrics_available else None,
            "max_drawdown": -0.099 if metrics_available else None,
            "calmar": 4.24 if metrics_available else None,
            "profit_loss_ratio": 1.45 if metrics_available else None,
            "annual_turnover": 0.78 if metrics_available else None,
            "positive_return_rate": 0.79 if metrics_available else None,
            "observations": 694 if metrics_available else None,
            "trade_observations": 350 if metrics_available else None,
            "decay_ratio": 0.85 if metrics_available else None,
            "metric_mode": normalized_metric_mode,
            "cs_rank_ic": 0.08
            if metrics_available and normalized_metric_mode == "cross_sectional"
            else None,
            "cs_icir": 1.92 if metrics_available and normalized_metric_mode == "cross_sectional" else None,
            "cs_score": 68.4 if metrics_available and normalized_metric_mode == "cross_sectional" else None,
            "universe_key": "main",
            "symbols": ["BTCUSDT", "ETHUSDT"]
            if normalized_metric_mode == "cross_sectional"
            else ["BTCUSDT"],
        }
        return {
            "session_id": version.worker_form.submitted.session_id,
            "form_id": version.worker_form.submitted.form_id,
            "pipeline_run_id": version.worker_form.pipeline_run_id,
            "report": {
                "report_no": f"AUTOTEST-{uuid4().hex}",
                "factor_name": f"autotest-composite-{uuid4().hex}",
                "conclusion": "autotest valid combination result",
                "combo": {
                    "research_methods": ["machine_learning"],
                    "algorithms": ["ElasticNet"],
                    "factor_code": f"autotest-factor-code-{uuid4().hex}",
                    "formula": "0.4*component_a-0.2*component_b",
                },
                "components": report_components,
                "performance": performance,
                "explanation": {"summary": "autotest report explanation"},
            },
            "factor_validity_status": self.build_validity_payload(
                validity_state,
                factor_bar_interval=factor_bar_interval,
                factor_window_bars=factor_window_bars,
            ),
        }

    def build_validity_payload(
        self,
        state: str = "unknown",
        *,
        factor_bar_interval: str = "4h",
        factor_window_bars: str | int = "24",
    ) -> dict[str, Any]:
        """构造 Worker 合约测试使用的明确有效性快照。

        参数 ``state`` 支持 ``valid``/``time_series_valid``（仅用于构造真实结果形状的离线数据）、
        ``cross_sectional_valid``（仅用于构造真实结果形状的离线数据）、``invalid``/``both_invalid``（时序和截面均失效）
        以及 ``unknown``；``factor_bar_interval`` 是因子 K 线级别，
        ``factor_window_bars`` 是因子窗口。返回不包含后端生成身份和审计字段的请求对象；未知维度的分数、状态和标志
        均显式为 ``null``。这些数据只用于兼容 Worker 合约，不能替代真实 Pipeline 结果。
        """

        normalized_state = str(state).strip().lower()
        if normalized_state not in {
            "valid",
            "time_series_valid",
            "cross_sectional_valid",
            "invalid",
            "both_invalid",
            "unknown",
        }:
            raise ValueError(
                "validity state must be valid, time_series_valid, cross_sectional_valid, invalid, both_invalid or unknown"
            )
        if normalized_state in {"valid", "time_series_valid"}:
            time_series_score: int | None = 80
            time_series_status: str = "valid"
            time_series_is_valid: bool | None = True
            cross_sectional_score: int | None = None
            cross_sectional_status: str = "unknown"
            cross_sectional_is_valid: bool | None = None
            overall_score: int | None = 80
            overall_status: str = "valid"
            overall_is_valid: bool | None = True
        elif normalized_state == "cross_sectional_valid":
            time_series_score = None
            time_series_status = "unknown"
            time_series_is_valid = None
            cross_sectional_score = 80
            cross_sectional_status = "valid"
            cross_sectional_is_valid = True
            overall_score = 80
            overall_status = "valid"
            overall_is_valid = True
        elif normalized_state in {"invalid", "both_invalid"}:
            time_series_score = 20
            time_series_status = "invalid"
            time_series_is_valid = False
            cross_sectional_score = 20
            cross_sectional_status = "invalid"
            cross_sectional_is_valid = False
            overall_score = 20
            overall_status = "invalid"
            overall_is_valid = False
        else:
            time_series_score = None
            time_series_status = "unknown"
            time_series_is_valid = None
            cross_sectional_score = None
            cross_sectional_status = "unknown"
            cross_sectional_is_valid = None
            overall_score = None
            overall_status = "unknown"
            overall_is_valid = None
        return {
            "universe_key": "main",
            "factor_bar_interval": factor_bar_interval,
            "factor_window_bars": str(factor_window_bars),
            "return_bar_interval": "1h",
            "forward_return_bars": 1,
            "window_scope": "rolling",
            "period_start": None,
            "period_end": None,
            "time_series_scoring_version": "autotest-ts-v1",
            "time_series_score": time_series_score,
            "time_series_status": time_series_status,
            "time_series_is_valid": time_series_is_valid,
            "cross_sectional_scoring_version": "autotest-cs-v1",
            "cross_sectional_score": cross_sectional_score,
            "cross_sectional_status": cross_sectional_status,
            "cross_sectional_is_valid": cross_sectional_is_valid,
            "overall_score": overall_score,
            "overall_status": overall_status,
            "overall_is_valid": overall_is_valid,
            "validity_threshold": 50,
            "status_reason_json": {"reason": "autotest validity"},
        }

    def register_report_request(self, payload: dict[str, Any]) -> requests.Response:
        """发送组合报告登记请求。

        参数 ``payload`` 是登记接口完整 JSON 请求体。
        返回原始 HTTP 响应；登记状态、返回对象和数据库落库由测试用例断言。
        """

        return self._factor_combo_api.register_report(payload)

    def start_real_run_request(
        self,
        form: SubmittedForm,
        *,
        agent_uid: str | None = None,
        feedback_id: int | None = None,
        force_fresh_pipeline_run: bool = False,
        research_round: int = 1,
    ) -> requests.Response:
        """发送真实 Run 启动请求并返回原始响应。

        参数 ``form`` 是当前用户已提交且可执行的表单，``agent_uid`` 是已经通过可见性校验的 Agent UID，``feedback_id``
        是下一轮反馈主键，``force_fresh_pipeline_run`` 控制是否要求创建新 Run，``research_round`` 是正整数研究轮次。
        返回原始 HTTP 响应；该方法只负责构造和发送请求，409 冲突及响应字段解析由调用方处理。请求发出前会保护表单，
        防止网络异常或响应解析失败后清理仍可能被外部 Pipeline 使用的数据。
        """

        selected_agent_uid = str(agent_uid or self._settings.agent_uid or "").strip()
        if not selected_agent_uid:
            raise RuntimeError("Factor combo Agent UID is not configured")
        if research_round < 1:
            raise ValueError("research_round must be positive")
        if feedback_id is not None:
            if isinstance(feedback_id, bool) or int(feedback_id) <= 0:
                raise ValueError("feedback_id must be a positive integer when present")
        payload: dict[str, Any] = {
            "agent_uid": selected_agent_uid,
            "force_fresh_pipeline_run": bool(force_fresh_pipeline_run),
        }
        if feedback_id is not None:
            payload["feedback_id"] = int(feedback_id)
        self.scope.protect_form(form.form_id)
        response = self._call_flow_request(
            "start real factor combo run",
            lambda: self._factor_combo_api.start_run(form.form_id, payload),
        )
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int) and 400 <= status_code < 500 and status_code != 409:
            # 明确的参数、鉴权或资源错误表示请求未被接受；网络错误、5xx 和 409 仍保留保护，避免误删未知状态资源。
            self.scope.release_form(form.form_id)
        return response

    def start_real_run(
        self,
        form: SubmittedForm,
        *,
        agent_uid: str | None = None,
        feedback_id: int | None = None,
        force_fresh_pipeline_run: bool = False,
        research_round: int = 1,
    ) -> RealRun:
        """启动一个真实组合 Agent Run 并读取运行 ID。

        参数 ``form`` 是当前用户已提交且可执行的表单，``agent_uid`` 是已经通过可见性校验的 Agent UID，``feedback_id``
        是下一轮研究反馈主键，``force_fresh_pipeline_run`` 控制技术重试是否强制新建 Pipeline，``research_round`` 是
        业务研究轮次。返回 ``RealRun``；正常新建返回 202，幂等重放返回 200，启动冲突时会尝试复用响应或数据库中
        已存在的真实 Run。接口失败、响应缺少运行 ID、无法恢复已有 Run 或运行轮次非法时抛出
        ``FactorComboFlowError``。
        """

        selected_agent_uid = str(agent_uid or self._settings.agent_uid or "").strip()
        response = self.start_real_run_request(
            form,
            agent_uid=selected_agent_uid,
            feedback_id=feedback_id,
            force_fresh_pipeline_run=force_fresh_pipeline_run,
            research_round=research_round,
        )
        if response.status_code == 409:
            return self.recover_existing_run(
                response,
                form,
                agent_uid=selected_agent_uid,
                research_round=research_round,
            )
        run = self.parse_started_run_response(
            response,
            form,
            agent_uid=selected_agent_uid,
            research_round=research_round,
        )
        return run

    def recover_existing_run(
        self,
        response: requests.Response,
        form: SubmittedForm,
        *,
        agent_uid: str,
        research_round: int = 1,
    ) -> RealRun:
        """从启动冲突响应或数据库表单指针中恢复已经存在的真实 Run。

        参数 ``response`` 是启动接口返回 HTTP 409 的原始响应，``form`` 是本次研究表单，``agent_uid`` 是已通过可见性
        校验的 Agent，``research_round`` 是研究轮次。返回标记 ``reused_existing=True`` 的 ``RealRun``；响应或数据库
        没有符合正式格式的真实 ``pipeline_run_id`` 时抛出 ``FactorComboFlowError(FAIL_CONTRACT)``，不会再次启动新 Run。
        """

        response_body = self._safe_json(response)
        response_data = response_body.get("data") if isinstance(response_body, dict) else None
        if isinstance(response_data, dict) and response_data.get("form_id") is not None:
            if isinstance(response_data["form_id"], bool):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Run conflict response form_id is not an integer",
                    response_body,
                )
            try:
                returned_form_id = int(response_data["form_id"])
            except (TypeError, ValueError) as error:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Run conflict response form_id is not an integer",
                    response_body,
                ) from error
            if returned_form_id != form.form_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Run conflict response form_id does not match the submitted form",
                    response_body,
                )
        response_run_id = self._find_pipeline_run_id(response_data)
        database_form = self._repository.get_form(form.form_id)
        database_run_id = None
        if isinstance(database_form, dict):
            database_run_id = database_form.get("pipeline_run_id")

        normalized_response_run_id = (
            response_run_id.strip()
            if isinstance(response_run_id, str) and _REAL_PIPELINE_RUN_ID.fullmatch(response_run_id.strip())
            else None
        )
        normalized_database_run_id = (
            database_run_id.strip()
            if isinstance(database_run_id, str) and _REAL_PIPELINE_RUN_ID.fullmatch(database_run_id.strip())
            else None
        )
        if (
            normalized_response_run_id is not None
            and normalized_database_run_id is not None
            and normalized_response_run_id != normalized_database_run_id
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "Run conflict response and database point to different pipeline_run_id values",
                {
                    "api": response_body,
                    "db": database_form,
                    "api_pipeline_run_id": normalized_response_run_id,
                    "db_pipeline_run_id": normalized_database_run_id,
                },
            )
        if isinstance(response_data, dict):
            conflict_agent_session_id = response_data.get("agent_session_id")
            if conflict_agent_session_id is not None and (
                isinstance(conflict_agent_session_id, bool)
                or not isinstance(conflict_agent_session_id, (str, int))
                or not str(conflict_agent_session_id).strip()
            ):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Run conflict response agent_session_id must be a non-empty string or integer when present",
                    response_body,
                )

        candidates = (
            ("api", normalized_response_run_id),
            ("db", normalized_database_run_id),
        )
        for source, candidate in candidates:
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            normalized_run_id = candidate.strip()
            if _REAL_PIPELINE_RUN_ID.fullmatch(normalized_run_id) is None:
                continue
            agent_session_id = response_data.get("agent_session_id") if isinstance(response_data, dict) else None
            return RealRun(
                form=form,
                pipeline_run_id=normalized_run_id,
                agent_uid=str(agent_uid).strip(),
                agent_session_id=agent_session_id,
                research_round=research_round,
                reused_existing=True,
            )

        raise FactorComboFlowError(
            FlowOutcome.FAIL_CONTRACT,
            "Run start returned HTTP 409 but no reusable real pipeline_run_id was available",
            {
                "api": response_body,
                "db": database_form,
                "candidate_sources": [source for source, value in candidates if value is not None],
                "action": "query_existing_run_or_repair_form_state; do_not_start_another_run",
            },
        )

    def parse_started_run_response(
        self,
        response: requests.Response,
        form: SubmittedForm,
        *,
        agent_uid: str,
        research_round: int = 1,
    ) -> RealRun:
        """解析并校验真实 Run 启动响应，不发起网络请求。

        参数 ``response`` 是启动接口原始响应，``form`` 是提交表单上下文，``agent_uid`` 是已选 Agent，``research_round``
        是研究轮次。返回严格校验后的 ``RealRun``；状态码、统一响应信封、表单归属、运行 ID 格式或字段类型不符合文档时
        抛出 ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        normalized_agent_uid = str(agent_uid).strip()
        if not normalized_agent_uid:
            raise ValueError("agent_uid must not be blank")
        if research_round < 1:
            raise ValueError("research_round must be positive")
        data = self._require_flow_data(response, {200, 202}, "start real factor combo run")
        returned_form_id = self._positive_int_or_failure(
            data.get("form_id"),
            FlowOutcome.FAIL_CONTRACT,
            "start response is missing a positive form_id",
            data,
        )
        if returned_form_id != form.form_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "start response form_id does not match the submitted form",
                data,
            )
        run_id = self._required_non_empty_string_or_failure(
            data.get("pipeline_run_id"),
            FlowOutcome.FAIL_CONTRACT,
            "start response is missing pipeline_run_id",
            data,
        )
        if _REAL_PIPELINE_RUN_ID.fullmatch(run_id) is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "start response pipeline_run_id does not match the documented format",
                data,
            )
        idempotent_replay = data.get("idempotent_replay")
        if not isinstance(idempotent_replay, bool):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "start response idempotent_replay must be a boolean",
                data,
            )
        if response.status_code == 202 and idempotent_replay is not False:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "HTTP 202 start response must contain idempotent_replay=false",
                data,
            )
        if response.status_code == 200 and idempotent_replay is not True:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "HTTP 200 start response must contain idempotent_replay=true",
                data,
            )
        agent_session_id = data.get("agent_session_id")
        if agent_session_id is not None and (
            isinstance(agent_session_id, bool)
            or not isinstance(agent_session_id, (str, int))
            or not str(agent_session_id).strip()
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "start response agent_session_id must be a non-empty string or integer when present",
                data,
            )
        return RealRun(
            form=form,
            pipeline_run_id=run_id,
            agent_uid=normalized_agent_uid,
            agent_session_id=agent_session_id,
            research_round=research_round,
        )

    def require_started_run_replay(
        self,
        response: requests.Response,
        form: SubmittedForm,
        *,
        agent_uid: str,
        expected_pipeline_run_id: str,
        research_round: int = 1,
    ) -> RealRun:
        """校验同一表单启动请求的幂等重放响应。

        参数 ``response`` 是第二次启动接口响应，``form``、``agent_uid`` 和 ``research_round`` 是原始启动上下文，
        ``expected_pipeline_run_id`` 是首次响应的真实运行 ID。返回与首次启动相同的 ``RealRun``；非 200、缺少
        ``idempotent_replay=true`` 或运行 ID 被替换时抛出 ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        if response.status_code != 200:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "idempotent run replay must return HTTP 200",
                self._safe_json(response),
            )
        data = self._require_flow_data(response, {200}, "replay real factor combo run")
        if data.get("idempotent_replay") is not True:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "run replay response must contain idempotent_replay=true",
                data,
            )
        run = self.parse_started_run_response(
            response,
            form,
            agent_uid=agent_uid,
            research_round=research_round,
        )
        if run.pipeline_run_id != expected_pipeline_run_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "run replay returned a different pipeline_run_id",
                {"expected_pipeline_run_id": expected_pipeline_run_id, "response": data},
            )
        return run

    def _validate_run_database_identity(self, run: RealRun, operation: str) -> dict[str, Any]:
        """确认数据库表单属于当前真实 Pipeline Run。

        参数 ``run`` 是启动接口返回的表单与 Run 上下文，``operation`` 是用于错误定位的操作名称。返回数据库中
        与该 Run 对应的表单快照；数据库查询异常分类为 ``FAIL_TECHNICAL``，表单不存在、表单 ID、会话 ID、Run ID
        或已持久化的因子池 ID 不一致时分类为 ``FAIL_CONTRACT``。方法不使用时间、状态或“最新一条记录”推断归属。
        """

        try:
            database_form = self._repository.get_form(run.form.form_id)
        except Exception as error:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_TECHNICAL,
                f"{operation} database form identity query failed",
                {
                    "form_id": run.form.form_id,
                    "pipeline_run_id": run.pipeline_run_id,
                    "exception_type": type(error).__name__,
                },
            ) from error

        if not isinstance(database_form, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{operation} database form does not exist",
                {
                    "form_id": run.form.form_id,
                    "pipeline_run_id": run.pipeline_run_id,
                    "database_form": database_form,
                },
            )
        database_form_dict = dict(database_form)
        database_form_id = self._positive_int_or_failure(
            database_form_dict.get("id"),
            FlowOutcome.FAIL_CONTRACT,
            f"{operation} database form is missing a positive id",
            database_form_dict,
        )
        if database_form_id != run.form.form_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{operation} database form id does not match the started form",
                {"run": run, "database_form": database_form_dict},
            )
        database_session_id = self._positive_int_or_failure(
            database_form_dict.get("session_id"),
            FlowOutcome.FAIL_CONTRACT,
            f"{operation} database form is missing a positive session_id",
            database_form_dict,
        )
        if database_session_id != run.form.session_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{operation} database form session_id does not match the started form",
                {"run": run, "database_form": database_form_dict},
            )
        database_pipeline_run_id = self._required_non_empty_string_or_failure(
            database_form_dict.get("pipeline_run_id"),
            FlowOutcome.FAIL_CONTRACT,
            f"{operation} database form is missing pipeline_run_id",
            database_form_dict,
        )
        if database_pipeline_run_id != run.pipeline_run_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{operation} database form pipeline_run_id does not match the started Run",
                {
                    "run_pipeline_run_id": run.pipeline_run_id,
                    "database_pipeline_run_id": database_pipeline_run_id,
                    "database_form": database_form_dict,
                },
            )
        if "factor_combo_pool_id" in database_form_dict:
            database_pool_id = self._positive_int_or_failure(
                database_form_dict.get("factor_combo_pool_id"),
                FlowOutcome.FAIL_CONTRACT,
                f"{operation} database form is missing a positive factor_combo_pool_id",
                database_form_dict,
            )
            if database_pool_id != run.form.pool_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{operation} database form factor_combo_pool_id does not match the started form",
                    {"run": run, "database_form": database_form_dict},
                )
        return database_form_dict

    def read_real_run_status(self, run: RealRun) -> dict[str, Any]:
        """读取并校验一次真实 Run 状态快照。

        参数 ``run`` 是启动接口返回的表单和 Pipeline Run 上下文。
        返回状态接口中的 ``data`` 对象；HTTP、统一响应信封、表单归属或运行 ID 不符合契约时抛出
        ``FactorComboFlowError``，网络错误归类为 ``FAIL_TECHNICAL``。
        """

        self._validate_run_database_identity(run, "read factor combo run status")
        response = self._call_flow_request(
            "read factor combo run status",
            lambda: self._factor_combo_api.get_run_status(run.form.form_id, run.pipeline_run_id),
        )
        data = self._require_flow_data(response, {200}, "read factor combo run status")
        returned_form_id = self._positive_int_or_failure(
            data.get("form_id"),
            FlowOutcome.FAIL_CONTRACT,
            "run status response is missing a positive form_id",
            data,
        )
        if returned_form_id != run.form.form_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "run status response form_id does not match the started form",
                data,
            )
        returned_run_id = self._required_non_empty_string_or_failure(
            data.get("pipeline_run_id"),
            FlowOutcome.FAIL_CONTRACT,
            "run status response is missing pipeline_run_id",
            data,
        )
        if returned_run_id != run.pipeline_run_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "run status response pipeline_run_id does not match the started run",
                data,
            )
        if not str(data.get("pipeline_status", data.get("status", ""))).strip():
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "run status response is missing pipeline_status",
                data,
            )
        return data

    def poll_real_run(self, run: RealRun) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """轮询真实组合 Run 直到终态或测试超时。

        参数 ``run`` 是启动接口返回的表单和 pipeline_run_id。
        返回状态响应数据列表及最后一条状态数据；状态接口的临时网络错误只在同一个 Run 上有限重试，重试耗尽或
        轮询超时抛出带 ``retry_pipeline=false`` 的 ``FactorComboFlowError(FAIL_TECHNICAL)``，不会把不确定状态误判为
        Pipeline 失败或自动创建第二个 Run。
        """

        deadline = time.monotonic() + self._settings.poll_timeout_seconds
        snapshots: list[dict[str, Any]] = []
        terminal_statuses = {"completed", *_PIPELINE_FAILED_STATUSES}
        last_data: dict[str, Any] | None = None
        transport_errors: list[dict[str, Any]] = []
        max_transport_retries = max(int(self._settings.max_technical_retries), 0)
        transport_retry_count = 0
        while time.monotonic() <= deadline:
            try:
                data = self.read_real_run_status(run)
            except FactorComboFlowError as error:
                if error.outcome != FlowOutcome.FAIL_TECHNICAL:
                    raise
                transport_retry_count += 1
                transport_errors.append(
                    {
                        "attempt": transport_retry_count,
                        "error": error.details,
                        "message": str(error),
                    }
                )
                if transport_retry_count > max_transport_retries:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_TECHNICAL,
                        "Pipeline status request remained unavailable; the existing Run must be inspected before any retry",
                        {
                            "pipeline_run_id": run.pipeline_run_id,
                            "retry_pipeline": False,
                            "reason": "run_status_request_unavailable",
                            "status_snapshots": snapshots,
                            "last_status": last_data,
                            "transport_errors": transport_errors,
                        },
                    ) from error
                if time.monotonic() + max(float(self._settings.poll_interval_seconds), 0.0) > deadline:
                    break
                self._sleep_for_poll_retry()
                continue

            transport_retry_count = 0
            snapshots.append(data)
            last_data = data
            status = self._normalize_status(data.get("pipeline_status", data.get("status", "")))
            recommended_action = self._normalize_status(data.get("recommended_action", ""))
            if status in terminal_statuses or recommended_action in {"read_result", "retry_run"}:
                return snapshots, data
            if time.monotonic() + max(float(self._settings.poll_interval_seconds), 0.0) > deadline:
                break
            self._sleep_for_poll_retry()
        raise FactorComboFlowError(
            FlowOutcome.FAIL_TECHNICAL,
            "Pipeline status polling timed out; the existing Run must be inspected before any retry",
            {
                "pipeline_run_id": run.pipeline_run_id,
                "retry_pipeline": False,
                "reason": "run_status_poll_timeout",
                "status_snapshots": snapshots,
                "last_status": last_data,
                "transport_errors": transport_errors,
            },
        )

    def get_run_result(self, run: RealRun) -> requests.Response:
        """读取真实 Run 的结构化组合报告结果。

        参数 ``run`` 是已启动并通常已进入可读结果状态的真实运行。
        返回原始结果 HTTP 响应；调用方负责断言结果对象和运行 ID。
        """

        return self._factor_combo_api.get_run_result(run.form.form_id, run.pipeline_run_id)

    def read_real_pipeline_result(self, run: RealRun, *, max_retries: int | None = None) -> RealPipelineResult:
        """读取真实 Pipeline 结果，并对结果尚未落库或临时服务错误执行有限重试。

        参数 ``run`` 是已经进入 completed 状态的真实运行，``max_retries`` 是对 404、408、429 和 5xx 响应的额外重试次数，
        缺省使用组合因子配置中的技术重试次数。返回通过完整结构契约校验的 ``RealPipelineResult``；重试耗尽抛出
        ``FactorComboFlowError(FAIL_TECHNICAL)``，非临时响应或 JSON 结构错误抛出对应的契约异常，不会把缺失结果当成无效业务结果。
        结果 404 会先查询同一 Run 的状态；若原 Run 已完成但结果仍不可读，异常详情会标记不可重新启动原 Pipeline，避免制造重复 Run。
        """

        retry_limit = self._settings.max_technical_retries if max_retries is None else int(max_retries)
        if retry_limit < 0:
            raise ValueError("max_retries must not be negative")
        transient_statuses = _TRANSIENT_HTTP_STATUSES | {404}
        attempts: list[dict[str, Any]] = []

        for attempt_index in range(retry_limit + 1):
            try:
                response = self._call_flow_request(
                    "read real factor combo result",
                    lambda: self.get_run_result(run),
                )
            except FactorComboFlowError as error:
                if error.outcome != FlowOutcome.FAIL_TECHNICAL:
                    raise
                attempts.append({"attempt": attempt_index + 1, "error": error.details})
                if attempt_index >= retry_limit:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_TECHNICAL,
                        "Pipeline result request failed after retries",
                        {
                            "pipeline_run_id": run.pipeline_run_id,
                            "attempts": attempts,
                            "retry_pipeline": False,
                            "reason": "result_request_unavailable",
                        },
                    ) from error
                self._sleep_for_poll_retry()
                continue

            response_body = self._safe_json(response)
            attempts.append(
                {
                    "attempt": attempt_index + 1,
                    "status_code": response.status_code,
                    "response": response_body,
                }
            )
            if response.status_code in transient_statuses:
                if response.status_code == 404:
                    try:
                        status_data = self.read_real_run_status(run)
                    except FactorComboFlowError as status_error:
                        attempts[-1]["status_after_result_404_error"] = {
                            "outcome": status_error.outcome,
                            "message": str(status_error),
                            "details": status_error.details,
                        }
                        if status_error.outcome != FlowOutcome.FAIL_TECHNICAL:
                            raise
                        if attempt_index >= retry_limit:
                            raise FactorComboFlowError(
                                FlowOutcome.FAIL_TECHNICAL,
                                "Pipeline result and status remained unavailable after retries",
                                {
                                    "pipeline_run_id": run.pipeline_run_id,
                                    "attempts": attempts,
                                    "retry_pipeline": False,
                                    "reason": "result_and_status_unavailable",
                                },
                            ) from status_error
                        self._sleep_for_poll_retry()
                        continue

                    attempts[-1]["status_after_result_404"] = status_data
                    status_after_404 = self._normalize_status(
                        status_data.get("pipeline_status", status_data.get("status", ""))
                    )
                    recommended_action = self._normalize_status(status_data.get("recommended_action", ""))
                    if recommended_action == "retry_run" or status_after_404 in {
                        *_PIPELINE_FAILED_STATUSES,
                    }:
                        raise FactorComboFlowError(
                            FlowOutcome.FAIL_TECHNICAL,
                            "Pipeline became unsuccessful while its structured result was unavailable",
                            {
                                "pipeline_run_id": run.pipeline_run_id,
                                "result_response": response_body,
                                "status_response": status_data,
                                "attempts": attempts,
                                "retry_pipeline": True,
                                "reason": "pipeline_failed_before_result_available",
                            },
                        )
                if attempt_index >= retry_limit:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_TECHNICAL,
                        "Pipeline structured result remained unavailable after retries",
                        {
                            "pipeline_run_id": run.pipeline_run_id,
                            "attempts": attempts,
                            "retry_pipeline": False,
                            "reason": "structured_result_unavailable",
                        },
                    )
                self._sleep_for_poll_retry()
                continue
            result = self.require_real_pipeline_result(response, run)
            # 只有结构化结果已成功解析，才允许 Fixture 在结束时考虑清理该表单。
            self.scope.release_form(run.form.form_id)
            return result

        raise FactorComboFlowError(
            FlowOutcome.FAIL_TECHNICAL,
            "Pipeline result read exited without a terminal response",
            {
                "pipeline_run_id": run.pipeline_run_id,
                "attempts": attempts,
                "retry_pipeline": False,
                "reason": "result_read_exited_without_terminal_response",
            },
        )

    def require_real_pipeline_result(
        self,
        response: requests.Response,
        run: RealRun,
    ) -> RealPipelineResult:
        """解析真实 Pipeline 结果并校验报告、评审和有效性对象的基本契约。

        参数 ``response`` 是 ``GET /factor-combo/forms/{form_id}/runs/{run_id}/result`` 的原始响应，``run`` 是对应运行
        上下文。返回 ``RealPipelineResult``；HTTP、运行关联、JSON 结构或三类结果对象不符合契约时抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``，不会构造任何替代指标。
        """

        self._validate_run_database_identity(run, "read real factor combo result")
        data = self._require_flow_data(response, {200}, "read real factor combo result")
        returned_form_id = self._positive_int_or_failure(
            data.get("form_id"),
            FlowOutcome.FAIL_CONTRACT,
            "result response is missing a positive form_id",
            data,
        )
        if returned_form_id != run.form.form_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "result form_id does not match the started form",
                data,
            )
        returned_run_id = self._required_non_empty_string_or_failure(
            data.get("pipeline_run_id"),
            FlowOutcome.FAIL_CONTRACT,
            "result response is missing pipeline_run_id",
            data,
        )
        if returned_run_id != run.pipeline_run_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "result pipeline_run_id does not match the started run",
                data,
            )
        if str(data.get("pipeline_status", "")).strip().lower() != "completed":
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "result endpoint returned a non-completed pipeline",
                data,
            )
        result = data.get("result")
        if not isinstance(result, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "result field must be an object",
                data,
            )
        report = result.get("factor_combo_report")
        review = result.get("factor_combo_review")
        validity = result.get("factor_validity_status")
        if not isinstance(report, dict) or not isinstance(review, dict) or not isinstance(validity, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "real result must contain factor_combo_report, factor_combo_review and factor_validity_status objects",
                data,
            )
        self._validate_real_report(report, data)
        self._validate_real_review(review, data)
        self._validate_real_validity(validity, data)
        return RealPipelineResult(
            run=run,
            report=dict(report),
            review=dict(review),
            validity=dict(validity),
            raw_data=dict(data),
        )

    def build_real_register_payload(self, result: RealPipelineResult) -> dict[str, Any]:
        """使用真实 Pipeline 产出的报告和有效性快照构造登记请求。

        参数 ``result`` 是结果接口返回并通过结构校验的真实结果。
        返回登记接口完整请求体；方法不会补造或改写任何指标、有效性标志和公式字段。
        """

        return {
            "session_id": result.run.form.session_id,
            "form_id": result.run.form.form_id,
            "pipeline_run_id": result.run.pipeline_run_id,
            "report": dict(result.report),
            "factor_validity_status": dict(result.validity),
        }

    def submit_real_feedback(self, result: RealPipelineResult, feedback: str) -> RealFeedback:
        """为真实 Pipeline 的无效结果提交回复 2，以便继续下一轮研究。

        参数 ``result`` 是当前真实运行结果，``feedback`` 是用户可读的非空反馈文本。
        返回 ``RealFeedback``；反馈接口失败、响应不是统一成功信封或缺少反馈 ID 时抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        normalized_feedback = str(feedback).strip()
        if not normalized_feedback:
            raise ValueError("feedback must not be blank")
        response = self._call_flow_request(
            "submit real factor combo feedback",
            lambda: self.submit_feedback_request(
                {
                    "session_id": result.run.form.session_id,
                    "form_id": result.run.form.form_id,
                    "pipeline_run_id": result.run.pipeline_run_id,
                    "reply": 2,
                    "feedback": normalized_feedback,
                }
            ),
        )
        data = self.require_feedback_response(
            response,
            expected_form_id=result.run.form.form_id,
            expected_experiment_info_id=result.raw_data.get("experiment_info_id")
            if isinstance(result.raw_data.get("experiment_info_id"), int)
            else None,
            expected_status="pending",
        )
        feedback_id = self._required_response_int(data, "feedback_id", "feedback response")
        feedback_round = data.get("feedback_round")
        normalized_round: int | None = None
        if feedback_round is not None:
            normalized_round = self._required_response_int(data, "feedback_round", "feedback response")
        return RealFeedback(feedback_id=feedback_id, feedback_round=normalized_round, response_data=data)

    def register_real_result_and_refresh(self, result: RealPipelineResult) -> RegisteredFlowResult:
        """执行真实结果登记后的完整验收链路。

        参数 ``result`` 是已完成且结构化校验通过的真实 Pipeline 结果，必须包含可登记的有效性快照。
        返回 ``RegisteredFlowResult``；方法依次执行登记、同请求幂等重放、刷新任务轮询和子因子回查。任何阶段的
        状态、响应字段或数据一致性不符合流程文档时抛出带分类的 ``FactorComboFlowError``，不会手工提交刷新任务。
        """

        if not self._has_registration_validity(result.validity):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "real result is marked registration-ready but has no valid time-series or cross-sectional snapshot",
                result.validity,
            )
        expected_factor_name = self._required_non_empty_string_or_failure(
            result.report.get("factor_name"),
            FlowOutcome.FAIL_CONTRACT,
            "real Pipeline report is missing factor_name required for registration verification",
            result.report,
        )
        payload = self.build_real_register_payload(result)
        first_response = self._call_flow_request(
            "register real factor combo report",
            lambda: self.register_report_request(payload),
        )
        first_body = self._safe_json(first_response)
        if first_response.status_code == 409 and self._is_completed_registration_conflict(first_body):
            existing = self.lookup_existing_registration(result.run.form.form_id)
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration reports an already completed decision; existing registration was queried and no opposite action was taken",
                {
                    "api": first_body,
                    "existing_registration": existing,
                    "action": "do_not_create_another_registration_or_performance_refresh",
                },
            )
        if first_response.status_code not in {200, 201}:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor combo registration must return HTTP 201 or a recovered HTTP 200 replay",
                first_body,
            )
        first_data = self._require_flow_data(first_response, {200, 201}, "register real factor combo report")
        expected_first_replay = first_response.status_code == 200
        if first_data.get("idempotent_replay") is not expected_first_replay:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration HTTP status and idempotent_replay marker are inconsistent",
                first_data,
            )
        if not isinstance(first_data.get("registered"), bool):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response registered must be a boolean",
                first_data,
            )
        if first_data.get("registered") is not True:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response does not confirm registered=true",
                first_data,
            )
        first_refresh_status = str(first_data.get("refresh_status", "")).strip().lower()
        if not first_refresh_status:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response is missing refresh_status",
                first_data,
            )
        first_submit_error = str(first_data.get("refresh_submit_error") or "").strip()
        if first_refresh_status in {"not_configured", "submit_failed"} or first_submit_error:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "registration succeeded but Performance Refresh submission failed",
                first_data,
            )
        first_task_id = self._required_identifier_string_or_failure(
            first_data.get("refresh_task_id"),
            FlowOutcome.FAIL_CONTRACT,
            "registration response is missing refresh_task_id",
            first_data,
        )
        if first_refresh_status not in _REFRESH_RESPONSE_STATUSES:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"registration response returned unknown refresh_status: {first_refresh_status}",
                first_data,
            )
        first_identity = self._validate_registration_response(
            first_data,
            context="first registration response",
            require_nested_objects=first_response.status_code == 201,
        )
        response_factor_name = str(first_identity.get("sub_factor_name", "")).strip()
        if response_factor_name and response_factor_name != expected_factor_name:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response sub_factor_name does not match the Pipeline report",
                {"report": result.report, "registration": first_data},
            )
        factor_name = expected_factor_name
        sub_factor_id = int(first_identity["sub_factor_id"])
        registration_id = int(first_identity["registration_id"])
        combo_id = int(first_identity["combo_id"])

        database_form = self._repository.get_form(result.run.form.form_id)
        if not isinstance(database_form, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered form is not readable from the database",
                {"api": first_data, "db": database_form},
            )
        database_session_id = self._positive_int_or_failure(
            database_form.get("session_id"),
            FlowOutcome.FAIL_CONTRACT,
            "registered form is missing a positive session_id",
            {"api": first_data, "db": database_form},
        )
        if database_session_id != result.run.form.session_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered form session_id does not match the current Factor session",
                {"api": first_data, "db": database_form},
            )
        database_pipeline_run_id = self._required_non_empty_string_or_failure(
            database_form.get("pipeline_run_id"),
            FlowOutcome.FAIL_CONTRACT,
            "registered form is missing pipeline_run_id",
            {"api": first_data, "db": database_form},
        )
        if database_pipeline_run_id != result.run.pipeline_run_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered form pipeline_run_id does not match the current Pipeline Run",
                {"api": first_data, "db": database_form},
            )
        database_version_id = self._positive_int_or_failure(
            database_form.get("factor_combo_id"),
            FlowOutcome.FAIL_CONTRACT,
            "registered form is missing a positive factor_combo_id",
            {"api": first_data, "db": database_form},
        )
        if database_version_id != first_identity["factor_combo_version_id"]:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response factor_combo_version_id does not match the form pointer",
                {"api": first_data, "db": database_form},
            )
        database_version = self._repository.get_combo_version(database_version_id)
        if not isinstance(database_version, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered form points to a missing combination version",
                {"api": first_data, "db": database_version},
            )
        database_combo_id = self._positive_int_or_failure(
            database_version.get("combo_id"),
            FlowOutcome.FAIL_CONTRACT,
            "combination version is missing a positive combo_id",
            {"api": first_data, "db": database_version},
        )
        database_combo_hash = self._required_sha256_or_failure(
            database_version.get("combo_version_hash"),
            FlowOutcome.FAIL_CONTRACT,
            "combination version is missing a valid combo_version_hash",
            {"api": first_data, "db": database_version},
        )
        if database_combo_id != combo_id or database_combo_hash != first_identity["combo_version_hash"]:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response and combination version are inconsistent",
                {"api": first_data, "db": database_version},
            )
        registration = self._repository.get_registration(
            combo_id,
            version_id=first_identity["factor_combo_version_id"],
            combo_version_hash=first_identity["combo_version_hash"],
        )
        if registration is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response and database registration marker are inconsistent",
                {"api": first_data, "db": registration},
            )
        database_registration_combo_id = self._positive_int_or_failure(
            registration.get("combo_id"),
            FlowOutcome.FAIL_CONTRACT,
            "database registration marker is missing a positive combo_id",
            {"api": first_data, "db": registration},
        )
        database_registration_hash = self._required_sha256_or_failure(
            registration.get("combo_version_hash"),
            FlowOutcome.FAIL_CONTRACT,
            "database registration marker is missing a valid combo_version_hash",
            {"api": first_data, "db": registration},
        )
        database_sub_factor_id = self._positive_int_or_failure(
            registration.get("sub_factor_id"),
            FlowOutcome.FAIL_CONTRACT,
            "database registration marker is missing a positive sub_factor_id",
            {"api": first_data, "db": registration},
        )
        if (
            database_registration_combo_id != combo_id
            or database_registration_hash != first_identity["combo_version_hash"]
            or database_sub_factor_id != sub_factor_id
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response and database registration marker are inconsistent",
                {"api": first_data, "db": registration},
            )
        database_registration_id = self._positive_int_or_failure(
            registration.get("id"),
            FlowOutcome.FAIL_CONTRACT,
            "database registration marker is missing a positive id",
            {"api": first_data, "db": registration},
        )
        if database_registration_id != registration_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration_id does not match the database registration marker",
                {"api": first_data, "db": registration},
            )

        database_sub_factor = self._repository.get_registered_sub_factor(sub_factor_id)
        if database_sub_factor is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response sub_factor is not readable from the database",
                {"api": first_data, "db": database_sub_factor},
            )
        database_sub_factor_db_id = self._positive_int_or_failure(
            database_sub_factor.get("id"),
            FlowOutcome.FAIL_CONTRACT,
            "database sub_factor is missing a positive id",
            {"api": first_data, "db": database_sub_factor},
        )
        if (
            database_sub_factor_db_id != sub_factor_id
            or str(database_sub_factor.get("sub_factor_name", "")).strip() != factor_name
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response and database sub_factor are inconsistent",
                {"api": first_data, "db": database_sub_factor},
            )
        if "type" in database_sub_factor and database_sub_factor.get("type") != 1:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered database sub_factor must have type=1",
                {"api": first_data, "db": database_sub_factor},
            )

        database_factor_detail = self._repository.get_registered_factor_detail(
            int(first_identity["factor_detail_id"])
        )
        if not isinstance(database_factor_detail, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response factor_detail is not readable from the database",
                {"api": first_data, "db": database_factor_detail},
            )
        self._validate_nested_identity(
            database_factor_detail,
            "id",
            int(first_identity["factor_detail_id"]),
            "database factor_detail.id",
            {"api": first_data, "db": database_factor_detail},
        )
        self._validate_nested_identity(
            database_factor_detail,
            "factor_id",
            sub_factor_id,
            "database factor_detail.factor_id",
            {"api": first_data, "db": database_factor_detail},
        )
        if database_factor_detail.get("is_sub_factor_id") not in (True, 1):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "database factor_detail.is_sub_factor_id must be true",
                {"api": first_data, "db": database_factor_detail},
            )

        database_validity = self._repository.get_registered_validity_status(
            int(first_identity["factor_validity_status_id"])
        )
        if not isinstance(database_validity, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration response factor_validity_status is not readable from the database",
                {"api": first_data, "db": database_validity},
            )
        self._validate_nested_identity(
            database_validity,
            "id",
            int(first_identity["factor_validity_status_id"]),
            "database factor_validity_status.id",
            {"api": first_data, "db": database_validity},
        )
        self._validate_nested_identity(
            database_validity,
            "factor_id",
            sub_factor_id,
            "database factor_validity_status.factor_id",
            {"api": first_data, "db": database_validity},
        )
        if database_validity.get("is_sub_factor_id") not in (True, 1):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "database factor_validity_status.is_sub_factor_id must be true",
                {"api": first_data, "db": database_validity},
            )

        registration_persistence: dict[str, Any] = {}
        experiment_reader = getattr(self._repository, "get_experiment", None)
        if callable(experiment_reader):
            database_experiment_id = self._positive_int_or_failure(
                database_form.get("factor_combo_experiment_info_id"),
                FlowOutcome.FAIL_CONTRACT,
                "registered form is missing a positive factor_combo_experiment_info_id",
                {"api": first_data, "db": database_form},
            )
            database_experiment = experiment_reader(database_experiment_id)
            if not isinstance(database_experiment, dict):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "registered form points to an unreadable experiment record",
                    {"api": first_data, "form": database_form, "experiment": database_experiment},
                )
            registration_persistence = self.validate_registration_persistence(
                first_data,
                payload,
                database_version,
                database_sub_factor,
                database_factor_detail,
                database_validity,
                registration,
                form_row=database_form,
                experiment_row=database_experiment,
            )

        formula_source_consistency: dict[str, Any] = {}
        source_reader = getattr(self._repository, "get_registered_source_relations", None)
        component_reader = getattr(self._repository, "get_components", None)
        if callable(source_reader) and callable(component_reader):
            formula_source_consistency = self.validate_registered_formula_and_sources(
                result.report,
                database_sub_factor,
                database_factor_detail,
                database_version,
                component_reader(int(first_identity["factor_combo_version_id"])),
                source_reader(sub_factor_id),
            )

        replay_response = self._call_flow_request(
            "replay real factor combo registration",
            lambda: self.register_report_request(payload),
        )
        replay_body = self._safe_json(replay_response)
        if replay_response.status_code == 409 and self._is_completed_registration_conflict(replay_body):
            existing = self.lookup_existing_registration(result.run.form.form_id)
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration replay reports an already completed decision; existing registration was queried and no opposite action was taken",
                {
                    "api": replay_body,
                    "existing_registration": existing,
                    "action": "do_not_create_another_registration_or_performance_refresh",
                },
            )
        if replay_response.status_code != 200:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration replay must return HTTP 200",
                replay_body,
            )
        replay_data = self._require_flow_data(replay_response, {200}, "replay real factor combo registration")
        if replay_data.get("registered") is not True or replay_data.get("idempotent_replay") is not True:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration replay is not explicitly idempotent",
                replay_data,
            )
        replay_identity = self._validate_registration_response(
            replay_data,
            context="registration replay response",
            require_nested_objects=False,
        )
        for field_name in (
            "factor_combo_version_id",
            "combo_id",
            "sub_factor_id",
            "factor_detail_id",
            "factor_validity_status_id",
            "registration_id",
            "sub_factor_type",
            "combo_version_hash",
        ):
            if replay_identity[field_name] != first_identity[field_name]:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"registration replay changed {field_name}",
                    {
                        "field": field_name,
                        "first": first_data,
                        "replay": replay_data,
                        "first_normalized": first_identity[field_name],
                        "replay_normalized": replay_identity[field_name],
                    },
                )
        replay_sub_factor_name = replay_identity.get("sub_factor_name")
        if replay_sub_factor_name and replay_sub_factor_name != factor_name:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration replay changed sub_factor_name",
                {"first": first_data, "replay": replay_data},
            )
        replay_status = str(replay_data.get("refresh_status", "")).strip().lower()
        replay_error = str(replay_data.get("refresh_submit_error") or "").strip()
        if replay_status in {"not_configured", "submit_failed"} or replay_error:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "registration replay reports a Performance Refresh submission failure",
                replay_data,
            )
        replay_task_id = self._required_identifier_string_or_failure(
            replay_data.get("refresh_task_id"),
            FlowOutcome.FAIL_CONTRACT,
            "registration replay is missing refresh_task_id",
            replay_data,
        )
        if replay_task_id != first_task_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration replay did not reuse the original Performance Refresh task",
                {"first": first_data, "replay": replay_data},
            )
        if replay_status not in _REFRESH_RESPONSE_STATUSES:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"registration replay returned unknown refresh_status: {replay_status}",
                replay_data,
            )

        refresh = self.poll_performance_refresh(first_task_id, factor_name)
        sub_factor = self.verify_registered_sub_factor(sub_factor_id, factor_name)
        database_refresh = self.verify_database_refresh_evidence(
            sub_factor_id,
            int(first_identity["factor_validity_status_id"]),
            refresh.data,
            api_sub_factor=sub_factor,
        )
        if callable(getattr(self._repository, "get_factor_refresh_calculation_slices", None)):
            core_metric_coverage = self.validate_core_metric_coverage(database_refresh)
        else:
            # 旧离线替身只提供聚合计数，不能冒充新版真实指标验收；真实 Repository 进入上面的严格分支。
            core_metric_coverage = {
                "mode": "legacy_offline_compatibility",
                "validated": False,
                "reason": "repository does not expose factor_ic_slice_metrics detail access",
            }
        database_sub_factor_after_refresh = self._read_database_sub_factor_after_refresh(
            sub_factor_id,
            factor_name,
        )
        return RegisteredFlowResult(
            outcome=FlowOutcome.PASS_REGISTERED,
            first_registration=first_data,
            replay_registration=replay_data,
            refresh=refresh,
            sub_factor=sub_factor,
            database_sub_factor=database_sub_factor_after_refresh,
            database_refresh=database_refresh,
            registration_persistence=registration_persistence,
            core_metric_coverage=core_metric_coverage,
            formula_source_consistency=formula_source_consistency,
        )

    def audit_registered_factor_core_data(
        self,
        choice: RegisteredFactorChoice,
    ) -> dict[str, Any]:
        """审计一个真实已登记复合子因子的指标、公式、权重和来源链路。

        参数 ``choice`` 由 Repository 动态选择，包含登记、版本、详情和初始有效性快照身份。返回详情接口数据、
        数据库刷新证据、核心指标覆盖及公式来源一致性诊断；接口不可读、实体身份不一致、指标/切片缺失、公式或组件
        不可追溯时抛出 ``FactorComboFlowError``。方法只执行真实 GET 和数据库查询，不创建 Run、不修改数据，也不
        使用历史期望值替代当前接口或数据库结果。
        """

        if not isinstance(choice, RegisteredFactorChoice):
            raise TypeError("choice must be a RegisteredFactorChoice")
        if self._sub_factor_api is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "Sub-factor API is not configured for registered factor audit",
            )

        response = self._call_flow_request(
            "read registered factor for core audit",
            lambda: self._sub_factor_api.get_sub_factor(
                choice.sub_factor_id,
                ic_mode="timeseries",
            ),
        )
        api_sub_factor = self._require_flow_data(
            response,
            {200},
            "read registered factor for core audit",
        )
        returned_id = self._positive_int_or_failure(
            api_sub_factor.get("id"),
            FlowOutcome.FAIL_CONTRACT,
            "registered factor audit response is missing id",
            api_sub_factor,
        )
        if returned_id != choice.sub_factor_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor audit response ID does not match the selected factor",
                {"choice": choice, "api": api_sub_factor},
            )
        returned_name = self._required_non_empty_string_or_failure(
            api_sub_factor.get("sub_factor_name"),
            FlowOutcome.FAIL_CONTRACT,
            "registered factor audit response is missing sub_factor_name",
            api_sub_factor,
        )
        if returned_name != choice.sub_factor_name:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor audit response name does not match the selected factor",
                {"choice": choice, "api": api_sub_factor},
            )
        if not self._contains_refresh_evidence(api_sub_factor):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "registered factor audit response contains no refreshed IC or validity data",
                api_sub_factor,
            )

        try:
            database_sub_factor = self._repository.get_registered_sub_factor(choice.sub_factor_id)
            factor_detail = self._repository.get_registered_factor_detail(choice.factor_detail_id)
            source_graph = self._repository.get_registered_source_relations(choice.sub_factor_id)
            validity_rows = self._repository.get_factor_refresh_validity_snapshots(
                choice.sub_factor_id,
                choice.registration_validity_status_id,
            )
        except Exception as error:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_TECHNICAL,
                "registered factor audit database query failed",
                {
                    "sub_factor_id": choice.sub_factor_id,
                    "exception_type": type(error).__name__,
                },
            ) from error
        if database_sub_factor is None or factor_detail is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor audit cannot find the selected catalog entities",
                {
                    "choice": choice,
                    "sub_factor": database_sub_factor,
                    "factor_detail": factor_detail,
                },
            )
        if source_graph.get("registration") is None or source_graph.get("version") is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor audit cannot resolve registration and version identity",
                {"choice": choice, "source_graph": source_graph},
            )
        source_version = source_graph["version"]
        if int(source_version.get("id") or 0) != choice.version_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor audit resolved a different combo version",
                {"choice": choice, "version": source_version},
            )

        refresh_run_ids: list[str] = []
        for validity_row in validity_rows:
            if not isinstance(validity_row, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "registered factor audit validity row must be an object",
                    validity_rows,
                )
            for field_name in (
                "time_series_summary_run_id",
                "cross_sectional_summary_run_id",
            ):
                run_id = validity_row.get(field_name)
                if run_id is None:
                    continue
                normalized_run_id = str(run_id).strip()
                if normalized_run_id and normalized_run_id not in refresh_run_ids:
                    refresh_run_ids.append(normalized_run_id)
        if not refresh_run_ids:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "registered factor audit found no calculation Run linked by validity snapshots",
                {"choice": choice, "validity_rows": validity_rows},
            )
        refresh_data = {
            "results": [{"run_id": run_id} for run_id in refresh_run_ids],
        }
        database_refresh = self.verify_database_refresh_evidence(
            choice.sub_factor_id,
            choice.registration_validity_status_id,
            refresh_data,
            api_sub_factor=api_sub_factor,
        )
        core_metric_coverage = self.validate_core_metric_coverage(database_refresh)

        metadata = self._parse_json_value(
            database_sub_factor.get("metadata"),
            "sub_factors.metadata",
        )
        if not isinstance(metadata, Mapping) or not isinstance(metadata.get("report"), Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered factor audit cannot read the original report snapshot",
                {"sub_factor_id": choice.sub_factor_id, "metadata": metadata},
            )
        formula_source_consistency = self.validate_registered_formula_and_sources(
            metadata["report"],
            database_sub_factor,
            factor_detail,
            source_version,
            source_graph.get("components", ()),
            source_graph,
        )
        return {
            "choice": choice,
            "api_sub_factor": api_sub_factor,
            "database_refresh": database_refresh,
            "core_metric_coverage": core_metric_coverage,
            "formula_source_consistency": formula_source_consistency,
        }

    def run_real_research_flow(
        self,
        form: SubmittedForm,
        user_id: int,
        *,
        preferred_agent_uid: str | None = None,
    ) -> RealResearchFlowResult:
        """执行真实 Agent、Pipeline、登记/反馈和刷新验收的完整业务流程。

        参数 ``form`` 是当前账号已提交的组合表单，``user_id`` 是与 JWT 对应的用户 ID，``preferred_agent_uid`` 是可选
        的明确 Agent UID。返回 ``RealResearchFlowResult``；技术失败会按配置重试，业务无效结果会按配置提交反馈并继续
        研究，登记结果则必须走完刷新任务和子因子回查。任何不可恢复的技术、刷新或契约问题抛出
        ``FactorComboFlowError``，不会伪造结果或手工调用刷新创建接口。
        """

        max_rounds = int(self._settings.max_research_rounds)
        max_technical_retries = int(self._settings.max_technical_retries)
        if max_rounds < 1 or max_technical_retries < 0:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "research and technical retry limits are invalid",
                {"max_rounds": max_rounds, "max_technical_retries": max_technical_retries},
            )
        agent = self.discover_agent(user_id, preferred_agent_uid or self._settings.agent_uid)
        rounds: list[dict[str, Any]] = []
        feedback_id: int | None = None
        last_result: RealPipelineResult | None = None

        for research_round in range(1, max_rounds + 1):
            technical_retry_count = 0
            force_fresh = False
            technical_attempts: list[dict[str, Any]] = []
            result: RealPipelineResult | None = None
            final_status: dict[str, Any] | None = None
            while True:
                run: RealRun | None = None
                attempt: dict[str, Any] = {
                    "attempt": technical_retry_count + 1,
                    "force_fresh_pipeline_run": force_fresh,
                }
                technical_error: FactorComboFlowError | None = None
                try:
                    run = self.start_real_run(
                        form,
                        agent_uid=agent.agent_uid,
                        feedback_id=feedback_id,
                        force_fresh_pipeline_run=force_fresh,
                        research_round=research_round,
                    )
                    attempt["pipeline_run_id"] = run.pipeline_run_id
                    snapshots, final_status = self.poll_real_run(run)
                except TimeoutError as error:
                    technical_error = FactorComboFlowError(
                        FlowOutcome.FAIL_TECHNICAL,
                        "Pipeline status polling timed out",
                        {
                            "pipeline_run_id": run.pipeline_run_id if run is not None else None,
                            "retry_pipeline": False,
                            "reason": "run_status_poll_timeout",
                            "error": str(error),
                        },
                    )
                except FactorComboFlowError as error:
                    if error.outcome != FlowOutcome.FAIL_TECHNICAL:
                        raise
                    technical_error = error

                if technical_error is not None:
                    attempt["error"] = {
                        "message": str(technical_error),
                        "details": technical_error.details,
                    }
                    technical_attempts.append(attempt)
                    retry_pipeline = (
                        isinstance(technical_error.details, dict)
                        and technical_error.details.get("retry_pipeline") is True
                    )
                    if retry_pipeline and technical_retry_count < max_technical_retries:
                        technical_retry_count += 1
                        force_fresh = True
                        continue
                    if not retry_pipeline:
                        raise technical_error
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_TECHNICAL,
                        "Pipeline technical failure and retries were exhausted",
                        {
                            "research_round": research_round,
                            "attempts": technical_attempts,
                        },
                    ) from technical_error

                attempt["status_snapshots"] = snapshots
                attempt["final_status"] = final_status
                final_pipeline_status = self._normalize_status(
                    final_status.get("pipeline_status", final_status.get("status", ""))
                )
                if final_pipeline_status == "completed":
                    try:
                        result = self.read_real_pipeline_result(run, max_retries=max_technical_retries)
                    except FactorComboFlowError as error:
                        if error.outcome != FlowOutcome.FAIL_TECHNICAL:
                            raise
                        attempt["result_error"] = {
                            "message": str(error),
                            "details": error.details,
                        }
                        technical_attempts.append(attempt)
                        retry_pipeline = isinstance(error.details, dict) and error.details.get("retry_pipeline") is True
                        if retry_pipeline and technical_retry_count < max_technical_retries:
                            technical_retry_count += 1
                            force_fresh = True
                            continue
                        if not retry_pipeline:
                            raise error
                        raise FactorComboFlowError(
                            FlowOutcome.FAIL_TECHNICAL,
                            "Pipeline result remained unavailable and retries were exhausted",
                            {
                                "research_round": research_round,
                                "attempts": technical_attempts,
                            },
                        ) from error
                    technical_attempts.append(attempt)
                    break
                retryable_failure = (
                    self._normalize_status(final_status.get("recommended_action", "")) == "retry_run"
                    or final_pipeline_status in _PIPELINE_FAILED_STATUSES
                )
                technical_attempts.append(attempt)
                if retryable_failure and technical_retry_count < max_technical_retries:
                    technical_retry_count += 1
                    force_fresh = True
                    continue
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_TECHNICAL,
                    "Pipeline returned a terminal failure and retries were exhausted",
                    {
                        "research_round": research_round,
                        "attempts": technical_attempts,
                    },
                )

            if run is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_TECHNICAL,
                    "Pipeline reached a completed state without a run context",
                    {"research_round": research_round, "attempts": technical_attempts},
                )
            if result is None or final_status is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_TECHNICAL,
                    "Pipeline reached a completed state without a readable structured result",
                    {"research_round": research_round, "attempts": technical_attempts},
                )
            last_result = result
            review = result.review
            experiment_valid = self._required_boolean_or_failure(
                review.get("experiment_valid"),
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_review.experiment_valid must be a boolean",
                result.raw_data,
            )
            registration_ready = self._required_boolean_or_failure(
                review.get("registration_ready"),
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_review.registration_ready must be a boolean",
                result.raw_data,
            )
            round_record: dict[str, Any] = {
                "research_round": research_round,
                "pipeline_run_id": run.pipeline_run_id,
                "agent_session_id": run.agent_session_id,
                "technical_retry_count": technical_retry_count,
                "pipeline_status": final_status,
                "technical_attempts": technical_attempts,
                "result": result.raw_data,
            }
            if experiment_valid and registration_ready:
                if not self._has_registration_validity(result.validity):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "Pipeline review is registration-ready but validity snapshot has no valid dimension",
                        round_record,
                    )
                registration = self.register_real_result_and_refresh(result)
                self.scope.release_form(form.form_id)
                round_record["registration"] = {
                    "sub_factor_id": registration.first_registration.get("sub_factor_id"),
                    "registration_id": registration.first_registration.get("registration_id"),
                    "refresh": registration.refresh.data,
                    "database_refresh": {
                        "calculation_runs": registration.database_refresh.calculation_runs,
                        "validity_snapshots": registration.database_refresh.validity_snapshots,
                        "refresh_run_ids": registration.database_refresh.refresh_run_ids,
                        "matched_run_ids": registration.database_refresh.matched_run_ids,
                        "core_metric_coverage": registration.core_metric_coverage,
                    },
                    "registration_persistence": registration.registration_persistence,
                    "formula_source_consistency": registration.formula_source_consistency,
                }
                rounds.append(round_record)
                return RealResearchFlowResult(
                    outcome=FlowOutcome.PASS_REGISTERED,
                    agent=agent,
                    rounds=tuple(rounds),
                    last_pipeline_result=result,
                    registration=registration,
                )

            search = review.get("search")
            continue_exploration = isinstance(search, dict) and search.get("continue_exploration_available") is True
            if continue_exploration and research_round < max_rounds:
                feedback = self.submit_real_feedback(
                    result,
                    "自动化测试：当前真实组合结果未达到登记条件，请保留结果并继续探索下一轮候选。",
                )
                round_record["feedback_id"] = feedback.feedback_id
                round_record["feedback_round"] = feedback.feedback_round
                rounds.append(round_record)
                feedback_id = feedback.feedback_id
                continue

            rounds.append(round_record)
            self.scope.release_form(form.form_id)
            return RealResearchFlowResult(
                outcome=FlowOutcome.PASS_INVALID,
                agent=agent,
                rounds=tuple(rounds),
                last_pipeline_result=result,
                registration=None,
            )

        raise FactorComboFlowError(
            FlowOutcome.FAIL_TECHNICAL,
            "real research flow exited without a terminal outcome",
            rounds,
        )

    @staticmethod
    def _normalize_status(value: Any) -> str:
        """规范化流程状态或推荐动作字符串。

        参数 ``value`` 是接口响应中的状态或动作字段。返回去除首尾空白、转为小写并将连字符和空格统一为下划线的
        字符串；非字符串值返回空字符串，供状态分支判断使用。
        """

        if not isinstance(value, str):
            return ""
        return value.strip().lower().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _require_flow_data(
        response: requests.Response,
        expected_statuses: set[int],
        operation: str,
    ) -> dict[str, Any]:
        """从真实流程接口响应中读取统一成功信封的数据对象。

        参数 ``response`` 是原始 HTTP 响应，``expected_statuses`` 是允许的成功状态集合，``operation`` 是脱敏操作名。
        返回复制后的 ``data`` 字典；状态码、success 标志、JSON 根节点或 data 类型不符合契约时抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        payload = FactorComboService._safe_json(response)
        if response.status_code not in expected_statuses:
            outcome = (
                FlowOutcome.FAIL_TECHNICAL
                if response.status_code in {408, 429, 500, 502, 503, 504}
                else FlowOutcome.FAIL_CONTRACT
            )
            raise FactorComboFlowError(
                outcome,
                f"{operation} returned HTTP {response.status_code}",
                payload,
            )
        if not isinstance(payload, dict) or payload.get("success") is not True:
            outcome = (
                FlowOutcome.FAIL_TECHNICAL
                if response.status_code in {408, 429, 500, 502, 503, 504}
                else FlowOutcome.FAIL_CONTRACT
            )
            raise FactorComboFlowError(
                outcome,
                f"{operation} returned an unsuccessful JSON envelope",
                payload,
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{operation} response data must be an object",
                payload,
            )
        return dict(data)

    @staticmethod
    def _call_flow_request(operation: str, request: Callable[[], Any]) -> Any:
        """执行真实流程中的一个 HTTP 请求，并把网络异常归类为技术失败。

        参数 ``operation`` 是不包含凭据的操作名称，``request`` 是延迟执行的 API 调用。返回 API 原始响应；发生
        ``requests.RequestException`` 时抛出 ``FactorComboFlowError(FAIL_TECHNICAL)``，异常消息只包含异常类型，不打印
        Token、密码或完整 URL。
        """

        try:
            return request()
        except requests.RequestException as error:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_TECHNICAL,
                f"{operation} network request failed: {type(error).__name__}",
                {"exception_type": type(error).__name__},
            ) from error

    def _sleep_for_poll_retry(self) -> None:
        """按 Pipeline 轮询间隔等待一次有限重试。

        不接收参数，也不返回值；间隔配置为零时立即返回，避免离线测试和快速契约失败产生无意义等待。
        """

        delay = max(float(self._settings.poll_interval_seconds), 0.0)
        if delay > 0:
            time.sleep(delay)

    def _sleep_for_refresh_retry(self) -> None:
        """按 Performance Refresh 轮询间隔等待一次最终一致性重试。

        不接收参数，也不返回值；间隔配置为零时立即返回，避免离线回归测试产生无意义等待。
        """

        delay = max(float(self._settings.refresh_poll_interval_seconds), 0.0)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _required_non_empty_string_or_failure(
        value: Any,
        outcome: str,
        message: str,
        details: Any,
    ) -> str:
        """读取真实流程中必需的非空字符串，否则抛出分类异常。

        参数 ``value`` 是待读取字段，``outcome``、``message`` 和 ``details`` 是失败分类、说明及诊断数据。
        返回去除首尾空白后的字符串；缺失、非字符串或空值时抛出 ``FactorComboFlowError``。
        """

        if not isinstance(value, str) or not value.strip():
            raise FactorComboFlowError(outcome, message, details)
        return value.strip()

    @staticmethod
    def _required_identifier_string_or_failure(
        value: Any,
        outcome: str,
        message: str,
        details: Any,
    ) -> str:
        """读取可由后端返回为字符串或正整数的资源标识，并统一为字符串。

        参数 ``value`` 是刷新任务标识，``outcome``、``message`` 和 ``details`` 是失败分类、说明及诊断数据。返回去除
        空白后的字符串；布尔值、空字符串、非整数数字、负数和其他类型均抛出 ``FactorComboFlowError``。该兼容规则
        只用于刷新任务 ID，Pipeline Run ID仍由严格字符串格式校验处理。
        """

        if isinstance(value, bool) or value is None:
            raise FactorComboFlowError(outcome, message, details)
        if isinstance(value, int):
            if value <= 0:
                raise FactorComboFlowError(outcome, message, details)
            return str(value)
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise FactorComboFlowError(outcome, message, details)

    @staticmethod
    def _positive_int_or_failure(
        value: Any,
        outcome: str,
        message: str,
        details: Any,
    ) -> int:
        """读取真实流程中必需的正整数，否则抛出分类异常。

        参数 ``value`` 是待读取字段，``outcome``、``message`` 和 ``details`` 是失败分类、说明及诊断数据。
        返回正整数；布尔值、小数、非数字和非正数均视为契约错误。
        """

        if isinstance(value, bool) or value is None:
            raise FactorComboFlowError(outcome, message, details)
        if isinstance(value, float) and not value.is_integer():
            raise FactorComboFlowError(outcome, message, details)
        try:
            normalized = int(value)
        except (TypeError, ValueError) as error:
            raise FactorComboFlowError(outcome, message, details) from error
        if normalized <= 0:
            raise FactorComboFlowError(outcome, message, details)
        return normalized

    @staticmethod
    def _non_negative_int_or_failure(
        value: Any,
        outcome: str,
        message: str,
        details: Any,
    ) -> int:
        """读取数据库聚合计数并要求其为非负整数。

        参数 ``value`` 是数据库返回的计数值，``outcome``、``message`` 和 ``details`` 是失败分类、说明及诊断信息。
        返回非负整数；布尔值、空值、小数或负数均抛出 ``FactorComboFlowError``。
        """

        if isinstance(value, bool) or value is None:
            raise FactorComboFlowError(outcome, message, details)
        if isinstance(value, Decimal) and value != value.to_integral_value():
            raise FactorComboFlowError(outcome, message, details)
        if isinstance(value, float) and not value.is_integer():
            raise FactorComboFlowError(outcome, message, details)
        if isinstance(value, str) and not re.fullmatch(r"[+]?[0-9]+", value.strip()):
            raise FactorComboFlowError(outcome, message, details)
        try:
            normalized = int(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise FactorComboFlowError(outcome, message, details) from error
        if normalized < 0:
            raise FactorComboFlowError(outcome, message, details)
        return normalized

    @classmethod
    def _validate_work_order_data_spec_shape(cls, data_spec: Mapping[str, Any]) -> None:
        """校验 Work Order 的动态 ``data_spec`` 结构，不假设它必须单独落库。

        参数 ``data_spec`` 是 Work Order 接口返回的工作单数据规格对象。
        不返回值；缺少文档要求字段、字段类型错误或 ``forward_return_bars`` 非正整数时抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        cls._require_response_fields(data_spec, _WORK_ORDER_SPEC_REQUIRED_FIELDS, "factor combo work order data_spec")
        for field_name in (
            "symbol",
            "interval",
            "combo_bar_interval",
            "return_bar_interval",
            "alignment_policy",
            "source_availability_rule",
        ):
            cls._required_response_string(data_spec, field_name, "factor combo work order data_spec")
        forward_return_bars = cls._required_response_int(
            data_spec,
            "forward_return_bars",
            "factor combo work order data_spec",
        )
        if forward_return_bars < 1:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order data_spec.forward_return_bars must be positive",
                dict(data_spec),
            )

    @classmethod
    def _validate_registration_response(
        cls,
        data: dict[str, Any],
        *,
        context: str,
        require_nested_objects: bool,
    ) -> dict[str, Any]:
        """校验登记响应的资源身份及嵌套对象关系。

        参数 ``data`` 是登记接口成功响应的 ``data`` 对象，``context`` 用于错误定位，``require_nested_objects`` 表示是否
        要求首次登记返回四个非空落库对象；首次响应必须为 ``True``，幂等重放允许对象为空但仍要求字段为对象类型。
        返回统一为整数和小写哈希的身份字典；缺少资源、ID 不一致、版本哈希非法或复合子因子类型不正确时抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        identity_fields = (
            "factor_combo_version_id",
            "combo_id",
            "sub_factor_id",
            "factor_detail_id",
            "factor_validity_status_id",
            "registration_id",
        )
        identity: dict[str, Any] = {
            field_name: cls._positive_int_or_failure(
                data.get(field_name),
                FlowOutcome.FAIL_CONTRACT,
                f"{context} is missing a positive {field_name}",
                data,
            )
            for field_name in identity_fields
        }
        combo_version_hash = cls._required_sha256_or_failure(
            data.get("combo_version_hash"),
            FlowOutcome.FAIL_CONTRACT,
            f"{context} is missing a valid combo_version_hash",
            data,
        )
        identity["combo_version_hash"] = combo_version_hash
        sub_factor_type = cls._positive_int_or_failure(
            data.get("sub_factor_type"),
            FlowOutcome.FAIL_CONTRACT,
            f"{context} is missing sub_factor_type=1",
            data,
        )
        if sub_factor_type != 1:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{context} sub_factor_type must be 1 for a composite sub-factor",
                data,
            )
        identity["sub_factor_type"] = sub_factor_type

        nested_fields = ("sub_factor", "factor_detail", "factor_validity_status", "registration")
        nested: dict[str, dict[str, Any]] = {}
        for field_name in nested_fields:
            value = data.get(field_name)
            if not isinstance(value, dict):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{context} is missing {field_name} object",
                    data,
                )
            if require_nested_objects and not value:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{context} {field_name} object must not be empty",
                    data,
                )
            nested[field_name] = value

        sub_factor = nested["sub_factor"]
        if sub_factor:
            cls._validate_nested_identity(
                sub_factor,
                "id",
                identity["sub_factor_id"],
                f"{context} sub_factor.id",
                data,
            )
            if require_nested_objects or "sub_factor_name" in sub_factor:
                cls._required_non_empty_string_or_failure(
                    sub_factor.get("sub_factor_name"),
                    FlowOutcome.FAIL_CONTRACT,
                    f"{context} sub_factor.sub_factor_name must not be empty",
                    data,
                )
            if "type" in sub_factor:
                nested_type = cls._positive_int_or_failure(
                    sub_factor.get("type"),
                    FlowOutcome.FAIL_CONTRACT,
                    f"{context} sub_factor.type must be 1",
                    data,
                )
                if nested_type != 1:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{context} sub_factor.type must be 1",
                        data,
                    )
            for field_name, expected_value in (
                ("serial_prefix", "COMBO"),
                ("mining_method", "factor_combo"),
                ("data_source", "factor_combo_report"),
            ):
                if field_name in sub_factor and str(sub_factor.get(field_name)).strip() != expected_value:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{context} sub_factor.{field_name} is inconsistent",
                        data,
                    )

        factor_detail = nested["factor_detail"]
        if factor_detail:
            cls._validate_nested_identity(
                factor_detail,
                "id",
                identity["factor_detail_id"],
                f"{context} factor_detail.id",
                data,
            )
            cls._validate_nested_identity(
                factor_detail,
                "factor_id",
                identity["sub_factor_id"],
                f"{context} factor_detail.factor_id",
                data,
            )
            cls._validate_boolean_flag_if_present(
                factor_detail,
                "is_sub_factor_id",
                f"{context} factor_detail.is_sub_factor_id",
                data,
            )

        validity = nested["factor_validity_status"]
        if validity:
            cls._validate_nested_identity(
                validity,
                "id",
                identity["factor_validity_status_id"],
                f"{context} factor_validity_status.id",
                data,
            )
            cls._validate_nested_identity(
                validity,
                "factor_id",
                identity["sub_factor_id"],
                f"{context} factor_validity_status.factor_id",
                data,
            )
            cls._validate_boolean_flag_if_present(
                validity,
                "is_sub_factor_id",
                f"{context} factor_validity_status.is_sub_factor_id",
                data,
            )

        registration = nested["registration"]
        if registration:
            cls._validate_nested_identity(
                registration,
                "id",
                identity["registration_id"],
                f"{context} registration.id",
                data,
            )
            cls._validate_nested_identity(
                registration,
                "combo_id",
                identity["factor_combo_version_id"],
                f"{context} registration.combo_id",
                data,
            )
            cls._validate_nested_identity(
                registration,
                "sub_factor_id",
                identity["sub_factor_id"],
                f"{context} registration.sub_factor_id",
                data,
            )
            if "combo_version_hash" in registration:
                nested_hash = cls._required_sha256_or_failure(
                    registration.get("combo_version_hash"),
                    FlowOutcome.FAIL_CONTRACT,
                    f"{context} registration.combo_version_hash is invalid",
                    data,
                )
                if nested_hash != combo_version_hash:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{context} registration.combo_version_hash does not match combo_version_hash",
                        data,
                    )

        identity["sub_factor_name"] = str(sub_factor.get("sub_factor_name", "")).strip()
        identity["nested"] = nested
        return identity

    @classmethod
    def _validate_nested_identity(
        cls,
        value: dict[str, Any],
        field_name: str,
        expected: int,
        message: str,
        details: Any,
    ) -> None:
        """校验嵌套资源中的正整数 ID 与顶层登记 ID 一致。

        参数 ``value`` 是嵌套资源对象，``field_name`` 是 ID 字段，``expected`` 是顶层规范化 ID，``message`` 和 ``details``
        是失败上下文。不返回值；字段缺失、类型非法或数值不一致时抛出契约异常。
        """

        normalized = cls._positive_int_or_failure(
            value.get(field_name),
            FlowOutcome.FAIL_CONTRACT,
            f"{message} is missing or invalid",
            details,
        )
        if normalized != expected:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{message} does not match the top-level identity",
                details,
            )

    @classmethod
    def _validate_boolean_flag_if_present(
        cls,
        value: dict[str, Any],
        field_name: str,
        message: str,
        details: Any,
    ) -> None:
        """校验嵌套资源中可选的布尔身份标识。

        参数 ``value`` 是嵌套资源对象，``field_name`` 是待校验字段，``message`` 和 ``details`` 是失败上下文。
        不返回值；字段存在时必须是严格布尔值 ``True``，否则抛出契约异常。
        """

        if field_name not in value:
            return
        if value[field_name] is not True:
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, f"{message} must be true", details)

    @staticmethod
    def _required_sha256_or_failure(
        value: Any,
        outcome: str,
        message: str,
        details: Any,
    ) -> str:
        """读取并校验 64 位十六进制 SHA-256 字符串。

        参数 ``value`` 是响应中的哈希字段，``outcome``、``message`` 和 ``details`` 是失败分类、说明及诊断数据。
        返回小写哈希字符串；缺失、类型错误或长度/字符集不符合 SHA-256 格式时抛出 ``FactorComboFlowError``。
        """

        if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()) is None:
            raise FactorComboFlowError(outcome, message, details)
        return value.strip().lower()

    @staticmethod
    def _has_registration_validity(validity: dict[str, Any]) -> bool:
        """判断有效性快照是否至少有一个可登记的有效维度。

        参数 ``validity`` 是真实 Pipeline 返回的 ``factor_validity_status`` 对象。
        返回 ``True`` 仅当时序或截面 ``is_valid`` 字段严格为布尔值 ``True``；不会把字符串、数字或 null 当成有效。
        """

        return validity.get("time_series_is_valid") is True or validity.get("cross_sectional_is_valid") is True

    @staticmethod
    def _required_boolean_or_failure(
        value: Any,
        outcome: str,
        message: str,
        details: Any,
    ) -> bool:
        """读取真实流程中必需的布尔字段。

        参数 ``value`` 是响应中的字段值，``outcome``、``message`` 和 ``details`` 是失败分类、说明及诊断数据。
        返回严格的布尔值；缺失、字符串、数字和 null 均抛出 ``FactorComboFlowError``。
        """

        if not isinstance(value, bool):
            raise FactorComboFlowError(outcome, message, details)
        return value

    @classmethod
    def _validate_real_report(cls, report: dict[str, Any], details: Any) -> None:
        """校验真实组合报告中会被后续登记使用的结构和字段类型。

        参数 ``report`` 是 Pipeline 返回的 ``factor_combo_report``，``details`` 是完整结果响应诊断对象。不返回值；
        报告名必须是非空字符串，明确返回的报告编号、公式、组件、方向和权重必须具有可登记的类型，且
        ``performance`` 必须满足最新版登记接口的模式、必填、类型和范围约束。契约不完整时抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``；未在响应中出现的可选字段不会被补造或猜测。
        """

        cls._required_non_empty_string_or_failure(
            report.get("factor_name"),
            FlowOutcome.FAIL_CONTRACT,
            "factor_combo_report.factor_name is missing or blank",
            details,
        )
        for field_name in ("report_no", "formula"):
            if field_name in report:
                cls._required_non_empty_string_or_failure(
                    report.get(field_name),
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_combo_report.{field_name} must be a non-empty string when present",
                    details,
                )

        combo = report.get("combo")
        if combo is not None:
            if not isinstance(combo, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_combo_report.combo must be an object when present",
                    details,
                )
            if "formula" in combo:
                cls._required_non_empty_string_or_failure(
                    combo.get("formula"),
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_combo_report.combo.formula must be a non-empty string when present",
                    details,
                )
            combo_components = combo.get("components")
            if combo_components is not None:
                cls._validate_real_report_components(combo_components, details, "factor_combo_report.combo.components")

        if "components" in report:
            cls._validate_real_report_components(report.get("components"), details, "factor_combo_report.components")
        performance = report.get("performance")
        if not isinstance(performance, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_report.performance must be an object",
                details,
            )
        cls._validate_real_performance(performance, details)

    @classmethod
    def _validate_real_performance(cls, performance: Mapping[str, Any], details: Any) -> None:
        """校验真实 Pipeline 报告的新版绩效对象。

        参数 ``performance`` 是 ``factor_combo_report.performance``，``details`` 是完整结果响应诊断对象。不返回值；
        方法校验 measured/unavailable、时序/截面模式、条件必填、数值范围、整数样本数和币池上下文。出现未定义字段、
        缺少必填字段、类型或范围不合法时抛出 ``FactorComboFlowError(FAIL_CONTRACT)``，不会修改或补齐 Pipeline 原值。
        """

        unknown_fields = sorted(set(performance) - _PERFORMANCE_ALLOWED_FIELDS)
        if unknown_fields:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_report.performance contains unsupported fields",
                {"unknown_fields": unknown_fields, "result": details},
            )

        metrics_status = performance.get("metrics_status", "measured")
        if not isinstance(metrics_status, str) or metrics_status not in {"measured", "unavailable"}:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_report.performance.metrics_status must be measured or unavailable",
                details,
            )
        metric_mode = performance.get("metric_mode", "time_series")
        if not isinstance(metric_mode, str) or metric_mode not in {"time_series", "cross_sectional"}:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_report.performance.metric_mode must be time_series or cross_sectional",
                details,
            )

        missing_fields = [field for field in _PERFORMANCE_REQUIRED_NUMERIC_FIELDS if field not in performance]
        if not any(field_name in performance for field_name in _PERFORMANCE_COMPATIBILITY_RATE_FIELDS):
            missing_fields.append("positive_return_rate or rolling_oos_win_rate")
        if metric_mode == "cross_sectional":
            missing_fields.extend(
                field
                for field in ("cs_rank_ic", "cs_icir", "universe_key", "symbols")
                if field not in performance
            )
        if missing_fields:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_report.performance is missing required fields",
                {"missing_fields": sorted(set(missing_fields)), "result": details},
            )

        decimal_values: dict[str, Decimal] = {}
        for field_name in _PERFORMANCE_NUMERIC_FIELDS:
            if field_name not in performance:
                continue
            raw_value = performance[field_name]
            if raw_value is None:
                continue
            if metrics_status == "unavailable":
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_combo_report.performance.{field_name} must be null when metrics are unavailable",
                    details,
                )
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, Decimal)):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_combo_report.performance.{field_name} must be a number or null",
                    details,
                )
            decimal_value = cls._coerce_decimal(raw_value)
            if decimal_value is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_combo_report.performance.{field_name} must be a finite number or null",
                    details,
                )
            if field_name in {"observations", "trade_observations"} and not isinstance(raw_value, int):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_combo_report.performance.{field_name} must be an integer or null",
                    details,
                )
            decimal_values[field_name] = decimal_value

        if metrics_status == "measured":
            measured_required = {
                "return_rate",
                "out_of_sample_icir",
                "net_sharpe",
                "max_drawdown",
                "annual_turnover",
            }
            if metric_mode == "time_series":
                measured_required.add("ts_ic")
            else:
                measured_required.update({"cs_rank_ic", "cs_icir"})
            missing_measured_values = sorted(
                field_name for field_name in measured_required if performance.get(field_name) is None
            )
            if not any(performance.get(field_name) is not None for field_name in _PERFORMANCE_COMPATIBILITY_RATE_FIELDS):
                missing_measured_values.append("positive_return_rate or rolling_oos_win_rate")
            if missing_measured_values:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_combo_report.performance measured metrics must contain valid numbers",
                    {"missing_numeric_fields": missing_measured_values, "result": details},
                )

        bounded_ranges = {
            "ts_ic": (Decimal("-1"), Decimal("1")),
            "max_drawdown": (Decimal("-1"), Decimal("0")),
            "positive_return_rate": (Decimal("0"), Decimal("1")),
            "rolling_oos_win_rate": (Decimal("0"), Decimal("1")),
            "cs_rank_ic": (Decimal("-1"), Decimal("1")),
        }
        for field_name, (minimum, maximum) in bounded_ranges.items():
            value = decimal_values.get(field_name)
            if value is not None and not minimum <= value <= maximum:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_combo_report.performance.{field_name} is outside the documented range",
                    details,
                )
        lower_bounds = {
            "return_rate": Decimal("-1"),
            "annualized_return": Decimal("-1"),
            "profit_loss_ratio": Decimal("0"),
            "annual_turnover": Decimal("0"),
            "observations": Decimal("0"),
            "trade_observations": Decimal("0"),
            "decay_ratio": Decimal("0"),
        }
        for field_name, minimum in lower_bounds.items():
            value = decimal_values.get(field_name)
            if value is not None and value < minimum:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_combo_report.performance.{field_name} is below the documented minimum",
                    details,
                )

        universe_key = performance.get("universe_key")
        if "universe_key" in performance and (not isinstance(universe_key, str) or not universe_key.strip()):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_report.performance.universe_key must be a non-empty string when present",
                details,
            )
        symbols = performance.get("symbols")
        if "symbols" in performance:
            if not isinstance(symbols, list) or not symbols or any(not isinstance(symbol, str) for symbol in symbols):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_combo_report.performance.symbols must be a non-empty string array when present",
                    details,
                )
        if metric_mode == "cross_sectional" and (universe_key is None or symbols is None):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "cross-sectional performance requires universe_key and symbols",
                details,
            )

    @classmethod
    def _validate_real_report_components(cls, components: Any, details: Any, field_name: str) -> None:
        """校验组合报告组件数组及其明确返回的身份、方向和权重。

        参数 ``components`` 是报告中的组件值，``details`` 是完整结果诊断对象，``field_name`` 是组件路径。不返回值；
        组件必须是非空数组且每项为对象，显式 ID 必须为正整数，方向只能为 -1/1，权重必须是非布尔数字。
        """

        if not isinstance(components, list) or not components:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{field_name} must be a non-empty array",
                details,
            )
        for index, component in enumerate(components):
            if not isinstance(component, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"{field_name}[{index}] must be an object",
                    details,
                )
            for identity_field in (
                "factor_id",
                "sub_factor_id",
                "component_factor_id",
                "component_sub_factor_id",
            ):
                if identity_field in component:
                    cls._positive_int_or_failure(
                        component.get(identity_field),
                        FlowOutcome.FAIL_CONTRACT,
                        f"{field_name}[{index}].{identity_field} must be a positive integer",
                        details,
                    )
            if "direction" in component:
                raw_direction = component.get("direction")
                if isinstance(raw_direction, bool):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{field_name}[{index}].direction must be -1 or 1",
                        details,
                    )
                if isinstance(raw_direction, Decimal):
                    if raw_direction != raw_direction.to_integral_value():
                        raise FactorComboFlowError(
                            FlowOutcome.FAIL_CONTRACT,
                            f"{field_name}[{index}].direction must be -1 or 1",
                            details,
                        )
                elif isinstance(raw_direction, str) and re.fullmatch(r"[+-]?[0-9]+", raw_direction.strip()) is None:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{field_name}[{index}].direction must be -1 or 1",
                        details,
                    )
                try:
                    direction = int(raw_direction)
                except (TypeError, ValueError, OverflowError) as error:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{field_name}[{index}].direction must be -1 or 1",
                        details,
                    ) from error
                if isinstance(raw_direction, float) and not raw_direction.is_integer():
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{field_name}[{index}].direction must be -1 or 1",
                        details,
                    )
                if direction not in {-1, 1}:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{field_name}[{index}].direction must be -1 or 1",
                        details,
                    )
            if "weight" in component:
                weight = component.get("weight")
                if isinstance(weight, bool) or cls._coerce_decimal(weight) is None:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"{field_name}[{index}].weight must be numeric",
                        details,
                    )

    @classmethod
    def _validate_real_review(cls, review: dict[str, Any], details: Any) -> None:
        """校验真实组合结果的评审决策字段，不把缺失字段当成无效业务结果。

        参数 ``review`` 是真实 Pipeline 返回的 ``factor_combo_review``，``details`` 是完整结果诊断对象。
        不返回值；``experiment_valid``、``registration_ready`` 或搜索继续标志类型错误时抛出契约异常。
        """

        experiment_valid = cls._required_boolean_or_failure(
            review.get("experiment_valid"),
            FlowOutcome.FAIL_CONTRACT,
            "factor_combo_review.experiment_valid is missing or not boolean",
            details,
        )
        registration_ready = cls._required_boolean_or_failure(
            review.get("registration_ready"),
            FlowOutcome.FAIL_CONTRACT,
            "factor_combo_review.registration_ready is missing or not boolean",
            details,
        )
        if registration_ready and not experiment_valid:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_review.registration_ready cannot be true when experiment_valid is false",
                details,
            )
        search = review.get("search")
        if search is not None and not isinstance(search, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_review.search must be an object when present",
                details,
            )
        if isinstance(search, dict) and "continue_exploration_available" in search:
            cls._required_boolean_or_failure(
                search.get("continue_exploration_available"),
                FlowOutcome.FAIL_CONTRACT,
                "factor_combo_review.search.continue_exploration_available must be boolean",
                details,
            )

    @classmethod
    def _validate_real_validity(cls, validity: dict[str, Any], details: Any) -> None:
        """校验真实有效性快照的时序/截面字段类型。

        参数 ``validity`` 是真实 Pipeline 返回的 ``factor_validity_status``，``details`` 是完整结果诊断对象。
        不返回值；两个维度必须存在且只能是布尔值或 null，禁止把字符串或数字当成有效性结论。
        """

        for field_name in ("time_series_is_valid", "cross_sectional_is_valid"):
            if field_name not in validity:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_validity_status.{field_name} is missing",
                    details,
                )
            value = validity[field_name]
            if value is not None and not isinstance(value, bool):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_validity_status.{field_name} must be boolean or null",
                    details,
                )
        for field_name in (
            "overall_score",
            "time_series_score",
            "cross_sectional_score",
            "validity_threshold",
        ):
            if field_name in validity and validity[field_name] is not None:
                if isinstance(validity[field_name], bool) or cls._coerce_decimal(validity[field_name]) is None:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"factor_validity_status.{field_name} must be numeric or null",
                        details,
                    )
        for field_name in ("overall_status", "time_series_status", "cross_sectional_status"):
            if field_name in validity and validity[field_name] is not None:
                if not isinstance(validity[field_name], str) or not validity[field_name].strip():
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"factor_validity_status.{field_name} must be a non-empty string or null",
                        details,
                    )



    def _worker_form_from_claim(self, submitted: SubmittedForm, data: dict[str, Any]) -> WorkerForm:
        """把兼容认领响应转换为 Worker 表单上下文。

        参数 ``submitted`` 是原提交表单，``data`` 是认领接口成功响应中的 data 对象。
        返回字段完整的 ``WorkerForm``；组件或必需标识缺失时抛出 ``RuntimeError``。
        """

        components = data.get("components")
        if not isinstance(components, list) or not components:
            raise RuntimeError(f"compatibility claim returned no components: {data}")
        normalized_components: list[dict[str, Any]] = []
        for component in components:
            if not isinstance(component, dict):
                raise RuntimeError(f"compatibility claim component is not an object: {component!r}")
            normalized_components.append(dict(component))
        feedback_id = data.get("feedback_id")
        feedback_round = data.get("feedback_round")
        return WorkerForm(
            submitted=SubmittedForm(
                session_id=submitted.session_id,
                form_id=submitted.form_id,
                pool_id=submitted.pool_id,
                status=str(data.get("form_status", submitted.status)),
            ),
            pipeline_run_id=self._required_string(data, "pipeline_run_id", "compatibility claim"),
            combo_id=self._required_int(data, "combo_id", "compatibility claim"),
            components=tuple(normalized_components),
            experiment_id=self._required_string(data, "experiment_id", "compatibility claim"),
            artifact_uri=self._required_string(data, "artifact_uri", "compatibility claim"),
            artifact_sha256=self._required_string(data, "artifact_sha256", "compatibility claim"),
            feedback_id=int(feedback_id) if feedback_id is not None else None,
            feedback_round=int(feedback_round) if feedback_round is not None else None,
        )

    @staticmethod
    def _require_success_data(response: requests.Response, expected_statuses: set[int], operation: str) -> dict[str, Any]:
        """从准备流程响应中提取成功数据并转换为字典。

        参数 ``response`` 是接口原始响应，``expected_statuses`` 是准备流程可接受的 HTTP 状态集合，``operation`` 是错误上下文。
        返回响应中的 ``data`` 字典；状态、JSON 或字段结构不满足时抛出 ``RuntimeError``，不执行 pytest 断言。
        """

        if response.status_code not in expected_statuses:
            raise RuntimeError(f"{operation} failed with HTTP {response.status_code}: {FactorComboService._safe_json(response)}")
        payload = FactorComboService._safe_json(response)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise RuntimeError(f"{operation} returned an unsuccessful JSON body: {payload}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"{operation} response data is not an object: {payload}")
        return data

    @staticmethod
    def _safe_json(response: requests.Response) -> Any:
        """尽量解析响应 JSON 以生成准备失败信息。

        参数 ``response`` 是 HTTP 响应对象。
        返回解析后的 JSON 或响应文本；解析异常不会覆盖原始准备错误。
        """

        try:
            return read_json_or_diagnostic(response)
        except (TypeError, ValueError):
            return read_json_or_diagnostic(response)

    @staticmethod
    def _find_pipeline_run_id(value: Any) -> str | None:
        """从冲突响应的有限结构中提取已有 Pipeline Run ID。

        参数 ``value`` 是启动冲突响应的 ``data`` 或其嵌套对象。返回第一个非空字符串形式的
        ``pipeline_run_id``/``run_id``；不会把任意报告字段或数字 ID转换成真实 Run ID。
        """

        if isinstance(value, dict):
            for field_name in ("pipeline_run_id", "run_id"):
                candidate = value.get(field_name)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            for field_name in ("run", "existing_run", "data"):
                nested = value.get(field_name)
                found = FactorComboService._find_pipeline_run_id(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = FactorComboService._find_pipeline_run_id(item)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _is_completed_registration_conflict(payload: Any) -> bool:
        """判断登记 409 是否明确表示该组合已经完成登记。

        参数 ``payload`` 是登记接口返回的 JSON 或文本。返回 ``True`` 仅当错误编码、状态字段或错误文本包含明确的
        已完成/已登记语义；“有效性不满足”等普通 409 不会被误判为已完成分支。
        """

        if not isinstance(payload, (dict, list, str)):
            return False
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).lower()
        compact = re.sub(r"[\s_-]+", "", normalized)
        markers = tuple(marker.lower() for marker in _COMPLETED_REGISTRATION_MARKERS)
        if any(marker in normalized for marker in markers):
            return True
        return any(marker.replace(" ", "") in compact for marker in ("alreadyregistered", "registrationcompleted"))

    @staticmethod
    def _require_response_fields(
        data: Mapping[str, Any],
        field_names: Iterable[str],
        resource_name: str,
    ) -> None:
        """要求接口响应明确包含文档声明的字段。

        参数 ``data`` 是接口成功响应的 ``data`` 对象，``field_names`` 是该响应契约的必填字段，``resource_name`` 是
        错误上下文。不返回值；只要字段缺失就抛出 ``FactorComboFlowError(FAIL_CONTRACT)``，不会用路径参数、请求参数或
        默认值替代缺失响应字段。
        """

        if not isinstance(data, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} response data must be an object",
                data,
            )
        missing = [field_name for field_name in field_names if field_name not in data]
        if missing:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} response is missing required fields: {', '.join(missing)}",
                dict(data),
            )

    @staticmethod
    def _required_response_int(
        data: Mapping[str, Any],
        field_name: str,
        resource_name: str,
        *,
        minimum: int = 1,
    ) -> int:
        """读取接口响应中的严格整数值。

        参数 ``data`` 是响应数据对象，``field_name`` 是字段名，``resource_name`` 是错误上下文，``minimum`` 是允许的
        最小值。返回 Python ``int``；布尔值、字符串、浮点数、小数和低于下限的值均抛出契约异常，避免响应类型错误被
        隐式转换后掩盖。
        """

        value = data.get(field_name)
        if type(value) is not int or value < minimum:  # noqa: E721 - 这里必须区分 bool 和 int
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} response field {field_name} must be an integer >= {minimum}",
                dict(data),
            )
        return value

    @staticmethod
    def _required_response_string(
        data: Mapping[str, Any],
        field_name: str,
        resource_name: str,
    ) -> str:
        """读取接口响应中的严格非空字符串。

        参数 ``data`` 是响应数据对象，``field_name`` 是字段名，``resource_name`` 是错误上下文。返回去除首尾空白的
        字符串；缺失、null、数字或空字符串均抛出 ``FactorComboFlowError(FAIL_CONTRACT)``，不把其他类型强转为字符串。
        """

        value = data.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} response field {field_name} must be a non-empty string",
                dict(data),
            )
        return value.strip()

    @staticmethod
    def _required_response_bool(
        data: Mapping[str, Any],
        field_name: str,
        resource_name: str,
    ) -> bool:
        """读取接口响应中的严格 JSON 布尔值。

        参数 ``data`` 是响应数据对象，``field_name`` 是字段名，``resource_name`` 是错误上下文。返回 ``True`` 或
        ``False``；缺失、数字、字符串和 null 均抛出 ``FactorComboFlowError(FAIL_CONTRACT)``。
        """

        value = data.get(field_name)
        if type(value) is not bool:  # noqa: E721 - 必须拒绝 0/1 和字符串布尔值
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"{resource_name} response field {field_name} must be a boolean",
                dict(data),
            )
        return value

    @staticmethod
    def _required_int(data: dict[str, Any], field_name: str, resource_name: str) -> int:
        """从接口数据中读取必需整数标识。

        参数 ``data`` 是响应数据对象，``field_name`` 是字段名，``resource_name`` 是错误上下文。
        返回正整数；字段缺失、非整数或非正数时抛出 ``RuntimeError``。
        """

        value = data.get(field_name)
        if isinstance(value, bool) or value is None:
            raise RuntimeError(f"{resource_name} response field is not an integer: {field_name}={value!r}")
        if isinstance(value, float) and not value.is_integer():
            raise RuntimeError(f"{resource_name} response field is not an integer: {field_name}={value!r}")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"{resource_name} response field is not an integer: {field_name}={value!r}") from error
        if normalized <= 0:
            raise RuntimeError(f"{resource_name} response field must be positive: {field_name}={value!r}")
        return normalized

    @staticmethod
    def _required_string(data: dict[str, Any], field_name: str, resource_name: str) -> str:
        """从接口数据中读取必需非空字符串字段。

        参数 ``data`` 是响应数据对象，``field_name`` 是字段名，``resource_name`` 是错误上下文。
        返回去除首尾空白后的字符串；字段缺失或为空时抛出 ``RuntimeError``。
        """

        value = data.get(field_name)
        if value is None:
            raise RuntimeError(f"{resource_name} response field is missing: {field_name}")
        normalized = str(value).strip()
        if not normalized:
            raise RuntimeError(f"{resource_name} response field is blank: {field_name}")
        return normalized
