"""组合因子业务流程使用的上下文、结果和异常模型。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


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
    slice_metrics: tuple[dict[str, Any], ...] = ()
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
    registration_persistence: dict[str, Any] = field(default_factory=dict)
    core_metric_coverage: dict[str, Any] = field(default_factory=dict)
    formula_source_consistency: dict[str, Any] = field(default_factory=dict)


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
