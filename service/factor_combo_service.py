"""组合因子台测试流程编排。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
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
    SubFactorChoice,
)


_REAL_PIPELINE_RUN_ID = re.compile(r"^combo-[1-9][0-9]*-[0-9a-f]{16}$")
_REFRESH_ACTIVE_STATUSES = {"queued", "running", "submitted"}
_REFRESH_FAILED_STATUSES = {"partial", "failed", "unknown"}
_REFRESH_RESPONSE_STATUSES = _REFRESH_ACTIVE_STATUSES | _REFRESH_FAILED_STATUSES | {"completed"}
_TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
_PIPELINE_FAILED_STATUSES = {
    "failed",
    "partial",
    "partial_failed",
    "partial_fail",
    "error",
    "cancelled",
    "canceled",
    "aborted",
}
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

# 新版 summary 表中可以证明计算已产生结果的字段。计数、身份和时间字段不纳入，避免只写入占位行时误判为完成。
_CALCULATION_METRIC_FIELDS = (
    "coverage_mean",
    "coverage_min",
    "mean_ic",
    "median_ic",
    "std_ic",
    "icir",
    "mean_abs_ic",
    "positive_ic_rate",
    "mean_rank_ic",
    "median_rank_ic",
    "std_rank_ic",
    "rank_icir",
    "mean_abs_rank_ic",
    "positive_rank_ic_rate",
    "ic_t_stat",
    "rank_ic_t_stat",
    "monotonicity_ratio",
    "mean_long_short_return",
    "long_short_annual_return",
    "long_short_t_stat",
    "is_icir",
    "oos_icir",
    "icir_oos_retention",
    "rank_is_icir",
    "rank_oos_icir",
    "rank_icir_oos_retention",
    "ic_score",
    "rank_ic_score",
    "icir_score",
    "rank_icir_score",
    "t_stat_score",
    "oos_retention_score",
    "monotonicity_score",
    "long_short_score",
    "final_score",
    "mean_stratification",
    # 旧版离线替身可能仍返回该字段；新版 summary 表不依赖它。
    "slice_score",
)
_REFRESH_EVIDENCE_FIELDS = {
    "ic",
    "rank_ic",
    "coverage",
    "mean_ic",
    "mean_rank_ic",
    "median_ic",
    "std_ic",
    "mean_abs_ic",
    "positive_ic_rate",
    "median_rank_ic",
    "std_rank_ic",
    "mean_abs_rank_ic",
    "positive_rank_ic_rate",
    "icir",
    "rank_icir",
    "tstat",
    "t_stat",
    "ic_t_stat",
    "rank_ic_t_stat",
    "oos_retention",
    "is_icir",
    "oos_icir",
    "icir_oos_retention",
    "rank_is_icir",
    "rank_oos_icir",
    "rank_icir_oos_retention",
    "score",
    "final_score",
    "ic_score",
    "rank_ic_score",
    "icir_score",
    "rank_icir_score",
    "t_stat_score",
    "oos_retention_score",
    "monotonicity_score",
    "long_short_score",
    "overall_score",
    "overall_is_valid",
    "time_series_score",
    "time_series_is_valid",
    "cross_sectional_score",
    "cross_sectional_is_valid",
    "annual_return",
    "annual_volatility",
    "sharpe_ratio",
    "max_drawdown",
    "win_rate_daily",
    "monotonicity_ratio",
    "mean_long_short_return",
    "long_short_return",
    "long_short_annual_return",
    "long_short_t_stat",
    "mean_stratification",
    "stratification",
    "coverage_mean",
    "coverage_min",
}
_REFRESH_EVIDENCE_STATUS_FIELDS = {
    "overall_status",
    "time_series_status",
    "cross_sectional_status",
}
_REFRESH_EVIDENCE_CONTAINER_FIELDS = (
    "factor_ic_summary_metrics",
    "factor_ic_summary_metric",
    "factor_ic_slice_metrics",
    "factor_ic_slice_metric",
    # 当前子因子详情接口仍可能通过兼容字段返回窗口展示数据；这些字段只作为 API 展示证据，
    # 不改变 DB 侧必须读取新版 factor_ic_summary_metrics 的规则。
    "factor_mining_window_metrics",
    "factor_mining_window_metric",
    "metrics",
)
_REFRESH_EVIDENCE_WRAPPER_FIELDS = {"data", "items", "rows", "results", "metrics", "summary"}

_API_METRIC_CONTAINER_FIELDS = _REFRESH_EVIDENCE_CONTAINER_FIELDS
_API_VALIDITY_CONTAINER_FIELDS = ("factor_validity_status", "validity_status", "validity")
_API_TO_DB_METRIC_FIELDS: dict[str, tuple[str, ...]] = {
    "coverage": ("coverage_mean", "coverage"),
    "coverage_mean": ("coverage_mean", "coverage"),
    "coverage_min": ("coverage_min",),
    "ic": ("mean_ic", "ic"),
    "mean_ic": ("mean_ic", "ic"),
    "median_ic": ("median_ic",),
    "std_ic": ("std_ic",),
    "mean_abs_ic": ("mean_abs_ic",),
    "positive_ic_rate": ("positive_ic_rate",),
    "rank_ic": ("mean_rank_ic", "rank_ic"),
    "mean_rank_ic": ("mean_rank_ic", "rank_ic"),
    "median_rank_ic": ("median_rank_ic",),
    "std_rank_ic": ("std_rank_ic",),
    "mean_abs_rank_ic": ("mean_abs_rank_ic",),
    "positive_rank_ic_rate": ("positive_rank_ic_rate",),
    "icir": ("icir",),
    "rank_icir": ("rank_icir",),
    "tstat": ("ic_t_stat", "tstat"),
    "t_stat": ("ic_t_stat", "t_stat"),
    "ic_t_stat": ("ic_t_stat", "t_stat"),
    "rank_ic_t_stat": ("rank_ic_t_stat",),
    "oos_retention": ("icir_oos_retention", "oos_retention"),
    "icir_oos_retention": ("icir_oos_retention", "oos_retention"),
    "is_icir": ("is_icir",),
    "oos_icir": ("oos_icir",),
    "rank_is_icir": ("rank_is_icir",),
    "rank_oos_icir": ("rank_oos_icir",),
    "rank_icir_oos_retention": ("rank_icir_oos_retention",),
    "monotonicity_ratio": ("monotonicity_ratio",),
    "mean_long_short_return": ("mean_long_short_return",),
    "long_short_annual_return": ("long_short_annual_return",),
    "long_short_t_stat": ("long_short_t_stat",),
    "long_short_return": ("mean_long_short_return", "long_short_return"),
    "mean_stratification": ("mean_stratification", "stratification"),
    "stratification": ("mean_stratification", "stratification"),
    "period_start": ("period_start",),
    "period_end": ("period_end",),
    "score": ("final_score", "score"),
    "final_score": ("final_score", "score"),
    "ic_score": ("ic_score",),
    "rank_ic_score": ("rank_ic_score",),
    "icir_score": ("icir_score",),
    "rank_icir_score": ("rank_icir_score",),
    "t_stat_score": ("t_stat_score",),
    "oos_retention_score": ("oos_retention_score",),
    "monotonicity_score": ("monotonicity_score",),
    "long_short_score": ("long_short_score",),
    "overall_score": ("overall_score",),
    "time_series_score": ("time_series_score",),
    "cross_sectional_score": ("cross_sectional_score",),
}
_API_TO_DB_VALIDITY_FIELDS = (
    "overall_score",
    "overall_status",
    "overall_is_valid",
    "time_series_score",
    "time_series_status",
    "time_series_is_valid",
    "cross_sectional_score",
    "cross_sectional_status",
    "cross_sectional_is_valid",
    "period_start",
    "period_end",
)
_METRIC_MATCH_IDENTITY_FIELDS = (
    "id",
    "summary_id",
    "run_id",
    "factor_id",
    "is_sub_factor_id",
    "ic_scope",
    "calculation_mode",
    "factor_bar_interval",
    "factor_window_bars",
    "return_bar_interval",
    "forward_return_bars",
    "interval_value",
    "forward_return_horizon",
    "universe_key",
    "symbol",
    "window_scope",
    "metric_window_bars",
    "metric_window_days",
    "period_start",
    "period_end",
)
_VALIDITY_MATCH_IDENTITY_FIELDS = (
    "id",
    "run_id",
    "factor_id",
    "is_sub_factor_id",
    "universe_key",
    "factor_bar_interval",
    "factor_window_bars",
    "return_bar_interval",
    "forward_return_bars",
    "window_scope",
    "time_series_summary_id",
    "cross_sectional_summary_id",
    "time_series_summary_run_id",
    "cross_sectional_summary_run_id",
    "period_start",
    "period_end",
)

_DATETIME_IDENTITY_FIELDS = {"period_start", "period_end"}


@dataclass
class TestResourceScope:
    """记录单个 pytest 用例创建的测试资源及不可自动删除的主链路资源。

    参数由 ``FactorComboService`` 在创建会话、表单和真实 Run 时填充。
    返回值由 Fixture 创建并在测试结束时交给 Service 清理；所有 ID 都只指向当前测试生成的数据。
    """

    session_ids: set[int] = field(default_factory=set)
    form_ids: set[int] = field(default_factory=set)
    protected_form_ids: set[int] = field(default_factory=set)
    session_forms: dict[int, set[int]] = field(default_factory=dict)

    def track_session(self, session_id: int) -> None:
        """登记一个由当前测试创建的 Factor 会话。

        参数 ``session_id`` 是创建接口返回的会话主键。
        不返回值；会话会在启用测试数据清理时交给仓储删除。
        """

        self.session_ids.add(int(session_id))
        self.session_forms.setdefault(int(session_id), set())

    def track_form(self, session_id: int, form_id: int) -> None:
        """登记一个属于测试会话的组合表单。

        参数 ``session_id`` 是表单所属会话主键，``form_id`` 是提交接口返回的表单主键。
        不返回值；表单会在测试结束时按关联顺序清理。
        """

        normalized_session_id = int(session_id)
        normalized_form_id = int(form_id)
        self.form_ids.add(normalized_form_id)
        self.session_forms.setdefault(normalized_session_id, set()).add(normalized_form_id)

    def protect_form(self, form_id: int) -> None:
        """标记仍可能被真实 Pipeline 使用的表单，禁止自动清理。

        参数 ``form_id`` 是已启动真实 Run 的表单主键。
        不返回值；该表单及其会话会保留，避免清理竞态破坏运行中的外部任务。
        """

        self.protected_form_ids.add(int(form_id))

    def cleanable_form_ids(self) -> set[int]:
        """计算当前可以安全交给数据库仓储清理的表单集合。

        不接收参数。
        返回未被保护的表单主键集合；真实 Pipeline 表单永远不会出现在集合中。
        """

        return self.form_ids - self.protected_form_ids

    def cleanable_session_ids(self) -> set[int]:
        """计算没有被保护表单占用、可以删除的会话集合。

        不接收参数。
        返回会话主键集合；只要会话下存在受保护表单，就不会自动删除该会话。
        """

        return {
            session_id
            for session_id, form_ids in self.session_forms.items()
            if not form_ids.intersection(self.protected_form_ids)
        }


@dataclass(frozen=True)
class SubmittedForm:
    """表示提交接口成功返回的组合表单。"""

    session_id: int
    form_id: int
    pool_id: int
    status: str


@dataclass(frozen=True)
class WorkerForm:
    """表示通过兼容认领接口准备完成的 Worker 临时表单。"""

    submitted: SubmittedForm
    pipeline_run_id: str
    combo_id: int
    components: tuple[dict[str, Any], ...]
    experiment_id: str
    artifact_uri: str
    artifact_sha256: str
    feedback_id: int | None = None
    feedback_round: int | None = None


@dataclass(frozen=True)
class ComboVersion:
    """表示初始或下一轮版本接口成功创建的具体组合版本。"""

    worker_form: WorkerForm
    version_id: int
    combo_id: int
    combo_family_key: str
    pool_id: int
    combo_version_hash: str


@dataclass(frozen=True)
class CompletedExperiment:
    """表示已写入有效实验结果、可供反馈或登记使用的组合链路。"""

    version: ComboVersion
    experiment_id: str
    experiment_info_id: int
    form_status: str
    valid: bool


@dataclass(frozen=True)
class PendingFeedback:
    """表示已经提交但尚未被 Worker 认领的下一轮反馈。"""

    experiment: CompletedExperiment
    feedback_id: int


@dataclass(frozen=True)
class ClaimedFeedback:
    """表示已提交反馈并通过兼容认领接口取得的下一轮任务。"""

    experiment: CompletedExperiment
    feedback_id: int
    worker_form: WorkerForm


@dataclass(frozen=True)
class RealRun:
    """表示真实组合 Run 启动接口返回的运行标识和归属信息。"""

    form: SubmittedForm
    pipeline_run_id: str
    agent_uid: str = ""
    agent_session_id: int | str | None = None
    research_round: int = 1
    reused_existing: bool = False


class FlowOutcome:
    """定义真实组合因子端到端流程的最终分类常量。"""

    PASS_REGISTERED = "PASS_REGISTERED"
    PASS_INVALID = "PASS_INVALID"
    FAIL_REFRESH = "FAIL_REFRESH"
    FAIL_TECHNICAL = "FAIL_TECHNICAL"
    FAIL_CONTRACT = "FAIL_CONTRACT"


class FactorComboFlowError(RuntimeError):
    """表示真实组合因子流程失败，并携带可供报告使用的分类和原始详情。

    参数 ``outcome`` 必须是 ``FlowOutcome`` 中的分类常量，``message`` 是脱敏后的失败说明，``details`` 是可选的
    原始响应或状态快照。异常不会自动重试、不会执行 pytest 断言，也不会删除已经登记的数据。
    """

    def __init__(self, outcome: str, message: str, details: Any = None) -> None:
        """保存流程失败分类和诊断信息。

        参数 ``outcome``、``message`` 和 ``details`` 分别对应失败分类、说明和诊断数据。
        不返回值；父类异常消息会包含分类，便于 JUnit 和控制台快速定位。
        """

        self.outcome = outcome
        self.classification = outcome
        self.details = details
        detail_suffix = ""
        if details is not None:
            try:
                serialized_details = json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
            except (TypeError, ValueError):
                serialized_details = repr(details)
            detail_suffix = f"; details={serialized_details}"
        super().__init__(f"{outcome}: {message}{detail_suffix}")


@dataclass(frozen=True)
class AgentSelection:
    """表示从当前账号可见列表中确定的投研 Agent。"""

    agent_uid: str
    name: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class RealPipelineResult:
    """表示真实 Pipeline 结果接口返回的结构化报告。"""

    run: RealRun
    report: dict[str, Any]
    review: dict[str, Any]
    validity: dict[str, Any]
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class PerformanceRefreshResult:
    """表示已完成的 Performance Refresh 任务及其完整汇总。"""

    task_id: str
    status: str
    poll_count: int
    data: dict[str, Any]


@dataclass(frozen=True)
class DatabaseRefreshEvidence:
    """表示数据库中可追溯到本次复合子因子刷新的计算结果。"""

    sub_factor_id: int
    calculation_runs: tuple[dict[str, Any], ...]
    validity_snapshots: tuple[dict[str, Any], ...]
    refresh_run_ids: tuple[str, ...]
    matched_run_ids: tuple[str, ...]
    calculation_metrics: tuple[dict[str, Any], ...] = ()
    api_db_matches: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class RegisteredFlowResult:
    """表示登记、幂等重放、刷新验收和刷新后子因子回查均完成的结果。

    ``database_sub_factor`` 是 Performance Refresh 完成后重新读取的最终数据库快照，不是登记阶段的临时读取结果。
    """

    outcome: str
    first_registration: dict[str, Any]
    replay_registration: dict[str, Any]
    refresh: PerformanceRefreshResult
    sub_factor: dict[str, Any]
    database_sub_factor: dict[str, Any]
    database_refresh: DatabaseRefreshEvidence


@dataclass(frozen=True)
class RealFeedback:
    """表示真实 Pipeline 无效结果提交的下一轮反馈。"""

    feedback_id: int
    feedback_round: int | None
    response_data: dict[str, Any]


@dataclass(frozen=True)
class RealResearchFlowResult:
    """表示一条真实研究链路的最终分类和各轮诊断数据。"""

    outcome: str
    agent: AgentSelection
    rounds: tuple[dict[str, Any], ...]
    last_pipeline_result: RealPipelineResult | None
    registration: RegisteredFlowResult | None


class FactorComboService:
    """编排组合因子测试所需的会话、表单、Worker 状态和真实 Run。"""

    def __init__(
        self,
        chat_api: ChatAPI,
        factor_combo_api: FactorComboAPI,
        repository: FactorComboRepository,
        settings: FactorComboSettings,
        scope: TestResourceScope,
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
        method_groups: Any = _METHOD_GROUPS_UNSET,
        objectives: list[dict[str, Any]] | None = None,
        notes: str | None = None,
        configuration_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造不包含已废弃 ``research_type`` 的组合表单请求体。

        参数 ``session_id`` 是当前用户 Factor 会话主键，``factor_names`` 是真实母因子或子因子名称列表，
        ``method_groups``、``objectives``、``notes`` 和 ``configuration_overrides`` 是可选接口配置。
        ``method_groups`` 省略时使用默认规则方法；显式传入 ``None`` 时保留为 JSON ``null``，其他 JSON 值原样保留。
        返回可直接传给表单提交接口的请求字典；不执行网络请求或数据库写入。
        """

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
            "combo_bar_interval": "auto",
            "return_bar_interval": "auto",
            "forward_return_bars": 1,
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
            "factors_name": list(factor_names),
            "method_groups": stored_method_groups,
            "configuration_parameters": configuration_parameters,
            "notes": notes if notes is not None else f"autotest-factor-combo-{uuid4().hex}",
        }

    def submit_form(self, payload: dict[str, Any]) -> requests.Response:
        """发送组合研究表单提交请求。

        参数 ``payload`` 是表单接口完整 JSON 请求体。
        返回原始 HTTP 响应；状态码和响应字段由对应 pytest 用例断言，准备流程可调用 ``require_submitted_form`` 解析。
        """

        return self._factor_combo_api.submit_form(payload)

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
        if str(data.get("form_id")) != str(form.form_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order form_id does not match the submitted form",
                data,
            )
        pool_members = data.get("pool_members")
        if not isinstance(pool_members, list) or len(pool_members) < 2:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "work order must contain at least two pool members",
                data,
            )
        for member in pool_members:
            if not isinstance(member, dict):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "work order pool member must be an object",
                    data,
                )
            for field_name in ("factor_id", "sub_factor_id"):
                self._positive_int_or_failure(
                    member.get(field_name),
                    FlowOutcome.FAIL_CONTRACT,
                    f"work order member is missing positive {field_name}",
                    data,
                )
            for field_name in ("feature_column", "factor_bar_interval"):
                if not isinstance(member.get(field_name), str) or not member[field_name].strip():
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"work order member is missing non-empty {field_name}",
                        data,
                    )
        return data

    def require_submitted_form(self, response: requests.Response, session_id: int) -> SubmittedForm:
        """把成功的表单响应转换为可继续编排的表单对象。

        参数 ``response`` 是表单提交接口响应，``session_id`` 是本次请求使用的会话主键。
        返回 ``SubmittedForm`` 并登记资源；响应非 2xx、字段缺失或数据库回读失败时抛出 ``RuntimeError``。
        """

        data = self._require_success_data(response, {200, 202}, "submit factor combo form")
        form_id = self._required_int(data, "form_id", "submitted factor combo form")
        pool_id = self._required_int(data, "factor_combo_pool_id", "submitted factor combo form")
        submitted = SubmittedForm(
            session_id=int(session_id),
            form_id=form_id,
            pool_id=pool_id,
            status=str(data.get("status", "")),
        )
        self.scope.track_form(submitted.session_id, submitted.form_id)
        return submitted

    def create_form_with_sub_factors(self) -> tuple[SubmittedForm, tuple[SubFactorChoice, SubFactorChoice]]:
        """创建一个由两个真实子因子组成的独立组合表单。

        不接收参数。
        返回表单和实际选中的两个子因子；测试数据库不足两个可用子因子或接口准备失败时抛出 ``RuntimeError``。
        """

        choices = self._repository.find_sub_factor_pair()
        if choices is None:
            raise RuntimeError("Test database has fewer than two usable sub-factors")
        session_id = self.create_session()
        payload = self.build_form_payload(session_id, [choice.sub_factor_name for choice in choices])
        submitted = self.require_submitted_form(self.submit_form(payload), session_id)
        return submitted, choices

    def create_form_with_parent(self) -> tuple[SubmittedForm, ParentFactorChoice]:
        """创建一个只选择母因子的独立组合表单。

        不接收参数。
        返回表单和母因子及其完整子因子集合；测试库没有足够关联数据或接口准备失败时抛出 ``RuntimeError``。
        """

        parent = self._repository.find_ranked_parent_with_sub_factors()
        if parent is None:
            raise RuntimeError("Test database has no parent factor with at least two sub-factors")
        session_id = self.create_session()
        payload = self.build_form_payload(session_id, [parent.factor_name])
        submitted = self.require_submitted_form(self.submit_form(payload), session_id)
        return submitted, parent

    def create_form_with_mixed_parent_and_sub_factor(self) -> tuple[SubmittedForm, ParentFactorChoice]:
        """创建一个同时选择母因子和其子因子的表单以验证展开去重。

        不接收参数。
        返回表单和母因子展开基线；接口不支持该业务时由准备阶段抛出异常，便于直接识别契约冲突。
        """

        parent = self._repository.find_ranked_parent_with_sub_factors()
        if parent is None:
            raise RuntimeError("Test database has no parent factor with at least two sub-factors")
        session_id = self.create_session()
        payload = self.build_form_payload(session_id, [parent.factor_name, parent.sub_factors[0].sub_factor_name])
        submitted = self.require_submitted_form(self.submit_form(payload), session_id)
        return submitted, parent

    def create_worker_form(self) -> WorkerForm:
        """创建表单并通过兼容认领接口准备 Worker 回调前置。

        不接收参数。
        返回包含后端分配组合 ID、组件、实验 ID 和 Artifact 的 ``WorkerForm``；认领失败时抛出 ``RuntimeError``。
        """

        submitted, _ = self.create_form_with_sub_factors()
        return self._claim_initial_worker_form(submitted)

    def create_worker_form_from_parent(self) -> WorkerForm:
        """创建母因子展开后的多成员表单并准备 Worker 回调前置。

        不接收参数。
        返回包含至少两个同一母因子来源池成员的 ``WorkerForm``；测试数据库没有满足展开条件的母因子或认领失败时抛出 ``RuntimeError``。
        """

        submitted, _ = self.create_form_with_parent()
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

        payload = {
            "pipeline_run_id": pipeline_run_id or worker_form.pipeline_run_id,
            "combo_id": combo_id if combo_id is not None else worker_form.combo_id,
            "generation_method": generation_method,
            "components": components if components is not None else list(worker_form.components),
        }
        return self._factor_combo_api.create_initial_version(worker_form.submitted.form_id, payload)

    def require_combo_version(self, response: requests.Response, worker_form: WorkerForm) -> ComboVersion:
        """把版本接口成功响应转换为组合版本对象。

        参数 ``response`` 是初始版本或下一轮版本接口响应，``worker_form`` 是对应独立表单上下文。
        返回 ``ComboVersion``；响应非成功或缺少版本字段时抛出 ``RuntimeError``。
        """

        data = self._require_success_data(response, {200, 201}, "create factor combo version")
        return ComboVersion(
            worker_form=worker_form,
            version_id=self._required_int(data, "factor_combo_version_id", "factor combo version"),
            combo_id=self._required_int(data, "combo_id", "factor combo version"),
            combo_family_key=str(data.get("combo_family_key", "")),
            pool_id=self._required_int(data, "pool_id", "factor combo version"),
            combo_version_hash=self._required_string(data, "combo_version_hash", "factor combo version"),
        )

    def create_worker_version(self, worker_form: WorkerForm) -> ComboVersion:
        """为独立 Worker 表单创建一个初始候选版本。

        参数 ``worker_form`` 是已锁定因子池且未关联版本的临时表单。
        返回创建的 ``ComboVersion``；接口前置条件或数据契约不满足时抛出 ``RuntimeError``。
        """

        response = self.create_initial_version_request(worker_form)
        return self.require_combo_version(response, worker_form)

    def build_experiment_payload(
        self,
        worker_form: WorkerForm,
        *,
        valid: bool = True,
        failure_reason: str | None = None,
        artifact_uri: str | None = None,
        artifact_sha256: str | None = None,
    ) -> dict[str, Any]:
        """构造实验结果写入接口的完整请求体。

        参数 ``worker_form`` 提供表单和运行 ID，``valid`` 与 ``failure_reason`` 描述实验结论，``artifact_uri`` 和
        ``artifact_sha256`` 可覆盖认领接口返回的产物标识。返回不包含路径 ``experiment_id`` 的实验请求字典。
        """

        return {
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
            "experiment_config": {
                "algorithm": "ElasticNet",
                "random_seed": 42,
                "component_count": len(worker_form.components),
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
    ) -> CompletedExperiment:
        """把有效实验写入成功响应转换为反馈或登记前置对象。

        参数 ``response`` 是实验接口响应，``version`` 是已创建组合版本，``experiment_id`` 是本次请求幂等键。
        返回 ``CompletedExperiment``；响应非 201 或缺少实验 ID 时抛出 ``RuntimeError``。
        """

        data = self._require_success_data(response, {200, 201}, "write factor combo experiment")
        experiment_info_id = self._required_int(data, "experiment_info_id", "factor combo experiment")
        returned_experiment_id = str(data.get("experiment_id", experiment_id))
        return CompletedExperiment(
            version=version,
            experiment_id=returned_experiment_id,
            experiment_info_id=experiment_info_id,
            form_status=str(data.get("form_status", "")),
            valid=bool(data.get("experiment_valid", True)),
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
        completed = self.require_completed_experiment(response, version, worker_form.experiment_id)
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

        data = self._require_success_data(response, {200}, "submit factor combo feedback")
        return self._required_int(data, "feedback_id", "factor combo feedback")

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
        feedback_id = self.require_feedback_id(response)
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

        payload = {
            "pipeline_run_id": pipeline_run_id or feedback.worker_form.pipeline_run_id,
            "generation_method": generation_method,
            "components": components if components is not None else list(feedback.worker_form.components),
        }
        return self._factor_combo_api.create_next_version(feedback.feedback_id, payload)

    def require_next_version(self, response: requests.Response, feedback: ClaimedFeedback) -> ComboVersion:
        """把下一轮版本接口成功响应转换为组合版本对象。

        参数 ``response`` 是下一轮版本接口响应，``feedback`` 是对应的反馈上下文。
        返回新 ``ComboVersion``；响应非成功或字段不完整时抛出 ``RuntimeError``。
        """

        data = self._require_success_data(response, {200, 201}, "create next factor combo version")
        return ComboVersion(
            worker_form=feedback.worker_form,
            version_id=self._required_int(data, "factor_combo_version_id", "next factor combo version"),
            combo_id=self._required_int(data, "combo_id", "next factor combo version"),
            combo_family_key=str(data.get("combo_family_key", "")),
            pool_id=self._required_int(data, "pool_id", "next factor combo version"),
            combo_version_hash=self._required_string(data, "combo_version_hash", "next factor combo version"),
        )

    def build_register_payload(
        self,
        experiment: CompletedExperiment,
        *,
        metrics_available: bool = True,
        validity_state: str = "valid",
    ) -> dict[str, Any]:
        """构造组合报告登记接口请求体。

        参数 ``experiment`` 是实验完成的组合链路，``metrics_available`` 决定绩效字段使用数值还是全部为空，
        ``validity_state`` 只能是 ``valid``、``invalid`` 或 ``unknown``，用于 Worker 合约测试明确构造有效性快照。
        返回包含报告、组件、绩效和时序/截面有效性字段的完整登记请求；真实 Agent 流程不得调用此模拟构造方法。
        """

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
            "ts_ic": 0.1 if metrics_available else None,
            "return_rate": 0.3 if metrics_available else None,
            "out_of_sample_icir": 0.87 if metrics_available else None,
            "net_sharpe": 1.39 if metrics_available else None,
            "benchmark_sharpe": 1.12 if metrics_available else None,
            "max_drawdown": -0.099 if metrics_available else None,
            "annual_turnover": 0.78 if metrics_available else None,
            "rolling_oos_win_rate": 0.79 if metrics_available else None,
        }
        primary_factor_code = str(
            components[0].get("factor_name") or components[0]["component_factor_id"]
        )
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
                "catalog_classification": {
                    "primary_parent_factor_code": primary_factor_code,
                    "selection_method": "autotest-deterministic",
                    "reason": "first component selected for traceability verification",
                },
                "performance": performance,
                "explanation": {"summary": "autotest report explanation"},
            },
            "factor_validity_status": self.build_validity_payload(validity_state),
        }

    def build_validity_payload(self, state: str = "valid") -> dict[str, Any]:
        """构造 Worker 合约测试使用的明确有效性快照。

        参数 ``state`` 必须是 ``valid``、``invalid`` 或 ``unknown``。返回不包含后端生成身份和审计字段的请求对象；
        ``valid`` 至少让时序维度严格为 ``true``，``invalid`` 明确为 ``false``，``unknown`` 的分数和标志均为 ``null``。
        这些数据只用于兼容 Worker 合约，不能替代真实 Pipeline 结果。
        """

        normalized_state = str(state).strip().lower()
        if normalized_state not in {"valid", "invalid", "unknown"}:
            raise ValueError("validity state must be valid, invalid or unknown")
        if normalized_state == "valid":
            time_series_score: int | None = 80
            time_series_status: str = "valid"
            time_series_is_valid: bool | None = True
            cross_sectional_score: int | None = None
            cross_sectional_status: str = "unknown"
            cross_sectional_is_valid: bool | None = None
            overall_score: int | None = 80
            overall_status: str = "valid"
            overall_is_valid: bool | None = True
        elif normalized_state == "invalid":
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
            "factor_bar_interval": "1h",
            "factor_window_bars": "24",
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
        return self._call_flow_request(
            "start real factor combo run",
            lambda: self._factor_combo_api.start_run(form.form_id, payload),
        )

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

    def read_real_run_status(self, run: RealRun) -> dict[str, Any]:
        """读取并校验一次真实 Run 状态快照。

        参数 ``run`` 是启动接口返回的表单和 Pipeline Run 上下文。
        返回状态接口中的 ``data`` 对象；HTTP、统一响应信封、表单归属或运行 ID 不符合契约时抛出
        ``FactorComboFlowError``，网络错误归类为 ``FAIL_TECHNICAL``。
        """

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
            return self.require_real_pipeline_result(response, run)

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
        data = self._require_flow_data(response, {200}, "submit real factor combo feedback")
        if data.get("feedback_status") != "pending":
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback response must remain pending for the next research round",
                data,
            )
        if "reply" in data and data["reply"] != 2:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "feedback response reply must be 2",
                data,
            )
        feedback_id = self._positive_int_or_failure(
            data.get("feedback_id"),
            FlowOutcome.FAIL_CONTRACT,
            "feedback response is missing a positive feedback_id",
            data,
        )
        feedback_round = data.get("feedback_round")
        normalized_round: int | None = None
        if feedback_round is not None:
            normalized_round = self._positive_int_or_failure(
                feedback_round,
                FlowOutcome.FAIL_CONTRACT,
                "feedback_round must be a positive integer when present",
                data,
            )
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
        registration = self._repository.get_registration(combo_id)
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
            or registration.get("factor_id") is not None
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
        )

    def _read_database_sub_factor_after_refresh(
        self,
        sub_factor_id: int,
        expected_factor_name: str,
    ) -> dict[str, Any]:
        """在 Performance Refresh 和 API 回查完成后重新读取登记子因子。

        参数 ``sub_factor_id`` 是登记响应返回的复合子因子 ID，``expected_factor_name`` 是原 Pipeline 报告中的因子名。
        返回刷新完成后的数据库子因子快照；数据库查询异常分类为 ``FAIL_TECHNICAL``，记录缺失分类为 ``FAIL_REFRESH``，
        ID、名称或明确返回的类型不一致分类为 ``FAIL_CONTRACT``。该方法只读数据库，不修改业务数据。
        """

        try:
            database_sub_factor = self._repository.get_registered_sub_factor(sub_factor_id)
        except Exception as error:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_TECHNICAL,
                "post-refresh database sub-factor query failed",
                {"sub_factor_id": sub_factor_id, "exception_type": type(error).__name__},
            ) from error
        if not isinstance(database_sub_factor, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "registered sub-factor is not readable from the database after Performance Refresh",
                {"sub_factor_id": sub_factor_id, "db": database_sub_factor},
            )
        database_sub_factor_db_id = self._positive_int_or_failure(
            database_sub_factor.get("id"),
            FlowOutcome.FAIL_CONTRACT,
            "post-refresh database sub_factor is missing a positive id",
            database_sub_factor,
        )
        if database_sub_factor_db_id != int(sub_factor_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "post-refresh database sub_factor ID does not match registration",
                {"expected_sub_factor_id": sub_factor_id, "db": database_sub_factor},
            )
        if str(database_sub_factor.get("sub_factor_name", "")).strip() != str(expected_factor_name).strip():
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "post-refresh database sub_factor name does not match registration",
                {"expected_factor_name": expected_factor_name, "db": database_sub_factor},
            )
        if "type" in database_sub_factor and database_sub_factor.get("type") != 1:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "post-refresh database sub_factor must have type=1",
                database_sub_factor,
            )
        return database_sub_factor

    def lookup_existing_registration(self, form_id: int) -> dict[str, Any]:
        """查询表单当前指向的组合版本及其正式登记记录。

        参数 ``form_id`` 是组合研究表单主键。返回包含表单快照、组合版本、业务组合 ID 和登记映射的诊断字典；表单
        尚未指向版本、版本尚未生成或组合尚未登记时，对应字段为 ``None``。注意 ``factor_combo_form.factor_combo_id``
        是 ``factor_combo.id`` 版本主键，必须先读取版本中的业务 ``combo_id``，再查询登记映射。该方法只查询数据库，
        不创建、修改或删除任何业务数据，专门用于处理登记接口返回“已完成”冲突时的恢复判断。
        """

        form = self._repository.get_form(int(form_id))
        version_id: int | None = None
        version: dict[str, Any] | None = None
        combo_id: int | None = None
        registration: dict[str, Any] | None = None
        if isinstance(form, dict) and form.get("factor_combo_id") is not None:
            try:
                version_id = int(form["factor_combo_id"])
            except (TypeError, ValueError):
                version_id = None
            if version_id is not None and version_id > 0:
                version = self._repository.get_combo_version(version_id)
                if isinstance(version, dict) and version.get("combo_id") is not None:
                    try:
                        combo_id = int(version["combo_id"])
                    except (TypeError, ValueError):
                        combo_id = None
                    if combo_id is not None and combo_id > 0:
                        registration = self._repository.get_registration(combo_id)
        return {
            "form": form,
            "version_id": version_id,
            "version": version,
            "combo_id": combo_id,
            "registration": registration,
        }

    def poll_performance_refresh(self, task_id: str, expected_factor_name: str) -> PerformanceRefreshResult:
        """轮询登记接口自动创建的 Performance Refresh 任务并执行严格完成验收。

        参数 ``task_id`` 是登记响应返回的刷新任务 ID，``expected_factor_name`` 是本次登记生成的子因子名称。
        返回完整完成的 ``PerformanceRefreshResult``；任务失败、部分完成、未知、超时或汇总不完整时抛出
        ``FactorComboFlowError(FAIL_REFRESH)``，响应结构非法或权限/契约错误时抛出 ``FAIL_CONTRACT``。
        此方法只调用 GET 查询，不调用刷新任务创建接口。
        """

        if self._performance_api is None:
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, "Performance API is not configured")
        normalized_task_id = str(task_id).strip()
        normalized_factor_name = str(expected_factor_name).strip()
        if not normalized_task_id or not normalized_factor_name:
            raise ValueError("task_id and expected_factor_name must not be blank")
        max_polls = int(self._settings.max_refresh_polls)
        if max_polls < 1:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "max_refresh_polls must be a positive integer",
                max_polls,
            )
        poll_interval = max(float(self._settings.refresh_poll_interval_seconds), 0.0)
        timeout = max(float(self._settings.refresh_poll_timeout_seconds), 0.0)
        deadline = time.monotonic() + timeout
        poll_count = 0
        last_data: dict[str, Any] | None = None
        last_transport_error: dict[str, Any] | None = None
        last_attempt_was_transport_error = False
        transient_http_statuses = _TRANSIENT_HTTP_STATUSES

        while poll_count < max_polls and time.monotonic() <= deadline:
            try:
                response = self._call_flow_request(
                    "read Performance Refresh run",
                    lambda: self._performance_api.get_refresh_run(normalized_task_id),
                )
            except FactorComboFlowError as error:
                if error.outcome != FlowOutcome.FAIL_TECHNICAL:
                    raise
                poll_count += 1
                last_transport_error = error.details if isinstance(error.details, dict) else {"error": error.details}
                last_attempt_was_transport_error = True
                if poll_count >= max_polls or time.monotonic() + poll_interval > deadline:
                    break
                if poll_interval > 0:
                    time.sleep(poll_interval)
                continue

            poll_count += 1
            if response.status_code != 200:
                response_body = self._safe_json(response)
                if response.status_code in transient_http_statuses:
                    last_transport_error = {
                        "status_code": response.status_code,
                        "response": response_body,
                    }
                    last_attempt_was_transport_error = True
                    if poll_count >= max_polls or time.monotonic() + poll_interval > deadline:
                        break
                    if poll_interval > 0:
                        time.sleep(poll_interval)
                    continue
                classification = (
                    FlowOutcome.FAIL_CONTRACT
                    if response.status_code in {400, 401, 403, 422}
                    else FlowOutcome.FAIL_REFRESH
                )
                raise FactorComboFlowError(
                    classification,
                    f"Performance Refresh query returned HTTP {response.status_code}",
                    response_body,
                )
            data = self._require_flow_data(response, {200}, "read Performance Refresh run")
            last_attempt_was_transport_error = False
            last_data = data
            returned_task_id = str(data.get("task_id", "")).strip()
            if returned_task_id != normalized_task_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Performance Refresh response task_id does not match the requested task",
                    data,
                )
            status = str(data.get("status", "")).strip().lower()
            if not status:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Performance Refresh response is missing status",
                    data,
                )
            if status in _REFRESH_ACTIVE_STATUSES:
                if poll_count >= max_polls or time.monotonic() + poll_interval > deadline:
                    break
                if poll_interval > 0:
                    time.sleep(poll_interval)
                continue
            if status in _REFRESH_FAILED_STATUSES:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_REFRESH,
                    f"Performance Refresh did not complete: status={status}",
                    data,
                )
            if status == "completed":
                self._validate_completed_refresh(data, normalized_factor_name)
                return PerformanceRefreshResult(
                    task_id=normalized_task_id,
                    status=status,
                    poll_count=poll_count,
                    data=data,
                )
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"Performance Refresh returned unknown status: {status}",
                data,
            )

        raise FactorComboFlowError(
            FlowOutcome.FAIL_TECHNICAL if last_attempt_was_transport_error else FlowOutcome.FAIL_REFRESH,
            "Performance Refresh polling timed out or exceeded max_refresh_polls",
            {
                "task_id": normalized_task_id,
                "poll_count": poll_count,
                "last": last_data,
                "last_transport_error": last_transport_error,
            },
        )

    def verify_database_refresh_evidence(
        self,
        sub_factor_id: int,
        registration_validity_status_id: int,
        refresh_data: dict[str, Any],
        api_sub_factor: dict[str, Any] | None = None,
    ) -> DatabaseRefreshEvidence:
        """验收刷新后数据库中的新版指标和有效性关联结果。

        参数 ``sub_factor_id`` 是登记生成的复合子因子主键，``registration_validity_status_id`` 是登记接口首次写入的
        有效性快照主键，``refresh_data`` 是 Performance Refresh completed 响应的 data 对象，``api_sub_factor`` 是可选的
        登记后子因子详情响应 data。返回
        ``DatabaseRefreshEvidence``；数据库连接异常分类为 ``FAIL_TECHNICAL``，没有非空新版指标、计算 Run 或刷新有效性
        关联时分类为 ``FAIL_REFRESH``，因子 ID、子因子标识、Run 或汇总外键不一致时分类为 ``FAIL_CONTRACT``。方法只执行
        查询，不写入数据库，也不会把登记初始快照当成计算结果；若 API 返回可比指标，会逐字段核对 API 与 DB。
        """

        normalized_sub_factor_id = self._positive_int_or_failure(
            sub_factor_id,
            FlowOutcome.FAIL_CONTRACT,
            "database refresh evidence requires a positive sub_factor_id",
            {"sub_factor_id": sub_factor_id},
        )
        normalized_registration_status_id = self._positive_int_or_failure(
            registration_validity_status_id,
            FlowOutcome.FAIL_CONTRACT,
            "database refresh evidence requires a positive registration validity status ID",
            {"registration_validity_status_id": registration_validity_status_id},
        )
        retry_limit = max(int(self._settings.max_technical_retries), 0)
        last_refresh_error: FactorComboFlowError | None = None
        for attempt_index in range(retry_limit + 1):
            try:
                validity_snapshots = self._repository.get_factor_refresh_validity_snapshots(
                    normalized_sub_factor_id,
                    normalized_registration_status_id,
                )
                calculation_metrics_reader = getattr(
                    self._repository,
                    "get_factor_refresh_calculation_metrics",
                    None,
                )
                if callable(calculation_metrics_reader):
                    calculation_rows = calculation_metrics_reader(normalized_sub_factor_id)
                else:
                    # 兼容尚未升级的离线替身；真实 Repository 一定使用带明细的新版查询。
                    calculation_rows = self._repository.get_factor_refresh_calculation_runs(normalized_sub_factor_id)
            except Exception as error:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_TECHNICAL,
                    "database refresh evidence query failed",
                    {
                        "sub_factor_id": normalized_sub_factor_id,
                        "exception_type": type(error).__name__,
                    },
                ) from error

            try:
                return self._validate_database_refresh_evidence(
                    normalized_sub_factor_id,
                    calculation_rows,
                    validity_snapshots,
                    refresh_data,
                    api_sub_factor=api_sub_factor,
                )
            except FactorComboFlowError as error:
                if error.outcome == FlowOutcome.FAIL_CONTRACT:
                    raise
                last_refresh_error = error
                if attempt_index >= retry_limit:
                    raise
                self._sleep_for_refresh_retry()

        if last_refresh_error is not None:
            raise last_refresh_error
        raise FactorComboFlowError(
            FlowOutcome.FAIL_REFRESH,
            "database refresh evidence was not available",
            {"sub_factor_id": normalized_sub_factor_id},
        )

    @classmethod
    def _validate_database_refresh_evidence(
        cls,
        sub_factor_id: int,
        calculation_rows: Any,
        validity_snapshots: Any,
        refresh_data: dict[str, Any],
        *,
        api_sub_factor: dict[str, Any] | None = None,
    ) -> DatabaseRefreshEvidence:
        """按刷新后有效性快照选择本次计算结果，并校验 API/DB 关联一致性。

        参数 ``sub_factor_id`` 是目标复合子因子 ID，``calculation_rows`` 是新版汇总明细或兼容聚合结果，
        ``validity_snapshots`` 是有效性快照查询结果，``refresh_data`` 是刷新任务响应，``api_sub_factor`` 是可选的
        API 详情。返回严格关联的数据库证据；数据缺失、指标为空或任务 Run 未落库时抛出 ``FAIL_REFRESH``，身份、
        外键或 API/DB 指标不一致时抛出 ``FAIL_CONTRACT``。
        """

        if not isinstance(calculation_rows, list):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_summary_metrics repository result must be a list",
                calculation_rows,
            )
        if not isinstance(validity_snapshots, list):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_validity_status repository result must be a list",
                validity_snapshots,
            )

        refresh_run_ids = cls._extract_refresh_run_ids(refresh_data)
        candidate_validity = cls._normalize_refresh_validity_rows(sub_factor_id, validity_snapshots)
        if not candidate_validity:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "factor_validity_status contains no refreshed snapshot linked to factor_ic_summary_metrics",
                {"factor_id": sub_factor_id, "rows": validity_snapshots},
            )

        selected_validity = cls._select_refresh_validity_rows(candidate_validity, refresh_run_ids, sub_factor_id)
        linked_run_ids: list[str] = []
        linked_summary_ids: set[int] = set()
        for row in selected_validity:
            for run_id in row["_linked_run_ids"]:
                if run_id not in linked_run_ids:
                    linked_run_ids.append(run_id)
            linked_summary_ids.update(row["_linked_summary_ids"])

        if refresh_run_ids:
            linked_run_id_set = set(linked_run_ids)
            missing_validity_run_ids = [
                run_id for run_id in refresh_run_ids if run_id not in linked_run_id_set
            ]
            if missing_validity_run_ids:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_REFRESH,
                    "Performance Refresh returned calculation Run IDs that are not referenced by factor_validity_status",
                    {
                        "factor_id": sub_factor_id,
                        "refresh_run_ids": refresh_run_ids,
                        "validity_linked_run_ids": tuple(linked_run_ids),
                        "missing_validity_run_ids": missing_validity_run_ids,
                    },
                )

        expected_run_ids = refresh_run_ids or tuple(linked_run_ids)
        filtered_rows: list[dict[str, Any]] = []
        for row in calculation_rows:
            if not isinstance(row, dict):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics repository row must be an object",
                    calculation_rows,
                )
            raw_run_id = row.get("run_id")
            if not isinstance(raw_run_id, str) or not raw_run_id.strip():
                # 无法与本次刷新锚点关联的历史/损坏行不能被当成本次结果；若最终没有有效行，会明确报告刷新失败。
                continue
            if raw_run_id.strip() not in expected_run_ids:
                continue
            filtered_rows.append(row)

        normalized_runs, normalized_metrics, database_run_ids, summary_ids = cls._normalize_calculation_rows(
            sub_factor_id,
            filtered_rows,
        )
        if not normalized_runs:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "factor_ic_summary_metrics contains no calculated rows for the registered sub-factor and refresh",
                {
                    "factor_id": sub_factor_id,
                    "expected_run_ids": expected_run_ids,
                    "database_rows": calculation_rows,
                },
            )

        missing_run_ids = [run_id for run_id in expected_run_ids if run_id not in database_run_ids]
        if missing_run_ids:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "Performance Refresh calculation run_id values are absent from factor_ic_runs/summary metrics",
                {
                    "refresh_run_ids": refresh_run_ids,
                    "expected_run_ids": expected_run_ids,
                    "database_run_ids": database_run_ids,
                    "missing_run_ids": missing_run_ids,
                },
            )

        cls._validate_summary_links(
            selected_validity,
            linked_summary_ids,
            normalized_metrics,
            summary_ids,
            expected_run_ids,
        )

        normalized_validity: list[dict[str, Any]] = []
        for row in selected_validity:
            normalized_row = {key: value for key, value in row.items() if not key.startswith("_")}
            normalized_validity.append(normalized_row)

        if not normalized_validity:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "factor_validity_status contains no selected refresh snapshot",
                {"factor_id": sub_factor_id, "rows": validity_snapshots},
            )

        matched_run_ids = tuple(
            run_id for run_id in (refresh_run_ids or tuple(linked_run_ids)) if run_id in database_run_ids
        )
        api_db_matches = cls._compare_api_and_database_refresh_data(
            sub_factor_id,
            api_sub_factor,
            normalized_metrics,
            normalized_validity,
        )
        return DatabaseRefreshEvidence(
            sub_factor_id=sub_factor_id,
            calculation_runs=tuple(normalized_runs),
            validity_snapshots=tuple(normalized_validity),
            refresh_run_ids=tuple(refresh_run_ids),
            matched_run_ids=matched_run_ids,
            calculation_metrics=tuple(normalized_metrics),
            api_db_matches=tuple(api_db_matches),
        )

    @classmethod
    def _validate_summary_links(
        cls,
        validity_rows: list[dict[str, Any]],
        linked_summary_ids: set[int],
        calculation_metrics: list[dict[str, Any]],
        database_summary_ids: set[int],
        expected_run_ids: tuple[str, ...],
    ) -> None:
        """校验有效性快照引用的 summary ID、因子和计算 Run 完整一致。

        参数 ``validity_rows`` 是本次刷新选出的有效性快照，``linked_summary_ids`` 是其中的 summary 外键集合，
        ``calculation_metrics`` 是新版 summary 明细，``database_summary_ids`` 是明细实际返回的 ID 集合，
        ``expected_run_ids`` 是刷新响应或有效性快照确定的计算 Run。新版明细模式下不返回值；外键缺失、Run 不一致、
        因子归属不一致时抛出 ``FAIL_CONTRACT``。仅有旧版离线聚合替身时保留兼容行为，因为该替身没有 summary 明细可供
        外键逐行核对。
        """

        if not linked_summary_ids or not calculation_metrics:
            return
        if not linked_summary_ids.issubset(database_summary_ids):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_validity_status references summary rows that were not returned for the refresh",
                {
                    "linked_summary_ids": sorted(linked_summary_ids),
                    "database_summary_ids": sorted(database_summary_ids),
                    "expected_run_ids": expected_run_ids,
                },
            )

        rows_by_summary_id: dict[int, dict[str, Any]] = {}
        for metric in calculation_metrics:
            summary_id = cls._positive_int_or_failure(
                metric.get("summary_id", metric.get("id")),
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_summary_metrics row is missing summary id while validating validity links",
                metric,
            )
            if summary_id in rows_by_summary_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics contains duplicate summary id in refresh evidence",
                    {"summary_id": summary_id, "rows": [rows_by_summary_id[summary_id], metric]},
                )
            rows_by_summary_id[summary_id] = metric

        for validity in validity_rows:
            for prefix in ("time_series", "cross_sectional"):
                summary_id = validity.get(f"{prefix}_summary_id")
                if summary_id is None:
                    continue
                normalized_summary_id = cls._positive_int_or_failure(
                    summary_id,
                    FlowOutcome.FAIL_CONTRACT,
                    f"refresh validity {prefix}_summary_id is invalid while validating summary links",
                    validity,
                )
                metric = rows_by_summary_id.get(normalized_summary_id)
                if metric is None:
                    # 这里理论上已由 subset 检查拦截，保留显式分支使错误信息不依赖实现细节。
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"refresh validity {prefix} summary row is absent from calculation evidence",
                        {"validity": validity, "calculation_metrics": calculation_metrics},
                    )
                metric_factor_id = cls._positive_int_or_failure(
                    metric.get("factor_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "linked summary row is missing factor_id",
                    metric,
                )
                validity_factor_id = cls._positive_int_or_failure(
                    validity.get("factor_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "refresh validity row is missing factor_id",
                    validity,
                )
                if metric_factor_id != validity_factor_id:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"refresh validity {prefix} summary belongs to another factor",
                        {"validity": validity, "summary": metric},
                    )
                if not cls._same_scalar(metric.get("is_sub_factor_id"), True):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"refresh validity {prefix} summary is not a sub-factor result",
                        {"validity": validity, "summary": metric},
                    )
                expected_summary_run_id = cls._required_non_empty_string_or_failure(
                    validity.get(f"{prefix}_summary_run_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    f"refresh validity {prefix} summary is missing run_id",
                    validity,
                )
                actual_summary_run_id = cls._required_non_empty_string_or_failure(
                    metric.get("run_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "linked summary row is missing run_id",
                    metric,
                )
                if actual_summary_run_id != expected_summary_run_id:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"refresh validity {prefix} summary run_id differs from calculation row",
                        {
                            "validity": validity,
                            "summary": metric,
                            "expected_run_id": expected_summary_run_id,
                            "actual_run_id": actual_summary_run_id,
                        },
                    )

    @classmethod
    def _normalize_refresh_validity_rows(
        cls,
        sub_factor_id: int,
        validity_snapshots: list[Any],
    ) -> list[dict[str, Any]]:
        """校验有效性快照并提取其实际引用的 summary/Run 身份。

        参数 ``sub_factor_id`` 是目标子因子 ID，``validity_snapshots`` 是 Repository 查询结果。
        返回带内部 ``_linked_run_ids``/``_linked_summary_ids`` 标记的候选快照；登记初始快照只有在被刷新补上
        summary 外键后才会进入候选集合。
        """

        candidates: list[dict[str, Any]] = []
        for row in validity_snapshots:
            if not isinstance(row, dict):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_validity_status repository row must be an object",
                    validity_snapshots,
                )
            row_factor_id = cls._positive_int_or_failure(
                row.get("factor_id"),
                FlowOutcome.FAIL_CONTRACT,
                "refresh factor_validity_status row is missing factor_id",
                row,
            )
            if row_factor_id != sub_factor_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "refresh factor_validity_status row belongs to another factor",
                    {"expected_factor_id": sub_factor_id, "row": row},
                )
            if row.get("is_sub_factor_id") not in (True, 1):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "refresh factor_validity_status row is not marked as a sub-factor",
                    row,
                )
            cls._positive_int_or_failure(
                row.get("id"),
                FlowOutcome.FAIL_CONTRACT,
                "refresh factor_validity_status row is missing a positive id",
                row,
            )
            cls._required_non_empty_string_or_failure(
                row.get("run_id"),
                FlowOutcome.FAIL_CONTRACT,
                "refresh factor_validity_status row is missing run_id",
                row,
            )
            linked_run_ids: list[str] = []
            linked_summary_ids: set[int] = set()
            for prefix in ("time_series", "cross_sectional"):
                summary_id = row.get(f"{prefix}_summary_id")
                if summary_id is None:
                    continue
                normalized_summary_id = cls._positive_int_or_failure(
                    summary_id,
                    FlowOutcome.FAIL_CONTRACT,
                    f"refresh factor_validity_status {prefix}_summary_id is invalid",
                    row,
                )
                linked_run_id = cls._required_non_empty_string_or_failure(
                    row.get(f"{prefix}_summary_run_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    f"refresh factor_validity_status {prefix} summary is missing run_id",
                    row,
                )
                linked_factor_id = cls._positive_int_or_failure(
                    row.get(f"{prefix}_summary_factor_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    f"refresh factor_validity_status {prefix} summary is missing factor_id",
                    row,
                )
                if linked_factor_id != sub_factor_id:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"refresh factor_validity_status {prefix} summary belongs to another factor",
                        row,
                    )
                if row.get(f"{prefix}_summary_is_sub_factor_id") not in (True, 1):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"refresh factor_validity_status {prefix} summary is not a sub-factor result",
                        row,
                    )
                if linked_run_id not in linked_run_ids:
                    linked_run_ids.append(linked_run_id)
                linked_summary_ids.add(normalized_summary_id)
            if linked_run_ids:
                normalized = dict(row)
                normalized["_linked_run_ids"] = tuple(linked_run_ids)
                normalized["_linked_summary_ids"] = frozenset(linked_summary_ids)
                candidates.append(normalized)
        return candidates

    @classmethod
    def _select_refresh_validity_rows(
        cls,
        candidates: list[dict[str, Any]],
        refresh_run_ids: tuple[str, ...],
        sub_factor_id: int,
    ) -> list[dict[str, Any]]:
        """从候选有效性快照中选择与本次刷新对应的最新记录。

        参数 ``candidates`` 是已校验且至少引用一个新版汇总的快照，``refresh_run_ids`` 是刷新响应明确给出的计算
        Run ID，``sub_factor_id`` 仅用于失败诊断。返回本次刷新快照集合；明确 Run 无对应快照或没有候选时抛出刷新失败。
        """

        if refresh_run_ids:
            selected = [
                row
                for row in candidates
                if set(row["_linked_run_ids"]).intersection(refresh_run_ids)
            ]
            if not selected:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_REFRESH,
                    "Performance Refresh run_id is not linked from factor_validity_status",
                    {
                        "factor_id": sub_factor_id,
                        "refresh_run_ids": refresh_run_ids,
                        "validity_rows": candidates,
                    },
                )
            return selected

        # 没有明确 Run ID 时，优先使用最新更新时间；测试替身若不提供时间则保留全部候选，避免丢失多个维度。
        recencies = [
            str(row.get("updated_at") or row.get("created_at") or "").strip()
            for row in candidates
        ]
        if not any(recencies):
            return candidates
        latest = max(recencies)
        return [row for row, recency in zip(candidates, recencies, strict=True) if recency == latest]

    @classmethod
    def _normalize_calculation_rows(
        cls,
        sub_factor_id: int,
        calculation_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], set[int]]:
        """规范化新版 summary 明细或旧版离线聚合替身，并验证计算 Run 状态。

        参数 ``sub_factor_id`` 是目标子因子 ID，``calculation_rows`` 是已按刷新 Run 过滤的数据库行。
        返回 ``(run_aggregates, metric_rows, run_ids, summary_ids)``；历史行已由调用方过滤，任何当前行的身份或状态
        错误会抛出契约/刷新异常。
        """

        if not calculation_rows:
            return [], [], [], set()
        detailed = any("summary_row_count" not in row for row in calculation_rows)
        if not detailed:
            normalized_runs: list[dict[str, Any]] = []
            run_ids: list[str] = []
            for row in calculation_rows:
                row_factor_id = cls._positive_int_or_failure(
                    row.get("factor_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics row is missing factor_id",
                    row,
                )
                if row_factor_id != sub_factor_id:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "factor_ic_summary_metrics row belongs to another factor",
                        {"expected_factor_id": sub_factor_id, "row": row},
                    )
                if row.get("is_sub_factor_id") not in (True, 1):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "factor_ic_summary_metrics row is not marked as a sub-factor",
                        row,
                    )
                run_id = cls._required_non_empty_string_or_failure(
                    row.get("run_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics row is missing run_id",
                    row,
                )
                summary_row_count = cls._non_negative_int_or_failure(
                    row.get("summary_row_count"),
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics summary_row_count is invalid",
                    row,
                )
                populated_metric_row_count = cls._non_negative_int_or_failure(
                    row.get("populated_metric_row_count"),
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics populated_metric_row_count is invalid",
                    row,
                )
                cls._validate_calculation_run_status(row)
                if summary_row_count == 0 or populated_metric_row_count == 0:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_REFRESH,
                        "factor_ic_summary_metrics has no non-null calculated IC/ICIR/t-stat/score result",
                        {"factor_id": sub_factor_id, "row": row},
                    )
                if populated_metric_row_count > summary_row_count:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "factor_ic_summary_metrics populated row count exceeds summary row count",
                        row,
                    )
                normalized = dict(row)
                normalized["factor_id"] = row_factor_id
                normalized["run_id"] = run_id
                normalized["summary_row_count"] = summary_row_count
                normalized["populated_metric_row_count"] = populated_metric_row_count
                normalized_runs.append(normalized)
                if run_id not in run_ids:
                    run_ids.append(run_id)
            return normalized_runs, [], run_ids, set()

        groups: dict[str, list[dict[str, Any]]] = {}
        metric_rows: list[dict[str, Any]] = []
        summary_ids: set[int] = set()
        for row in calculation_rows:
            row_factor_id = cls._positive_int_or_failure(
                row.get("factor_id"),
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_summary_metrics row is missing factor_id",
                row,
            )
            if row_factor_id != sub_factor_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics row belongs to another factor",
                    {"expected_factor_id": sub_factor_id, "row": row},
                )
            if row.get("is_sub_factor_id") not in (True, 1):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics row is not marked as a sub-factor",
                    row,
                )
            run_id = cls._required_non_empty_string_or_failure(
                row.get("run_id"),
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_summary_metrics row is missing run_id",
                row,
            )
            cls._validate_calculation_run_status(row)
            summary_id = cls._positive_int_or_failure(
                row.get("summary_id", row.get("id")),
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_summary_metrics row is missing summary id",
                row,
            )
            normalized = dict(row)
            normalized["factor_id"] = row_factor_id
            normalized["run_id"] = run_id
            normalized["summary_id"] = summary_id
            metric_rows.append(normalized)
            summary_ids.add(summary_id)
            groups.setdefault(run_id, []).append(normalized)

        normalized_runs = []
        run_ids = []
        for run_id, rows in groups.items():
            populated_count = sum(1 for row in rows if cls._is_populated_calculation_metric(row))
            if populated_count == 0:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_REFRESH,
                    "factor_ic_summary_metrics has no non-null calculated IC/ICIR/t-stat/score result",
                    {"factor_id": sub_factor_id, "run_id": run_id, "rows": rows},
                )
            status_values = {cls._normalize_status(row.get("run_status")) for row in rows}
            status_values.discard("")
            normalized_status = next(iter(status_values), "")
            aggregate = {
                "factor_id": sub_factor_id,
                "is_sub_factor_id": True,
                "run_id": run_id,
                "run_status": normalized_status,
                "summary_row_count": len(rows),
                "populated_metric_row_count": populated_count,
                "ic_scope_count": len({row.get("ic_scope") for row in rows if row.get("ic_scope") is not None}),
                "summary_ids": tuple(row["summary_id"] for row in rows),
            }
            normalized_runs.append(aggregate)
            run_ids.append(run_id)
        return normalized_runs, metric_rows, run_ids, summary_ids

    @classmethod
    def _validate_calculation_run_status(cls, row: dict[str, Any]) -> None:
        """验证一条 summary 行关联的计算 Run 已完成。

        参数 ``row`` 是包含 ``run_status`` 的数据库行。不返回值；缺失或进行中的 Run 归类为刷新失败，未知状态归类为
        契约失败。
        """

        run_status = cls._normalize_status(row.get("run_status"))
        if not run_status:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "factor_ic_runs row is missing for a summary metrics run",
                row,
            )
        if run_status in _PIPELINE_FAILED_STATUSES or run_status in {"queued", "running", "submitted", "pending"}:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                f"factor_ic_runs is not completed: status={run_status}",
                row,
            )
        if run_status not in {"completed", "complete", "success", "succeeded", "done"}:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"factor_ic_runs returned unknown status: {run_status}",
                row,
            )

    @staticmethod
    def _is_populated_calculation_metric(row: dict[str, Any]) -> bool:
        """判断新版 summary 行是否至少包含一个非空计算指标。

        参数 ``row`` 是 ``factor_ic_summary_metrics`` 的一行或兼容离线替身行。
        返回 ``True`` 表示至少有一个新版 IC、OOS、分层、多空或评分字段已经写入；只包含身份、计数和时间字段时返回
        ``False``。
        """

        return any(row.get(field_name) is not None for field_name in _CALCULATION_METRIC_FIELDS)

    @classmethod
    def _compare_api_and_database_refresh_data(
        cls,
        sub_factor_id: int,
        api_sub_factor: dict[str, Any] | None,
        calculation_metrics: list[dict[str, Any]],
        validity_snapshots: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """比较 API 详情中明确返回的指标/有效性字段与已选数据库记录。

        参数 ``sub_factor_id`` 是目标子因子 ID，``api_sub_factor`` 是详情接口 data，``calculation_metrics`` 和
        ``validity_snapshots`` 是已按本次刷新筛选的数据库记录。返回实际比较过的字段清单；API 未暴露某个字段时不猜测
        其值，但 API 明确暴露且能定位到 DB 记录时，任何值不一致都抛出 ``FAIL_CONTRACT``。
        """

        if not isinstance(api_sub_factor, dict):
            return []
        matches: list[dict[str, Any]] = []
        api_metrics = cls._extract_api_metric_objects(api_sub_factor)
        for api_metric in api_metrics:
            cls._validate_api_factor_identity(api_metric, sub_factor_id, "metric")
            db_candidates = cls._find_matching_metric_rows(api_metric, calculation_metrics)
            if not db_candidates:
                # 没有身份字段且 DB 有多个窗口/范围时，无法安全判定对应关系；API 明确给出任一身份条件却找不到时必须报错。
                if cls._has_identity_value(api_metric, _METRIC_MATCH_IDENTITY_FIELDS):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "API refresh metric cannot be matched to factor_ic_summary_metrics",
                        {"api": api_metric, "database_metrics": calculation_metrics},
                    )
                continue
            if len(db_candidates) > 1:
                if cls._has_strong_identity_value(
                    api_metric,
                    ("id", "summary_id", "run_id"),
                ):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "API refresh metric matches multiple factor_ic_summary_metrics rows",
                        {"api": api_metric, "database_metrics": db_candidates},
                    )
                continue
            if len(db_candidates) != 1:
                continue
            db_metric = db_candidates[0]
            compared_fields = cls._compare_metric_fields(api_metric, db_metric)
            if compared_fields:
                matches.append(
                    {
                        "kind": "metric",
                        "api_identity": cls._metric_identity(api_metric),
                        "db_summary_id": db_metric.get("summary_id", db_metric.get("id")),
                        "fields": tuple(compared_fields),
                    }
                )

        for api_validity in cls._extract_api_validity_objects(api_sub_factor):
            cls._validate_api_factor_identity(api_validity, sub_factor_id, "validity")
            db_candidates = cls._find_matching_validity_rows(api_validity, validity_snapshots)
            if not db_candidates:
                if cls._has_identity_value(api_validity, _VALIDITY_MATCH_IDENTITY_FIELDS):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "API refresh validity cannot be matched to factor_validity_status",
                        {"api": api_validity, "database_validity": validity_snapshots},
                    )
                continue
            if len(db_candidates) > 1:
                if cls._has_strong_identity_value(api_validity, ("id", "run_id")):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "API refresh validity matches multiple factor_validity_status rows",
                        {"api": api_validity, "database_validity": db_candidates},
                    )
                continue
            if len(db_candidates) != 1:
                continue
            compared_fields = cls._compare_validity_fields(api_validity, db_candidates[0])
            if compared_fields:
                matches.append(
                    {
                        "kind": "validity",
                        "api_identity": {key: api_validity.get(key) for key in ("id", "run_id")},
                        "db_validity_id": db_candidates[0].get("id"),
                        "fields": tuple(compared_fields),
                    }
                )
        return matches

    @staticmethod
    def _has_identity_value(data: dict[str, Any], fields: tuple[str, ...]) -> bool:
        """判断对象是否明确提供了至少一个非空身份字段。

        参数 ``data`` 是 API 指标或有效性对象，``fields`` 是允许参与匹配的身份字段集合。返回 ``True`` 表示调用方
        提供了可用于定位记录的值；显式空字符串也算已提供，便于发现接口返回的错误身份。日期时间身份字段即使明确为
        ``None`` 也算已提供，以便严格核对 API 与 DB 的空值。
        """

        return any(
            field in data and (data[field] is not None or field in _DATETIME_IDENTITY_FIELDS)
            for field in fields
        )

    @staticmethod
    def _has_strong_identity_value(data: dict[str, Any], fields: tuple[str, ...]) -> bool:
        """判断对象是否提供了可以唯一锚定记录的强身份字段。

        参数 ``data`` 是 API 指标或有效性对象，``fields`` 通常是主键、summary ID 或 Run ID。返回 ``True`` 表示
        多条候选也不应被静默忽略；只有窗口等不足以唯一定位的展示维度时，保留无法安全比较的兼容跳过行为。
        """

        return FactorComboService._has_identity_value(data, fields)

    @classmethod
    def _extract_api_metric_objects(cls, data: dict[str, Any]) -> list[dict[str, Any]]:
        """从 API 详情的指标容器中提取对象，不读取普通报告元数据。"""

        found: list[dict[str, Any]] = []

        def visit(value: Any) -> None:
            """递归遍历一个指标容器。"""

            if isinstance(value, dict):
                if any(key in value for key in _API_TO_DB_METRIC_FIELDS):
                    found.append(value)
                for key, child in value.items():
                    if key in _REFRESH_EVIDENCE_WRAPPER_FIELDS:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        for field_name in _API_METRIC_CONTAINER_FIELDS:
            if field_name in data:
                visit(data[field_name])
        return found

    @staticmethod
    def _extract_api_validity_objects(data: dict[str, Any]) -> list[dict[str, Any]]:
        """提取 API 详情中明确命名的有效性对象。"""

        return [
            data[field_name]
            for field_name in _API_VALIDITY_CONTAINER_FIELDS
            if isinstance(data.get(field_name), dict)
        ]

    @classmethod
    def _validate_api_factor_identity(
        cls,
        api_object: dict[str, Any],
        expected_factor_id: int,
        object_kind: str,
    ) -> None:
        """校验 API 指标或有效性对象中的因子归属身份。

        参数 ``api_object`` 是 API 返回的指标/有效性字典，``expected_factor_id`` 是本次登记生成的子因子 ID，
        ``object_kind`` 用于错误信息区分对象类型。不返回值；当对象明确返回了错误的 ``factor_id`` 或非子因子标识时
        抛出 ``FAIL_CONTRACT``。字段缺省时保留对旧详情接口的兼容性。
        """

        if api_object.get("factor_id") is not None:
            api_factor_id = cls._positive_int_or_failure(
                api_object.get("factor_id"),
                FlowOutcome.FAIL_CONTRACT,
                f"API refresh {object_kind} factor_id is invalid",
                api_object,
            )
            if api_factor_id != expected_factor_id:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"API refresh {object_kind} belongs to another factor",
                    {"expected_factor_id": expected_factor_id, "api": api_object},
                )
        if api_object.get("is_sub_factor_id") is not None and not cls._same_scalar(
            api_object.get("is_sub_factor_id"), True
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"API refresh {object_kind} is not marked as a sub-factor",
                {"expected_factor_id": expected_factor_id, "api": api_object},
            )

    @classmethod
    def _find_matching_metric_rows(
        cls,
        api_metric: dict[str, Any],
        database_metrics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """按完整身份条件查找数据库指标候选。

        参数 ``api_metric`` 是详情接口中的一个指标对象，``database_metrics`` 是已经限定到目标子因子和本次刷新
        Run 的 summary 行。返回同时满足所有已提供 ``summary_id``、``run_id``、范围和窗口字段的候选；不会因为先命中
        一个 ID 就跳过后续身份条件。
        """

        candidates = list(database_metrics)
        for api_key, db_keys in (("summary_id", ("summary_id", "id")), ("id", ("summary_id", "id"))):
            if api_key not in api_metric or api_metric[api_key] is None:
                continue
            expected = cls._positive_int_or_failure(
                api_metric[api_key],
                FlowOutcome.FAIL_CONTRACT,
                "API refresh metric summary id is invalid",
                api_metric,
            )
            candidates = [
                row
                for row in candidates
                if any(cls._safe_int(row.get(key)) == expected for key in db_keys)
            ]
        if "run_id" in api_metric and api_metric["run_id"] is not None:
            run_id = cls._required_non_empty_string_or_failure(
                api_metric["run_id"],
                FlowOutcome.FAIL_CONTRACT,
                "API refresh metric run_id is invalid",
                api_metric,
            )
            candidates = [row for row in candidates if str(row.get("run_id", "")).strip() == run_id]
        for key in (
            "ic_scope",
            "calculation_mode",
            "factor_bar_interval",
            "factor_window_bars",
            "return_bar_interval",
            "forward_return_bars",
            "interval_value",
            "forward_return_horizon",
            "universe_key",
            "symbol",
            "window_scope",
            "metric_window_bars",
            "metric_window_days",
            "period_start",
            "period_end",
        ):
            if key not in api_metric or (api_metric[key] is None and key not in _DATETIME_IDENTITY_FIELDS):
                continue
            expected_value = api_metric[key]
            candidates = [
                row
                for row in candidates
                if key in row and cls._same_identity_scalar(key, row.get(key), expected_value)
            ]
        return candidates

    @classmethod
    def _find_matching_validity_rows(
        cls,
        api_validity: dict[str, Any],
        database_validity: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """按有效性快照及其汇总外键身份查找数据库候选。

        参数 ``api_validity`` 是详情接口中的有效性对象，``database_validity`` 是本次刷新选出的数据库快照。返回
        同时满足所有已提供身份字段的候选；空字符串的 ``symbol`` 等合法值不会被误当成缺省条件。
        """

        candidates = list(database_validity)
        if "id" in api_validity and api_validity["id"] is not None:
            expected = cls._positive_int_or_failure(
                api_validity["id"],
                FlowOutcome.FAIL_CONTRACT,
                "API refresh validity id is invalid",
                api_validity,
            )
            candidates = [row for row in candidates if cls._safe_int(row.get("id")) == expected]
        if "run_id" in api_validity and api_validity["run_id"] is not None:
            expected_run_id = cls._required_non_empty_string_or_failure(
                api_validity["run_id"],
                FlowOutcome.FAIL_CONTRACT,
                "API refresh validity run_id is invalid",
                api_validity,
            )
            candidates = [row for row in candidates if str(row.get("run_id", "")).strip() == expected_run_id]
        for key in (
            "universe_key",
            "factor_bar_interval",
            "factor_window_bars",
            "return_bar_interval",
            "forward_return_bars",
            "window_scope",
            "time_series_summary_id",
            "cross_sectional_summary_id",
            "time_series_summary_run_id",
            "cross_sectional_summary_run_id",
            "period_start",
            "period_end",
        ):
            if key not in api_validity or (api_validity[key] is None and key not in _DATETIME_IDENTITY_FIELDS):
                continue
            expected_value = api_validity[key]
            candidates = [
                row
                for row in candidates
                if key in row and cls._same_identity_scalar(key, row.get(key), expected_value)
            ]
        return candidates

    @classmethod
    def _compare_metric_fields(cls, api_metric: dict[str, Any], db_metric: dict[str, Any]) -> list[str]:
        """比较一个 API 指标对象与一个 DB summary 行的共同字段。

        参数 ``api_metric`` 和 ``db_metric`` 分别是 API 与数据库的同一汇总行。返回实际比较的字段名列表；API 明确
        返回的字段即使值为 ``None`` 也必须在 DB 中存在并保持相同值，DB 缺字段或值不一致时抛出 ``FAIL_CONTRACT``。
        """

        compared: list[str] = []
        for api_field, db_fields in _API_TO_DB_METRIC_FIELDS.items():
            if api_field not in api_metric:
                continue
            db_field = next((field for field in db_fields if field in db_metric), None)
            if db_field is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"DB refresh metric is missing field required by API: {api_field}",
                    {"field": api_field, "db_fields": db_fields, "api_metric": api_metric, "db_metric": db_metric},
                )
            db_value = db_metric[db_field]
            if not cls._same_identity_scalar(api_field, api_metric[api_field], db_value):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"API and DB refresh metric differ at {api_field}",
                    {"field": api_field, "api": api_metric[api_field], "db": db_value, "api_metric": api_metric},
                )
            compared.append(api_field)
        return compared

    @classmethod
    def _compare_validity_fields(cls, api_validity: dict[str, Any], db_validity: dict[str, Any]) -> list[str]:
        """比较 API 有效性对象与 DB 快照的共同业务字段。

        参数 ``api_validity`` 和 ``db_validity`` 分别是 API 与数据库的同一有效性快照。返回实际比较的字段名列表；
        API 明确返回的字段即使值为 ``None`` 也必须存在于 DB 并保持一致。
        """

        compared: list[str] = []
        for field_name in _API_TO_DB_VALIDITY_FIELDS:
            if field_name not in api_validity:
                continue
            if field_name not in db_validity:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"DB refresh validity is missing field required by API: {field_name}",
                    {"field": field_name, "api_validity": api_validity, "db_validity": db_validity},
                )
            if not cls._same_identity_scalar(field_name, api_validity[field_name], db_validity[field_name]):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"API and DB refresh validity differ at {field_name}",
                    {
                        "field": field_name,
                        "api": api_validity[field_name],
                        "db": db_validity[field_name],
                        "api_validity": api_validity,
                    },
                )
            compared.append(field_name)
        return compared

    @staticmethod
    def _metric_identity(metric: dict[str, Any]) -> dict[str, Any]:
        """返回用于诊断 API 指标匹配的身份字段。"""

        return {
            key: metric.get(key)
            for key in ("id", "summary_id", "run_id", "ic_scope", "window_scope", "universe_key", "symbol")
            if metric.get(key) not in (None, "")
        }

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        """将可选 ID 转成整数；非法值返回 ``None``。"""

        try:
            if isinstance(value, bool):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _same_scalar(left: Any, right: Any) -> bool:
        """比较 API/DB 标量，兼容 JSON 布尔、MySQL tinyint、Decimal 和数字字符串。

        参数 ``left``、``right`` 是待对账的两个标量。返回值表示两者业务语义是否一致；``None`` 只与 ``None`` 相等，
        ``True/False`` 可分别与 ``1/0`` 及其字符串表示相等，普通数值允许极小的 Decimal 精度误差。
        """

        if left is None or right is None:
            return left is None and right is None

        left_bool = FactorComboService._coerce_boolean(left)
        right_bool = FactorComboService._coerce_boolean(right)
        if FactorComboService._has_explicit_boolean_semantics(left) or FactorComboService._has_explicit_boolean_semantics(
            right
        ):
            return left_bool is not None and right_bool is not None and left_bool == right_bool

        left_decimal = FactorComboService._coerce_decimal(left)
        right_decimal = FactorComboService._coerce_decimal(right)
        if left_decimal is not None and right_decimal is not None:
            return abs(left_decimal - right_decimal) <= Decimal("0.00000001")
        return str(left).strip().casefold() == str(right).strip().casefold()

    @classmethod
    def _same_identity_scalar(cls, field_name: str, left: Any, right: Any) -> bool:
        """按身份字段语义比较 API 与数据库值。

        参数 ``field_name`` 是参与记录定位的字段名，``left`` 与 ``right`` 是接口和数据库的候选值。返回值表示
        两者是否代表同一身份值；日期时间字段使用 ``_same_datetime_identity``，其余字段沿用普通标量比较规则。
        不抛出解析异常，无法解析的时间会退回严格标量比较，避免把底层 ``TypeError`` 泄露为测试框架错误。
        """

        if field_name in _DATETIME_IDENTITY_FIELDS:
            return cls._same_datetime_identity(left, right)
        return cls._same_scalar(left, right)

    @staticmethod
    def _same_datetime_identity(left: Any, right: Any) -> bool:
        """比较两个日期时间身份值，并处理 API ISO 时间与 MySQL DATETIME 的表示差异。

        参数 ``left`` 与 ``right`` 可以是 Python ``datetime``/``date`` 或 ISO/MySQL 日期时间字符串。双方都带时区时
        按同一 UTC 时刻比较；双方都不带时区时按数据库保存的本地时间比较；只有一方带时区时，因 MySQL
        ``DATETIME(6)`` 不携带时区元数据，按去除时区后的墙上时间比较。返回值表示语义是否一致；无法解析时只在
        原始标量完全一致时返回真。
        """

        left_datetime = FactorComboService._parse_datetime_identity(left)
        right_datetime = FactorComboService._parse_datetime_identity(right)
        if left_datetime is None or right_datetime is None:
            return FactorComboService._same_scalar(left, right)

        left_aware = left_datetime.tzinfo is not None and left_datetime.utcoffset() is not None
        right_aware = right_datetime.tzinfo is not None and right_datetime.utcoffset() is not None
        if left_aware and right_aware:
            return left_datetime.astimezone(timezone.utc) == right_datetime.astimezone(timezone.utc)
        if left_aware != right_aware:
            return left_datetime.replace(tzinfo=None) == right_datetime.replace(tzinfo=None)
        return left_datetime == right_datetime

    @staticmethod
    def _parse_datetime_identity(value: Any) -> datetime | None:
        """把常见 API/数据库日期时间值转换为 Python ``datetime``。

        参数 ``value`` 是待解析的日期时间值。返回解析后的 ``datetime`` 或 ``None``；不修改原始值，也不因非法输入
        抛出异常，调用方可据此把非法身份值判定为不匹配。
        """

        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith(("Z", "z")):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            for format_string in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
                try:
                    return datetime.strptime(normalized, format_string)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _has_explicit_boolean_semantics(value: Any) -> bool:
        """判断一个值是否明确采用布尔语义，而不是普通数值语义。

        参数 ``value`` 是待比较的 API/DB 标量。返回 ``True`` 表示值是实际布尔或 ``true/false`` 文本；普通的数值
        ``0/1`` 保留为数值，避免破坏指标的小数容差比较。
        """

        if isinstance(value, bool):
            return True
        if isinstance(value, str):
            return value.strip().casefold() in {"true", "false"}
        return False

    @staticmethod
    def _coerce_boolean(value: Any) -> bool | None:
        """把 JSON/MySQL 常见布尔表示转换为布尔值。

        参数 ``value`` 是任意 API 或 DB 标量。返回 ``True``、``False`` 或 ``None``；非布尔语义的值返回 ``None``，
        供严格标量比较继续尝试数字或文本比较。
        """

        if isinstance(value, bool):
            return value
        if isinstance(value, (int, Decimal)) and value in (0, 1):
            return bool(value)
        if isinstance(value, float) and value in (0.0, 1.0):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "1"}:
                return True
            if normalized in {"false", "0"}:
                return False
        return None

    @staticmethod
    def _coerce_decimal(value: Any) -> Decimal | None:
        """尝试把标量转换为有限 Decimal。

        参数 ``value`` 是待比较的数值或数字字符串。返回有限 Decimal；布尔、空值、非数字和非有限值返回 ``None``。
        """

        if value is None or isinstance(value, bool):
            return None
        try:
            decimal_value = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            return None
        if not decimal_value.is_finite():
            return None
        return decimal_value

    @staticmethod
    def _extract_refresh_run_ids(refresh_data: dict[str, Any]) -> tuple[str, ...]:
        """从刷新响应的 ``results`` 容器提取后端明确返回的指标计算 Run ID。

        参数 ``refresh_data`` 是 Performance Refresh 的 data 对象。返回去重且保持出现顺序的计算 Run ID；只识别
        ``run_id``、``ic_run_id``、``factor_ic_run_id``、``summary_run_id`` 和维度汇总 Run ID，不把刷新任务 ID或普通
        Pipeline Run ID当作指标 Run。响应没有提供这些字段时返回空元组，由数据库自身的复合子因子隔离性完成证据关联。
        """

        run_keys = {
            "run_id",
            "ic_run_id",
            "factor_ic_run_id",
            "summary_run_id",
            "time_series_run_id",
            "cross_sectional_run_id",
        }
        found: list[str] = []

        def visit(value: Any) -> None:
            """递归遍历刷新 results 中的 JSON 容器。"""

            if isinstance(value, dict):
                for key, item in value.items():
                    if key in run_keys and isinstance(item, (str, int)) and not isinstance(item, bool):
                        normalized = str(item).strip()
                        if normalized and normalized not in found:
                            found.append(normalized)
                    elif isinstance(item, (dict, list)):
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(refresh_data.get("results"))
        return tuple(found)

    def verify_registered_sub_factor(
        self,
        sub_factor_id: int,
        expected_factor_name: str,
        *,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """回查登记生成的子因子，并确认刷新结果在详情接口中有可见数据。

        参数 ``sub_factor_id`` 是登记响应中的子因子 ID，``expected_factor_name`` 是登记响应中的子因子名称。
        ``max_retries`` 是子因子详情在刷新完成后仍处于最终一致性窗口时，对 404、408、429 和 5xx 或网络异常的额外
        重试次数，缺省使用技术重试配置。返回详情接口的 ``data`` 对象；HTTP、ID、名称或刷新指标证据缺失时抛出
        ``FactorComboFlowError``。查询固定携带 ``ic_mode=timeseries``，不会触发任何写操作。
        """

        if self._sub_factor_api is None:
            raise FactorComboFlowError(FlowOutcome.FAIL_CONTRACT, "Sub-factor API is not configured")
        retry_limit = self._settings.max_technical_retries if max_retries is None else int(max_retries)
        if retry_limit < 0:
            raise ValueError("max_retries must not be negative")
        attempts: list[dict[str, Any]] = []
        response: requests.Response | None = None
        for attempt_index in range(retry_limit + 1):
            try:
                response = self._call_flow_request(
                    "read registered sub-factor",
                    lambda: self._sub_factor_api.get_sub_factor(sub_factor_id, ic_mode="timeseries"),
                )
            except FactorComboFlowError as error:
                if error.outcome != FlowOutcome.FAIL_TECHNICAL:
                    raise
                attempts.append({"attempt": attempt_index + 1, "error": error.details})
                if attempt_index >= retry_limit:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_TECHNICAL,
                        "registered sub-factor remained unavailable after retries",
                        {"sub_factor_id": sub_factor_id, "attempts": attempts},
                    ) from error
                self._sleep_for_refresh_retry()
                continue

            attempts.append(
                {
                    "attempt": attempt_index + 1,
                    "status_code": response.status_code,
                    "response": self._safe_json(response),
                }
            )
            if response.status_code in _TRANSIENT_HTTP_STATUSES or response.status_code == 404:
                if attempt_index >= retry_limit:
                    outcome = FlowOutcome.FAIL_REFRESH if response.status_code == 404 else FlowOutcome.FAIL_TECHNICAL
                    raise FactorComboFlowError(
                        outcome,
                        "registered sub-factor remained unavailable after retries",
                        {
                            "sub_factor_id": sub_factor_id,
                            "attempts": attempts,
                            "reason": "registered_sub_factor_not_visible"
                            if response.status_code == 404
                            else "registered_sub_factor_query_unavailable",
                        },
                    )
                self._sleep_for_refresh_retry()
                continue
            break

        if response is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_TECHNICAL,
                "registered sub-factor query exited without a response",
                {"sub_factor_id": sub_factor_id, "attempts": attempts},
            )
        data = self._require_flow_data(response, {200}, "read registered sub-factor")
        returned_id = self._positive_int_or_failure(
            data.get("id"),
            FlowOutcome.FAIL_CONTRACT,
            "registered sub-factor response is missing id",
            data,
        )
        if returned_id != int(sub_factor_id):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered sub-factor response ID does not match registration",
                data,
            )
        returned_name = self._required_non_empty_string_or_failure(
            data.get("sub_factor_name"),
            FlowOutcome.FAIL_CONTRACT,
            "registered sub-factor response is missing sub_factor_name",
            data,
        )
        if returned_name != str(expected_factor_name).strip():
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered sub-factor name does not match registration response",
                data,
            )
        for field_name, expected_value in (
            ("type", 1),
            ("mining_method", "factor_combo"),
            ("data_source", "factor_combo_report"),
            ("serial_prefix", "COMBO"),
        ):
            if field_name not in data:
                continue
            actual_value = data.get(field_name)
            if field_name == "type":
                if actual_value != expected_value or isinstance(actual_value, bool):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"registered sub-factor {field_name} is inconsistent",
                        data,
                    )
            elif str(actual_value).strip() != expected_value:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"registered sub-factor {field_name} is inconsistent",
                    data,
                )
        if not self._contains_refresh_evidence(data):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "registered sub-factor response contains no refreshed IC/validity data",
                data,
            )
        return data

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
                round_record["registration"] = {
                    "sub_factor_id": registration.first_registration.get("sub_factor_id"),
                    "registration_id": registration.first_registration.get("registration_id"),
                    "refresh": registration.refresh.data,
                    "database_refresh": {
                        "calculation_runs": registration.database_refresh.calculation_runs,
                        "validity_snapshots": registration.database_refresh.validity_snapshots,
                        "refresh_run_ids": registration.database_refresh.refresh_run_ids,
                        "matched_run_ids": registration.database_refresh.matched_run_ids,
                    },
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
        if isinstance(value, float) and not value.is_integer():
            raise FactorComboFlowError(outcome, message, details)
        try:
            normalized = int(value)
        except (TypeError, ValueError) as error:
            raise FactorComboFlowError(outcome, message, details) from error
        if normalized < 0:
            raise FactorComboFlowError(outcome, message, details)
        return normalized

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
                identity["combo_id"],
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
    def _validate_real_review(cls, review: dict[str, Any], details: Any) -> None:
        """校验真实组合结果的评审决策字段，不把缺失字段当成无效业务结果。

        参数 ``review`` 是真实 Pipeline 返回的 ``factor_combo_review``，``details`` 是完整结果诊断对象。
        不返回值；``experiment_valid``、``registration_ready`` 或搜索继续标志类型错误时抛出契约异常。
        """

        cls._required_boolean_or_failure(
            review.get("experiment_valid"),
            FlowOutcome.FAIL_CONTRACT,
            "factor_combo_review.experiment_valid is missing or not boolean",
            details,
        )
        cls._required_boolean_or_failure(
            review.get("registration_ready"),
            FlowOutcome.FAIL_CONTRACT,
            "factor_combo_review.registration_ready is missing or not boolean",
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

    @staticmethod
    def _validate_completed_refresh(data: dict[str, Any], expected_factor_name: str) -> None:
        """验证刷新任务 completed 状态下的因子和任务单元完整性。

        参数 ``data`` 是刷新查询响应中的 data 对象，``expected_factor_name`` 是本次登记生成的因子名。
        不返回值；字段类型不符合接口契约时抛出 ``FAIL_CONTRACT``，任务单元不完整或因子未完成时抛出
        ``FAIL_REFRESH``。
        """

        completed_factors = data.get("completed_factors")
        incomplete_factors = data.get("incomplete_factors")
        summary = data.get("summary")
        if not isinstance(completed_factors, list) or not isinstance(incomplete_factors, list) or not isinstance(summary, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "completed Performance Refresh response must contain factor arrays and summary object",
                data,
            )
        if any(not isinstance(item, str) or not item.strip() for item in completed_factors + incomplete_factors):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "Performance Refresh factor arrays must contain non-empty strings",
                data,
            )
        if len(set(completed_factors)) != len(completed_factors) or len(set(incomplete_factors)) != len(incomplete_factors):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "Performance Refresh factor arrays must not contain duplicate names",
                data,
            )
        if set(completed_factors).intersection(incomplete_factors):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "Performance Refresh completed and incomplete factor arrays overlap",
                data,
            )
        required_counts = (
            "total_units",
            "completed_units",
            "skipped_window_count",
            "failed_unit_count",
            "not_run_unit_count",
        )
        for field_name in required_counts:
            value = summary.get(field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"summary.{field_name} must be a non-negative integer",
                    data,
                )
        if summary["total_units"] == 0:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "Performance Refresh completed response contains no task units",
                data,
            )
        if summary["completed_units"] > summary["total_units"]:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "Performance Refresh completed_units cannot exceed total_units",
                data,
            )
        calculated_problem_count = (
            summary["skipped_window_count"]
            + summary["failed_unit_count"]
            + summary["not_run_unit_count"]
        )
        if "problem_unit_count" in summary:
            reported_problem_count = summary["problem_unit_count"]
            if isinstance(reported_problem_count, bool) or not isinstance(reported_problem_count, int) or reported_problem_count < 0:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "summary.problem_unit_count must be a non-negative integer",
                    data,
                )
            if reported_problem_count != calculated_problem_count:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "summary.problem_unit_count is inconsistent with the derived problem count",
                    data,
                )
            problem_count = reported_problem_count
        else:
            problem_count = calculated_problem_count

        if (
            str(expected_factor_name) not in completed_factors
            or incomplete_factors
            or summary["completed_units"] != summary["total_units"]
            or problem_count != 0
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "Performance Refresh reported completed but its factor or task units are incomplete",
                data,
            )

    @staticmethod
    def _contains_refresh_evidence(data: dict[str, Any]) -> bool:
        """判断子因子详情中是否存在刷新后的有效性或 IC 数据证据。

        参数 ``data`` 是子因子详情接口的 data 对象。
        返回 ``True`` 表示明确的有效性、IC 或计算指标容器中至少存在一个有实际值的字段；返回 ``False`` 表示只能证明
        ID、名称或普通元数据可读，无法证明 Performance Refresh 已经把计算结果写回。
        """

        def has_value(value: Any) -> bool:
            """判断指标值是否足以证明刷新产生了结果。"""

            if value is None:
                return False
            if isinstance(value, str):
                return value.strip().lower() not in {"", "unknown", "pending", "null", "none"}
            return True

        def has_metric_fields(value: dict[str, Any]) -> bool:
            """判断一个指标对象是否包含有意义的数值或有效性状态。"""

            for field_name in _REFRESH_EVIDENCE_FIELDS:
                if field_name in value and has_value(value[field_name]):
                    return True
            for field_name in _REFRESH_EVIDENCE_STATUS_FIELDS:
                status = value.get(field_name)
                if isinstance(status, str) and status.strip().lower() not in {"", "unknown", "pending"}:
                    return True
            return False

        def has_container_evidence(value: Any) -> bool:
            """只在指标容器及其常见包装字段内递归检查刷新证据。"""

            if isinstance(value, dict):
                if has_metric_fields(value):
                    return True
                return any(
                    has_container_evidence(item)
                    for key, item in value.items()
                    if key in _REFRESH_EVIDENCE_WRAPPER_FIELDS
                )
            if isinstance(value, list):
                return any(has_container_evidence(item) for item in value)
            return False

        for field_name in _REFRESH_EVIDENCE_CONTAINER_FIELDS:
            if field_name in data and has_container_evidence(data[field_name]):
                return True
        for field_name in ("factor_validity_status", "validity_status", "validity"):
            value = data.get(field_name)
            if isinstance(value, dict) and has_metric_fields(value):
                return True
        return False

    def cleanup(self) -> None:
        """按配置清理本次测试创建且未被真实 Run 使用的数据。

        不接收参数。
        不返回值；未开启 ``cleanup_test_data`` 时保留测试数据供排查，受保护的真实 Run 表单始终保留。
        """

        if not self._settings.cleanup_test_data:
            return
        self._repository.clean_test_graph(self.scope.cleanable_form_ids(), self.scope.cleanable_session_ids())

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
            return response.json()
        except ValueError:
            return response.text

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
