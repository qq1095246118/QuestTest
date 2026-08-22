"""组合因子台测试流程编排。"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Protocol
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
_EXPERIMENT_CONFIG_UNSET = object()

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
    "slice_count": ("slice_count",),
    "valid_slice_count": ("valid_slice_count",),
    "is_period_start": ("is_period_start",),
    "is_period_end": ("is_period_end",),
    "oos_period_start": ("oos_period_start",),
    "oos_period_end": ("oos_period_end",),
    "is_slice_count": ("is_slice_count",),
    "oos_slice_count": ("oos_slice_count",),
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
# Run 标识由 Agent/IC 刷新服务生成，虽然字段名以 ``_id`` 结尾，但不是数据库自增整数。
_STRING_RUN_ID_FIELDS = {"run_id"}

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


class ResourceScope(Protocol):
    """描述 Service 需要的测试资源生命周期操作。

    具体实现位于 ``tests`` 测试基础设施；Service 只负责在业务流程节点登记或解除保护，
    不依赖 pytest Fixture 的具体实现，也不负责执行数据库清理。
    """

    def track_session(self, session_id: int) -> None:
        """登记本次业务流程创建的会话。"""

    def track_form(self, session_id: int, form_id: int) -> None:
        """登记本次业务流程创建的表单。"""

    def protect_form(self, form_id: int) -> None:
        """保护仍可能被异步流程使用的表单。"""

    def release_form(self, form_id: int) -> None:
        """解除已进入安全终态表单的保护。"""



@dataclass(frozen=True)
class SubmittedForm:
    """表示提交接口成功返回的组合表单。"""

    session_id: int
    form_id: int
    pool_id: int
    status: str
    form_no: str = ""


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
    form_status: str = ""
    combo_status: str = ""
    component_count: int = 0
    idempotent_replay: bool = False


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
    run_details: tuple[dict[str, Any], ...] = ()


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
            body = response.json()
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

        parent = self._repository.find_parent_with_sub_factors()
        if parent is None:
            raise RuntimeError("Test database has no parent factor with at least two sub-factors")
        session_id = self.create_session()
        payload = self.build_form_payload(session_id, [parent.factor_name], is_sub_factor=0)
        submitted = self.require_submitted_form(self.submit_form(payload), session_id)
        return submitted, parent

    def create_form_with_mixed_parent_and_sub_factor(self) -> tuple[SubmittedForm, ParentFactorChoice]:
        """创建一个同时选择母因子和其子因子的表单以验证展开去重。

        不接收参数。
        返回表单和母因子展开基线；新版接口允许母因子与子因子混选，并应将展开结果和直接选择结果去重。
        接口准备失败时抛出 ``RuntimeError``，便于直接识别契约冲突。
        """

        parent = self._repository.find_parent_with_sub_factors()
        if parent is None:
            raise RuntimeError("Test database has no parent factor with at least two sub-factors")
        session_id = self.create_session()
        payload = self.build_form_payload(
            session_id,
            [parent.factor_name, parent.sub_factors[0].sub_factor_name],
            is_sub_factor=0,
        )
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
        # 只有请求名称数量与最终成员数量一致时，才能按“直接子因子逐项对应”核对名称。新版接口允许母因子与
        # 子因子混选；此时母因子名称会展开为多个成员，不能再把请求名称列表直接与成员名称列表比较。
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
        parent_relation_count: int | None = None,
    ) -> dict[str, Any]:
        """深度核对登记响应、请求内容和四个完整落库实体。

        参数 ``response_data`` 是登记接口 data，``request_payload`` 是登记请求，``version_row`` 是具体组合版本，后四个
        参数依次是完整的子因子、因子详情、有效性快照和登记映射记录；``form_row`` 和 ``experiment_row`` 是同一登记
        链路的表单、实验记录，``parent_relation_count`` 是可选的谱系关系数量快照。返回逐实体比较诊断；响应嵌套对象
        中的每个明确字段都必须能在对应 DB 记录中找到并保持一致，且必须证明表单、实验、版本、登记四者指向同一具体
        版本。未提供表单或实验行时无法完成深层对账，直接抛出契约异常，而不是退化成只校验四个登记资源。
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
        if parent_relation_count is not None:
            if isinstance(parent_relation_count, bool) or int(parent_relation_count) < 0:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor combo registration parent relation count must be non-negative",
                    parent_relation_count,
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
        if "factor_id" not in registration_row or registration_row.get("factor_id") is not None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registration mapping factor_id must be NULL for an unparented composite sub-factor",
                {"api": dict(response_data), "registration": dict(registration_row)},
            )
        if parent_relation_count is not None and int(parent_relation_count) != 0:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered composite sub-factor unexpectedly has parent relations",
                {"api": dict(response_data), "parent_relation_count": parent_relation_count},
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
        ``metric_mode`` 只能是 ``time_series`` 或 ``cross_sectional``，``validity_state`` 只能是 ``invalid`` 或
        ``unknown``；``factor_bar_interval`` 和 ``factor_window_bars`` 是登记有效性快照及复合子因子要写入的周期参数。
        登记接口的初始快照不允许提交 valid 状态。返回包含报告、组件、新版绩效字段和时序/截面有效性字段的完整登记
        请求；指标模式非法时抛出 ``ValueError``，真实 Agent 流程不得调用此模拟构造方法。
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

        参数 ``state`` 必须是 ``valid``、``invalid`` 或 ``unknown``；``factor_bar_interval`` 是因子 K 线级别，
        ``factor_window_bars`` 是因子窗口。返回不包含后端生成身份和审计字段的请求对象；``valid`` 仅保留给其他
        Worker 兼容场景，登记接口默认使用允许的 ``unknown``，``invalid`` 明确为 ``false``，``unknown`` 的分数
        和标志均为 ``null``。
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
                        registration = self._repository.get_registration(
                            combo_id,
                            version_id=version_id,
                            combo_version_hash=str(version.get("combo_version_hash") or "").strip().lower(),
                        )
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
                run_details_reader = getattr(self._repository, "get_factor_refresh_run_details", None)
                run_details = (
                    run_details_reader(normalized_sub_factor_id)
                    if callable(run_details_reader)
                    else None
                )
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
                    run_details=run_details,
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
        run_details: Any | None = None,
    ) -> DatabaseRefreshEvidence:
        """按刷新后有效性快照选择本次计算结果，并校验 API/DB 关联一致性。

        参数 ``sub_factor_id`` 是目标复合子因子 ID，``calculation_rows`` 是新版汇总明细或兼容聚合结果，
        ``validity_snapshots`` 是有效性快照查询结果，``refresh_data`` 是刷新任务响应，``api_sub_factor`` 是可选的
        API 详情，``run_details`` 是真实 Repository 从 ``factor_ic_runs`` 主表读取的完整 Run 记录；未提供 Run 明细时
        仅保留离线替身兼容路径。返回严格关联的数据库证据；数据缺失、指标为空或任务 Run 未落库时抛出
        ``FAIL_REFRESH``，身份、外键或 API/DB 指标不一致时抛出 ``FAIL_CONTRACT``。
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
        refresh_run_contexts = cls._extract_refresh_run_contexts(refresh_data)
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

        normalized_run_details = cls._validate_refresh_run_details(
            sub_factor_id,
            expected_run_ids,
            run_details,
            normalized_metrics,
            refresh_run_contexts,
        )

        cls._validate_summary_links(
            selected_validity,
            linked_summary_ids,
            normalized_metrics,
            summary_ids,
            expected_run_ids,
            allow_aggregate_compatibility=bool(
                not normalized_metrics
                and calculation_rows
                and all("summary_row_count" in row for row in calculation_rows if isinstance(row, dict))
            ),
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
            run_details=tuple(normalized_run_details),
        )

    @classmethod
    def _validate_summary_links(
        cls,
        validity_rows: list[dict[str, Any]],
        linked_summary_ids: set[int],
        calculation_metrics: list[dict[str, Any]],
        database_summary_ids: set[int],
        expected_run_ids: tuple[str, ...],
        *,
        allow_aggregate_compatibility: bool = False,
    ) -> None:
        """校验有效性快照引用的 summary ID、因子和计算 Run 完整一致。

        参数 ``validity_rows`` 是本次刷新选出的有效性快照，``linked_summary_ids`` 是其中的 summary 外键集合，
        ``calculation_metrics`` 是新版 summary 明细，``database_summary_ids`` 是明细实际返回的 ID 集合，
        ``expected_run_ids`` 是刷新响应或有效性快照确定的计算 Run。新版明细模式下不返回值；外键缺失、Run 不一致、
        因子归属不一致时抛出 ``FAIL_CONTRACT``。仅在调用方明确标记旧版离线聚合替身时允许没有明细；真实 Repository
        必须返回 summary 明细，否则即使汇总计数存在也判定为契约缺口。
        """

        if not linked_summary_ids:
            return
        if not calculation_metrics:
            if allow_aggregate_compatibility:
                return
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_validity_status summary links cannot be verified without summary detail rows",
                {"linked_summary_ids": sorted(linked_summary_ids), "expected_run_ids": expected_run_ids},
            )
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
                expected_scope = prefix
                actual_scope = metric.get("ic_scope")
                if not isinstance(actual_scope, str) or actual_scope.strip().lower() != expected_scope:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"refresh validity {prefix} summary has wrong ic_scope",
                        {
                            "expected_ic_scope": expected_scope,
                            "actual_ic_scope": actual_scope,
                            "validity": validity,
                            "summary": metric,
                        },
                    )
                summary_scope_alias_field = f"{prefix}_summary_ic_scope"
                summary_scope_alias = validity.get(summary_scope_alias_field)
                if summary_scope_alias_field in validity and (
                    not isinstance(summary_scope_alias, str)
                    or summary_scope_alias.strip().lower() != expected_scope
                ):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"refresh validity {prefix} summary scope alias is inconsistent",
                        {"validity": validity, "summary": metric, "expected_ic_scope": expected_scope},
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

                # 有效性表保存的窗口/样本池等维度必须和被外键引用的 summary 行一致；不能只凭 summary_id 命中。
                for dimension_name in (
                    "universe_key",
                    "factor_bar_interval",
                    "factor_window_bars",
                    "return_bar_interval",
                    "forward_return_bars",
                    "window_scope",
                    "period_start",
                    "period_end",
                ):
                    validity_fields = (
                        f"{prefix}_summary_{dimension_name}",
                        dimension_name,
                    )
                    present_validity_fields = [
                        field_name for field_name in validity_fields if field_name in validity
                    ]
                    if not present_validity_fields:
                        continue
                    if dimension_name not in metric:
                        raise FactorComboFlowError(
                            FlowOutcome.FAIL_CONTRACT,
                            f"linked {prefix} summary is missing dimension {dimension_name}",
                            {"validity": validity, "summary": metric},
                        )
                    for validity_field in present_validity_fields:
                        if not cls._same_identity_scalar(
                            dimension_name,
                            validity.get(validity_field),
                            metric.get(dimension_name),
                        ):
                            raise FactorComboFlowError(
                                FlowOutcome.FAIL_CONTRACT,
                                f"refresh validity {prefix} summary differs at {dimension_name}",
                                {
                                    "dimension": dimension_name,
                                    "validity_field": validity_field,
                                    "validity": validity.get(validity_field),
                                    "summary": metric.get(dimension_name),
                                    "validity_row": validity,
                                    "summary_row": metric,
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
            unexpected_run_ids = sorted(
                {
                    linked_run_id
                    for row in selected
                    for linked_run_id in row["_linked_run_ids"]
                    if linked_run_id not in refresh_run_ids
                }
            )
            if unexpected_run_ids:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_validity_status links the selected refresh to unexpected calculation Runs",
                    {
                        "factor_id": sub_factor_id,
                        "refresh_run_ids": refresh_run_ids,
                        "unexpected_run_ids": unexpected_run_ids,
                        "validity_rows": selected,
                    },
                )
            return selected

        # 没有明确 Run ID 时不能用 updated_at 猜“最新批次”。只有当所有候选明确属于同一组计算 Run 时，
        # 才能把这组快照作为本次证据；多个 Run 组意味着无法证明批次归属，应直接暴露契约歧义。
        run_sets = {frozenset(row["_linked_run_ids"]) for row in candidates}
        if not run_sets:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "factor_validity_status has no linked calculation Run set",
                {"factor_id": sub_factor_id, "validity_rows": candidates},
            )
        if len(run_sets) != 1:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "refresh validity rows belong to multiple calculation Run batches but the refresh response has no Run ID",
                {"factor_id": sub_factor_id, "run_sets": [sorted(value) for value in run_sets], "validity_rows": candidates},
            )
        return candidates

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
        detail_flags = ["summary_row_count" not in row for row in calculation_rows]
        if any(detail_flags) and not all(detail_flags):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_summary_metrics mixes aggregate Run rows with summary detail rows",
                calculation_rows,
            )
        detailed = all(detail_flags)
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
            if "summary_id" in row and "id" in row:
                row_id = cls._positive_int_or_failure(
                    row.get("id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics row id is invalid",
                    row,
                )
                if row_id != summary_id:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "factor_ic_summary_metrics id and summary_id refer to different rows",
                        row,
                    )
            if summary_id in summary_ids:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_summary_metrics contains duplicate summary id",
                    {"summary_id": summary_id, "rows": metric_rows + [row]},
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

    @classmethod
    def _validate_refresh_run_details(
        cls,
        sub_factor_id: int,
        expected_run_ids: tuple[str, ...],
        run_details: Any,
        calculation_metrics: list[dict[str, Any]],
        refresh_run_contexts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """核对 ``factor_ic_runs`` 主表及其与 summary、刷新响应的关联。

        参数 ``sub_factor_id`` 是本次刷新目标子因子，``expected_run_ids`` 是刷新响应或有效性快照确定的 Run 集合，
        ``run_details`` 是 Repository 查询的 ``factor_ic_runs`` 完整记录，``calculation_metrics`` 是同一批次的
        summary 明细，``refresh_run_contexts`` 是刷新接口明确返回的 Run 上下文。返回按 Run 排序的规范化主表记录；
        缺少主表、状态未完成、运行配置缺失、summary 维度与 Run 不一致或 API 上下文无法对账时抛出
        ``FactorComboFlowError``。``run_details is None`` 只代表离线旧替身没有实现该查询，保留兼容模式，不代表真实
        数据可以省略主表校验。
        """

        if run_details is None:
            return []
        if not isinstance(run_details, list):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_runs repository result must be a list",
                run_details,
            )

        expected_set = set(expected_run_ids)
        rows_by_run: dict[str, dict[str, Any]] = {}
        for raw_row in run_details:
            if not isinstance(raw_row, dict):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_runs repository row must be an object",
                    run_details,
                )
            run_id = cls._required_non_empty_string_or_failure(
                raw_row.get("run_id"),
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_runs row is missing run_id",
                raw_row,
            )
            if run_id not in expected_set:
                # Repository 查询的是目标子因子的历史 Run，历史行不属于本次刷新，可以保留在诊断中但不能参与对账。
                continue
            if run_id in rows_by_run:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_runs contains multiple master rows for the same run_id",
                    {"run_id": run_id, "rows": [rows_by_run[run_id], raw_row]},
                )
            row = dict(raw_row)
            row["run_id"] = run_id
            status_row = dict(row)
            status_row["run_status"] = row.get("status", row.get("run_status"))
            cls._validate_calculation_run_status(status_row)

            required_fields = (
                "interval_value",
                "forward_return_horizon",
                "universe_key",
                "config_hash",
                "config_json",
            )
            missing_fields = [
                field_name
                for field_name in required_fields
                if field_name not in row or row[field_name] is None or (isinstance(row[field_name], str) and not row[field_name].strip())
            ]
            if missing_fields:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_runs row is missing configuration fields required for refresh reconciliation",
                    {"run_id": run_id, "missing_fields": missing_fields, "row": row},
                )
            cls._required_sha256_or_failure(
                row["config_hash"],
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_runs config_hash is not a SHA-256 value",
                row,
            )
            parsed_config = cls._parse_json_value(row["config_json"], "factor_ic_runs.config_json")
            if not isinstance(parsed_config, Mapping):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_runs config_json must be a JSON object",
                    row,
                )
            row["config_json"] = dict(parsed_config)
            rows_by_run[run_id] = row

        missing_run_ids = [run_id for run_id in expected_run_ids if run_id not in rows_by_run]
        if missing_run_ids:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "factor_ic_runs master rows are missing for refresh run_id values",
                {"expected_run_ids": expected_run_ids, "missing_run_ids": missing_run_ids, "run_details": run_details},
            )

        for metric in calculation_metrics:
            run_id = cls._required_non_empty_string_or_failure(
                metric.get("run_id"),
                FlowOutcome.FAIL_CONTRACT,
                "summary metric is missing run_id while reconciling factor_ic_runs",
                metric,
            )
            run = rows_by_run.get(run_id)
            if run is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "summary metric points to a Run outside the selected factor_ic_runs set",
                    {"metric": metric, "expected_run_ids": expected_run_ids},
                )
            dimension_pairs = (
                (
                    "interval_value",
                    ("interval_value", "factor_bar_interval"),
                    ("interval_value", "run_interval_value", "run_factor_bar_interval"),
                ),
                (
                    "forward_return_horizon",
                    ("forward_return_horizon", "forward_return_bars"),
                    ("forward_return_horizon", "run_forward_return_horizon", "run_forward_return_bars"),
                ),
                ("universe_key", ("universe_key",), ("universe_key", "run_universe_key")),
            )
            for dimension_name, summary_fields, run_fields in dimension_pairs:
                summary_field = next((field for field in summary_fields if field in metric and metric[field] is not None), None)
                run_field = next((field for field in run_fields if field in run and run[field] is not None), None)
                if summary_field is None or run_field is None:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"summary/Run reconciliation is missing {dimension_name}",
                        {"metric": metric, "run": run, "dimension": dimension_name},
                    )
                if not cls._same_identity_scalar(dimension_name, metric[summary_field], run[run_field]):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"summary metric and factor_ic_runs differ at {dimension_name}",
                        {
                            "dimension": dimension_name,
                            "summary": metric[summary_field],
                            "run": run[run_field],
                            "metric": metric,
                            "run_detail": run,
                        },
                    )

        contexts_by_run: dict[str, dict[str, Any]] = {}
        for context in refresh_run_contexts:
            run_id = cls._required_non_empty_string_or_failure(
                context.get("run_id"),
                FlowOutcome.FAIL_CONTRACT,
                "Performance Refresh run context is missing run_id",
                context,
            )
            if run_id not in expected_set:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Performance Refresh returned a Run context outside the selected Run set",
                    {"context": context, "expected_run_ids": expected_run_ids},
                )
            existing = contexts_by_run.setdefault(run_id, {})
            for key, value in context.items():
                if key == "run_id" or value is None:
                    continue
                if key in existing and not cls._same_persisted_value(existing[key], value, field_name=key):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "Performance Refresh returned conflicting Run context values",
                        {"run_id": run_id, "field": key, "first": existing[key], "second": value},
                    )
                existing[key] = value

        for run_id, context in contexts_by_run.items():
            run = rows_by_run[run_id]
            context_to_db = {
                "status": ("status", "run_status"),
                "interval_value": ("interval_value", "run_interval_value"),
                "forward_return_horizon": ("forward_return_horizon", "run_forward_return_horizon"),
                "universe_key": ("universe_key", "run_universe_key"),
                "config_hash": ("config_hash", "run_config_hash"),
                "config_json": ("config_json", "run_config_json"),
            }
            for context_field, db_fields in context_to_db.items():
                if context_field not in context:
                    continue
                db_field = next((field for field in db_fields if field in run), None)
                if db_field is None:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"factor_ic_runs is missing field returned by Performance Refresh: {context_field}",
                        {"context": context, "run": run},
                    )
                if not cls._same_persisted_value(
                    context[context_field],
                    run[db_field],
                    field_name=context_field,
                    allow_database_extra=context_field == "config_json",
                ):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"Performance Refresh and factor_ic_runs differ at {context_field}",
                        {"field": context_field, "api": context[context_field], "database": run[db_field], "run": run},
                    )
        return [rows_by_run[run_id] for run_id in expected_run_ids]

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

        if api_sub_factor is None:
            return []
        if not isinstance(api_sub_factor, dict):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "registered sub-factor refresh response must be an object when API/DB comparison is requested",
                api_sub_factor,
            )

        metric_container_names = tuple(
            field_name for field_name in _API_METRIC_CONTAINER_FIELDS if field_name in api_sub_factor
        )
        validity_container_names = tuple(
            field_name for field_name in _API_VALIDITY_CONTAINER_FIELDS if field_name in api_sub_factor
        )
        # API 未暴露任何明确的刷新指标容器时，不猜测它的值；一旦显式返回容器，就必须至少完成一条真实字段对账。
        if not metric_container_names and not validity_container_names:
            return []

        matches: list[dict[str, Any]] = []
        api_metrics = cls._extract_api_metric_objects(api_sub_factor)
        if metric_container_names and not api_metrics:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "API refresh metric container is present but contains no comparable metric object",
                {
                    "container_names": metric_container_names,
                    "api_sub_factor": api_sub_factor,
                },
            )
        for api_metric in api_metrics:
            cls._require_refresh_object_identity(api_metric, "metric")
            cls._validate_api_factor_identity(api_metric, sub_factor_id, "metric")
            db_candidates = cls._find_matching_metric_rows(api_metric, calculation_metrics)
            if not db_candidates:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "API refresh metric cannot be matched to exactly one factor_ic_summary_metrics row",
                    {"api": api_metric, "database_metrics": calculation_metrics},
                )
            if len(db_candidates) > 1:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "API refresh metric matches multiple factor_ic_summary_metrics rows",
                    {"api": api_metric, "database_metrics": db_candidates},
                )
            db_metric = db_candidates[0]
            cls._validate_database_refresh_identity(db_metric, sub_factor_id, "metric")
            compared_fields = cls._compare_metric_fields(api_metric, db_metric)
            if not compared_fields:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "API refresh metric contains no comparable calculation field",
                    {"api": api_metric, "database_metric": db_metric},
                )
            matches.append(
                {
                    "kind": "metric",
                    "api_identity": cls._metric_identity(api_metric),
                    "db_summary_id": db_metric.get("summary_id", db_metric.get("id")),
                    "fields": tuple(compared_fields),
                }
            )

        api_validities = cls._extract_api_validity_objects(api_sub_factor)
        if validity_container_names and not api_validities:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "API refresh validity container is present but contains no comparable validity object",
                {
                    "container_names": validity_container_names,
                    "api_sub_factor": api_sub_factor,
                },
            )
        for api_validity in api_validities:
            cls._require_refresh_object_identity(api_validity, "validity")
            cls._validate_api_factor_identity(api_validity, sub_factor_id, "validity")
            db_candidates = cls._find_matching_validity_rows(api_validity, validity_snapshots)
            if not db_candidates:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "API refresh validity cannot be matched to exactly one factor_validity_status row",
                    {"api": api_validity, "database_validity": validity_snapshots},
                )
            if len(db_candidates) > 1:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "API refresh validity matches multiple factor_validity_status rows",
                    {"api": api_validity, "database_validity": db_candidates},
                )
            db_validity = db_candidates[0]
            cls._validate_database_refresh_identity(db_validity, sub_factor_id, "validity")
            compared_fields = cls._compare_validity_fields(api_validity, db_validity)
            if not compared_fields:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "API refresh validity contains no comparable validity field",
                    {"api": api_validity, "database_validity": db_candidates[0]},
                )
            matches.append(
                {
                    "kind": "validity",
                    "api_identity": {key: api_validity.get(key) for key in ("id", "run_id")},
                    "db_validity_id": db_validity.get("id"),
                    "fields": tuple(compared_fields),
                }
            )
        if not matches:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "API refresh response exposed evidence containers but no API/DB field was compared",
                {"api_sub_factor": api_sub_factor},
            )
        return matches

    @classmethod
    def _validate_database_refresh_identity(
        cls,
        database_row: Mapping[str, Any],
        expected_factor_id: int,
        object_kind: str,
    ) -> None:
        """校验刷新对账命中的数据库行确实属于目标子因子。

        参数 ``database_row`` 是从 ``factor_ic_summary_metrics`` 或 ``factor_validity_status`` 命中的数据库行，
        ``expected_factor_id`` 是登记后目标子因子 ID，``object_kind`` 只能是 ``metric`` 或 ``validity``。
        不返回值；数据库行缺少因子归属字段、指向其他因子或未标记为子因子时抛出 ``FAIL_CONTRACT``。该校验即使
        调用方已经提前按 ID 查询，也必须保留，避免错误的 Repository 实现、测试替身或后续重构把其他因子的行带入对账。
        """

        if object_kind not in {"metric", "validity"}:
            raise ValueError(f"Unsupported refresh object kind: {object_kind}")
        details = {
            "expected_factor_id": expected_factor_id,
            "object_kind": object_kind,
            "database_row": dict(database_row),
        }
        if "factor_id" not in database_row or database_row.get("factor_id") is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"database refresh {object_kind} row is missing factor_id",
                details,
            )
        database_factor_id = cls._positive_int_or_failure(
            database_row.get("factor_id"),
            FlowOutcome.FAIL_CONTRACT,
            f"database refresh {object_kind} factor_id is invalid",
            details,
        )
        if database_factor_id != expected_factor_id:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"database refresh {object_kind} belongs to another factor",
                details,
            )
        if "is_sub_factor_id" not in database_row or database_row.get("is_sub_factor_id") is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"database refresh {object_kind} row is missing is_sub_factor_id",
                details,
            )
        if not cls._same_scalar(database_row.get("is_sub_factor_id"), True):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"database refresh {object_kind} row is not marked as a sub-factor",
                details,
            )

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
    def _require_refresh_object_identity(cls, value: dict[str, Any], object_kind: str) -> None:
        """要求刷新指标或有效性对象具备可唯一定位数据库行的身份。

        参数 ``value`` 是详情接口返回的指标/有效性对象，``object_kind`` 只能是 ``metric`` 或 ``validity``。不返回
        值；对象必须提供主键/汇总 ID，或提供 Run 加窗口维度的组合身份。只有数值字段（例如单独的 ``mean_ic``）
        不足以证明它对应哪一条数据库记录，遇到这种响应直接抛出 ``FAIL_CONTRACT``。
        """

        if object_kind == "metric":
            identity_fields = ("summary_id", "id")
            has_primary_identity = any(
                field_name in value and value[field_name] is not None
                for field_name in identity_fields
            )
            has_run_dimension_identity = (
                "run_id" in value
                and value.get("run_id") is not None
                and "ic_scope" in value
                and value.get("ic_scope") is not None
                and "window_scope" in value
                and value.get("window_scope") is not None
                and any(
                    field_name in value and value[field_name] is not None
                    for field_name in (
                        "universe_key",
                        "symbol",
                        "period_start",
                        "period_end",
                        "metric_window_bars",
                        "metric_window_days",
                    )
                )
            )
        elif object_kind == "validity":
            has_primary_identity = "id" in value and value.get("id") is not None
            has_run_dimension_identity = (
                "run_id" in value
                and value.get("run_id") is not None
                and any(
                    field_name in value and value[field_name] is not None
                    for field_name in (
                        "time_series_summary_id",
                        "cross_sectional_summary_id",
                        "period_start",
                        "period_end",
                    )
                )
            )
        else:
            raise ValueError(f"Unsupported refresh object kind: {object_kind}")

        null_identity_fields = identity_fields if object_kind == "metric" else ("id", "run_id")
        for field_name in null_identity_fields:
            if field_name in value and value[field_name] is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"API refresh {object_kind} {field_name} must not be null when returned",
                    value,
                )
        if not has_primary_identity and not has_run_dimension_identity:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"API refresh {object_kind} has no strong database identity",
                value,
            )

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

        if "factor_id" in api_object and api_object.get("factor_id") is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"API refresh {object_kind} factor_id must not be null when returned",
                {"expected_factor_id": expected_factor_id, "api": api_object},
            )
        if "factor_id" in api_object:
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
        if "is_sub_factor_id" in api_object and api_object.get("is_sub_factor_id") is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"API refresh {object_kind} is_sub_factor_id must not be null when returned",
                {"expected_factor_id": expected_factor_id, "api": api_object},
            )
        if "is_sub_factor_id" in api_object and not cls._same_scalar(
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
        # ``summary_id`` 与普通 ``id`` 即使当前数值相同，也代表不同的契约语义，不能交叉兜底。
        for api_key, db_key in (("summary_id", "summary_id"), ("id", "id")):
            if api_key not in api_metric:
                continue
            if api_metric[api_key] is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"API refresh metric {api_key} must not be null when returned",
                    api_metric,
                )
            expected = cls._positive_int_or_failure(
                api_metric[api_key],
                FlowOutcome.FAIL_CONTRACT,
                "API refresh metric summary id is invalid",
                api_metric,
            )
            candidates = [
                row
                for row in candidates
                if cls._safe_int(row.get(db_key)) == expected
            ]
        if "run_id" in api_metric and api_metric["run_id"] is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "API refresh metric run_id must not be null when returned",
                api_metric,
            )
        if "run_id" in api_metric:
            run_id = cls._required_non_empty_string_or_failure(
                api_metric["run_id"],
                FlowOutcome.FAIL_CONTRACT,
                "API refresh metric run_id is invalid",
                api_metric,
            )
            candidates = [row for row in candidates if str(row.get("run_id", "")).strip() == run_id]
        for key in ("factor_id", "is_sub_factor_id"):
            if key not in api_metric:
                continue
            if api_metric[key] is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"API refresh metric {key} must not be null when returned",
                    api_metric,
                )
            candidates = [
                row
                for row in candidates
                if key in row and cls._same_identity_scalar(key, row.get(key), api_metric[key])
            ]
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
            if key not in api_metric:
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
        if "id" in api_validity and api_validity["id"] is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "API refresh validity id must not be null when returned",
                api_validity,
            )
        if "id" in api_validity:
            expected = cls._positive_int_or_failure(
                api_validity["id"],
                FlowOutcome.FAIL_CONTRACT,
                "API refresh validity id is invalid",
                api_validity,
            )
            candidates = [row for row in candidates if cls._safe_int(row.get("id")) == expected]
        if "run_id" in api_validity and api_validity["run_id"] is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "API refresh validity run_id must not be null when returned",
                api_validity,
            )
        if "run_id" in api_validity:
            expected_run_id = cls._required_non_empty_string_or_failure(
                api_validity["run_id"],
                FlowOutcome.FAIL_CONTRACT,
                "API refresh validity run_id is invalid",
                api_validity,
            )
            candidates = [row for row in candidates if str(row.get("run_id", "")).strip() == expected_run_id]
        for key in ("factor_id", "is_sub_factor_id"):
            if key not in api_validity:
                continue
            if api_validity[key] is None:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"API refresh validity {key} must not be null when returned",
                    api_validity,
                )
            candidates = [
                row
                for row in candidates
                if key in row and cls._same_identity_scalar(key, row.get(key), api_validity[key])
            ]
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
            if key not in api_validity:
                continue
            if key.endswith("_summary_id") or key.endswith("_summary_run_id"):
                if api_validity[key] is None:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"API refresh validity {key} must not be null when returned",
                        api_validity,
                    )
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
            present_fields = [field for field in db_fields if field in db_metric]
            if not present_fields:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"DB refresh metric is missing field required by API: {api_field}",
                    {"field": api_field, "db_fields": db_fields, "api_metric": api_metric, "db_metric": db_metric},
                )
            db_field = present_fields[0]
            db_value = db_metric[db_field]
            if not cls._same_identity_scalar(api_field, api_metric[api_field], db_value):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"API and DB refresh metric differ at {api_field}",
                    {"field": api_field, "api": api_metric[api_field], "db": db_value, "api_metric": api_metric},
                )
            for alias_field in present_fields[1:]:
                if not cls._same_identity_scalar(api_field, db_value, db_metric[alias_field]):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        f"DB refresh metric alias columns conflict for {api_field}",
                        {
                            "field": api_field,
                            "database_fields": present_fields,
                            "database_values": {field: db_metric[field] for field in present_fields},
                        },
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

    @staticmethod
    def _extract_refresh_run_contexts(refresh_data: dict[str, Any]) -> list[dict[str, Any]]:
        """提取 Performance Refresh 结果中明确返回的 Run 配置上下文。

        参数 ``refresh_data`` 是刷新任务完成响应的 data 对象。返回按出现顺序收集的 Run 上下文；只读取结果对象中
        与 Run 关联的字段，不把刷新任务本身的 ``task_id`` 当作指标 Run。接口只返回 Run ID 时，返回对象也只包含
        ``run_id``，由数据库主表继续完成配置核验。
        """

        run_keys = (
            "run_id",
            "ic_run_id",
            "factor_ic_run_id",
            "summary_run_id",
            "time_series_run_id",
            "cross_sectional_run_id",
        )
        context_fields = (
            "status",
            "run_status",
            "factor_id",
            "is_sub_factor_id",
            "interval_value",
            "interval",
            "factor_bar_interval",
            "forward_return_horizon",
            "forward_return_bars",
            "universe_key",
            "config_hash",
            "config_json",
            "method",
            "weighting_method",
            "data_start",
            "data_end",
        )
        contexts: list[dict[str, Any]] = []

        def visit(value: Any) -> None:
            """递归遍历刷新结果对象。"""

            if isinstance(value, dict):
                run_id: str | None = None
                for key in run_keys:
                    candidate = value.get(key)
                    if isinstance(candidate, (str, int)) and not isinstance(candidate, bool) and str(candidate).strip():
                        run_id = str(candidate).strip()
                        break
                if run_id is not None:
                    context: dict[str, Any] = {"run_id": run_id}
                    for field_name in context_fields:
                        if field_name in value:
                            context[field_name] = value[field_name]
                    # 统一常见别名，后续只按标准字段对账。
                    if "run_status" in context and "status" not in context:
                        context["status"] = context["run_status"]
                    if "interval" in context and "interval_value" not in context:
                        context["interval_value"] = context["interval"]
                    if "factor_bar_interval" in context and "interval_value" not in context:
                        context["interval_value"] = context["factor_bar_interval"]
                    contexts.append(context)
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(refresh_data.get("results"))
        return contexts

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
