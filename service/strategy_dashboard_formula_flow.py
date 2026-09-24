"""原始数据注入到计算结果的编排；尚未绑定到已部署的产品注入接口。

测试侧协议见 docs/strategy-dashboard-formula-injection.md。不会把本地 Oracle
结果交给服务端，也不会在缺少服务端身份/结果时回退到预期值。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import math
import time
from typing import Any
from uuid import uuid4

from api.strategy_dashboard_formula_api import FormulaInjectionContractError, StrategyDashboardFormulaAPI
from service.strategy_dashboard_formula_scenarios import FormulaScenario


class FormulaFlowBlocked(RuntimeError):
    """缺少真实注入接口或公式口径，不代表产品计算失败。"""


class FormulaFlowError(RuntimeError):
    """HTTP、任务、身份或资源清理失败。"""


@dataclass
class FormulaFlowResult:
    """单次真实请求、服务端身份、计算结果和清理证据。"""

    scenario_id: str
    request_id: str
    fixture_id: str | None = None
    run_id: str | None = None
    result: dict[str, Any] | None = None
    terminal_status: str | None = None
    cleanup_status: str = "not_created"
    events: list[dict[str, Any]] = field(default_factory=list)


def _body(response: Any, stage: str) -> dict[str, Any]:
    code = response.status_code
    if not 200 <= code < 300:
        raise FormulaFlowError(f"{stage}: HTTP {code}")
    try:
        payload = response.json()
    except ValueError:
        raise FormulaFlowError(f"{stage}: 非 JSON 响应") from None
    if not isinstance(payload, dict) or ("success" in payload and payload["success"] is not True):
        raise FormulaFlowError(f"{stage}: 非成功业务响应")
    if "code" in payload and (isinstance(payload["code"], bool) or payload["code"] not in (0, 200, "0", "200")):
        raise FormulaFlowError(f"{stage}: 业务错误码，需核对真实契约")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise FormulaFlowError(f"{stage}: 缺少 data 对象，需完成真实 schema 映射")
    return data


def _id(data: Mapping[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(c in value for c in "/\\?#%") or value in {".", ".."}:
        raise FormulaFlowError(f"响应缺少合法 {field_name}")
    return value


def _matches(data: Mapping[str, Any], **identity: str | None) -> None:
    for key, expected in identity.items():
        if expected is None or data.get(key) != expected:
            raise FormulaFlowError(f"{key} 与本次请求不匹配，拒绝使用别的测试/缓存结果")


class FormulaFlow:
    """执行写入、触发、轮询、读取和精确资源清理；业务值由 Case 断言。"""

    def __init__(self, api: StrategyDashboardFormulaAPI, *, timeout_seconds: float = 180,
                 poll_interval_seconds: float = 1,
                 clock: Callable[[], float] = time.monotonic,
                 pause: Callable[[float], None] = time.sleep,
                 evidence_sink: Callable[[FormulaFlowResult], None] | None = None) -> None:
        self.api = api
        self.timeout = timeout_seconds
        self.interval = poll_interval_seconds
        self.clock = clock
        self.pause = pause
        self.sink = evidence_sink
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or not math.isfinite(poll_interval_seconds) or not 0 < poll_interval_seconds <= 10:
            raise ValueError("轮询必须具有正有限时限，间隔不超过10秒")

    def _record(self, result: FormulaFlowResult, stage: str, **evidence: Any) -> None:
        result.events.append({"stage": stage, **evidence})
        if self.sink:
            self.sink(result)

    @contextmanager
    def execute(self, scenario: FormulaScenario) -> Iterator[FormulaFlowResult]:
        """准备并返回真实计算结果，Case 断言后清理；缺端点明确阻断。

        触发响应丢失、运行中或未知状态会保留资源和恢复标识，防止后台任务
        仍读取数据时提前删源记录。已知终态与未触发资源可清理；失败不会被吞掉。
        """
        try:
            self.api.endpoints.validate()
        except FormulaInjectionContractError as exc:
            raise FormulaFlowBlocked(f"BLOCKED_INJECTION_ENDPOINT: {exc}") from None
        if scenario.expected.get("status") == "blocked_contract":
            raise FormulaFlowBlocked(f"BLOCKED_CONTRACT: {scenario.scenario_id}")
        result = FormulaFlowResult(scenario.scenario_id, f"qtformula-{uuid4().hex}")
        trigger_attempted = False
        original_error: BaseException | None = None
        # 先保存客户端身份，写请求超时也可据此找回，绝不自动重复POST。
        self._record(result, "planned", family=scenario.family, source=dict(scenario.inputs))
        try:
            payload = {"request_id": result.request_id, "scenario_id": scenario.scenario_id,
                       "family": scenario.family, "source": dict(scenario.inputs)}
            response = self.api.write_fixture(payload)
            data = _body(response, "write_fixture")
            _matches(data, request_id=result.request_id)
            result.fixture_id = _id(data, "fixture_id")
            result.cleanup_status = "pending"
            self._record(result, "fixture_written", http_status=response.status_code, fixture_id=result.fixture_id)
            trigger_attempted = True
            response = self.api.trigger_calculation({"request_id": result.request_id, "fixture_id": result.fixture_id})
            data = _body(response, "trigger_calculation")
            _matches(data, request_id=result.request_id, fixture_id=result.fixture_id)
            result.run_id = _id(data, "run_id")
            self._record(result, "triggered", run_id=result.run_id)
            deadline = self.clock() + self.timeout
            while True:
                data = _body(self.api.get_status(result.run_id), "get_status")
                _matches(data, run_id=result.run_id, fixture_id=result.fixture_id)
                status = data.get("status")
                self._record(result, "polled", status=status)
                if status in {"completed", "failed", "cancelled"}:
                    result.terminal_status = status
                    if status != "completed":
                        raise FormulaFlowError(f"计算任务终态为 {status}")
                    break
                if status not in {"queued", "running"}:
                    raise FormulaFlowError("计算任务状态未知，保留测试资源")
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise FormulaFlowError("计算任务轮询超时，保留测试资源")
                self.pause(min(self.interval, remaining))
            data = _body(self.api.get_result(result.run_id), "get_result")
            _matches(data, request_id=result.request_id, fixture_id=result.fixture_id, run_id=result.run_id)
            actual = data.get("result")
            if not isinstance(actual, dict) or not actual:
                raise FormulaFlowError("结果接口缺少非空 result；不得回退到测试预期")
            result.result = actual
            self._record(result, "result_read", formula_result=actual)
            yield result
        except BaseException as exc:
            original_error = exc
            self._record(result, "execution_failed", error_type=type(exc).__name__)
            raise
        finally:
            if result.fixture_id and (not trigger_attempted or result.terminal_status is not None):
                try:
                    response = self.api.cleanup(result.fixture_id)
                    if response.status_code != 204:
                        data = _body(response, "cleanup")
                        _matches(data, fixture_id=result.fixture_id)
                        if data.get("deleted") is not True:
                            raise FormulaFlowError("清理响应没有确认 deleted=true")
                    result.cleanup_status = "deleted"
                    self._record(result, "cleanup", fixture_id=result.fixture_id, http_status=response.status_code)
                except Exception as cleanup_error:
                    result.cleanup_status = "failed"
                    self._record(result, "cleanup_failed", error_type=type(cleanup_error).__name__)
                    if original_error is not None:
                        original_error.add_note("测试资源清理失败，详见本次运行的证据文件")
                    else:
                        raise FormulaFlowError("清理测试数据失败，不能计作通过") from cleanup_error
            else:
                result.cleanup_status = "retained_unknown" if trigger_attempted or result.fixture_id is None else "not_created"
                self._record(result, "retained", reason="unknown_write_or_running_task")
